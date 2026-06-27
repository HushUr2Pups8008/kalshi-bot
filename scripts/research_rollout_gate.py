#!/usr/bin/env python3
"""Read-only rollout proof gate for the real web-research path.

This gate deliberately fails closed. It does not mutate runtime config, restart
services, write databases, or call external APIs.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import botcheck


ACTIVE_RESEARCH_MODES = {"shadow", "production"}
SUCCESSFUL_RESEARCH_STATUSES = {"trade_candidate"}


@dataclass(frozen=True)
class ResearchRolloutAssessment:
    ok: bool
    failures: list[str] = field(default_factory=list)
    mode: str = "off"
    mode_source: str = "default"
    prewarm_enabled: bool = False
    prewarm_source: str = "default"
    research_rows: int = 0
    successful_research_rows: int = 0
    matched_research_proofs: int = 0
    prewarm_backlog: list[str] = field(default_factory=list)
    unresolved_prewarm_backlog: list[str] = field(default_factory=list)
    dossier_state: str = "missing"
    fresh_evidence_rows_24h: int = 0
    live_cache_eligible_dossiers: int = 0
    latest_bot_version: str | None = None


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(time.time(), tz=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_bot_version(bot_log: Path | None) -> str | None:
    if bot_log is None:
        return None
    sessions = botcheck.read_sessions(bot_log)
    if not sessions:
        return None
    return sessions[-1].version


def _dossier_state(dossier_stats: botcheck.ResearchDossierStats) -> str:
    if not dossier_stats.exists:
        return "missing"
    if dossier_stats.error:
        return dossier_stats.error
    return "present"


def _successful_research_proofs(path: Path, *, since: datetime) -> set[tuple[str, str]]:
    lines_total = [0]
    lines_malformed = [0]
    proofs: set[tuple[str, str]] = set()
    for record in botcheck._iter_trade_records(  # noqa: SLF001 - read-only helper.
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ts is not None and ts < since:
            continue
        status = str(record.get("research_status") or "").strip()
        if status not in SUCCESSFUL_RESEARCH_STATUSES:
            continue
        ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
        run_id = str(record.get("research_run_id") or "").strip()
        if ticker and run_id:
            proofs.add((ticker, run_id))
    return proofs


def _live_cache_eligible_proofs(repo_root: Path, *, now: datetime) -> set[tuple[str, str]]:
    db_path = botcheck._research_dossier_db_path(repo_root)  # noqa: SLF001
    if not db_path.exists():
        return set()

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not all(
                botcheck._sqlite_table_exists(conn, table_name)  # noqa: SLF001
                for table_name in ("research_dossiers", "research_evidence")
            ):
                return set()
            live_cache_since = now - timedelta(
                seconds=botcheck.RESEARCH_DOSSIER_MAX_AGE_SECONDS,
            )
            evidence_by_proof: dict[tuple[str, str], list[str]] = {}
            for row in conn.execute(
                """
                SELECT
                    market_ticker,
                    research_run_id,
                    source_class,
                    COALESCE(retrieved_at, inserted_at) AS ts
                FROM research_evidence
                """
            ):
                evidence_ts = botcheck._parse_research_ts(row["ts"])  # noqa: SLF001
                if evidence_ts is None or evidence_ts < live_cache_since:
                    continue
                ticker = str(row["market_ticker"] or "").strip()
                run_id = str(row["research_run_id"] or "").strip()
                if ticker and run_id:
                    evidence_by_proof.setdefault((ticker, run_id), []).append(
                        str(row["source_class"] or "").strip()
                    )

            vetted_proofs = {
                (
                    str(row["market_ticker"] or "").strip(),
                    str(row["last_research_run_id"] or "").strip(),
                )
                for row in conn.execute(
                    """
                    SELECT market_ticker, last_research_run_id
                    FROM research_dossiers
                    WHERE last_verdict_status = 'trade_candidate'
                      AND last_force_side IN ('yes', 'no')
                      AND last_estimated_probability IS NOT NULL
                      AND last_confidence IS NOT NULL
                    """
                )
            }
    except sqlite3.Error:
        return set()

    return {
        proof
        for proof in vetted_proofs
        if proof[0]
        and proof[1]
        and len(evidence_by_proof.get(proof, [])) >= 2
        and any(
            source_class in botcheck.RESEARCH_REQUIRED_SOURCE_CLASSES
            for source_class in evidence_by_proof[proof]
        )
    }


def evaluate_research_rollout(
    repo_root: Path,
    trades_log: Path,
    *,
    now: datetime | None = None,
    window_hours: float = 24.0,
    bot_log: Path | None = None,
    expected_version: str | None = None,
    allow_prewarm_off: bool = False,
) -> ResearchRolloutAssessment:
    """Evaluate whether research rollout proof is strong enough to treat as live."""
    now = now or datetime.fromtimestamp(time.time(), tz=timezone.utc)
    repo_root = repo_root.resolve()
    trades_log = trades_log.resolve()
    mode, mode_source = botcheck._research_env_value(  # noqa: SLF001 - pure helper.
        repo_root,
        "REAL_WEB_RESEARCH_MODE",
        "off",
    )
    prewarm_raw, prewarm_source = botcheck._research_env_value(  # noqa: SLF001
        repo_root,
        "ENABLE_RESEARCH_PREWARM_TASK",
        "false",
    )
    mode = mode.strip().lower() or "off"
    prewarm_enabled = botcheck._env_bool(prewarm_raw)  # noqa: SLF001

    signal_stats = botcheck.summarize_signal_flow(
        trades_log,
        now=now,
        window_hours=window_hours,
    )
    prewarm_backlog = botcheck.summarize_research_prewarm_backlog(
        signal_stats.path,
        since=signal_stats.since,
    )
    successful_research_proofs = _successful_research_proofs(
        signal_stats.path,
        since=signal_stats.since,
    )
    live_cache_eligible_proofs = _live_cache_eligible_proofs(repo_root, now=now)
    matched_research_proofs = successful_research_proofs & live_cache_eligible_proofs
    proven_researched_tickers = {ticker for ticker, _run_id in matched_research_proofs}
    unresolved_prewarm_backlog = [
        ticker for ticker in prewarm_backlog if ticker not in proven_researched_tickers
    ]
    dossier_stats = botcheck.summarize_research_dossiers(repo_root, now=now)
    latest_version = _latest_bot_version(bot_log)
    successful_research_rows = sum(
        count
        for status, count in signal_stats.research_status_counts.items()
        if status in SUCCESSFUL_RESEARCH_STATUSES
    )
    failures: list[str] = []

    if mode not in ACTIVE_RESEARCH_MODES:
        failures.append(
            "REAL_WEB_RESEARCH_MODE inactive: "
            f"{mode} ({mode_source}); expected shadow or production"
        )
    if expected_version is None:
        failures.append("expected deployed version not supplied")
    else:
        if latest_version is None:
            failures.append(
                f"bot restart/version proof missing: expected {expected_version}"
            )
        elif latest_version != expected_version:
            failures.append(
                "bot restart/version mismatch: "
                f"latest={latest_version} expected={expected_version}"
            )
    if signal_stats.research_records <= 0:
        failures.append(
            f"no recent research_* rows in signal log window ({window_hours:g}h)"
        )
    elif successful_research_rows <= 0:
        failures.append(
            "no successful recent research rows "
            f"(accepted statuses={','.join(sorted(SUCCESSFUL_RESEARCH_STATUSES))})"
        )
    elif not matched_research_proofs:
        failures.append(
            "no successful recent research rows with matching live-cache dossier evidence"
        )

    state = _dossier_state(dossier_stats)
    if state == "missing":
        failures.append("research dossier database missing")
    elif state == "not_initialized":
        failures.append("research dossier database not initialized")
    elif state != "present":
        failures.append(f"research dossier database unreadable: {state}")
    else:
        if dossier_stats.dossiers <= 0:
            failures.append("research dossier database has zero dossiers")
        if dossier_stats.fresh_evidence_rows_24h <= 0:
            failures.append("research dossier database has zero fresh evidence rows")
        if dossier_stats.live_cache_eligible_dossiers <= 0:
            failures.append("no live-cache-eligible researched dossiers")

    if (
        mode in ACTIVE_RESEARCH_MODES
        and unresolved_prewarm_backlog
        and not prewarm_enabled
        and not allow_prewarm_off
    ):
        failures.append(
            "research prewarm disabled with "
            f"{len(unresolved_prewarm_backlog)} unresolved targetable information gaps"
        )

    return ResearchRolloutAssessment(
        ok=not failures,
        failures=failures,
        mode=mode,
        mode_source=mode_source,
        prewarm_enabled=prewarm_enabled,
        prewarm_source=prewarm_source,
        research_rows=signal_stats.research_records,
        successful_research_rows=successful_research_rows,
        matched_research_proofs=len(matched_research_proofs),
        prewarm_backlog=prewarm_backlog,
        unresolved_prewarm_backlog=unresolved_prewarm_backlog,
        dossier_state=state,
        fresh_evidence_rows_24h=dossier_stats.fresh_evidence_rows_24h,
        live_cache_eligible_dossiers=dossier_stats.live_cache_eligible_dossiers,
        latest_bot_version=latest_version,
    )


def _default_repo_root() -> Path:
    return Path(os.environ.get("KALSHI_HOME", Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = _default_repo_root()
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument(
        "--trades-log",
        type=Path,
        default=default_home / "logs" / "trades",
    )
    parser.add_argument(
        "--bot-log",
        type=Path,
        default=default_home / "logs" / "app" / "bot.log",
    )
    parser.add_argument("--expected-version")
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--now", help="ISO-8601 timestamp for deterministic tests")
    parser.add_argument(
        "--allow-prewarm-off",
        action="store_true",
        help="Do not fail active research mode solely because prewarm is off.",
    )
    args = parser.parse_args()

    assessment = evaluate_research_rollout(
        args.home,
        args.trades_log,
        now=_parse_now(args.now),
        window_hours=args.window_hours,
        bot_log=args.bot_log,
        expected_version=args.expected_version,
        allow_prewarm_off=args.allow_prewarm_off,
    )

    print(f"Research rollout gate: {'PASS' if assessment.ok else 'FAIL'}")
    print(f"mode: {assessment.mode} ({assessment.mode_source})")
    print(
        "prewarm: "
        f"{'on' if assessment.prewarm_enabled else 'off'} "
        f"({assessment.prewarm_source})"
    )
    print(f"research_rows: {assessment.research_rows}")
    print(f"successful_research_rows: {assessment.successful_research_rows}")
    print(f"matched_research_proofs: {assessment.matched_research_proofs}")
    print(f"prewarm_backlog: {len(assessment.prewarm_backlog)}")
    print(f"unresolved_prewarm_backlog: {len(assessment.unresolved_prewarm_backlog)}")
    print(f"dossier_state: {assessment.dossier_state}")
    print(f"fresh_evidence_rows_24h: {assessment.fresh_evidence_rows_24h}")
    print(f"live_cache_eligible_dossiers: {assessment.live_cache_eligible_dossiers}")
    if assessment.latest_bot_version is not None:
        print(f"latest_bot_version: {assessment.latest_bot_version}")
    if assessment.failures:
        print("failures:")
        for failure in assessment.failures:
            print(f"- {failure}")
    return 0 if assessment.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
