"""Isolated, non-authoritative receipt ledger for verified Kalshi FIX UMS reports.

This module records process-local, upstream-attested FIX material.  It does not
authenticate the transport, establish pagination completeness, bind a report to
canonical settlement state, calculate fee-net P&L, or update trading state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Literal

from utils.output_paths import DB_STATE_DIR


KALSHI_FIX_SETTLEMENT_INGRESS_DB = DB_STATE_DIR / "kalshi_fix_settlement_ingress.db"
KALSHI_FIX_SETTLEMENT_INGRESS_SOURCE_ID = "kalshi-fix-market-settlement-v1"
KALSHI_FIX_SETTLEMENT_INGRESS_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 5.0
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_MAX_SETTLEMENT_PRICE_CENTS = Decimal("100.00")


class KalshiFixSettlementIngressSchemaError(RuntimeError):
    """Raised when an existing receipt database differs from the v1 contract."""


class KalshiFixSettlementIngressValidationError(ValueError):
    """Typed validation failure suitable for a quarantined verified envelope."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_json(value: object) -> str:
    """Return the only JSON encoding stored by this receipt ledger."""
    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise KalshiFixSettlementIngressValidationError("message_key_must_be_text")
            result[key] = _json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise KalshiFixSettlementIngressValidationError("message_decimal_must_be_finite")
        return format(value, "f")
    raise KalshiFixSettlementIngressValidationError("message_contains_noncanonical_value")


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.strip().rstrip(";").lower().split()).replace(" if not exists ", " ")


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS fix_settlement_schema_meta (
        schema_version INTEGER PRIMARY KEY,
        ddl_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fix_settlement_reports (
        source_id TEXT NOT NULL,
        account_party_id_sha256 TEXT NOT NULL,
        market_settlement_report_id TEXT NOT NULL,
        market_id TEXT NOT NULL,
        clearing_business_date TEXT NOT NULL,
        market_result TEXT NOT NULL CHECK (market_result IN ('yes', 'no', 'scalar')),
        settlement_price_cents TEXT NOT NULL,
        message_json TEXT NOT NULL,
        message_sha256 TEXT NOT NULL,
        source_payload_json TEXT NOT NULL,
        source_payload_sha256 TEXT NOT NULL,
        first_received_at TEXT NOT NULL,
        PRIMARY KEY (source_id, account_party_id_sha256, market_settlement_report_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fix_settlement_wire_observations (
        observation_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_party_id_sha256 TEXT NOT NULL,
        market_settlement_report_id TEXT NOT NULL,
        message_sha256 TEXT NOT NULL,
        raw_fix BLOB NOT NULL,
        raw_fix_sha256 TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        provenance_sha256 TEXT NOT NULL,
        received_at TEXT NOT NULL,
        UNIQUE (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id,
            raw_fix_sha256
        ),
        FOREIGN KEY (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id
        ) REFERENCES fix_settlement_reports (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fix_settlement_conflicts (
        conflict_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        account_party_id_sha256 TEXT NOT NULL,
        market_settlement_report_id TEXT NOT NULL,
        accepted_message_sha256 TEXT NOT NULL,
        incoming_message_json TEXT NOT NULL,
        incoming_message_sha256 TEXT NOT NULL,
        raw_fix BLOB NOT NULL,
        raw_fix_sha256 TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        provenance_sha256 TEXT NOT NULL,
        received_at TEXT NOT NULL,
        conflict_reason TEXT NOT NULL,
        UNIQUE (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id,
            incoming_message_sha256,
            raw_fix_sha256
        ),
        FOREIGN KEY (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id
        ) REFERENCES fix_settlement_reports (
            source_id,
            account_party_id_sha256,
            market_settlement_report_id
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fix_settlement_quarantines (
        quarantine_id TEXT PRIMARY KEY,
        raw_fix BLOB NOT NULL,
        raw_fix_sha256 TEXT NOT NULL,
        message_json TEXT,
        message_sha256 TEXT,
        provenance_json TEXT,
        provenance_sha256 TEXT,
        received_at TEXT NOT NULL,
        reason TEXT NOT NULL
    )
    """,
)
_IMMUTABLE_TABLES = (
    "fix_settlement_schema_meta",
    "fix_settlement_reports",
    "fix_settlement_wire_observations",
    "fix_settlement_conflicts",
    "fix_settlement_quarantines",
)


def _immutable_triggers() -> tuple[tuple[str, str], ...]:
    statements: list[tuple[str, str]] = []
    for table in _IMMUTABLE_TABLES:
        for operation in ("UPDATE", "DELETE"):
            name = f"immutable_{table}_{operation.lower()}"
            statements.append(
                (
                    name,
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {name}
                    BEFORE {operation} ON {table}
                    BEGIN
                        SELECT RAISE(ABORT, 'kalshi fix settlement ingress is append-only');
                    END
                    """,
                )
            )
    return tuple(statements)


_EXPECTED_SCHEMA_SQL = {
    "fix_settlement_schema_meta": _normalize_schema_sql(_SCHEMA_STATEMENTS[0]),
    "fix_settlement_reports": _normalize_schema_sql(_SCHEMA_STATEMENTS[1]),
    "fix_settlement_wire_observations": _normalize_schema_sql(_SCHEMA_STATEMENTS[2]),
    "fix_settlement_conflicts": _normalize_schema_sql(_SCHEMA_STATEMENTS[3]),
    "fix_settlement_quarantines": _normalize_schema_sql(_SCHEMA_STATEMENTS[4]),
    **{name: _normalize_schema_sql(sql) for name, sql in _immutable_triggers()},
}
KALSHI_FIX_SETTLEMENT_INGRESS_DDL_SHA256 = sha256(
    "\n".join(f"{name}:{sql}" for name, sql in sorted(_EXPECTED_SCHEMA_SQL.items())).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class SessionProvenance:
    """Process-local facts supplied by the upstream FIX-session verifier."""

    verifier_name: str
    verifier_version: str
    session_fingerprint: str
    account_party_id_sha256: str
    received_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "verifier_name",
            "verifier_version",
            "session_fingerprint",
            "account_party_id_sha256",
        ):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be text")
        if not isinstance(self.received_at, datetime):
            raise TypeError("received_at must be datetime")


@dataclass(frozen=True)
class VerifiedKalshiFixUMSEnvelope:
    """One upstream-verified in-memory UMS envelope, never a file/import input."""

    raw_fix: bytes
    message: Mapping[str, object]
    provenance: SessionProvenance

    def __post_init__(self) -> None:
        if type(self.raw_fix) is not bytes:
            raise TypeError("raw_fix must be bytes")
        if not isinstance(self.message, Mapping):
            raise TypeError("message must be a Mapping")
        if not isinstance(self.provenance, SessionProvenance):
            raise TypeError("provenance must be SessionProvenance")
        object.__setattr__(self, "message", _freeze_mapping(self.message))


@dataclass(frozen=True)
class KalshiFixSettlementIngressRecord:
    source_id: str
    account_party_id_sha256: str
    report_id: str
    market_id: str
    clearing_business_date: str
    market_result: str
    settlement_price_cents: str
    message_json: str
    message_sha256: str
    source_payload_json: str
    source_payload_sha256: str
    first_received_at: datetime


@dataclass(frozen=True)
class KalshiFixSettlementWireObservation:
    observation_id: str
    source_id: str
    account_party_id_sha256: str
    report_id: str
    message_sha256: str
    raw_fix: bytes
    raw_fix_sha256: str
    provenance_json: str
    provenance_sha256: str
    received_at: datetime


@dataclass(frozen=True)
class KalshiFixSettlementIngressAppendResult:
    status: Literal["accepted", "identical", "retransmit", "conflict", "quarantined"]
    source_id: str | None
    account_party_id_sha256: str | None
    report_id: str | None
    message_json: str | None
    message_sha256: str | None
    raw_fix_sha256: str
    receipt_id: str | None
    reason: str | None


@dataclass(frozen=True)
class KalshiFixSettlementIngressSnapshot:
    status: Literal[
        "absent_non_authoritative",
        "captured_non_authoritative",
        "invalid_non_authoritative",
    ]
    exists: bool
    schema_valid: bool
    integrity_check: str
    report_count: int
    wire_observation_count: int
    conflict_count: int
    quarantine_count: int
    latest_received_at: datetime | None
    transport_authentication: Literal["upstream_attested_not_proven_by_ledger"]
    raw_capture: Literal["present", "absent"]
    session_provenance: Literal["present", "absent"]
    pagination_coverage: Literal["unknown"]
    canonical_settlement_binding: Literal["absent"]
    fee_net_pnl: Literal["unscorable"]
    paper_trader_updated: Literal[False]
    orders_changed: Literal[False]
    promotion_eligible: Literal[False]


@dataclass(frozen=True)
class _ValidatedEnvelope:
    source_id: str
    account_party_id_sha256: str
    report_id: str
    market_id: str
    clearing_business_date: str
    market_result: str
    settlement_price_cents: str
    message_json: str
    message_sha256: str
    source_payload_json: str
    source_payload_sha256: str
    provenance_json: str
    provenance_sha256: str
    received_at: datetime


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _require_provenance(provenance: SessionProvenance) -> tuple[str, str]:
    for name in ("verifier_name", "verifier_version", "session_fingerprint"):
        value = getattr(provenance, name)
        if not value or value != value.strip() or len(value) > 512:
            raise KalshiFixSettlementIngressValidationError(f"untrusted_{name}")
    account_hash = provenance.account_party_id_sha256
    if not _SHA256_RE.fullmatch(account_hash):
        raise KalshiFixSettlementIngressValidationError("untrusted_account_party_id_sha256")
    received_at = _require_aware_utc(provenance.received_at, "untrusted_received_at")
    payload = {
        "account_party_id_sha256": account_hash,
        "received_at": _utc_iso(received_at),
        "session_fingerprint": provenance.session_fingerprint,
        "verifier_name": provenance.verifier_name,
        "verifier_version": provenance.verifier_version,
    }
    provenance_json = canonical_json(payload)
    return provenance_json, sha256(provenance_json.encode("utf-8")).hexdigest()


def _require_aware_utc(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise KalshiFixSettlementIngressValidationError(code)
    return value.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _lookup(mapping: Mapping[str, object], names: tuple[str, ...], code: str) -> object:
    values = [(name, mapping[name]) for name in names if name in mapping]
    if not values:
        raise KalshiFixSettlementIngressValidationError(f"missing_{code}")
    first = values[0][1]
    if any(_safe_canonical(first) != _safe_canonical(value) for _, value in values[1:]):
        raise KalshiFixSettlementIngressValidationError(f"ambiguous_{code}")
    return first


def _safe_canonical(value: object) -> str:
    try:
        return canonical_json(value)
    except KalshiFixSettlementIngressValidationError:
        return repr(value)


def _required_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise KalshiFixSettlementIngressValidationError(code)
    return value


def _required_decimal(
    value: object,
    code: str,
    *,
    maximum: Decimal | None = None,
    fractional_places: int | None = None,
) -> str:
    if not isinstance(value, str) or not _DECIMAL_RE.fullmatch(value) or len(value) > 64:
        raise KalshiFixSettlementIngressValidationError(code)
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise KalshiFixSettlementIngressValidationError(code) from exc
    if not decimal_value.is_finite() or decimal_value < Decimal("0"):
        raise KalshiFixSettlementIngressValidationError(code)
    if maximum is not None and decimal_value > maximum:
        raise KalshiFixSettlementIngressValidationError(code)
    fractional = value.partition(".")[2]
    if fractional_places is not None and len(fractional) > fractional_places:
        raise KalshiFixSettlementIngressValidationError(code)
    if fractional_places is not None:
        return format(decimal_value.quantize(Decimal(1).scaleb(-fractional_places)), "f")
    normalized = format(decimal_value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _party_groups(message: Mapping[str, object]) -> list[Mapping[str, object]]:
    value = _lookup(
        message,
        ("20108", "NoMarketSettlementPartyIDs"),
        "market_settlement_party_groups",
    )
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)) or not value:
        raise KalshiFixSettlementIngressValidationError("invalid_market_settlement_party_groups")
    parties: list[Mapping[str, object]] = []
    for party in value:
        if not isinstance(party, Mapping):
            raise KalshiFixSettlementIngressValidationError("invalid_market_settlement_party")
        parties.append(party)
    return parties


def _fee_group(party: Mapping[str, object]) -> Mapping[str, object]:
    value = _lookup(party, ("136", "MiscFees"), "misc_fee_group")
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)) or len(value) != 1:
        raise KalshiFixSettlementIngressValidationError("invalid_misc_fee_group")
    fee = value[0]
    if not isinstance(fee, Mapping):
        raise KalshiFixSettlementIngressValidationError("invalid_misc_fee")
    count = party.get("NoMiscFees")
    if count is not None and count not in (1, "1"):
        raise KalshiFixSettlementIngressValidationError("invalid_misc_fee_count")
    return fee


def _validated_economics_message(
    message: Mapping[str, object],
    account_hash: str,
) -> tuple[str, str, str, str, str, str, str, dict[str, object]]:
    message_type = _required_text(_lookup(message, ("35", "MsgType"), "message_type"), "invalid_message_type")
    if message_type != "UMS":
        raise KalshiFixSettlementIngressValidationError("message_type_must_be_ums")
    report_id = _required_text(
        _lookup(message, ("20105", "MarketSettlementReportID"), "market_settlement_report_id"),
        "invalid_market_settlement_report_id",
    )
    market_id = _required_text(_lookup(message, ("55", "Symbol"), "symbol"), "invalid_symbol")
    clearing_date = _required_text(
        _lookup(message, ("715", "ClearingBusinessDate"), "clearing_business_date"),
        "invalid_clearing_business_date",
    )
    try:
        datetime.strptime(clearing_date, "%Y%m%d")
    except ValueError as exc:
        raise KalshiFixSettlementIngressValidationError("invalid_clearing_business_date") from exc
    result = _required_text(_lookup(message, ("20107", "MarketResult"), "market_result"), "invalid_market_result")
    if result not in {"yes", "no", "scalar"}:
        raise KalshiFixSettlementIngressValidationError("invalid_market_result")
    price = _required_decimal(
        _lookup(message, ("730", "SettlementPrice"), "settlement_price"),
        "invalid_settlement_price",
        maximum=_MAX_SETTLEMENT_PRICE_CENTS,
        fractional_places=2,
    )

    matching: list[tuple[str, str, str, str, Mapping[str, object]]] = []
    for party in _party_groups(message):
        party_id = _required_text(
            _lookup(party, ("20109", "MarketSettlementPartyID"), "market_settlement_party_id"),
            "invalid_market_settlement_party_id",
        )
        if sha256(party_id.encode("utf-8")).hexdigest() != account_hash:
            continue
        role = _required_text(
            _lookup(party, ("20110", "MarketSettlementPartyRole"), "market_settlement_party_role"),
            "invalid_market_settlement_party_role",
        )
        long_qty = _required_decimal(
            _lookup(party, ("704", "LongQty"), "long_qty"),
            "invalid_long_qty",
        )
        short_qty = _required_decimal(
            _lookup(party, ("705", "ShortQty"), "short_qty"),
            "invalid_short_qty",
        )
        matching.append((party_id, role, long_qty, short_qty, party))
    if len(matching) != 1 or matching[0][1] != "24":
        raise KalshiFixSettlementIngressValidationError("missing_bound_customer_account_party")
    party_id, _, long_qty, short_qty, bound_party = matching[0]
    fee = _fee_group(bound_party)
    fee_amount = _required_decimal(
        _lookup(fee, ("137", "MiscFeeAmt"), "misc_fee_amount"),
        "invalid_misc_fee_amount",
    )
    currency = _required_text(_lookup(fee, ("138", "MiscFeeCurr"), "misc_fee_currency"), "invalid_misc_fee_currency")
    fee_type = _required_text(_lookup(fee, ("139", "MiscFeeType"), "misc_fee_type"), "invalid_misc_fee_type")
    fee_basis = _required_text(_lookup(fee, ("891", "MiscFeeBasis"), "misc_fee_basis"), "invalid_misc_fee_basis")
    if currency != "USD" or fee_type not in {"4", "exchange"} or fee_basis not in {"0", "absolute"}:
        raise KalshiFixSettlementIngressValidationError("invalid_usd_exchange_fee")
    economics_message = {
        "ClearingBusinessDate": clearing_date,
        "MarketResult": result,
        "MarketSettlementReportID": report_id,
        "NoMarketSettlementPartyIDs": [
            {
                "LongQty": long_qty,
                "MarketSettlementPartyID": party_id,
                "MarketSettlementPartyRole": "24",
                "MiscFees": [
                    {
                        "MiscFeeAmt": fee_amount,
                        "MiscFeeBasis": "0",
                        "MiscFeeCurr": "USD",
                        "MiscFeeType": "4",
                    }
                ],
                "NoMiscFees": "1",
                "ShortQty": short_qty,
            }
        ],
        "SettlementPrice": price,
        "Symbol": market_id,
    }
    return report_id, market_id, clearing_date, result, price, party_id, fee_amount, economics_message


def validate_verified_kalshi_fix_ums_envelope(
    envelope: VerifiedKalshiFixUMSEnvelope,
) -> _ValidatedEnvelope:
    """Validate one typed process-local envelope without any database or network I/O."""
    if not isinstance(envelope, VerifiedKalshiFixUMSEnvelope):
        raise TypeError("envelope must be a VerifiedKalshiFixUMSEnvelope")
    provenance_json, provenance_sha256 = _require_provenance(envelope.provenance)
    (
        report_id,
        market_id,
        clearing_date,
        result,
        price,
        _,
        _,
        economics_message,
    ) = _validated_economics_message(envelope.message, envelope.provenance.account_party_id_sha256)
    message_json = canonical_json(envelope.message)
    message_sha256 = sha256(message_json.encode("utf-8")).hexdigest()
    economics_message_json = canonical_json(economics_message)
    source_payload_json = canonical_json(
        {
            "market_id": market_id,
            "settlement_fee_receipt": {
                "message": json.loads(economics_message_json),
                "message_sha256": sha256(economics_message_json.encode("utf-8")).hexdigest(),
            },
        }
    )
    return _ValidatedEnvelope(
        source_id=KALSHI_FIX_SETTLEMENT_INGRESS_SOURCE_ID,
        account_party_id_sha256=envelope.provenance.account_party_id_sha256,
        report_id=report_id,
        market_id=market_id,
        clearing_business_date=clearing_date,
        market_result=result,
        settlement_price_cents=price,
        message_json=message_json,
        message_sha256=message_sha256,
        source_payload_json=source_payload_json,
        source_payload_sha256=sha256(source_payload_json.encode("utf-8")).hexdigest(),
        provenance_json=provenance_json,
        provenance_sha256=provenance_sha256,
        received_at=_require_aware_utc(envelope.provenance.received_at, "untrusted_received_at"),
    )


def _open_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _schema_contract_matches(conn: sqlite3.Connection) -> bool:
    try:
        meta = conn.execute("SELECT schema_version, ddl_sha256 FROM fix_settlement_schema_meta").fetchall()
        if meta != [(KALSHI_FIX_SETTLEMENT_INGRESS_SCHEMA_VERSION, KALSHI_FIX_SETTLEMENT_INGRESS_DDL_SHA256)]:
            return False
        actual = {
            str(name): _normalize_schema_sql(str(sql))
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'trigger') "
                "AND (name LIKE 'fix_settlement_%' OR name LIKE 'immutable_fix_settlement_%')"
            ).fetchall()
            if sql is not None
        }
        return actual == _EXPECTED_SCHEMA_SQL
    except sqlite3.DatabaseError:
        return False


def _schema_objects_match(conn: sqlite3.Connection) -> bool:
    try:
        actual = {
            str(name): _normalize_schema_sql(str(sql))
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'trigger') "
                "AND (name LIKE 'fix_settlement_%' OR name LIKE 'immutable_fix_settlement_%')"
            ).fetchall()
            if sql is not None
        }
        return actual == _EXPECTED_SCHEMA_SQL
    except sqlite3.DatabaseError:
        return False


