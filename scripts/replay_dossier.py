"""Read-only dossier replay utility.

Reconstructs a market's dossier trajectory from persisted evidence rows by
feeding each event back through the existing dossier builder. The tool never
writes to the evidence store.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from analysis.dossier_builder import update_dossier
from analysis.evidence_types import Dossier, EvidenceScore
from tasks.evidence_store import DEFAULT_EVIDENCE_DB_PATH, DossierState, EvidenceRecord


class ReplayError(Exception):
    """Base class for replay failures."""


class ReplayDefect(ReplayError):
    """Raised when persisted evidence cannot reconstruct dossier state."""


@dataclass(frozen=True)
class ReplayStep:
    index: int
    evidence_id: str
    event_ts: str
    source: str
    source_class: str
    headline: str
    update_type: str
    resulting_version: int
    resulting_estimate: float | None
    resulting_confidence: float
    drift_suspect: bool
    in_recovery: bool


@dataclass(frozen=True)
class ReplayComparison:
    status: str
    differences: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ReplayResult:
    market_ticker: str
    event_count: int
    steps: list[ReplayStep]
    final_dossier: Dossier | None
    stored_dossier: DossierState | None
    comparison: ReplayComparison


def replay_market(
    market_ticker: str,
    *,
    db_path: Path = DEFAULT_EVIDENCE_DB_PATH,
    limit: int | None = None,
    since: str | None = None,
) -> ReplayResult:
    """Replay one market's dossier trajectory from persisted evidence rows."""
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when provided")
    if since is not None:
        _parse_iso_ts(since)

    with _connect_read_only(db_path) as conn:
        evidence_rows = _load_evidence(conn, market_ticker, limit=limit, since=since)
        stored_dossier = _load_stored_dossier(conn, market_ticker)

    dossier: Dossier | None = None
    steps: list[ReplayStep] = []
    for index, record in enumerate(evidence_rows, start=1):
        score = _score_from_record(record)
        if dossier is None:
            dossier = _initial_dossier(record)
        dossier = update_dossier(
            dossier,
            score,
            record.update_type,
            now=_parse_iso_ts(record.ingested_ts),
        )
        steps.append(_step_from_record(index, record, dossier))

    return ReplayResult(
        market_ticker=market_ticker,
        event_count=len(evidence_rows),
        steps=steps,
        final_dossier=dossier,
        stored_dossier=stored_dossier,
        comparison=_compare_to_stored(dossier, stored_dossier),
    )


def render_text(result: ReplayResult) -> str:
    """Render a human-readable audit trail."""
    lines = [
        "DOSSIER REPLAY",
        f"  Market:              {result.market_ticker}",
        f"  Evidence events:     {result.event_count}",
        f"  Stored comparison:   {result.comparison.status}",
    ]
    if result.comparison.differences:
        lines.append("  Differences:")
        for field, values in result.comparison.differences.items():
            lines.append(
                f"    {field}: replay={values['replay']!r} stored={values['stored']!r}"
            )

    lines.append("")
    lines.append("EVENT TRAJECTORY")
    if not result.steps:
        lines.append("  No evidence events found.")
    for step in result.steps:
        lines.extend(
            [
                f"  [{step.index}] {step.event_ts} {step.evidence_id}",
                f"      source={step.source} class={step.source_class} update={step.update_type}",
                f"      headline={step.headline}",
                (
                    "      state="
                    f"version={step.resulting_version} "
                    f"estimate={_fmt_prob(step.resulting_estimate)} "
                    f"confidence={step.resulting_confidence:.6f} "
                    f"drift_suspect={step.drift_suspect} "
                    f"in_recovery={step.in_recovery}"
                ),
            ]
        )

    lines.append("")
    lines.append("FINAL RECONSTRUCTED DOSSIER")
    if result.final_dossier is None:
        lines.append("  None")
    else:
        final = result.final_dossier
        lines.extend(
            [
                f"  version:             {final.dossier_version}",
                f"  current_estimate:    {_fmt_prob(final.current_estimate)}",
                f"  confidence:          {final.confidence:.6f}",
                f"  prior_estimate:      {_fmt_prob(final.prior_estimate)}",
                f"  drift_suspect:       {final.drift_suspect}",
                f"  in_recovery:         {final.in_recovery}",
                f"  updated_ts:          {final.updated_ts}",
            ]
        )
    return "\n".join(lines)


