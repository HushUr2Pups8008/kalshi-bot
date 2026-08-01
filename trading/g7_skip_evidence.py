"""Append-only diagnostic receipts for non-executed G7 gate decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Literal


UTC = timezone.utc
G7_SKIP_EVIDENCE_DB = Path("data/g7_skip_evidence.db")
G7_SKIP_EVIDENCE_SCHEMA_VERSION = 2
G7_SKIP_EVIDENCE_RECEIPT_VERSION = 1
_BUSY_TIMEOUT_SECONDS = 5.0
_LIQUIDITY_STATUSES = frozenset({"observed", "unavailable", "not_queried"})
_OBSERVED_LIQUIDITY_KEYS = frozenset(
    {
        "source",
        "side",
        "limit_price",
        "best_price",
        "executable_quantity",
        "executable_notional",
        "as_of",
        "raw_payload_hash",
    }
)
_FORBIDDEN_RECEIPT_KEY_PARTS = (
    "candidate",
    "fill",
    "settlement",
    "outbox",
    "bankroll",
    "pnl",
    "profit_loss",
)
_TYPED_REASON_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_G7_INPUT_KEYS = frozenset(
    {
        "minimum_market_liquidity_dollars",
        "maximum_open_exposure_drawdown_pct",
        "market_liquidity_dollars",
        "market_price_momentum_cents",
        "intended_side",
        "open_exposure_drawdown_pct",
    }
)
_G7_RESULT_KEYS = frozenset(
    {
        "ordered_failures",
        "g7_failures",
        "non_drawdown_g7_failures",
        "trade_blocked_reason",
    }
)


class G7SkipEvidenceSchemaError(RuntimeError):
    """Raised when an existing receipt store does not meet this schema contract."""


def canonical_json(value: object) -> str:
    """Return the sole JSON representation accepted by the receipt store."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _normalized_schema_sql(value: str) -> str:
    return (
        " ".join(value.strip().rstrip(";").lower().split())
        .replace(" if not exists ", " ")
        .replace('"', "")
    )


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS g7_skip_evidence_schema_meta (
        schema_version INTEGER PRIMARY KEY,
        ddl_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS g7_skip_evidence_records (
        evidence_id TEXT PRIMARY KEY,
        receipt_version INTEGER NOT NULL,
        decision_key TEXT NOT NULL UNIQUE,
        payload_sha256 TEXT NOT NULL,
        decision_at TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        lifecycle_id TEXT NOT NULL,
        venue TEXT NOT NULL,
        market_ticker TEXT NOT NULL,
        intended_side TEXT,
        market_family TEXT,
        runtime_paper_cohort_id TEXT,
        runtime_paper_cohort_kind TEXT,
        ordered_failures_json TEXT NOT NULL,
        g7_failures_json TEXT NOT NULL,
        trade_blocked_reason TEXT NOT NULL,
        g7_inputs_json TEXT NOT NULL,
        g7_results_json TEXT NOT NULL,
        liquidity_evidence_status TEXT NOT NULL
            CHECK (liquidity_evidence_status IN ('observed', 'unavailable', 'not_queried')),
        execution_liquidity_json TEXT NOT NULL,
        diagnostic_only INTEGER NOT NULL CHECK (diagnostic_only = 1)
    )
    """,
)
_SCHEMA_META_MIGRATION_STAGE = "g7_skip_evidence_schema_meta_migrated"
_SCHEMA_META_MIGRATION_STAGE_STATEMENT = _SCHEMA_STATEMENTS[0].replace(
    "CREATE TABLE IF NOT EXISTS g7_skip_evidence_schema_meta",
    f"CREATE TABLE {_SCHEMA_META_MIGRATION_STAGE}",
)
_RECORDS_MIGRATION_STAGE = "g7_skip_evidence_records_migrated"
_RECORDS_MIGRATION_STAGE_STATEMENT = _SCHEMA_STATEMENTS[1].replace(
    "CREATE TABLE IF NOT EXISTS g7_skip_evidence_records",
    f"CREATE TABLE {_RECORDS_MIGRATION_STAGE}",
)
_IMMUTABLE_TABLES = (
    "g7_skip_evidence_schema_meta",
    "g7_skip_evidence_records",
)
_SCHEMA_META_COLUMNS = (
    "schema_version",
    "ddl_sha256",
    "applied_at",
)
_RECORD_COLUMNS = (
    "evidence_id",
    "receipt_version",
    "decision_key",
    "payload_sha256",
    "decision_at",
    "captured_at",
    "lifecycle_id",
    "venue",
    "market_ticker",
    "intended_side",
    "market_family",
    "runtime_paper_cohort_id",
    "runtime_paper_cohort_kind",
    "ordered_failures_json",
    "g7_failures_json",
    "trade_blocked_reason",
    "g7_inputs_json",
    "g7_results_json",
    "liquidity_evidence_status",
    "execution_liquidity_json",
    "diagnostic_only",
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
                        SELECT RAISE(ABORT, 'g7 skip evidence is append-only');
                    END
                    """,
                )
            )
    return tuple(statements)