def _receipt_id(
    source_id: str,
    account_hash: str,
    report_id: str,
    message_hash: str,
    raw_hash: str,
    kind: str,
) -> str:
    return sha256(
        "\x1f".join((kind, source_id, account_hash, report_id, message_hash, raw_hash)).encode("utf-8")
    ).hexdigest()


def _safe_message_json(envelope: VerifiedKalshiFixUMSEnvelope) -> tuple[str | None, str | None]:
    try:
        payload = canonical_json(envelope.message)
    except KalshiFixSettlementIngressValidationError:
        return None, None
    return payload, sha256(payload.encode("utf-8")).hexdigest()


def _safe_provenance_json(envelope: VerifiedKalshiFixUMSEnvelope) -> tuple[str | None, str | None, str]:
    provenance = envelope.provenance
    received_at = provenance.received_at
    received_text = received_at.isoformat() if isinstance(received_at, datetime) else "unknown"
    try:
        payload = canonical_json(
            {
                "account_party_id_sha256": provenance.account_party_id_sha256,
                "received_at": received_text,
                "session_fingerprint": provenance.session_fingerprint,
                "verifier_name": provenance.verifier_name,
                "verifier_version": provenance.verifier_version,
            }
        )
    except KalshiFixSettlementIngressValidationError:
        return None, None, received_text
    return payload, sha256(payload.encode("utf-8")).hexdigest(), received_text


