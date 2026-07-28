#!/usr/bin/env python3
"""Read-only audit of exact authoritative receipts for open paper positions.

This script never resolves a paper trade. It reports only independently
retrieved, exact-identity settlement observations and leaves pending or
untrusted results explicitly unresolved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from polymarket.public_client import PolymarketPublicClient  # noqa: E402
from polymarket.settlement_reconciler import SettlementNotFound  # noqa: E402
from trading.authoritative_settlement_source import (  # noqa: E402
    DEFAULT_AUTHORITATIVE_SETTLEMENT_TIMEOUT_SECONDS,
    AuthoritativeSettlementSource,
)
from trading.settlement import SettlementDriftError, SettlementObservation  # noqa: E402
from trading.venue import MarketRef, Venue  # noqa: E402


MAX_AUDIT_SNAPSHOT_FILE_BYTES = 64 * 1024 * 1024
_NUMERIC_MARKET_ID = re.compile(r"[0-9]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TERMINAL_STATE_COLUMNS = (
    "terminal_state",
    "settlement_observation_sha256",
    "settled_at",
    "gross_payout_cents",
    "gross_pnl_cents",
    "resolved_ts",
    "resolved_yes",
    "pnl_dollars",
)


class ExactSettlementSource(Protocol):
    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None: ...


class PaperSettlementAuditSnapshotError(RuntimeError):
    """Raised when a stable, non-mutating SQLite audit snapshot is unavailable."""


@dataclass(frozen=True)
class OpenPaperRow:
    trade_id: str | None
    ticker: str | None
    venue: str | None
    canonical_market_id: str | None
    identity_status: str | None
    quarantine_reason: str | None
    snapshot_close_time: str | None
    snapshot_close_at: datetime | None
    market_snapshot_sha256: str | None
    market_snapshot_malformed: bool
    trade_id_malformed: bool
    identity_status_malformed: bool
    persisted_terminal_fields: tuple[str, ...]


@dataclass(frozen=True)
class SettlementAuditRow:
    trade_id: str | None
    ticker: str | None
    venue: str | None
    canonical_market_id: str | None
    identity_status: str | None
    quarantine_reason: str | None
    snapshot_close_time: str | None
    status: str
    outcome: str | None = None
    source_id: str | None = None
    rules_version: str | None = None
    observed_at: str | None = None
    effective_at: str | None = None
    payload_sha256: str | None = None
    observation_sha256: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    persisted_terminal_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditSnapshotArtifact:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class _AuditSnapshot:
    rows: tuple[OpenPaperRow, ...]
    artifacts: tuple[AuditSnapshotArtifact, ...]
    open_rows_sha256: str


@dataclass(frozen=True)
class SettlementAuditReport:
    db_path: str
    generated_at: str
    fetched_markets: int
    rows: tuple[SettlementAuditRow, ...]
    snapshot_artifacts: tuple[AuditSnapshotArtifact, ...]
    open_rows_sha256: str
    read_only: bool = True
    resolution_applied: bool = False

    def to_dict(self) -> dict[str, object]:
        counts = Counter(row.status for row in self.rows)
        body = {
            "db_path": self.db_path,
            "generated_at": self.generated_at,
            "read_only": self.read_only,
            "resolution_applied": self.resolution_applied,
            "fetched_markets": self.fetched_markets,
            "counts": dict(sorted(counts.items())),
            "snapshot_artifacts": [asdict(artifact) for artifact in self.snapshot_artifacts],
            "open_rows_sha256": self.open_rows_sha256,
            "rows": [asdict(row) for row in self.rows],
        }
        evidence_body = {
            "read_only": self.read_only,
            "resolution_applied": self.resolution_applied,
            "fetched_markets": self.fetched_markets,
            "counts": body["counts"],
            "snapshot_artifacts": [
                {"size": artifact.size, "sha256": artifact.sha256}
                for artifact in self.snapshot_artifacts
            ],
            "open_rows_sha256": self.open_rows_sha256,
            "rows": body["rows"],
        }
        return {**body, "report_sha256": _sha256_json(evidence_body)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


@dataclass(frozen=True)
class _MarketAudit:
    status: str
    outcome: str | None = None
    source_id: str | None = None
    rules_version: str | None = None
    observed_at: str | None = None
    effective_at: str | None = None
    payload_sha256: str | None = None
    observation_sha256: str | None = None
    error_type: str | None = None
    error_detail: str | None = None


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_trades)")}


def _optional_column(columns: set[str], name: str) -> str:
    return name if name in columns else f"NULL AS {name}"


def _audit_source_paths(path: Path) -> tuple[Path, ...]:
    source = path.expanduser().resolve(strict=True)
    journal = source.with_name(source.name + "-journal")
    if journal.exists():
        raise PaperSettlementAuditSnapshotError("cannot audit a database with an active rollback journal")
    paths = [source]
    wal = source.with_name(source.name + "-wal")
    if wal.exists():
        raise PaperSettlementAuditSnapshotError(
            "cannot audit an input snapshot with an active WAL"
        )
    shm = source.with_name(source.name + "-shm")
    if shm.exists():
        raise PaperSettlementAuditSnapshotError(
            "cannot audit an input snapshot with a SQLite shared-memory sidecar"
        )
    for item in paths:
        if item.stat().st_size > MAX_AUDIT_SNAPSHOT_FILE_BYTES:
            raise PaperSettlementAuditSnapshotError("audit input exceeds the bounded snapshot size")
    return tuple(paths)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_source_signature(path: Path) -> tuple[AuditSnapshotArtifact, ...]:
    return tuple(
        AuditSnapshotArtifact(
            name=item.name,
            size=item.stat().st_size,
            sha256=_file_sha256(item),
        )
        for item in _audit_source_paths(path)
    )


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_value_sha256(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        payload = b"text\x00" + value.encode("utf-8")
    elif isinstance(value, bytes):
        payload = b"blob\x00" + value
    else:
        payload = b"other\x00" + repr(value).encode("utf-8", errors="backslashreplace")
    return hashlib.sha256(payload).hexdigest()


def _open_rows_sha256(rows: tuple[OpenPaperRow, ...]) -> str:
    return _sha256_json(
        [
            {
                "trade_id": row.trade_id,
                "ticker": row.ticker,
                "venue": row.venue,
                "canonical_market_id": row.canonical_market_id,
                "identity_status": row.identity_status,
                "quarantine_reason": row.quarantine_reason,
                "snapshot_close_time": row.snapshot_close_time,
                "snapshot_close_at": (
                    row.snapshot_close_at.isoformat() if row.snapshot_close_at is not None else None
                ),
                "market_snapshot_sha256": row.market_snapshot_sha256,
                "market_snapshot_malformed": row.market_snapshot_malformed,
                "trade_id_malformed": row.trade_id_malformed,
                "identity_status_malformed": row.identity_status_malformed,
                "persisted_terminal_fields": list(row.persisted_terminal_fields),
            }
            for row in rows
        ]
    )


def _exact_text(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _parse_snapshot_close_time(raw_snapshot: object) -> tuple[str | None, datetime | None, bool]:
    if raw_snapshot is None:
        return None, None, False
    if not isinstance(raw_snapshot, str) or not raw_snapshot.strip():
        return None, None, True
    try:
        snapshot = json.loads(raw_snapshot)
    except json.JSONDecodeError:
        return None, None, True
    if not isinstance(snapshot, dict):
        return None, None, True
    close_time = snapshot.get("close_time")
    if close_time is None:
        return None, None, False
    if not isinstance(close_time, str) or not close_time.strip():
        return None, None, True
    try:
        parsed = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
    except ValueError:
        return close_time, None, True
    if parsed.tzinfo is None:
        return close_time, None, True
    return close_time, parsed.astimezone(timezone.utc), False


def _load_open_rows(conn: sqlite3.Connection) -> list[OpenPaperRow]:
    columns = _columns(conn)
    required = {"trade_id", "ticker", "venue", "resolved"}
    missing = sorted(required - columns)
    if missing:
        raise ValueError(f"paper_trades is missing required columns: {', '.join(missing)}")
    optional = {
        name: _optional_column(columns, name)
        for name in (
            "venue_market_id",
            "identity_status",
            "quarantine_reason",
            "market_snapshot",
            *_TERMINAL_STATE_COLUMNS,
        )
    }
    rows = conn.execute(
        "SELECT trade_id, ticker, venue, "
        f"{optional['venue_market_id']}, {optional['identity_status']}, "
        f"{optional['quarantine_reason']}, {optional['market_snapshot']}, "
        f"{', '.join(optional[name] for name in _TERMINAL_STATE_COLUMNS)} "
        "FROM paper_trades WHERE resolved=0 ORDER BY trade_id"
    ).fetchall()
    result: list[OpenPaperRow] = []
    for row in rows:
        raw_snapshot = row["market_snapshot"]
        snapshot_close_time, snapshot_close_at, market_snapshot_malformed = _parse_snapshot_close_time(
            raw_snapshot
        )
        raw_trade_id = row["trade_id"]
        trade_id = _exact_text(raw_trade_id)
        raw_identity_status = row["identity_status"]
        identity_status = _exact_text(raw_identity_status)
        result.append(
            OpenPaperRow(
                trade_id=trade_id,
                ticker=_exact_text(row["ticker"]),
                venue=_exact_text(row["venue"]),
                canonical_market_id=_exact_text(row["venue_market_id"]),
                identity_status=identity_status,
                quarantine_reason=_exact_text(row["quarantine_reason"]),
                snapshot_close_time=snapshot_close_time,
                snapshot_close_at=snapshot_close_at,
                market_snapshot_sha256=_snapshot_value_sha256(raw_snapshot),
                market_snapshot_malformed=market_snapshot_malformed,
                trade_id_malformed=raw_trade_id is not None and trade_id is None,
                identity_status_malformed=(raw_identity_status is not None and identity_status is None),
                persisted_terminal_fields=tuple(name for name in _TERMINAL_STATE_COLUMNS if row[name] is not None),
            )
        )
    return result


def _load_open_rows_from_quiescent_snapshot(
    path: Path,
    *,
    expected_sha256: str | None,
) -> _AuditSnapshot:
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise PaperSettlementAuditSnapshotError(
            "audit requires a snapshot SHA-256 from a caller-attested snapshot"
        )
    input_signature = _audit_source_signature(path)
    if input_signature[0].sha256 != expected_sha256:
        raise PaperSettlementAuditSnapshotError(
            "snapshot SHA-256 does not match the caller-attested snapshot"
        )
    # immutable=1 prevents any SQLite reader locks or -shm writes if a writer
    # violates the caller's external-quiescence prerequisite during the read.
    uri = f"{path.expanduser().resolve().as_uri()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                detail = "missing result" if integrity is None else str(integrity[0])
                raise PaperSettlementAuditSnapshotError(
                    f"caller-attested snapshot failed SQLite integrity check: {detail}"
                )
            rows = _load_open_rows(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise PaperSettlementAuditSnapshotError(
            f"caller-attested snapshot failed SQLite integrity check: {_error_detail(exc)}"
        ) from exc
    if _audit_source_signature(path) != input_signature:
        raise PaperSettlementAuditSnapshotError(
            "caller-attested snapshot changed while the audit read it"
        )
    frozen_rows = tuple(rows)
    return _AuditSnapshot(
        rows=frozen_rows,
        artifacts=input_signature,
        open_rows_sha256=_open_rows_sha256(frozen_rows),
    )


def _error_detail(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:300] or type(exc).__name__


def _nonfetchable_status(row: OpenPaperRow) -> str | None:
    if row.persisted_terminal_fields:
        return "inconsistent_persisted_state"
    if row.market_snapshot_malformed:
        return "invalid_snapshot"
    if (
        row.trade_id is None
        or row.trade_id_malformed
        or row.ticker is None
        or row.venue is None
        or row.identity_status_malformed
    ):
        return "invalid_identity"
    if row.venue != Venue.POLYMARKET_US.value:
        return "unsupported_venue"
    if row.identity_status == "quarantined":
        return "quarantined"
    if row.identity_status != "mapped":
        return "unmapped_identity"
    if row.canonical_market_id is None or _NUMERIC_MARKET_ID.fullmatch(row.canonical_market_id) is None:
        return "invalid_identity"
    return None


async def _audit_market(
    market_ref: MarketRef,
    source: ExactSettlementSource,
) -> _MarketAudit:
    try:
        observation = await source.get_settlement_exact(
            market_ref,
            prior_observation=None,
        )
    except SettlementNotFound as exc:
        return _MarketAudit(
            status="not_found",
            error_type=type(exc).__name__,
            error_detail=_error_detail(exc),
        )
    except SettlementDriftError as exc:
        return _MarketAudit(
            status="settlement_drift",
            error_type=type(exc).__name__,
            error_detail=_error_detail(exc),
        )
    except (TimeoutError, OSError) as exc:
        return _MarketAudit(
            status="transport_error",
            error_type=type(exc).__name__,
            error_detail=_error_detail(exc),
        )
    except Exception as exc:
        return _MarketAudit(
            status="source_error",
            error_type=type(exc).__name__,
            error_detail=_error_detail(exc),
        )

    if observation is None:
        return _MarketAudit(status="pending_receipt")
    if not isinstance(observation, SettlementObservation):
        return _MarketAudit(
            status="settlement_drift",
            error_type="InvalidSettlementObservation",
            error_detail="source returned an invalid settlement observation",
        )
    if observation.market_ref != market_ref:
        return _MarketAudit(
            status="settlement_drift",
            error_type="SettlementIdentityMismatch",
            error_detail="source observation did not match the requested market identity",
        )
    return _MarketAudit(
        status="authoritative_terminal",
        outcome=observation.outcome.value,
        source_id=observation.source_id,
        rules_version=observation.rules_version,
        observed_at=observation.observed_at.isoformat(),
        effective_at=observation.effective_at.isoformat(),
        payload_sha256=observation.payload_sha256,
        observation_sha256=observation.observation_sha256,
    )


def _row_from_market_audit(
    row: OpenPaperRow,
    audit: _MarketAudit,
    *,
    now: datetime,
) -> SettlementAuditRow:
    status = audit.status
    if status == "pending_receipt" and row.snapshot_close_at is not None and row.snapshot_close_at <= now:
        status = "expired_snapshot_pending_receipt"
    return SettlementAuditRow(
        trade_id=row.trade_id,
        ticker=row.ticker,
        venue=row.venue,
        canonical_market_id=row.canonical_market_id,
        identity_status=row.identity_status,
        quarantine_reason=row.quarantine_reason,
        snapshot_close_time=row.snapshot_close_time,
        status=status,
        outcome=audit.outcome,
        source_id=audit.source_id,
        rules_version=audit.rules_version,
        observed_at=audit.observed_at,
        effective_at=audit.effective_at,
        payload_sha256=audit.payload_sha256,
        observation_sha256=audit.observation_sha256,
        error_type=audit.error_type,
        error_detail=audit.error_detail,
        persisted_terminal_fields=row.persisted_terminal_fields,
    )


async def _audit_rows(
    rows: list[OpenPaperRow],
    source: ExactSettlementSource,
    *,
    db_path: Path,
    now: datetime,
    snapshot_artifacts: tuple[AuditSnapshotArtifact, ...],
    open_rows_sha256: str,
) -> SettlementAuditReport:
    by_market: dict[tuple[str, str], list[OpenPaperRow]] = {}
    skipped: dict[int, SettlementAuditRow] = {}
    for index, row in enumerate(rows):
        status = _nonfetchable_status(row)
        if status is not None:
            skipped[index] = SettlementAuditRow(
                trade_id=row.trade_id,
                ticker=row.ticker,
                venue=row.venue,
                canonical_market_id=row.canonical_market_id,
                identity_status=row.identity_status,
                quarantine_reason=row.quarantine_reason,
                snapshot_close_time=row.snapshot_close_time,
                status=status,
                persisted_terminal_fields=row.persisted_terminal_fields,
            )
            continue
        assert row.trade_id is not None
        assert row.canonical_market_id is not None
        assert row.ticker is not None
        by_market.setdefault((row.canonical_market_id, row.ticker), []).append(row)

    market_results: dict[tuple[str, str], _MarketAudit] = {}
    for canonical_market_id, ticker in sorted(by_market):
        market_ref = MarketRef(Venue.POLYMARKET_US, canonical_market_id, ticker)
        market_results[(canonical_market_id, ticker)] = await _audit_market(market_ref, source)

    audit_rows: list[SettlementAuditRow] = []
    for index, row in enumerate(rows):
        skipped_row = skipped.get(index)
        if skipped_row is not None:
            audit_rows.append(skipped_row)
            continue
        assert row.canonical_market_id is not None
        audit_rows.append(
            _row_from_market_audit(
                row,
                market_results[(row.canonical_market_id, row.ticker)],
                now=now,
            )
        )
    return SettlementAuditReport(
        db_path=str(db_path.expanduser().resolve()),
        generated_at=now.isoformat(),
        fetched_markets=len(by_market),
        rows=tuple(audit_rows),
        snapshot_artifacts=snapshot_artifacts,
        open_rows_sha256=open_rows_sha256,
    )


async def audit_database(
    db_path: Path,
    source: ExactSettlementSource,
    *,
    snapshot_sha256: str | None = None,
    now: datetime | None = None,
) -> SettlementAuditReport:
    """Audit a hash-attested snapshot supplied after caller-managed quiescence."""
    audit_now = now or datetime.now(timezone.utc)
    if audit_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    resolved_path = db_path.expanduser().resolve()
    snapshot = _load_open_rows_from_quiescent_snapshot(
        resolved_path,
        expected_sha256=snapshot_sha256,
    )
    return await _audit_rows(
        list(snapshot.rows),
        source,
        db_path=resolved_path,
        now=audit_now.astimezone(timezone.utc),
        snapshot_artifacts=snapshot.artifacts,
        open_rows_sha256=snapshot.open_rows_sha256,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only audit of authoritative receipts for open paper positions.")
    parser.add_argument(
        "--snapshot-db",
        type=Path,
        required=True,
        help="Caller-attested SQLite copy created after the paper-trade writer was quiesced.",
    )
    parser.add_argument(
        "--snapshot-sha256",
        required=True,
        help="SHA-256 identity attestation for --snapshot-db; it does not prove quiescence.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_AUTHORITATIVE_SETTLEMENT_TIMEOUT_SECONDS,
        help="Per-market authoritative source deadline in seconds.",
    )
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    source = AuthoritativeSettlementSource(
        kalshi_client=None,
        polymarket_client=PolymarketPublicClient(),
        timeout_seconds=args.timeout_seconds,
    )
    report = await audit_database(
        args.snapshot_db,
        source,
        snapshot_sha256=args.snapshot_sha256,
    )
    print(report.to_json())
    return 0


def main() -> int:
    return asyncio.run(_main_async(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