_EXPECTED_SCHEMA_SQL = {
    "g7_skip_evidence_schema_meta": _normalized_schema_sql(_SCHEMA_STATEMENTS[0]),
    "g7_skip_evidence_records": _normalized_schema_sql(_SCHEMA_STATEMENTS[1]),
    **{
        name: _normalized_schema_sql(statement)
        for name, statement in _immutable_triggers()
    },
}
_EXPECTED_SCHEMA_META_MIGRATION_STAGE_SQL = _normalized_schema_sql(
    _SCHEMA_META_MIGRATION_STAGE_STATEMENT
)
_EXPECTED_RECORDS_MIGRATION_STAGE_SQL = _normalized_schema_sql(
    _RECORDS_MIGRATION_STAGE_STATEMENT
)
G7_SKIP_EVIDENCE_DDL_SHA256 = sha256(
    "\n".join(f"{name}:{sql}" for name, sql in sorted(_EXPECTED_SCHEMA_SQL.items())).encode("utf-8")
).hexdigest()


def _require_text(value: object, label: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a non-empty string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} must be a non-empty string")
    return text


def _require_utc_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
    return value.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_utc_iso(value: object, label: str) -> str:
    text = _require_text(value, label)
    assert text is not None
    parseable = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    return _utc_iso(_require_utc_datetime(parsed, label))