def result_to_dict(result: ReplayResult) -> dict[str, Any]:
    return {
        "market_ticker": result.market_ticker,
        "event_count": result.event_count,
        "steps": [asdict(step) for step in result.steps],
        "final_dossier": asdict(result.final_dossier) if result.final_dossier else None,
        "stored_dossier": asdict(result.stored_dossier) if result.stored_dossier else None,
        "comparison": asdict(result.comparison),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", required=True, help="Market ticker to replay")
    parser.add_argument("--limit", type=int, help="Maximum evidence events to replay")
    parser.add_argument("--since", help="Replay evidence ingested at or after this ISO timestamp")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_EVIDENCE_DB_PATH,
        help=f"Evidence-store DB path (default: {DEFAULT_EVIDENCE_DB_PATH})",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        result = replay_market(
            args.market,
            db_path=args.db,
            limit=args.limit,
            since=args.since,
        )
    except ReplayDefect as exc:
        parser.exit(2, f"Replay defect: {exc}\n")
    except ReplayError as exc:
        parser.exit(1, f"Replay failed: {exc}\n")

    if args.json:
        print(json.dumps(result_to_dict(result), sort_keys=True, indent=2))
    else:
        print(render_text(result))
    return 0


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise ReplayError(f"evidence store not found: {db_path}")
    uri = f"file:{quote(str(db_path.resolve()), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _load_evidence(
    conn: sqlite3.Connection,
    market_ticker: str,
    *,
    limit: int | None,
    since: str | None,
) -> list[EvidenceRecord]:
    clauses = ["market_ticker = ?"]
    params: list[Any] = [market_ticker]
    if since is not None:
        clauses.append("ingested_ts >= ?")
        params.append(since)
    query = f"""
        SELECT *
        FROM evidence
        WHERE {' AND '.join(clauses)}
        ORDER BY ingested_ts ASC, evidence_id ASC
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_evidence_from_row(row) for row in rows]


def _load_stored_dossier(
    conn: sqlite3.Connection,
    market_ticker: str,
) -> DossierState | None:
    row = conn.execute(
        "SELECT * FROM dossiers WHERE market_ticker = ?",
        (market_ticker,),
    ).fetchone()
    if row is None:
        return None
    return DossierState(
        market_ticker=row["market_ticker"],
        dossier_version=int(row["dossier_version"]),
        current_estimate=row["current_estimate"],
        confidence=float(row["confidence"]),
        prior_estimate=row["prior_estimate"],
        drift_suspect=bool(row["drift_suspect"]),
        in_recovery=bool(row["in_recovery"]),
        freeze_started_ts=row["freeze_started_ts"],
        recovery_started_ts=row["recovery_started_ts"],
        recovery_until_ts=row["recovery_until_ts"],
        last_cross_class_state_update_ts=row["last_cross_class_state_update_ts"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
    )


def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=row["evidence_id"],
        market_ticker=row["market_ticker"],
        source=row["source"],
        source_class=row["source_class"],
        headline=row["headline"],
        ingested_ts=row["ingested_ts"],
        content_hash=row["content_hash"],
        update_type=row["update_type"],
        dossier_version_before=int(row["dossier_version_before"]),
        dossier_version_after=int(row["dossier_version_after"]),
        url=row["url"],
        published_ts=row["published_ts"],
        raw_payload_json=row["raw_payload_json"],
        headline_ngram_fingerprint=row["headline_ngram_fingerprint"],
        correlation_cluster_id=row["correlation_cluster_id"],
        is_duplicate=bool(row["is_duplicate"]),
        correlation_discount_applied=bool(row["correlation_discount_applied"]),
        quality_score=row["quality_score"],
        original_weight=row["original_weight"],
    )


def _score_from_record(record: EvidenceRecord) -> EvidenceScore:
    missing: list[str] = []
    if record.quality_score is None:
        missing.append("quality_score")
    if record.original_weight is None:
        missing.append("original_weight")
    implied_probability = _implied_probability_from_payload(record)
    if implied_probability is None:
        missing.append("raw_payload_json.implied_probability")
    if missing:
        raise ReplayDefect(
            f"evidence {record.evidence_id} missing replay-critical field(s): "
            + ", ".join(missing)
        )
    return EvidenceScore(
        evidence_id=record.evidence_id,
        source_class=record.source_class,
        quality_score=float(record.quality_score),
        original_weight=float(record.original_weight),
        is_duplicate=record.is_duplicate,
        correlation_discount_applied=record.correlation_discount_applied,
        is_independent=not record.correlation_discount_applied,
        same_class_count=0,
        implied_probability=implied_probability,
    )


def _implied_probability_from_payload(record: EvidenceRecord) -> float | None:
    if not record.raw_payload_json:
        return None
    try:
        payload = json.loads(record.raw_payload_json)
    except json.JSONDecodeError as exc:
        raise ReplayDefect(
            f"evidence {record.evidence_id} has malformed raw_payload_json"
        ) from exc
    candidate = _nested_get(payload, ("implied_probability",))
    if candidate is None:
        candidate = _nested_get(payload, ("evidence", "implied_probability"))
    if candidate is None:
        candidate = _nested_get(payload, ("score", "implied_probability"))
    if candidate is None:
        return None
    try:
        value = float(candidate)
    except (TypeError, ValueError) as exc:
        raise ReplayDefect(
            f"evidence {record.evidence_id} has non-numeric implied_probability"
        ) from exc
    if not 0.0 <= value <= 1.0:
        raise ReplayDefect(
            f"evidence {record.evidence_id} implied_probability out of range: {value}"
        )
    return value


def _nested_get(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _initial_dossier(record: EvidenceRecord) -> Dossier:
    return Dossier(
        market_ticker=record.market_ticker,
        dossier_version=0,
        confidence=0.0,
        drift_suspect=False,
        in_recovery=False,
        created_ts=record.ingested_ts,
        updated_ts=record.ingested_ts,
    )


def _step_from_record(index: int, record: EvidenceRecord, dossier: Dossier) -> ReplayStep:
    return ReplayStep(
        index=index,
        evidence_id=record.evidence_id,
        event_ts=record.ingested_ts,
        source=record.source,
        source_class=record.source_class,
        headline=record.headline,
        update_type=record.update_type,
        resulting_version=dossier.dossier_version,
        resulting_estimate=dossier.current_estimate,
        resulting_confidence=dossier.confidence,
        drift_suspect=dossier.drift_suspect,
        in_recovery=dossier.in_recovery,
    )


def _compare_to_stored(
    replayed: Dossier | None,
    stored: DossierState | None,
) -> ReplayComparison:
    if replayed is None and stored is None:
        return ReplayComparison(status="match", differences={})
    if replayed is None or stored is None:
        return ReplayComparison(
            status="diverged",
            differences={
                "presence": {
                    "replay": replayed is not None,
                    "stored": stored is not None,
                }
            },
        )

    differences: dict[str, dict[str, Any]] = {}
    fields = {
        "dossier_version": (replayed.dossier_version, stored.dossier_version),
        "current_estimate": (replayed.current_estimate, stored.current_estimate),
        "confidence": (replayed.confidence, stored.confidence),
        "prior_estimate": (replayed.prior_estimate, stored.prior_estimate),
        "drift_suspect": (replayed.drift_suspect, stored.drift_suspect),
        "in_recovery": (replayed.in_recovery, stored.in_recovery),
        "freeze_started_ts": (replayed.freeze_started_ts, stored.freeze_started_ts),
        "recovery_started_ts": (replayed.recovery_started_ts, stored.recovery_started_ts),
        "recovery_until_ts": (replayed.recovery_until_ts, stored.recovery_until_ts),
        "last_cross_class_state_update_ts": (
            replayed.last_cross_class_state_update_ts,
            stored.last_cross_class_state_update_ts,
        ),
        "created_ts": (replayed.created_ts, stored.created_ts),
        "updated_ts": (replayed.updated_ts, stored.updated_ts),
    }
    for field, (replay_value, stored_value) in fields.items():
        if not _values_match(replay_value, stored_value):
            differences[field] = {"replay": replay_value, "stored": stored_value}

    return ReplayComparison(
        status="match" if not differences else "diverged",
        differences=differences,
    )


def _values_match(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, float) or isinstance(right, float):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    return left == right


def _parse_iso_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReplayDefect(f"invalid ISO timestamp: {value}") from exc


def _fmt_prob(value: float | None) -> str:
    return "None" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    raise SystemExit(main())
