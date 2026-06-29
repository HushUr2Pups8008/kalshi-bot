#!/usr/bin/env python3
"""Read-only multi-agent QA workflow for the research feature.

The "agents" here are deterministic verification lanes.  They deliberately
share no mutable state and only reconcile existing logs/config/databases.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import botcheck
from scripts.research_activation_status import evaluate_activation_profile
from scripts.research_rollout_gate import evaluate_research_rollout


ACTIVE_RESEARCH_MODES = {"shadow", "production"}


@dataclass(frozen=True)
class ResearchAgentResult:
    name: str
    ok: bool
    priority: str
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "priority": self.priority,
            "metrics": self.metrics,
            "findings": self.findings,
        }


@dataclass(frozen=True)
class ResearchWorkflowAssessment:
    ok: bool
    agents: list[ResearchAgentResult]
    failures: list[str] = field(default_factory=list)

    def agent(self, name: str) -> ResearchAgentResult:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": self.failures,
            "agents": [agent.to_dict() for agent in self.agents],
        }


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(__import__("time").time(), tz=timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trade_records_in_window(
    trades_log: Path,
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    lines_total = [0]
    lines_malformed = [0]
    records: list[dict[str, Any]] = []
    for record in botcheck._iter_trade_records(  # noqa: SLF001 - read-only helper.
        trades_log,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ts is not None and ts < since:
            continue
        records.append(record)
    return records


def _latest_active_boot_since(bot_log: Path | None, *, now: datetime) -> datetime | None:
    if bot_log is None:
        return None
    sessions = [
        session.boot_ts
        for session in botcheck.read_sessions(bot_log)
        if session.shutdown_ts is None and session.boot_ts <= now
    ]
    if not sessions:
        return None
    return max(sessions)


def _activation_agent(
    repo_root: Path,
    profile_path: Path,
    env_path: Path | None,
) -> ResearchAgentResult:
    assessment = evaluate_activation_profile(repo_root, profile_path, env_path=env_path)
    findings: list[str] = []
    findings.extend(f"missing {key}" for key in assessment.missing)
    findings.extend(
        f"mismatch {key}: expected={expected} actual={actual}"
        for key, expected, actual in assessment.mismatched
    )
    findings.extend(assessment.unsafe)
    mode = assessment.env_values.get("REAL_WEB_RESEARCH_MODE", "off").strip().lower()
    if mode not in ACTIVE_RESEARCH_MODES:
        findings.append(f"research mode inactive: {mode}")
    return ResearchAgentResult(
        name="activation",
        ok=assessment.ok and mode in ACTIVE_RESEARCH_MODES,
        priority="Protect Capital",
        metrics={
            "mode": mode,
            "profile": str(assessment.profile_path),
            "env": str(assessment.env_path),
        },
        findings=findings,
    )


def _signal_flow_agent(stats: botcheck.SignalFlowStats, *, now: datetime) -> ResearchAgentResult:
    findings: list[str] = []
    latest_age_seconds = None
    if stats.latest_research_ts is None:
        findings.append("no research rows in workflow window")
    else:
        latest_age_seconds = max(0.0, (now - stats.latest_research_ts).total_seconds())
    if stats.research_records <= 0:
        findings.append("research feature produced zero recent records")
    return ResearchAgentResult(
        name="signal_flow",
        ok=not findings,
        priority="High ROI",
        metrics={
            "records_kept": stats.records_kept,
            "research_records": stats.research_records,
            "latest_research_age_seconds": latest_age_seconds,
            "research_status_counts": dict(stats.research_status_counts),
        },
        findings=findings,
    )


def _prewarm_quality_agent(
    records: list[dict[str, Any]],
    *,
    max_duplicate_ratio: float,
    target_cooldown_seconds: float,
    prewarm_window_since: datetime,
) -> ResearchAgentResult:
    prewarm = [
        record
        for record in records
        if str(record.get("type") or "") == "RESEARCH_PREWARM_RESULT"
    ]
    findings: list[str] = []
    tickers = [
        str(record.get("ticker") or record.get("market_ticker") or "").strip()
        for record in prewarm
    ]
    tickers = [ticker for ticker in tickers if ticker]
    synthetic = [
        ticker
        for ticker in tickers
        if ticker.startswith("KXSTARTUP") or "PROBE" in ticker or ticker.startswith("TEST")
    ]
    non_kalshi = [ticker for ticker in tickers if not ticker.startswith("KX")]
    duplicate_ratio = 0.0
    timestamps_by_ticker: dict[str, list[datetime]] = {}
    for record in prewarm:
        ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
        ts = botcheck._parse_trade_ts(record.get("ts"))  # noqa: SLF001
        if ticker and ts is not None:
            timestamps_by_ticker.setdefault(ticker, []).append(ts)
    within_cooldown_repeats: list[str] = []
    for ticker, timestamps in timestamps_by_ticker.items():
        timestamps.sort()
        for previous, current in zip(timestamps, timestamps[1:], strict=False):
            gap_seconds = (current - previous).total_seconds()
            if gap_seconds < target_cooldown_seconds:
                within_cooldown_repeats.append(
                    f"{ticker} repeated after {gap_seconds:.0f}s"
                )
                break
    if tickers:
        unique_count = len(set(tickers))
        duplicate_ratio = 1.0 - (unique_count / len(tickers))
        if within_cooldown_repeats:
            findings.append(
                "duplicate prewarm spend inside cooldown: "
                + "; ".join(within_cooldown_repeats[:5])
            )
    else:
        findings.append("no RESEARCH_PREWARM_RESULT rows in workflow window")
    if synthetic:
        findings.append(f"synthetic/probe prewarm tickers observed: {len(synthetic)}")
    if non_kalshi:
        findings.append(f"non-Kalshi prewarm tickers observed: {len(non_kalshi)}")
    status_counts = Counter(
        str(record.get("research_status") or "unknown") for record in prewarm
    )
    return ResearchAgentResult(
        name="prewarm_quality",
        ok=not findings,
        priority="High ROI",
        metrics={
            "prewarm_rows": len(prewarm),
            "unique_tickers": len(set(tickers)),
            "duplicate_ratio": round(duplicate_ratio, 4),
            "max_duplicate_ratio": max_duplicate_ratio,
            "target_cooldown_seconds": target_cooldown_seconds,
            "within_cooldown_repeats": len(within_cooldown_repeats),
            "status_counts": dict(status_counts),
            "prewarm_window_since": prewarm_window_since.isoformat(),
        },
        findings=findings,
    )


def _dossier_evidence_agent(
    repo_root: Path,
    *,
    now: datetime,
) -> ResearchAgentResult:
    stats = botcheck.summarize_research_dossiers(repo_root, now=now)
    findings: list[str] = []
    if not stats.exists:
        findings.append("research dossier database missing")
    elif stats.error:
        findings.append(f"research dossier database unreadable: {stats.error}")
    else:
        if stats.dossiers <= 0:
            findings.append("research dossier database has zero dossiers")
        if stats.fresh_evidence_rows_24h <= 0:
            findings.append("research dossier database has zero fresh evidence rows")
    return ResearchAgentResult(
        name="dossier_evidence",
        ok=not findings,
        priority="High ROI",
        metrics={
            "db_path": str(stats.db_path),
            "dossiers": stats.dossiers,
            "evidence_rows": stats.evidence_rows,
            "fresh_evidence_rows_24h": stats.fresh_evidence_rows_24h,
            "live_cache_eligible_dossiers": stats.live_cache_eligible_dossiers,
            "verdict_counts": dict(stats.verdict_counts),
        },
        findings=findings,
    )


def _capital_safety_agent(
    stats: botcheck.SignalFlowStats,
    *,
    allow_paper_trades: bool,
) -> ResearchAgentResult:
    findings: list[str] = []
    live_orders = int(stats.counts.get("LIVE_ORDER", 0))
    paper_trades = int(stats.counts.get("PAPER_TRADE", 0))
    if live_orders:
        findings.append(f"LIVE_ORDER rows observed in research workflow window: {live_orders}")
    if paper_trades and not allow_paper_trades:
        findings.append(
            f"PAPER_TRADE rows observed; rerun with --allow-paper-trades if expected: {paper_trades}"
        )
    return ResearchAgentResult(
        name="capital_safety",
        ok=not findings,
        priority="Protect Capital",
        metrics={
            "live_orders": live_orders,
            "paper_trades": paper_trades,
            "allow_paper_trades": allow_paper_trades,
        },
        findings=findings,
    )


def _rollout_readiness_agent(
    repo_root: Path,
    trades_log: Path,
    *,
    now: datetime,
    window_hours: float,
    bot_log: Path | None,
    expected_version: str | None,
    require_live_cache: bool,
) -> ResearchAgentResult:
    assessment = evaluate_research_rollout(
        repo_root,
        trades_log,
        now=now,
        window_hours=window_hours,
        bot_log=bot_log,
        expected_version=expected_version,
        allow_prewarm_off=False,
    )
    findings = list(assessment.failures) if require_live_cache else []
    return ResearchAgentResult(
        name="rollout_readiness",
        ok=assessment.ok if require_live_cache else True,
        priority="Protect Capital" if require_live_cache else "Nice-to-have",
        metrics={
            "research_rows": assessment.research_rows,
            "successful_research_rows": assessment.successful_research_rows,
            "matched_research_proofs": assessment.matched_research_proofs,
            "prewarm_backlog": len(assessment.prewarm_backlog),
            "unresolved_prewarm_backlog": len(assessment.unresolved_prewarm_backlog),
            "live_cache_eligible_dossiers": assessment.live_cache_eligible_dossiers,
        },
        findings=findings,
    )


def evaluate_research_multi_agent_workflow(
    repo_root: Path,
    trades_log: Path,
    *,
    profile_path: Path | None = None,
    env_path: Path | None = None,
    now: datetime | None = None,
    window_hours: float = 24.0,
    bot_log: Path | None = None,
    expected_version: str | None = None,
    require_live_cache: bool = False,
    allow_paper_trades: bool = False,
    max_prewarm_duplicate_ratio: float = 0.25,
) -> ResearchWorkflowAssessment:
    repo_root = repo_root.resolve()
    trades_log = trades_log.resolve()
    now = now or datetime.fromtimestamp(__import__("time").time(), tz=timezone.utc)
    profile_path = profile_path or repo_root / "docs/governance/research-shadow.env.example"
    env_path = env_path or repo_root / ".env"
    signal_stats = botcheck.summarize_signal_flow(
        trades_log,
        now=now,
        window_hours=window_hours,
    )
    active_boot_since = _latest_active_boot_since(bot_log, now=now)
    prewarm_window_since = max(
        value for value in (signal_stats.since, active_boot_since) if value is not None
    )
    records = _trade_records_in_window(trades_log, since=prewarm_window_since)
    cooldown_raw, _cooldown_source = botcheck._research_env_value(  # noqa: SLF001
        repo_root,
        "RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS",
        "1800",
    )
    try:
        target_cooldown_seconds = float(cooldown_raw)
    except ValueError:
        target_cooldown_seconds = 1800.0
    agents = [
        _activation_agent(repo_root, profile_path, env_path),
        _signal_flow_agent(signal_stats, now=now),
        _prewarm_quality_agent(
            records,
            max_duplicate_ratio=max_prewarm_duplicate_ratio,
            target_cooldown_seconds=target_cooldown_seconds,
            prewarm_window_since=prewarm_window_since,
        ),
        _dossier_evidence_agent(repo_root, now=now),
        _capital_safety_agent(signal_stats, allow_paper_trades=allow_paper_trades),
        _rollout_readiness_agent(
            repo_root,
            trades_log,
            now=now,
            window_hours=window_hours,
            bot_log=bot_log,
            expected_version=expected_version,
            require_live_cache=require_live_cache,
        ),
    ]
    failures = [
        f"{agent.name}: {finding}"
        for agent in agents
        if not agent.ok
        for finding in agent.findings
    ]
    return ResearchWorkflowAssessment(
        ok=not failures,
        agents=agents,
        failures=failures,
    )


def _default_repo_root() -> Path:
    return Path(os.environ.get("KALSHI_HOME", Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    default_home = _default_repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument(
        "--trades-log",
        type=Path,
        default=default_home / "logs" / "trades",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path("docs/governance/research-shadow.env.example"),
    )
    parser.add_argument("--env", type=Path, default=None)
    parser.add_argument("--bot-log", type=Path, default=default_home / "logs/app/bot.log")
    parser.add_argument("--expected-version")
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--now", help="ISO-8601 timestamp for deterministic checks")
    parser.add_argument("--require-live-cache", action="store_true")
    parser.add_argument("--allow-paper-trades", action="store_true")
    parser.add_argument("--max-prewarm-duplicate-ratio", type=float, default=0.25)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    assessment = evaluate_research_multi_agent_workflow(
        args.home,
        args.trades_log,
        profile_path=args.profile,
        env_path=args.env,
        now=_parse_now(args.now),
        window_hours=args.window_hours,
        bot_log=args.bot_log,
        expected_version=args.expected_version,
        require_live_cache=args.require_live_cache,
        allow_paper_trades=args.allow_paper_trades,
        max_prewarm_duplicate_ratio=args.max_prewarm_duplicate_ratio,
    )
    if args.json_output:
        print(json.dumps(assessment.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Research multi-agent workflow: {'PASS' if assessment.ok else 'FAIL'}")
        for agent in assessment.agents:
            print(f"{agent.name}: {'PASS' if agent.ok else 'FAIL'}")
            for finding in agent.findings:
                print(f"- {finding}")
    return 0 if assessment.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