def _require_finite_number(value: object, label: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _reject_forbidden_keys(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} JSON object keys must be strings")
            lowered = key.lower()
            if any(part in lowered for part in _FORBIDDEN_RECEIPT_KEY_PARTS):
                raise ValueError(f"{label} cannot contain financial or execution fields")
            _reject_forbidden_keys(nested, label)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_forbidden_keys(nested, label)


def _canonical_object(value: object, label: str) -> tuple[dict[str, object], str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    _reject_forbidden_keys(value, label)
    try:
        encoded = canonical_json(value)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain canonical JSON values") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be a JSON object")
    return decoded, encoded


def _normalized_failures(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a non-empty ordered sequence")
    normalized = tuple(_require_text(item, label) for item in value)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must be non-empty and unique")
    return normalized


def _normalize_execution_liquidity(
    status: str,
    value: object,
    *,
    decision_at: datetime,
) -> tuple[dict[str, object], str]:
    metadata, _ = _canonical_object(value, "execution_liquidity")
    keys = frozenset(metadata)
    if status == "observed":
        if keys != _OBSERVED_LIQUIDITY_KEYS:
            raise ValueError("observed execution_liquidity must contain the complete observed metadata")
        source = _require_text(metadata["source"], "execution_liquidity.source")
        side = _require_text(metadata["side"], "execution_liquidity.side")
        limit_price = _require_finite_number(metadata["limit_price"], "execution_liquidity.limit_price")
        best_price = _require_finite_number(
            metadata["best_price"],
            "execution_liquidity.best_price",
            allow_none=True,
        )
        quantity = _require_finite_number(
            metadata["executable_quantity"],
            "execution_liquidity.executable_quantity",
        )
        notional = _require_finite_number(
            metadata["executable_notional"],
            "execution_liquidity.executable_notional",
        )
        as_of = _require_utc_iso(metadata["as_of"], "execution_liquidity.as_of")
        raw_payload_hash = _require_text(
            metadata["raw_payload_hash"],
            "execution_liquidity.raw_payload_hash",
        )
        assert source is not None and side is not None and raw_payload_hash is not None
        if side not in {"yes", "no"}:
            raise ValueError("execution_liquidity.side must be yes or no")
        if not _SHA256_RE.fullmatch(raw_payload_hash):
            raise ValueError("execution_liquidity.raw_payload_hash must be a lowercase SHA-256")
        assert limit_price is not None and quantity is not None and notional is not None
        if limit_price <= 0 or limit_price >= 1:
            raise ValueError("execution_liquidity.limit_price must be between zero and one")
        if best_price is not None and (best_price <= 0 or best_price > limit_price):
            raise ValueError("execution_liquidity.best_price must be positive and no worse than the limit")
        if quantity < 0 or notional < 0:
            raise ValueError("execution_liquidity quantity and notional must be non-negative")
        as_of_datetime = _require_utc_datetime(
            datetime.fromisoformat(as_of.replace("Z", "+00:00")),
            "execution_liquidity.as_of",
        )
        if as_of_datetime > decision_at:
            raise ValueError("execution_liquidity.as_of cannot follow decision_at")
        normalized: dict[str, object] = {
            "source": source,
            "side": side,
            "limit_price": limit_price,
            "best_price": best_price,
            "executable_quantity": quantity,
            "executable_notional": notional,
            "as_of": as_of,
            "raw_payload_hash": raw_payload_hash,
        }
    elif status == "unavailable":
        if keys != {"source", "status", "reason"}:
            raise ValueError("unavailable execution_liquidity must not include observed metadata")
        source = _require_text(metadata["source"], "execution_liquidity.source")
        unavailable_status = _require_text(metadata["status"], "execution_liquidity.status")
        reason = _require_text(metadata["reason"], "execution_liquidity.reason")
        assert source is not None and unavailable_status is not None and reason is not None
        if unavailable_status != "unavailable" or not _TYPED_REASON_RE.fullmatch(reason):
            raise ValueError("unavailable execution_liquidity requires a typed reason")
        normalized = {"source": source, "status": unavailable_status, "reason": reason}
    else:
        if keys != {"status", "reason"}:
            raise ValueError("not_queried execution_liquidity must not include observed metadata")
        not_queried_status = _require_text(metadata["status"], "execution_liquidity.status")
        reason = _require_text(metadata["reason"], "execution_liquidity.reason")
        assert not_queried_status is not None and reason is not None
        if not_queried_status != "not_queried" or not _TYPED_REASON_RE.fullmatch(reason):
            raise ValueError("not_queried execution_liquidity requires a typed reason")
        normalized = {"status": not_queried_status, "reason": reason}
    encoded = canonical_json(normalized)
    return normalized, encoded


def _require_optional_non_negative_number(value: object, label: str) -> float | None:
    number = _require_finite_number(value, label, allow_none=True)
    if number is not None and number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def _validate_g7_inputs(
    value: dict[str, object],
    *,
    intended_side: str | None,
) -> None:
    if frozenset(value) != _G7_INPUT_KEYS:
        raise ValueError("g7_inputs must contain the complete G7 decision projection")
    minimum_liquidity = _require_optional_non_negative_number(
        value["minimum_market_liquidity_dollars"],
        "g7_inputs.minimum_market_liquidity_dollars",
    )
    maximum_drawdown = _require_optional_non_negative_number(
        value["maximum_open_exposure_drawdown_pct"],
        "g7_inputs.maximum_open_exposure_drawdown_pct",
    )
    _require_optional_non_negative_number(
        value["market_liquidity_dollars"],
        "g7_inputs.market_liquidity_dollars",
    )
    _require_finite_number(
        value["market_price_momentum_cents"],
        "g7_inputs.market_price_momentum_cents",
        allow_none=True,
    )
    input_side = _require_text(value["intended_side"], "g7_inputs.intended_side", allow_none=True)
    drawdown = _require_optional_non_negative_number(
        value["open_exposure_drawdown_pct"],
        "g7_inputs.open_exposure_drawdown_pct",
    )
    if input_side not in {None, "yes", "no"} or input_side != intended_side:
        raise ValueError("g7_inputs.intended_side must match intended_side")
    if maximum_drawdown is None or maximum_drawdown > 1:
        raise ValueError("g7_inputs.maximum_open_exposure_drawdown_pct must be between zero and one")
    if drawdown is not None and drawdown > 1:
        raise ValueError("g7_inputs.open_exposure_drawdown_pct must be between zero and one")
    if minimum_liquidity is None:
        raise ValueError("g7_inputs.minimum_market_liquidity_dollars must be present")


def _validate_g7_results(
    value: dict[str, object],
    *,
    ordered_failures: tuple[str, ...],
    g7_failures: tuple[str, ...],
    trade_blocked_reason: str,
) -> None:
    if frozenset(value) != _G7_RESULT_KEYS:
        raise ValueError("g7_results must contain the complete G7 result projection")
    expected_non_drawdown = [
        failure for failure in g7_failures if failure != "G7_open_exposure_drawdown"
    ]
    if value["ordered_failures"] != list(ordered_failures):
        raise ValueError("g7_results.ordered_failures must match ordered_failures")
    if value["g7_failures"] != list(g7_failures):
        raise ValueError("g7_results.g7_failures must match g7_failures")
    if value["non_drawdown_g7_failures"] != expected_non_drawdown:
        raise ValueError("g7_results.non_drawdown_g7_failures must match G7 failures")
    if value["trade_blocked_reason"] != trade_blocked_reason:
        raise ValueError("g7_results.trade_blocked_reason must match trade_blocked_reason")


@dataclass(frozen=True)
class G7SkipEvidenceRecord:
    """One final G7 skip decision receipt, permanently diagnostic-only."""

    decision_key: str
    lifecycle_id: str
    decision_at: datetime
    captured_at: datetime
    venue: str
    market_ticker: str
    intended_side: str | None
    market_family: str | None
    runtime_paper_cohort_id: str | None
    runtime_paper_cohort_kind: str | None
    ordered_failures: Sequence[str]
    g7_failures: Sequence[str]
    trade_blocked_reason: str
    g7_inputs: Mapping[str, object]
    g7_results: Mapping[str, object]
    liquidity_evidence_status: Literal["observed", "unavailable", "not_queried"]
    execution_liquidity: Mapping[str, object]
    diagnostic_only: bool = True
    receipt_version: int = G7_SKIP_EVIDENCE_RECEIPT_VERSION
    _g7_inputs_json: str = field(init=False, repr=False, compare=False)
    _g7_results_json: str = field(init=False, repr=False, compare=False)
    _execution_liquidity_json: str = field(init=False, repr=False, compare=False)
    _payload_contract: Literal["current", "pre_lineage"] = field(
        init=False,
        default="current",
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.receipt_version != G7_SKIP_EVIDENCE_RECEIPT_VERSION:
            raise ValueError("unsupported G7 skip evidence receipt_version")
        if self.diagnostic_only is not True:
            raise ValueError("G7 skip evidence records must be diagnostic_only")

        decision_key = _require_text(self.decision_key, "decision_key")
        lifecycle_id = _require_text(self.lifecycle_id, "lifecycle_id")
        venue = _require_text(self.venue, "venue")
        market_ticker = _require_text(self.market_ticker, "market_ticker")
        intended_side = _require_text(self.intended_side, "intended_side", allow_none=True)
        market_family = _require_text(self.market_family, "market_family", allow_none=True)
        runtime_paper_cohort_id = _require_text(
            self.runtime_paper_cohort_id,
            "runtime_paper_cohort_id",
            allow_none=True,
        )
        runtime_paper_cohort_kind = _require_text(
            self.runtime_paper_cohort_kind,
            "runtime_paper_cohort_kind",
            allow_none=True,
        )
        blocked_reason = _require_text(self.trade_blocked_reason, "trade_blocked_reason")
        decision_at = _require_utc_datetime(self.decision_at, "decision_at")
        captured_at = _require_utc_datetime(self.captured_at, "captured_at")
        if captured_at < decision_at:
            raise ValueError("captured_at cannot precede decision_at")
        ordered_failures = _normalized_failures(self.ordered_failures, "ordered_failures")
        g7_failures = _normalized_failures(self.g7_failures, "g7_failures")
        expected_g7_failures = tuple(failure for failure in ordered_failures if failure.startswith("G7_"))
        if g7_failures != expected_g7_failures:
            raise ValueError("g7_failures must exactly match the ordered G7 failures")
        assert blocked_reason is not None
        if blocked_reason not in ordered_failures:
            raise ValueError("trade_blocked_reason must be one of ordered_failures")
        if blocked_reason not in g7_failures or blocked_reason == "G7_open_exposure_drawdown":
            raise ValueError("G7 skip evidence requires a non-drawdown final G7 failure")
        if self.liquidity_evidence_status not in _LIQUIDITY_STATUSES:
            raise ValueError("unsupported liquidity_evidence_status")

        g7_inputs, g7_inputs_json = _canonical_object(self.g7_inputs, "g7_inputs")
        g7_results, g7_results_json = _canonical_object(self.g7_results, "g7_results")
        _validate_g7_inputs(g7_inputs, intended_side=intended_side)
        _validate_g7_results(
            g7_results,
            ordered_failures=ordered_failures,
            g7_failures=g7_failures,
            trade_blocked_reason=blocked_reason,
        )
        execution_liquidity, execution_liquidity_json = _normalize_execution_liquidity(
            self.liquidity_evidence_status,
            self.execution_liquidity,
            decision_at=decision_at,
        )

        assert decision_key is not None and lifecycle_id is not None
        assert venue is not None and market_ticker is not None
        object.__setattr__(self, "decision_key", decision_key)
        object.__setattr__(self, "lifecycle_id", lifecycle_id)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "market_ticker", market_ticker)
        object.__setattr__(self, "intended_side", intended_side)
        object.__setattr__(self, "market_family", market_family)
        object.__setattr__(self, "runtime_paper_cohort_id", runtime_paper_cohort_id)
        object.__setattr__(self, "runtime_paper_cohort_kind", runtime_paper_cohort_kind)
        object.__setattr__(self, "ordered_failures", ordered_failures)
        object.__setattr__(self, "g7_failures", g7_failures)
        object.__setattr__(self, "trade_blocked_reason", blocked_reason)
        object.__setattr__(self, "g7_inputs", g7_inputs)
        object.__setattr__(self, "g7_results", g7_results)
        object.__setattr__(self, "execution_liquidity", execution_liquidity)
        object.__setattr__(self, "_g7_inputs_json", g7_inputs_json)
        object.__setattr__(self, "_g7_results_json", g7_results_json)
        object.__setattr__(self, "_execution_liquidity_json", execution_liquidity_json)
        object.__setattr__(self, "_payload_contract", "current")

    @property
    def evidence_id(self) -> str:
        identity = canonical_json(
            {
                "decision_key": self.decision_key,
                "receipt_version": self.receipt_version,
            }
        )
        return sha256(identity.encode("utf-8")).hexdigest()

    def _payload_for_contract(
        self,
        contract: Literal["current", "pre_lineage"],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "decision_key": self.decision_key,
            "lifecycle_id": self.lifecycle_id,
            "decision_at": _utc_iso(self.decision_at),
            "captured_at": _utc_iso(self.captured_at),
            "venue": self.venue,
            "market_ticker": self.market_ticker,
            "intended_side": self.intended_side,
            "market_family": self.market_family,
            "ordered_failures": list(self.ordered_failures),
            "g7_failures": list(self.g7_failures),
            "trade_blocked_reason": self.trade_blocked_reason,
            "g7_inputs": json.loads(self._g7_inputs_json),
            "g7_results": json.loads(self._g7_results_json),
            "liquidity_evidence_status": self.liquidity_evidence_status,
            "execution_liquidity": json.loads(self._execution_liquidity_json),
            "diagnostic_only": True,
        }
        if contract == "current":
            payload["runtime_paper_cohort_id"] = self.runtime_paper_cohort_id
            payload["runtime_paper_cohort_kind"] = self.runtime_paper_cohort_kind
        return payload

    def _payload_sha256_for_contract(
        self,
        contract: Literal["current", "pre_lineage"],
    ) -> str:
        return sha256(canonical_json(self._payload_for_contract(contract)).encode("utf-8")).hexdigest()

    @property
    def payload(self) -> dict[str, object]:
        return self._payload_for_contract(self._payload_contract)

    @property
    def payload_sha256(self) -> str:
        return self._payload_sha256_for_contract(self._payload_contract)


@dataclass(frozen=True)
class G7SkipEvidenceAppendResult:
    status: Literal["inserted", "identical", "conflict"]
    evidence_id: str
    payload_sha256: str
    existing_payload_sha256: str | None = None


@dataclass(frozen=True)
class G7SkipEvidenceSnapshot:
    exists: bool
    schema_valid: bool
    integrity_check: str
    record_count: int
    receipt_counts_by_status: tuple[tuple[str, int], ...]
    latest_captured_at: datetime | None


def _open_writable(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def _open_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=_BUSY_TIMEOUT_SECONDS,
    )


def _schema_contract_matches(conn: sqlite3.Connection) -> bool:
    try:
        meta_rows = conn.execute(
            "SELECT schema_version, ddl_sha256 FROM g7_skip_evidence_schema_meta"
        ).fetchall()
        if meta_rows != [(G7_SKIP_EVIDENCE_SCHEMA_VERSION, G7_SKIP_EVIDENCE_DDL_SHA256)]:
            return False
        actual_sql = {
            str(name): _normalized_schema_sql(str(sql))
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'trigger') AND "
                "(name LIKE 'g7_skip_evidence_%' OR name LIKE 'immutable_g7_skip_evidence_%')"
            ).fetchall()
            if sql is not None
        }
        return actual_sql == _EXPECTED_SCHEMA_SQL
    except sqlite3.DatabaseError:
        return False


