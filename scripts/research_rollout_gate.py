#!/usr/bin/env python3
"""Read-only rollout proof gate for the real web-research path.

This gate deliberately fails closed. It does not mutate runtime config, restart
services, write databases, or call external APIs.
"""
from __future__ import annotations

import argparse
import json
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
from utils.research_evidence_quality import (
    OFFICIAL_RESEARCH_SOURCE_CLASSES,
    STRUCTURED_OFFICIAL_RESEARCH_METRICS,
    has_reliable_research_source_path,
    research_evidence_temporally_valid,
)
from utils.research_market_eligibility import evaluate_research_market_eligibility


ACTIVE_RESEARCH_MODES = {"shadow", "production"}
DECISION_GRADE_RESEARCH_STATUSES = {"decision_grade_candidate"}
DECISION_GRADE_NO_TRADE_SKIP_REASONS = {
    "contradictory_evidence_unresolved",
    "market_closed",
    "no_edge",
    "no_reliable_source_path",
    "non_actionable_market_price",
}
SUCCESSFUL_RESEARCH_STATUSES = DECISION_GRADE_RESEARCH_STATUSES | {"untradeable"}
OPERATIONAL_RESEARCH_STATUSES = {
    "research_adjudicator_error",
    "research_operational_error",
    "research_provider_error",
}


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


def _research_proofs(
    path: Path,
    *,
    since: datetime,
    statuses: set[str],
    skip_reasons: set[str] | None = None,
) -> set[tuple[str, str, str]]:
    lines_total = [0]
    lines_malformed = [0]
    proofs: set[tuple[str, str, str]] = set()
    for record in botcheck._iter_trade_records(  # noqa: SLF001 - read-only helper.
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ts is not None and ts < since:
            continue
        status = str(record.get("research_status") or "").strip()
        if status not in statuses:
            continue
        if skip_reasons is not None:
            skip_reason = str(record.get("research_skip_reason") or "").strip()
            if skip_reason not in skip_reasons:
                continue
        ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
        run_id = str(record.get("research_run_id") or "").strip()
        fingerprint = str(record.get("research_contract_fingerprint") or "").strip()
        if ticker and run_id and fingerprint:
            proofs.add((ticker, run_id, fingerprint))
    return proofs


def _successful_research_proofs(
    path: Path,
    *,
    since: datetime,
) -> set[tuple[str, str, str]]:
    return _research_proofs(
        path,
        since=since,
        statuses=DECISION_GRADE_RESEARCH_STATUSES,
    ) | _research_proofs(
        path,
        since=since,
        statuses={"untradeable"},
        skip_reasons=DECISION_GRADE_NO_TRADE_SKIP_REASONS,
    )


def _successful_research_row_count(path: Path, *, since: datetime) -> int:
    lines_total = [0]
    lines_malformed = [0]
    count = 0
    for record in botcheck._iter_trade_records(  # noqa: SLF001 - read-only helper.
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ts is not None and ts < since:
            continue
        status = str(record.get("research_status") or "").strip()
        if status in DECISION_GRADE_RESEARCH_STATUSES:
            count += 1
            continue
        if status == "untradeable":
            skip_reason = str(record.get("research_skip_reason") or "").strip()
            if skip_reason in DECISION_GRADE_NO_TRADE_SKIP_REASONS:
                count += 1
    return count


def _operational_research_error_count(path: Path, *, since: datetime) -> int:
    lines_total = [0]
    lines_malformed = [0]
    count = 0
    for record in botcheck._iter_trade_records(  # noqa: SLF001 - read-only helper.
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ts is not None and ts < since:
            continue
        status = str(record.get("research_status") or "").strip()
        if status in OPERATIONAL_RESEARCH_STATUSES:
            count += 1
    return count


def _decision_grade_research_proofs(
    path: Path,
    *,
    since: datetime,
) -> set[tuple[str, str, str]]:
    return _research_proofs(path, since=since, statuses=DECISION_GRADE_RESEARCH_STATUSES)


def _active_decision_grade_proofs(
    repo_root: Path,
    proofs: set[tuple[str, str, str]],
    *,
    now: datetime,
) -> set[tuple[str, str, str]]:
    if not proofs:
        return set()
    db_path = botcheck._research_dossier_db_path(repo_root)  # noqa: SLF001
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not all(
                botcheck._sqlite_table_exists(conn, table_name)  # noqa: SLF001
                for table_name in ("research_dossiers", "research_runs")
            ):
                return set()
            run_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
            }
            dossier_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(research_dossiers)"
                ).fetchall()
            }
            if not {
                "research_run_id",
                "market_ticker",
                "verdict_status",
                "decision_grade_status",
            } <= run_columns:
                return set()
            if not {
                "market_ticker",
                "last_research_run_id",
                "last_contract_fingerprint",
                "last_verdict_status",
                "last_decision_grade_status",
                "market_status",
                "market_close_time",
            } <= dossier_columns:
                return set()

            active_dossier_proofs: set[tuple[str, str, str]] = set()
            for row in conn.execute(
                """
                SELECT
                    d.market_ticker,
                    d.last_research_run_id AS research_run_id,
                    d.last_contract_fingerprint AS contract_fingerprint,
                    d.market_status,
                    d.market_close_time
                FROM research_dossiers AS d
                JOIN research_runs AS r
                  ON r.market_ticker = d.market_ticker
                 AND r.research_run_id = d.last_research_run_id
                WHERE d.last_verdict_status = 'decision_grade_candidate'
                  AND d.last_decision_grade_status = 'decision_grade_candidate'
                  AND r.verdict_status = 'decision_grade_candidate'
                  AND r.decision_grade_status = 'decision_grade_candidate'
                """
            ):
                ticker = str(row["market_ticker"] or "").strip()
                run_id = str(row["research_run_id"] or "").strip()
                fingerprint = str(row["contract_fingerprint"] or "").strip()
                eligibility = evaluate_research_market_eligibility(
                    status=row["market_status"],
                    close_time=row["market_close_time"],
                    now=now,
                )
                if not ticker or not run_id or not fingerprint or not eligibility.eligible:
                    continue
                active_dossier_proofs.add((ticker, run_id, fingerprint))
    except sqlite3.Error:
        return set()
    return proofs & active_dossier_proofs