class KalshiFixSettlementIngressStore:
    """Explicit writer for the isolated append-only UMS ingress ledger."""

    def __init__(
        self,
        db_path: Path | str = KALSHI_FIX_SETTLEMENT_INGRESS_DB,
        *,
        existing_only: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.existing_only = existing_only

    def initialize(self, *, applied_at: datetime | None = None) -> bool:
        """Create/validate the writer schema only after an explicit caller action."""
        if self.existing_only:
            return False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _require_aware_utc(applied_at or datetime.now(UTC), "invalid_applied_at")
        with _open_writable(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA_STATEMENTS:
                    conn.execute(statement)
                for _, statement in _immutable_triggers():
                    conn.execute(statement)
                meta = conn.execute("SELECT schema_version, ddl_sha256 FROM fix_settlement_schema_meta").fetchall()
                if not meta:
                    if not _schema_objects_match(conn):
                        raise KalshiFixSettlementIngressSchemaError("FIX ingress schema objects do not match")
                    conn.execute(
                        "INSERT INTO fix_settlement_schema_meta (schema_version, ddl_sha256, applied_at) "
                        "VALUES (?, ?, ?)",
                        (
                            KALSHI_FIX_SETTLEMENT_INGRESS_SCHEMA_VERSION,
                            KALSHI_FIX_SETTLEMENT_INGRESS_DDL_SHA256,
                            _utc_iso(timestamp),
                        ),
                    )
                if not _schema_contract_matches(conn):
                    raise KalshiFixSettlementIngressSchemaError("FIX ingress schema contract does not match")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return True

    def append_verified_envelope(
        self,
        envelope: VerifiedKalshiFixUMSEnvelope,
    ) -> KalshiFixSettlementIngressAppendResult:
        """Append exactly one typed in-memory envelope or quarantine invalid material."""
        if not isinstance(envelope, VerifiedKalshiFixUMSEnvelope):
            raise TypeError("envelope must be a VerifiedKalshiFixUMSEnvelope")
        if self.existing_only:
            raise RuntimeError("existing_only store cannot append")
        if not self.db_path.is_file():
            raise FileNotFoundError(f"FIX ingress store is not initialized: {self.db_path}")
        with _open_writable(self.db_path) as conn:
            if not _schema_contract_matches(conn):
                raise KalshiFixSettlementIngressSchemaError("FIX ingress schema contract does not match")
            try:
                validated = validate_verified_kalshi_fix_ums_envelope(envelope)
            except KalshiFixSettlementIngressValidationError as exc:
                return self._append_quarantine(conn, envelope, exc.code)
            return self._append_validated(conn, envelope, validated)

    def _append_validated(
        self,
        conn: sqlite3.Connection,
        envelope: VerifiedKalshiFixUMSEnvelope,
        validated: _ValidatedEnvelope,
    ) -> KalshiFixSettlementIngressAppendResult:
        raw_hash = sha256(envelope.raw_fix).hexdigest()
        key = (validated.source_id, validated.account_party_id_sha256, validated.report_id)
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT message_json, message_sha256 FROM fix_settlement_reports "
                "WHERE source_id = ? AND account_party_id_sha256 = ? AND market_settlement_report_id = ?",
                key,
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO fix_settlement_reports ("
                    "source_id, account_party_id_sha256, market_settlement_report_id, market_id, "
                    "clearing_business_date, market_result, settlement_price_cents, message_json, "
                    "message_sha256, source_payload_json, source_payload_sha256, first_received_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        validated.source_id,
                        validated.account_party_id_sha256,
                        validated.report_id,
                        validated.market_id,
                        validated.clearing_business_date,
                        validated.market_result,
                        validated.settlement_price_cents,
                        validated.message_json,
                        validated.message_sha256,
                        validated.source_payload_json,
                        validated.source_payload_sha256,
                        _utc_iso(validated.received_at),
                    ),
                )
                self._insert_wire_observation(conn, envelope, validated, raw_hash)
                conn.commit()
                return _append_result("accepted", validated, raw_hash, None)
            accepted_json, accepted_hash = str(existing[0]), str(existing[1])
            if accepted_hash != validated.message_sha256 or accepted_json != validated.message_json:
                result = self._append_conflict(conn, envelope, validated, raw_hash, accepted_hash)
                conn.commit()
                return result
            existing_wire = conn.execute(
                "SELECT raw_fix FROM fix_settlement_wire_observations WHERE source_id = ? "
                "AND account_party_id_sha256 = ? AND market_settlement_report_id = ? "
                "AND raw_fix_sha256 = ?",
                (*key, raw_hash),
            ).fetchone()
            if existing_wire is not None and bytes(existing_wire[0]) == envelope.raw_fix:
                conn.commit()
                return _append_result("identical", validated, raw_hash, None)
            self._insert_wire_observation(conn, envelope, validated, raw_hash)
            conn.commit()
            return _append_result("retransmit", validated, raw_hash, None)
        except Exception:
            conn.rollback()
            raise

    def _insert_wire_observation(
        self,
        conn: sqlite3.Connection,
        envelope: VerifiedKalshiFixUMSEnvelope,
        validated: _ValidatedEnvelope,
        raw_hash: str,
    ) -> None:
        observation_id = _receipt_id(
            validated.source_id,
            validated.account_party_id_sha256,
            validated.report_id,
            validated.message_sha256,
            raw_hash,
            "wire",
        )
        conn.execute(
            "INSERT INTO fix_settlement_wire_observations ("
            "observation_id, source_id, account_party_id_sha256, market_settlement_report_id, "
            "message_sha256, raw_fix, raw_fix_sha256, provenance_json, provenance_sha256, received_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                validated.source_id,
                validated.account_party_id_sha256,
                validated.report_id,
                validated.message_sha256,
                envelope.raw_fix,
                raw_hash,
                validated.provenance_json,
                validated.provenance_sha256,
                _utc_iso(validated.received_at),
            ),
        )

    def _append_conflict(
        self,
        conn: sqlite3.Connection,
        envelope: VerifiedKalshiFixUMSEnvelope,
        validated: _ValidatedEnvelope,
        raw_hash: str,
        accepted_hash: str,
    ) -> KalshiFixSettlementIngressAppendResult:
        conflict_id = _receipt_id(
            validated.source_id,
            validated.account_party_id_sha256,
            validated.report_id,
            validated.message_sha256,
            raw_hash,
            "conflict",
        )
        exists = conn.execute(
            "SELECT conflict_id FROM fix_settlement_conflicts WHERE conflict_id = ?",
            (conflict_id,),
        ).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO fix_settlement_conflicts ("
                "conflict_id, source_id, account_party_id_sha256, market_settlement_report_id, "
                "accepted_message_sha256, incoming_message_json, incoming_message_sha256, raw_fix, "
                "raw_fix_sha256, provenance_json, provenance_sha256, received_at, conflict_reason"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conflict_id,
                    validated.source_id,
                    validated.account_party_id_sha256,
                    validated.report_id,
                    accepted_hash,
                    validated.message_json,
                    validated.message_sha256,
                    envelope.raw_fix,
                    raw_hash,
                    validated.provenance_json,
                    validated.provenance_sha256,
                    _utc_iso(validated.received_at),
                    "identity_message_mismatch",
                ),
            )
        return _append_result("conflict", validated, raw_hash, "identity_message_mismatch", conflict_id)

    def _append_quarantine(
        self,
        conn: sqlite3.Connection,
        envelope: VerifiedKalshiFixUMSEnvelope,
        reason: str,
    ) -> KalshiFixSettlementIngressAppendResult:
        message_json, message_hash = _safe_message_json(envelope)
        provenance_json, provenance_hash, received_at = _safe_provenance_json(envelope)
        raw_hash = sha256(envelope.raw_fix).hexdigest()
        quarantine_id = sha256(
            "\x1f".join(("quarantine", raw_hash, message_hash or "", provenance_hash or "", reason)).encode("utf-8")
        ).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            exists = conn.execute(
                "SELECT quarantine_id FROM fix_settlement_quarantines WHERE quarantine_id = ?",
                (quarantine_id,),
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO fix_settlement_quarantines ("
                    "quarantine_id, raw_fix, raw_fix_sha256, message_json, message_sha256, "
                    "provenance_json, provenance_sha256, received_at, reason"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        quarantine_id,
                        envelope.raw_fix,
                        raw_hash,
                        message_json,
                        message_hash,
                        provenance_json,
                        provenance_hash,
                        received_at,
                        reason,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return KalshiFixSettlementIngressAppendResult(
            status="quarantined",
            source_id=None,
            account_party_id_sha256=None,
            report_id=None,
            message_json=message_json,
            message_sha256=message_hash,
            raw_fix_sha256=raw_hash,
            receipt_id=quarantine_id,
            reason=reason,
        )

    def snapshot(self) -> KalshiFixSettlementIngressSnapshot:
        return read_kalshi_fix_settlement_ingress_snapshot(self.db_path)

    def records(self) -> tuple[KalshiFixSettlementIngressRecord, ...]:
        return read_kalshi_fix_settlement_ingress_records(self.db_path)

    def wire_observations(self) -> tuple[KalshiFixSettlementWireObservation, ...]:
        return read_kalshi_fix_settlement_wire_observations(self.db_path)


def _append_result(
    status: Literal["accepted", "identical", "retransmit", "conflict"],
    validated: _ValidatedEnvelope,
    raw_hash: str,
    reason: str | None,
    receipt_id: str | None = None,
) -> KalshiFixSettlementIngressAppendResult:
    return KalshiFixSettlementIngressAppendResult(
        status=status,
        source_id=validated.source_id,
        account_party_id_sha256=validated.account_party_id_sha256,
        report_id=validated.report_id,
        message_json=validated.message_json,
        message_sha256=validated.message_sha256,
        raw_fix_sha256=raw_hash,
        receipt_id=receipt_id,
        reason=reason,
    )


def _absent_snapshot() -> KalshiFixSettlementIngressSnapshot:
    return KalshiFixSettlementIngressSnapshot(
        status="absent_non_authoritative",
        exists=False,
        schema_valid=False,
        integrity_check="not_applicable",
        report_count=0,
        wire_observation_count=0,
        conflict_count=0,
        quarantine_count=0,
        latest_received_at=None,
        transport_authentication="upstream_attested_not_proven_by_ledger",
        raw_capture="absent",
        session_provenance="absent",
        pagination_coverage="unknown",
        canonical_settlement_binding="absent",
        fee_net_pnl="unscorable",
        paper_trader_updated=False,
        orders_changed=False,
        promotion_eligible=False,
    )


def _invalid_snapshot(integrity_check: str) -> KalshiFixSettlementIngressSnapshot:
    return KalshiFixSettlementIngressSnapshot(
        status="invalid_non_authoritative",
        exists=True,
        schema_valid=False,
        integrity_check=integrity_check,
        report_count=0,
        wire_observation_count=0,
        conflict_count=0,
        quarantine_count=0,
        latest_received_at=None,
        transport_authentication="upstream_attested_not_proven_by_ledger",
        raw_capture="absent",
        session_provenance="absent",
        pagination_coverage="unknown",
        canonical_settlement_binding="absent",
        fee_net_pnl="unscorable",
        paper_trader_updated=False,
        orders_changed=False,
        promotion_eligible=False,
    )


def read_kalshi_fix_settlement_ingress_snapshot(
    db_path: Path | str = KALSHI_FIX_SETTLEMENT_INGRESS_DB,
) -> KalshiFixSettlementIngressSnapshot:
    """Inspect an existing store with SQLite ``mode=ro`` and never create a file."""
    path = Path(db_path)
    if not path.is_file():
        return _absent_snapshot()
    try:
        with _open_read_only(path) as conn:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok" or not _schema_contract_matches(conn):
                return _invalid_snapshot(integrity)
            report_count = int(conn.execute("SELECT COUNT(*) FROM fix_settlement_reports").fetchone()[0])
            wire_count = int(conn.execute("SELECT COUNT(*) FROM fix_settlement_wire_observations").fetchone()[0])
            conflict_count = int(conn.execute("SELECT COUNT(*) FROM fix_settlement_conflicts").fetchone()[0])
            quarantine_count = int(conn.execute("SELECT COUNT(*) FROM fix_settlement_quarantines").fetchone()[0])
            raw_count = wire_count + conflict_count + quarantine_count
            provenance_count = sum(
                int(row[0])
                for row in conn.execute(
                    "SELECT COUNT(*) FROM fix_settlement_wire_observations WHERE provenance_json IS NOT NULL "
                    "UNION ALL SELECT COUNT(*) FROM fix_settlement_conflicts WHERE provenance_json IS NOT NULL "
                    "UNION ALL SELECT COUNT(*) FROM fix_settlement_quarantines WHERE provenance_json IS NOT NULL"
                ).fetchall()
            )
            latest = conn.execute(
                "SELECT MAX(received_at) FROM ("
                "SELECT received_at FROM fix_settlement_wire_observations "
                "UNION ALL SELECT received_at FROM fix_settlement_conflicts "
                "UNION ALL SELECT received_at FROM fix_settlement_quarantines"
                ")"
            ).fetchone()[0]
            captured = raw_count > 0
            return KalshiFixSettlementIngressSnapshot(
                status=(
                    "captured_non_authoritative"
                    if captured
                    else "absent_non_authoritative"
                ),
                exists=True,
                schema_valid=True,
                integrity_check=integrity,
                report_count=report_count,
                wire_observation_count=wire_count,
                conflict_count=conflict_count,
                quarantine_count=quarantine_count,
                latest_received_at=_parse_utc(latest) if latest is not None else None,
                transport_authentication="upstream_attested_not_proven_by_ledger",
                raw_capture="present" if captured else "absent",
                session_provenance="present" if captured and provenance_count else "absent",
                pagination_coverage="unknown",
                canonical_settlement_binding="absent",
                fee_net_pnl="unscorable",
                paper_trader_updated=False,
                orders_changed=False,
                promotion_eligible=False,
            )
    except (OSError, sqlite3.DatabaseError, ValueError):
        return _invalid_snapshot("unreadable")


def read_kalshi_fix_settlement_ingress_records(
    db_path: Path | str = KALSHI_FIX_SETTLEMENT_INGRESS_DB,
) -> tuple[KalshiFixSettlementIngressRecord, ...]:
    path = Path(db_path)
    if not path.is_file():
        return ()
    try:
        with _open_read_only(path) as conn:
            if not _schema_contract_matches(conn):
                return ()
            rows = conn.execute(
                "SELECT source_id, account_party_id_sha256, market_settlement_report_id, market_id, "
                "clearing_business_date, market_result, settlement_price_cents, message_json, "
                "message_sha256, source_payload_json, source_payload_sha256, first_received_at "
                "FROM fix_settlement_reports ORDER BY first_received_at, market_settlement_report_id"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError):
        return ()
    return tuple(
        KalshiFixSettlementIngressRecord(
            source_id=str(row[0]),
            account_party_id_sha256=str(row[1]),
            report_id=str(row[2]),
            market_id=str(row[3]),
            clearing_business_date=str(row[4]),
            market_result=str(row[5]),
            settlement_price_cents=str(row[6]),
            message_json=str(row[7]),
            message_sha256=str(row[8]),
            source_payload_json=str(row[9]),
            source_payload_sha256=str(row[10]),
            first_received_at=_parse_utc(str(row[11])),
        )
        for row in rows
    )


def read_kalshi_fix_settlement_wire_observations(
    db_path: Path | str = KALSHI_FIX_SETTLEMENT_INGRESS_DB,
) -> tuple[KalshiFixSettlementWireObservation, ...]:
    path = Path(db_path)
    if not path.is_file():
        return ()
    try:
        with _open_read_only(path) as conn:
            if not _schema_contract_matches(conn):
                return ()
            rows = conn.execute(
                "SELECT observation_id, source_id, account_party_id_sha256, market_settlement_report_id, "
                "message_sha256, raw_fix, raw_fix_sha256, provenance_json, provenance_sha256, received_at "
                "FROM fix_settlement_wire_observations ORDER BY received_at, observation_id"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError):
        return ()
    return tuple(
        KalshiFixSettlementWireObservation(
            observation_id=str(row[0]),
            source_id=str(row[1]),
            account_party_id_sha256=str(row[2]),
            report_id=str(row[3]),
            message_sha256=str(row[4]),
            raw_fix=bytes(row[5]),
            raw_fix_sha256=str(row[6]),
            provenance_json=str(row[7]),
            provenance_sha256=str(row[8]),
            received_at=_parse_utc(str(row[9])),
        )
        for row in rows
    )


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone aware")
    return parsed.astimezone(UTC)