def _schema_tables_match(conn: sqlite3.Connection) -> bool:
    try:
        meta_rows = conn.execute(
            "SELECT schema_version, ddl_sha256 FROM g7_skip_evidence_schema_meta"
        ).fetchall()
        if meta_rows != [(G7_SKIP_EVIDENCE_SCHEMA_VERSION, G7_SKIP_EVIDENCE_DDL_SHA256)]:
            return False
        actual_sql = {
            str(name): _normalized_schema_sql(str(sql))
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name IN (?, ?)",
                _IMMUTABLE_TABLES,
            ).fetchall()
            if sql is not None
        }
        return actual_sql == {
            name: _EXPECTED_SCHEMA_SQL[name]
            for name in _IMMUTABLE_TABLES
        }
    except sqlite3.DatabaseError:
        return False


def _table_sql(conn: sqlite3.Connection, table: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return _normalized_schema_sql(str(row[0]))


def _migration_stage_is_empty(conn: sqlite3.Connection, stage: str) -> bool:
    return conn.execute(f"SELECT NOT EXISTS(SELECT 1 FROM {stage})").fetchone() == (1,)


def _migration_stage_matches_source(
    conn: sqlite3.Connection,
    *,
    source: str,
    stage: str,
    identity_columns: tuple[str, ...],
) -> bool:
    columns = ", ".join(identity_columns)
    try:
        source_count = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()
        stage_count = conn.execute(f"SELECT COUNT(*) FROM {stage}").fetchone()
        if source_count != stage_count:
            return False
        difference = conn.execute(
            f"""
            SELECT 1 FROM (
                SELECT {columns} FROM {source}
                EXCEPT
                SELECT {columns} FROM {stage}
            )
            UNION ALL
            SELECT 1 FROM (
                SELECT {columns} FROM {stage}
                EXCEPT
                SELECT {columns} FROM {source}
            )
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise G7SkipEvidenceSchemaError(
            "G7 skip evidence migration recovery cannot reconcile staging table"
        ) from exc
    return difference is None


def _validate_stage_only_records(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_RECORD_COLUMNS)} FROM {_RECORDS_MIGRATION_STAGE}"
        ).fetchall()
        for row in rows:
            _record_from_row(tuple(row))
    except (G7SkipEvidenceSchemaError, sqlite3.DatabaseError) as exc:
        raise G7SkipEvidenceSchemaError(
            "G7 skip evidence migration recovery staging receipt is invalid"
        ) from exc


def _recover_interrupted_records_migration(conn: sqlite3.Connection) -> bool:
    source = "g7_skip_evidence_records"
    stage_sql = _table_sql(conn, _RECORDS_MIGRATION_STAGE)
    if stage_sql is None:
        return False
    if stage_sql != _EXPECTED_RECORDS_MIGRATION_STAGE_SQL:
        raise G7SkipEvidenceSchemaError(
            "G7 skip evidence migration recovery staging table has an unexpected schema"
        )

    if _table_sql(conn, source) is None:
        _validate_stage_only_records(conn)
        conn.execute(
            f"ALTER TABLE {_RECORDS_MIGRATION_STAGE} RENAME TO {source}"
        )
        return True

    if _migration_stage_is_empty(conn, _RECORDS_MIGRATION_STAGE) or _migration_stage_matches_source(
        conn,
        source=source,
        stage=_RECORDS_MIGRATION_STAGE,
        identity_columns=("evidence_id", "decision_key", "payload_sha256"),
    ):
        conn.execute(f"DROP TABLE {_RECORDS_MIGRATION_STAGE}")
        return True

    raise G7SkipEvidenceSchemaError(
        "G7 skip evidence migration recovery cannot prove staging records match source"
    )


def _recover_interrupted_schema_meta_migration(conn: sqlite3.Connection) -> bool:
    source = "g7_skip_evidence_schema_meta"
    stage_sql = _table_sql(conn, _SCHEMA_META_MIGRATION_STAGE)
    if stage_sql is None:
        return False
    if stage_sql != _EXPECTED_SCHEMA_META_MIGRATION_STAGE_SQL:
        raise G7SkipEvidenceSchemaError(
            "G7 skip evidence migration recovery metadata staging table has an unexpected schema"
        )

    if _table_sql(conn, source) is None:
        conn.execute(f"ALTER TABLE {_SCHEMA_META_MIGRATION_STAGE} RENAME TO {source}")
        return True

    if _migration_stage_is_empty(conn, _SCHEMA_META_MIGRATION_STAGE) or _migration_stage_matches_source(
        conn,
        source=source,
        stage=_SCHEMA_META_MIGRATION_STAGE,
        identity_columns=("schema_version", "ddl_sha256", "applied_at"),
    ):
        conn.execute(f"DROP TABLE {_SCHEMA_META_MIGRATION_STAGE}")
        return True

    raise G7SkipEvidenceSchemaError(
        "G7 skip evidence migration recovery cannot prove metadata staging matches source"
    )


def _drop_immutable_triggers(conn: sqlite3.Connection) -> None:
    for name, _ in _immutable_triggers():
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")


def _migrate_schema(conn: sqlite3.Connection, *, applied_at: datetime) -> None:
    existing_meta_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'g7_skip_evidence_schema_meta'"
    ).fetchone()
    existing_meta_sql = (
        _normalized_schema_sql(str(existing_meta_sql_row[0]))
        if existing_meta_sql_row and existing_meta_sql_row[0] is not None
        else ""
    )
    expected_meta_sql = _EXPECTED_SCHEMA_SQL["g7_skip_evidence_schema_meta"]
    if existing_meta_sql and existing_meta_sql != expected_meta_sql:
        conn.execute(_SCHEMA_META_MIGRATION_STAGE_STATEMENT)
        conn.execute(
            """
            INSERT INTO g7_skip_evidence_schema_meta_migrated (
                schema_version, ddl_sha256, applied_at
            )
            SELECT schema_version, ddl_sha256, applied_at
            FROM g7_skip_evidence_schema_meta
            """
        )
        conn.execute("DROP TABLE g7_skip_evidence_schema_meta")
        conn.execute(
            "ALTER TABLE g7_skip_evidence_schema_meta_migrated RENAME TO g7_skip_evidence_schema_meta"
        )
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(g7_skip_evidence_records)").fetchall()
        }
    except sqlite3.DatabaseError as exc:
        raise G7SkipEvidenceSchemaError("G7 skip evidence schema contract does not match") from exc
    existing_table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'g7_skip_evidence_records'"
    ).fetchone()
    existing_table_sql = (
        _normalized_schema_sql(str(existing_table_sql_row[0]))
        if existing_table_sql_row and existing_table_sql_row[0] is not None
        else ""
    )
    expected_table_sql = _EXPECTED_SCHEMA_SQL["g7_skip_evidence_records"]
    if columns and existing_table_sql != expected_table_sql:
        runtime_paper_cohort_id_expr = (
            "runtime_paper_cohort_id"
            if "runtime_paper_cohort_id" in columns
            else "NULL"
        )
        runtime_paper_cohort_kind_expr = (
            "runtime_paper_cohort_kind"
            if "runtime_paper_cohort_kind" in columns
            else "NULL"
        )
        conn.execute(_RECORDS_MIGRATION_STAGE_STATEMENT)
        conn.execute(
            """
            INSERT INTO g7_skip_evidence_records_migrated (
                evidence_id, receipt_version, decision_key, payload_sha256, decision_at,
                captured_at, lifecycle_id, venue, market_ticker, intended_side,
                market_family, runtime_paper_cohort_id, runtime_paper_cohort_kind,
                ordered_failures_json, g7_failures_json, trade_blocked_reason,
                g7_inputs_json, g7_results_json, liquidity_evidence_status,
                execution_liquidity_json, diagnostic_only
            )
            SELECT evidence_id, receipt_version, decision_key, payload_sha256, decision_at,
                   captured_at, lifecycle_id, venue, market_ticker, intended_side,
                   market_family, {runtime_paper_cohort_id_expr}, {runtime_paper_cohort_kind_expr},
                   ordered_failures_json, g7_failures_json,
                   trade_blocked_reason, g7_inputs_json, g7_results_json,
                   liquidity_evidence_status, execution_liquidity_json, diagnostic_only
            FROM g7_skip_evidence_records
            """.format(
                runtime_paper_cohort_id_expr=runtime_paper_cohort_id_expr,
                runtime_paper_cohort_kind_expr=runtime_paper_cohort_kind_expr,
            )
        )
        conn.execute("DROP TABLE g7_skip_evidence_records")
        conn.execute(
            "ALTER TABLE g7_skip_evidence_records_migrated RENAME TO g7_skip_evidence_records"
        )
    current = [(G7_SKIP_EVIDENCE_SCHEMA_VERSION, G7_SKIP_EVIDENCE_DDL_SHA256)]
    existing = conn.execute(
        "SELECT schema_version, ddl_sha256 FROM g7_skip_evidence_schema_meta"
    ).fetchall()
    if existing != current:
        conn.execute("DELETE FROM g7_skip_evidence_schema_meta")
        conn.execute(
            "INSERT INTO g7_skip_evidence_schema_meta (schema_version, ddl_sha256, applied_at) "
            "VALUES (?, ?, ?)",
            (G7_SKIP_EVIDENCE_SCHEMA_VERSION, G7_SKIP_EVIDENCE_DDL_SHA256, _utc_iso(applied_at)),
        )


class G7SkipEvidenceStore:
    """Dedicated SQLite store with no constructor-time I/O."""

    def __init__(
        self,
        db_path: Path | str = G7_SKIP_EVIDENCE_DB,
        *,
        existing_only: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.existing_only = existing_only

    def initialize(self, *, applied_at: datetime | None = None) -> bool:
        """Create and validate the isolated schema only after an explicit call."""
        if self.existing_only and not self.db_path.exists():
            return False
        if not self.existing_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        applied = _require_utc_datetime(applied_at or datetime.now(UTC), "applied_at")
        with _open_writable(self.db_path) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                _recover_interrupted_schema_meta_migration(conn)
                _recover_interrupted_records_migration(conn)
                for statement in _SCHEMA_STATEMENTS:
                    conn.execute(statement)
                if not _schema_tables_match(conn):
                    _drop_immutable_triggers(conn)
                    _migrate_schema(conn, applied_at=applied)
                if not _schema_tables_match(conn):
                    raise G7SkipEvidenceSchemaError("G7 skip evidence schema contract does not match")
                for _, statement in _immutable_triggers():
                    conn.execute(statement)
                if not _schema_contract_matches(conn):
                    raise G7SkipEvidenceSchemaError("G7 skip evidence schema contract does not match")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return True

    def append_record(self, record: G7SkipEvidenceRecord) -> G7SkipEvidenceAppendResult:
        """Append one canonical receipt; conflicting decision identities cannot overwrite it."""
        if not isinstance(record, G7SkipEvidenceRecord):
            raise TypeError("record must be a G7SkipEvidenceRecord")
        if not self.db_path.exists():
            if self.existing_only:
                raise FileNotFoundError(self.db_path)
            self.initialize()
        with _open_writable(self.db_path) as conn:
            if not _schema_contract_matches(conn):
                raise G7SkipEvidenceSchemaError("G7 skip evidence schema contract does not match")
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT evidence_id, payload_sha256 FROM g7_skip_evidence_records WHERE decision_key = ?",
                    (record.decision_key,),
                ).fetchone()
                if existing is not None:
                    existing_id, existing_hash = str(existing[0]), str(existing[1])
                    conn.commit()
                    return G7SkipEvidenceAppendResult(
                        status="identical" if existing_hash == record.payload_sha256 else "conflict",
                        evidence_id=existing_id,
                        payload_sha256=record.payload_sha256,
                        existing_payload_sha256=existing_hash,
                    )
                payload = record.payload
                conn.execute(
                    """
                    INSERT INTO g7_skip_evidence_records (
                        evidence_id, receipt_version, decision_key, payload_sha256, decision_at,
                        captured_at, lifecycle_id, venue, market_ticker, intended_side,
                        market_family, runtime_paper_cohort_id, runtime_paper_cohort_kind,
                        ordered_failures_json, g7_failures_json, trade_blocked_reason,
                        g7_inputs_json, g7_results_json, liquidity_evidence_status,
                        execution_liquidity_json, diagnostic_only
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.evidence_id,
                        record.receipt_version,
                        record.decision_key,
                        record.payload_sha256,
                        payload["decision_at"],
                        payload["captured_at"],
                        record.lifecycle_id,
                        record.venue,
                        record.market_ticker,
                        record.intended_side,
                        record.market_family,
                        record.runtime_paper_cohort_id,
                        record.runtime_paper_cohort_kind,
                        canonical_json(payload["ordered_failures"]),
                        canonical_json(payload["g7_failures"]),
                        record.trade_blocked_reason,
                        record._g7_inputs_json,
                        record._g7_results_json,
                        record.liquidity_evidence_status,
                        record._execution_liquidity_json,
                        1,
                    ),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return G7SkipEvidenceAppendResult(
            status="inserted",
            evidence_id=record.evidence_id,
            payload_sha256=record.payload_sha256,
        )


def _record_from_row(row: tuple[object, ...]) -> G7SkipEvidenceRecord:
    (
        evidence_id,
        receipt_version,
        decision_key,
        payload_sha256,
        decision_at,
        captured_at,
        lifecycle_id,
        venue,
        market_ticker,
        intended_side,
        market_family,
        runtime_paper_cohort_id,
        runtime_paper_cohort_kind,
        ordered_failures_json,
        g7_failures_json,
        trade_blocked_reason,
        g7_inputs_json,
        g7_results_json,
        liquidity_evidence_status,
        execution_liquidity_json,
        diagnostic_only,
    ) = row
    try:
        record = G7SkipEvidenceRecord(
            decision_key=str(decision_key),
            lifecycle_id=str(lifecycle_id),
            decision_at=_require_utc_datetime(
                datetime.fromisoformat(str(decision_at).replace("Z", "+00:00")),
                "decision_at",
            ),
            captured_at=_require_utc_datetime(
                datetime.fromisoformat(str(captured_at).replace("Z", "+00:00")),
                "captured_at",
            ),
            venue=str(venue),
            market_ticker=str(market_ticker),
            intended_side=None if intended_side is None else str(intended_side),
            market_family=None if market_family is None else str(market_family),
            runtime_paper_cohort_id=(
                None if runtime_paper_cohort_id is None else str(runtime_paper_cohort_id)
            ),
            runtime_paper_cohort_kind=(
                None if runtime_paper_cohort_kind is None else str(runtime_paper_cohort_kind)
            ),
            ordered_failures=json.loads(str(ordered_failures_json)),
            g7_failures=json.loads(str(g7_failures_json)),
            trade_blocked_reason=str(trade_blocked_reason),
            g7_inputs=json.loads(str(g7_inputs_json)),
            g7_results=json.loads(str(g7_results_json)),
            liquidity_evidence_status=str(liquidity_evidence_status),
            execution_liquidity=json.loads(str(execution_liquidity_json)),
            diagnostic_only=diagnostic_only is True or diagnostic_only == 1,
            receipt_version=int(receipt_version),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise G7SkipEvidenceSchemaError("stored G7 skip evidence receipt is invalid") from exc
    if str(evidence_id) != record.evidence_id:
        raise G7SkipEvidenceSchemaError("stored G7 skip evidence receipt hash does not match")
    stored_payload_sha256 = str(payload_sha256)
    if stored_payload_sha256 == record.payload_sha256:
        return record
    # Pre-lineage receipts omitted both cohort keys; retain their original hash contract.
    if (
        record.runtime_paper_cohort_id is None
        and record.runtime_paper_cohort_kind is None
        and stored_payload_sha256 == record._payload_sha256_for_contract("pre_lineage")
    ):
        object.__setattr__(record, "_payload_contract", "pre_lineage")
        return record
    raise G7SkipEvidenceSchemaError("stored G7 skip evidence receipt hash does not match")


_RECORD_SELECT = """
SELECT evidence_id, receipt_version, decision_key, payload_sha256, decision_at,
       captured_at, lifecycle_id, venue, market_ticker, intended_side,
       market_family, runtime_paper_cohort_id, runtime_paper_cohort_kind,
       ordered_failures_json, g7_failures_json, trade_blocked_reason,
       g7_inputs_json, g7_results_json, liquidity_evidence_status,
       execution_liquidity_json, diagnostic_only
FROM g7_skip_evidence_records
ORDER BY decision_at, evidence_id
"""


def read_g7_skip_evidence_records(
    db_path: Path | str = G7_SKIP_EVIDENCE_DB,
) -> tuple[G7SkipEvidenceRecord, ...]:
    """Read validated receipts through a strictly read-only SQLite handle."""
    path = Path(db_path)
    if not path.exists():
        return ()
    try:
        with _open_read_only(path) as conn:
            if not _schema_contract_matches(conn):
                raise G7SkipEvidenceSchemaError("G7 skip evidence schema contract does not match")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise G7SkipEvidenceSchemaError("G7 skip evidence database integrity check failed")
            return tuple(_record_from_row(row) for row in conn.execute(_RECORD_SELECT).fetchall())
    except sqlite3.DatabaseError as exc:
        raise G7SkipEvidenceSchemaError("G7 skip evidence database is unreadable") from exc


def read_g7_skip_evidence_snapshot(
    db_path: Path | str = G7_SKIP_EVIDENCE_DB,
) -> G7SkipEvidenceSnapshot:
    """Read schema, integrity, and count state without creating a database."""
    path = Path(db_path)
    if not path.exists():
        return G7SkipEvidenceSnapshot(
            exists=False,
            schema_valid=False,
            integrity_check="missing",
            record_count=0,
            receipt_counts_by_status=(),
            latest_captured_at=None,
        )
    try:
        with _open_read_only(path) as conn:
            schema_valid = _schema_contract_matches(conn)
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity_check = str(integrity_row[0]) if integrity_row is not None else "unreadable"
            if not schema_valid or integrity_check != "ok":
                return G7SkipEvidenceSnapshot(
                    exists=True,
                    schema_valid=schema_valid,
                    integrity_check=integrity_check,
                    record_count=0,
                    receipt_counts_by_status=(),
                    latest_captured_at=None,
                )
            try:
                records = tuple(_record_from_row(row) for row in conn.execute(_RECORD_SELECT).fetchall())
            except G7SkipEvidenceSchemaError:
                return G7SkipEvidenceSnapshot(
                    exists=True,
                    schema_valid=False,
                    integrity_check="receipt_invalid",
                    record_count=0,
                    receipt_counts_by_status=(),
                    latest_captured_at=None,
                )
            counts_by_status: dict[str, int] = {}
            for record in records:
                status = record.liquidity_evidence_status
                counts_by_status[status] = counts_by_status.get(status, 0) + 1
            counts = tuple(sorted(counts_by_status.items()))
            latest_captured_at = max((record.captured_at for record in records), default=None)
            return G7SkipEvidenceSnapshot(
                exists=True,
                schema_valid=True,
                integrity_check="ok",
                record_count=len(records),
                receipt_counts_by_status=counts,
                latest_captured_at=latest_captured_at,
            )
    except (OSError, sqlite3.DatabaseError, ValueError):
        return G7SkipEvidenceSnapshot(
            exists=True,
            schema_valid=False,
            integrity_check="unreadable",
            record_count=0,
            receipt_counts_by_status=(),
            latest_captured_at=None,
        )