def _latest_dossier_proofs(repo_root: Path) -> set[tuple[str, str, str]]:
    db_path = botcheck._research_dossier_db_path(repo_root)  # noqa: SLF001
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not botcheck._sqlite_table_exists(  # noqa: SLF001
                conn,
                "research_dossiers",
            ):
                return set()
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(research_dossiers)"
                ).fetchall()
            }
            if not {
                "market_ticker",
                "last_research_run_id",
                "last_contract_fingerprint",
            } <= columns:
                return set()
            proofs = {
                (
                    str(row["market_ticker"] or "").strip(),
                    str(row["last_research_run_id"] or "").strip(),
                    str(row["last_contract_fingerprint"] or "").strip(),
                )
                for row in conn.execute(
                    """
                    SELECT market_ticker, last_research_run_id,
                           last_contract_fingerprint
                    FROM research_dossiers
                    """
                )
            }
    except sqlite3.Error:
        return set()
    return {proof for proof in proofs if all(proof)}


def _live_cache_eligible_proofs(
    repo_root: Path,
    *,
    now: datetime,
) -> set[tuple[str, str, str]]:
    db_path = botcheck._research_dossier_db_path(repo_root)  # noqa: SLF001
    if not db_path.exists():
        return set()

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not all(
                botcheck._sqlite_table_exists(conn, table_name)  # noqa: SLF001
                for table_name in (
                    "research_dossiers",
                    "research_runs",
                    "research_evidence",
                )
            ):
                return set()
            evidence_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_evidence)").fetchall()
            }
            dossier_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_dossiers)").fetchall()
            }
            run_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
            }
            required_evidence_columns = {
                "market_ticker",
                "research_run_id",
                "contract_fingerprint",
                "source_class",
                "source_name",
                "source_url",
                "claim_type",
                "supports_direction",
                "supports_confidence",
                "retrieved_at",
                "inserted_at",
            }
            required_dossier_columns = {
                "market_ticker",
                "last_research_run_id",
                "last_contract_fingerprint",
                "last_verdict_status",
                "last_decision_grade_status",
                "last_force_side",
                "last_estimated_probability",
                "last_confidence",
                "last_market_price",
                "last_estimated_edge",
                "market_status",
                "market_close_time",
            }
            if not required_evidence_columns <= evidence_columns:
                return set()
            if not required_dossier_columns <= dossier_columns:
                return set()
            if not {
                "market_ticker",
                "research_run_id",
                "verdict_status",
                "decision_grade_status",
            } <= run_columns:
                return set()
            live_cache_since = now - timedelta(
                seconds=botcheck.RESEARCH_DOSSIER_MAX_AGE_SECONDS,
            )
            evidence_by_proof: dict[
                tuple[str, str, str],
                list[dict[str, object]],
            ] = {}
            cache_evidence_ts_sql = """
                CASE
                    WHEN inserted_at IS NOT NULL
                         AND (retrieved_at IS NULL OR inserted_at > retrieved_at)
                    THEN inserted_at
                    ELSE retrieved_at
                END
            """
            metric_name_select = (
                "metric_name" if "metric_name" in evidence_columns else "NULL"
            )
            metric_value_select = (
                "metric_value" if "metric_value" in evidence_columns else "NULL"
            )
            extraction_confidence_select = (
                "extraction_confidence"
                if "extraction_confidence" in evidence_columns
                else "NULL"
            )
            published_at_select = (
                "published_at" if "published_at" in evidence_columns else "NULL"
            )
            retrieved_at_select = (
                "retrieved_at" if "retrieved_at" in evidence_columns else "NULL"
            )
            raw_payload_select = (
                "raw_payload_json"
                if "raw_payload_json" in evidence_columns
                else "NULL"
            )
            for row in conn.execute(
                f"""
                SELECT
                    market_ticker,
                    research_run_id,
                    contract_fingerprint,
                    source_class,
                    source_name,
                    source_url,
                    claim_type,
                    supports_direction,
                    supports_confidence,
                    {metric_name_select} AS metric_name,
                    {metric_value_select} AS metric_value,
                    {extraction_confidence_select} AS extraction_confidence,
                    {published_at_select} AS published_at,
                    {retrieved_at_select} AS retrieved_at,
                    {raw_payload_select} AS raw_payload_json,
                    {cache_evidence_ts_sql} AS ts
                FROM research_evidence
                """
            ):
                try:
                    raw_payload = json.loads(row["raw_payload_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    raw_payload = {}
                if not research_evidence_temporally_valid(
                    {
                        "metric_name": row["metric_name"],
                        "published_at": row["published_at"],
                        "retrieved_at": row["retrieved_at"],
                        "available_at": (
                            raw_payload.get("available_at")
                            if isinstance(raw_payload, dict)
                            else None
                        ),
                    },
                    as_of=now,
                ):
                    continue
                evidence_ts = botcheck._parse_research_ts(row["ts"])  # noqa: SLF001
                if evidence_ts is None or evidence_ts < live_cache_since:
                    continue
                ticker = str(row["market_ticker"] or "").strip()
                run_id = str(row["research_run_id"] or "").strip()
                fingerprint = str(row["contract_fingerprint"] or "").strip()
                if ticker and run_id and fingerprint:
                    proof = (ticker, run_id, fingerprint)
                    evidence_by_proof.setdefault(proof, []).append(
                        {
                            "source_class": row["source_class"],
                            "source_name": row["source_name"],
                            "source_url": row["source_url"],
                            "claim_type": row["claim_type"],
                            "supports_direction": row["supports_direction"],
                            "supports_confidence": row["supports_confidence"],
                            "metric_name": row["metric_name"],
                            "metric_value": row["metric_value"],
                            "extraction_confidence": row["extraction_confidence"],
                        }
                    )
            vetted_proofs: set[tuple[str, str, str]] = set()
            side_by_proof: dict[tuple[str, str, str], str] = {}
            for row in conn.execute(
                """
                SELECT
                    d.market_ticker,
                    d.last_research_run_id,
                    d.last_contract_fingerprint,
                    d.last_force_side,
                    d.market_status,
                    d.market_close_time
                FROM research_dossiers AS d
                JOIN research_runs AS r
                  ON r.market_ticker = d.market_ticker
                 AND r.research_run_id = d.last_research_run_id
                WHERE d.last_verdict_status = 'decision_grade_candidate'
                  AND d.last_decision_grade_status = 'decision_grade_candidate'
                  AND r.verdict_status = 'decision_grade_candidate'
                  AND r.decision_grade_status = 'decision_grade_candidate'
                  AND d.last_force_side IN ('yes', 'no')
                  AND d.last_estimated_probability IS NOT NULL
                  AND d.last_confidence IS NOT NULL
                  AND d.last_market_price IS NOT NULL
                  AND d.last_estimated_edge IS NOT NULL
                """
            ):
                eligibility = evaluate_research_market_eligibility(
                    status=row["market_status"],
                    close_time=row["market_close_time"],
                    now=now,
                )
                if not eligibility.eligible:
                    continue
                proof = (
                    str(row["market_ticker"] or "").strip(),
                    str(row["last_research_run_id"] or "").strip(),
                    str(row["last_contract_fingerprint"] or "").strip(),
                )
                if proof not in evidence_by_proof:
                    continue
                vetted_proofs.add(proof)
                side_by_proof[proof] = str(row["last_force_side"] or "").strip()
    except sqlite3.Error:
        return set()

    return {
        proof
        for proof in vetted_proofs
        if proof[0]
        and proof[1]
        and proof[2]
        and has_reliable_research_source_path(evidence_by_proof.get(proof, []))
        and _proof_has_countercase(
            side_by_proof.get(proof, ""),
            evidence_by_proof.get(proof, []),
        )
    }


def _proof_has_countercase(
    side: str,
    evidence: list[dict[str, object]],
) -> bool:
    normalized_side = side.strip().lower()
    if normalized_side not in {"yes", "no"}:
        return False
    opposite = "no" if normalized_side == "yes" else "yes"
    structured_support_metrics = {
        str(item.get("metric_name") or "").strip()
        for item in evidence
        if str(item.get("supports_direction") or "").strip().lower()
        == normalized_side
        and has_reliable_research_source_path([item])
    }
    for item in evidence:
        claim_type = str(item.get("claim_type") or "").strip().lower()
        if claim_type not in {"disconfirming", "contradiction_check"}:
            continue
        direction = str(item.get("supports_direction") or "").strip().lower()
        if direction in {opposite, "neutral"}:
            return True
        source_class = str(item.get("source_class") or "").strip().lower()
        metric_name = str(item.get("metric_name") or "").strip()
        if (
            direction == normalized_side
            and source_class in OFFICIAL_RESEARCH_SOURCE_CLASSES
            and metric_name in STRUCTURED_OFFICIAL_RESEARCH_METRICS
            and metric_name in structured_support_metrics
            and _optional_float(item.get("supports_confidence")) >= 0.8
            and (
                item.get("metric_value") is not None
                or _optional_float(item.get("extraction_confidence")) >= 0.8
            )
        ):
            return True
    return False


def _optional_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


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
    decision_grade_research_proofs = _decision_grade_research_proofs(
        signal_stats.path,
        since=signal_stats.since,
    )
    active_decision_grade_proofs = _active_decision_grade_proofs(
        repo_root,
        decision_grade_research_proofs,
        now=now,
    )
    live_cache_eligible_proofs = _live_cache_eligible_proofs(repo_root, now=now)
    matched_research_proofs = active_decision_grade_proofs & live_cache_eligible_proofs
    latest_dossier_proofs = _latest_dossier_proofs(repo_root)
    proven_researched_tickers = {
        ticker for ticker, _run_id, _fingerprint in matched_research_proofs
    }
    unresolved_prewarm_backlog = [
        ticker for ticker in prewarm_backlog if ticker not in proven_researched_tickers
    ]
    dossier_stats = botcheck.summarize_research_dossiers(repo_root, now=now)
    latest_version = _latest_bot_version(bot_log)
    successful_research_rows = _successful_research_row_count(
        signal_stats.path,
        since=signal_stats.since,
    )
    operational_research_errors = _operational_research_error_count(
        signal_stats.path,
        since=signal_stats.since,
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
    elif (
        successful_research_rows <= 0
        and (
            operational_research_errors > 0
            or not live_cache_eligible_proofs
        )
    ):
        failures.append(
            "no successful recent research rows "
            "(accepted: decision_grade_candidate or terminal untradeable/no-trade)"
        )
    elif active_decision_grade_proofs and not matched_research_proofs:
        failures.append(
            "no successful recent research rows with matching live-cache dossier evidence"
        )
    decision_grade_tickers = {
        ticker for ticker, _run_id, _fingerprint in decision_grade_research_proofs
    }
    active_decision_grade_tickers = {
        ticker for ticker, _run_id, _fingerprint in active_decision_grade_proofs
    }
    terminal_success_proofs = (
        successful_research_proofs - decision_grade_research_proofs
    ) & latest_dossier_proofs
    terminal_success_tickers = {
        ticker
        for ticker, _run_id, _fingerprint in terminal_success_proofs
    }
    unexcused_ineligible_tickers = (
        decision_grade_tickers
        - active_decision_grade_tickers
        - terminal_success_tickers
    )
    if unexcused_ineligible_tickers:
        failures.append(
            "no active market-eligible decision-grade proof matches the latest dossier run"
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
        if active_decision_grade_proofs and not live_cache_eligible_proofs:
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
        live_cache_eligible_dossiers=len(live_cache_eligible_proofs),
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
