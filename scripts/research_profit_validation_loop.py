#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from math import inf
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.profit_evidence_report import collect_replay_evidence
from utils.output_paths import EVIDENCE_STORE_DB, PAPER_TRADES_DB, RAW_EDGE_REPLAY_DIR
from utils.research_evidence_quality import (
    MIN_COUNTER_EVIDENCE_CONFIDENCE,
    MIN_DIRECTIONAL_SUPPORT_CONFIDENCE,
    OFFICIAL_RESEARCH_SOURCE_CLASSES as OFFICIAL_SOURCE_CLASSES,
    STRUCTURED_OFFICIAL_RESEARCH_METRICS as STRUCTURED_SIGNAL_METRICS,
    STRUCTURED_OFFICIAL_SETTLEMENT_CLAIM_TYPES as SETTLEMENT_CLAIM_TYPES,
    build_contract_relevance_spec,
    effective_research_source_class,
    evidence_is_relevant_to_contract,
    has_reliable_research_source_path,
)
from utils.research_market_eligibility import evaluate_research_market_eligibility
from utils.trade_log_reader import iter_trade_records


Verdict = Literal[
    "STRONGLY_SUCCESSFUL",
    "PROVISIONALLY_SUCCESSFUL",
    "NOT_SUCCESSFUL",
]
Action = Literal[
    "CONTINUE_SHADOW",
    "PROMOTE_TO_PAPER_REVIEW",
    "KEEP_PAPER_RUNNING",
    "ROLL_BACK_OR_PATCH",
    "CONSIDER_LIVE_ONLY_AFTER_OPERATOR_APPROVAL",
]

RESEARCH_TRADE_READY_STATUSES = {"decision_grade_candidate"}
DECISION_GRADE_NO_TRADE_SKIP_REASONS = {"no_edge"}
DECISION_GRADE_TASK_TERMINAL_REASONS = {
    "contradictory_evidence_unresolved",
    "insufficient_directional_evidence",
    "market_closed",
    "no_edge",
    "no_reliable_source_path",
    "non_actionable_market_price",
}


@dataclass(frozen=True)
class RuntimeEvidence:
    window_hours: float
    live_order_count: int
    paper_trade_count: int
    paper_resolution_count: int
    within_cooldown_repeats: int
    error_critical_count: int
    error_critical_samples: list[str]
    trade_log_event_counts: dict[str, int]
    research_status_counts: dict[str, int]
    latest_trade_log_ts: str | None
    botcheck_summary: list[str]


@dataclass(frozen=True)
class FunnelEvidence:
    trade_candidates: int
    live_cache_eligible: int
    opportunities: int
    research_backed_opportunities: int
    research_backed_trades: int
    candidate_to_opportunity_rate: float | None
    opportunity_to_trade_rate: float | None
    seven_day_candidate_to_opportunity_rate: float | None
    seven_day_opportunity_to_trade_rate: float | None
    thirty_day_candidate_to_opportunity_rate: float | None
    thirty_day_opportunity_to_trade_rate: float | None
    candidate_to_opportunity_delta_vs_7d: float | None
    candidate_to_opportunity_delta_vs_30d: float | None
    opportunity_to_trade_delta_vs_7d: float | None
    opportunity_to_trade_delta_vs_30d: float | None
    lost_positive_edge_candidates: int
    top_skip_reasons: dict[str, int]


@dataclass(frozen=True)
class ProfitEvidence:
    total_trades: int
    resolved_trades: int
    wins: int
    losses: int
    net_pnl: float
    win_rate: float | None
    profit_factor: float | None
    avg_edge_captured: float | None
    max_drawdown: float | None
    unrealized_pnl: float | None
    unrealized_pnl_unavailable_reason: str | None
    deployed_capital: float
    roi_on_deployed_capital: float | None
    low_edge_overbet_rate: float | None
    seven_day_net_pnl: float
    thirty_day_net_pnl: float


@dataclass(frozen=True)
class ResearchQualityEvidence:
    evidence_rows: int
    official_evidence_rows: int
    source_class_counts: dict[str, int]
    stale_or_weak_candidate_tickers: list[str]


@dataclass(frozen=True)
class DecisionGradeEvidence:
    decision_grade_candidates: int
    blocked_by_missing_price: int
    blocked_by_no_reliable_source_path: int
    blocked_by_official_data_pending: int
    blocked_by_provider_error: int
    blocked_by_neutral_evidence: int
    blocked_by_ambiguous_direction: int
    blocked_by_no_counter_evidence: int
    blocked_by_generic_summary: int
    blocked_by_unresolved_contradiction: int
    stale_but_researchable: int
    terminal_untradeable: int
    terminal_timeout_exhausted: int
    blocked_by_market_ineligible: int = 0


@dataclass(frozen=True)
class ReplayEvidence:
    scored_items: int
    positive_items: int
    best_trade_count: int | None
    best_realized_pnl: float | None
    best_per_trade_ev: float | None
    best_ev_ci_95_lo: float | None


@dataclass(frozen=True)
class ChangeEvidence:
    head: str | None
    branch: str | None
    origin_main: str | None
    gitlab_main: str | None
    remotes_synced: bool | None
    recent_commits: list[str]
    dirty_paths: list[str]


@dataclass(frozen=True)
class RiskEvidence:
    unauthorized_live_orders: int
    paper_trades_observed: int
    drawdown_breach: bool
    capital_at_risk: float


@dataclass(frozen=True)
class WorkflowGateEvidence:
    normal_gate_ok: bool | None
    strict_live_cache_gate_ok: bool | None
    bothealth_verdict: str | None
    bothealth_capital_safe: bool | None
    bothealth_summary: str | None
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchProfitValidationReport:
    ok: bool
    verdict: Verdict
    action: Action
    research_operating_cleanly: bool
    research_supports_trades: bool
    reasons: list[str]
    runtime: RuntimeEvidence
    funnel: FunnelEvidence
    profit: ProfitEvidence
    research_quality: ResearchQualityEvidence
    decision_grade: DecisionGradeEvidence
    replay: ReplayEvidence
    changes: ChangeEvidence
    risk: RiskEvidence
    workflow: WorkflowGateEvidence

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["profit"]["profit_factor"] = _json_number(data["profit"]["profit_factor"])
        return data


@dataclass(frozen=True)
class CandidateProof:
    ticker: str
    research_run_id: str
    created_ts: datetime | None
    evidence: tuple[dict[str, Any], ...]

    @property
    def live_cache_eligible(self) -> bool:
        return has_reliable_research_source_path(self.evidence)


@dataclass(frozen=True)
class CandidateEvidenceQuality:
    evidence: tuple[dict[str, Any], ...]
    supports_directions: frozenset[str]
    has_counter_query: bool
    has_counter_evidence: bool
    force_side: str

    @property
    def has_reliable_source_path(self) -> bool:
        return has_reliable_research_source_path(self.evidence)

    @property
    def has_directional_evidence(self) -> bool:
        return self.force_side in self.supports_directions

    @property
    def live_cache_eligible(self) -> bool:
        return (
            self.has_reliable_source_path
            and self.has_directional_evidence
            and self.has_counter_query
            and self.has_counter_evidence
        )


@dataclass(frozen=True)
class PaperRow:
    trade_id: str
    ts: datetime | None
    ticker: str
    cost_dollars: float
    edge: float | None
    resolved: bool
    pnl_dollars: float | None
    notional_bankroll_before: float | None
    notional_bankroll_after: float | None


def evaluate_research_profit_validation(
    repo_root: Path,
    *,
    trades_log: Path,
    paper_db: Path = PAPER_TRADES_DB,
    evidence_db: Path = EVIDENCE_STORE_DB,
    replay_root: Path = RAW_EDGE_REPLAY_DIR,
    app_log: Path | None = None,
    now: datetime | None = None,
    window_hours: float = 24.0,
    baseline_days: int = 7,
    baseline_days_2: int = 30,
    min_edge: float = 0.02,
    max_drawdown: float = 0.20,
    allow_live_orders: bool = False,
    run_workflow_gates: bool = False,
    run_bothealth: bool = False,
    bothealth_output: str | None = None,
    expected_version: str | None = None,
    run_botcheck: bool = False,
    botcheck_output: str | None = None,
) -> ResearchProfitValidationReport:
    repo_root = repo_root.resolve()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    app_log = app_log or repo_root / "logs" / "app" / "bot.log"
    since = now - timedelta(hours=window_hours)
    seven_day_since = now - timedelta(days=baseline_days)
    thirty_day_since = now - timedelta(days=baseline_days_2)

    candidate_proofs = _load_candidate_proofs(
        evidence_db,
        fresh_since=now - timedelta(hours=window_hours),
        now=now,
    )
    candidate_tickers = set(candidate_proofs)
    thirty_day_records = list(
        iter_trade_records(trades_log, since=thirty_day_since, until=now)
    )
    seven_day_records = [
        record
        for record in thirty_day_records
        if (ts := _parse_ts(record.get("ts"))) is not None and ts >= seven_day_since
    ]
    trade_records = [
        record
        for record in thirty_day_records
        if (ts := _parse_ts(record.get("ts"))) is not None and ts >= since
    ]
    runtime = _runtime_evidence(
        trade_records,
        app_log=app_log,
        since=since,
        window_hours=window_hours,
        botcheck_output=botcheck_output
        if botcheck_output is not None
        else (_run_botcheck(repo_root) if run_botcheck else None),
    )
    funnel = _funnel_evidence(
        trade_records,
        candidate_proofs,
        min_edge=min_edge,
        seven_day_records=seven_day_records,
        thirty_day_records=thirty_day_records,
    )
    paper_rows = _load_paper_rows(paper_db)
    profit = _profit_evidence(
        paper_rows,
        candidate_tickers=candidate_tickers,
        since=since,
        seven_day_since=seven_day_since,
        thirty_day_since=thirty_day_since,
        until=now,
        min_edge=min_edge,
    )
    research_quality = _research_quality_evidence(
        evidence_db,
        candidate_proofs,
        fresh_since=since,
    )
    decision_grade = _decision_grade_evidence(
        evidence_db,
        fresh_since=since,
        now=now,
    )
    replay = _replay_evidence(replay_root)
    changes = _change_evidence(repo_root)
    workflow = _workflow_gate_evidence(
        repo_root,
        trades_log=trades_log,
        app_log=app_log,
        now=now,
        window_hours=window_hours,
        run_workflow_gates=run_workflow_gates,
        run_bothealth=run_bothealth,
        bothealth_output=bothealth_output,
        expected_version=expected_version or _latest_boot_version(app_log),
    )
    risk = RiskEvidence(
        unauthorized_live_orders=(
            runtime.live_order_count if not allow_live_orders else 0
        ),
        paper_trades_observed=runtime.paper_trade_count,
        drawdown_breach=(
            profit.max_drawdown is not None and profit.max_drawdown > max_drawdown
        ),
        capital_at_risk=sum(
            row.cost_dollars
            for row in _filter_rows(
                paper_rows,
                candidate_tickers=candidate_tickers,
                since=since,
                until=now,
            )
            if not row.resolved
        ),
    )
    verdict, action, reasons = _decide(
        runtime,
        funnel,
        profit,
        research_quality,
        decision_grade,
        replay,
        risk,
        workflow,
    )
    research_operating_cleanly = verdict != "NOT_SUCCESSFUL"
    research_supports_trades = (
        action in {"PROMOTE_TO_PAPER_REVIEW", "KEEP_PAPER_RUNNING"}
        or funnel.research_backed_trades > 0
    )
    return ResearchProfitValidationReport(
        ok=research_operating_cleanly,
        verdict=verdict,
        action=action,
        research_operating_cleanly=research_operating_cleanly,
        research_supports_trades=research_supports_trades,
        reasons=reasons,
        runtime=runtime,
        funnel=funnel,
        profit=profit,
        research_quality=research_quality,
        decision_grade=decision_grade,
        replay=replay,
        changes=changes,
        risk=risk,
        workflow=workflow,
    )


def render_markdown(report: ResearchProfitValidationReport) -> str:
    lines = [
        f"**Research Profit Verdict:** {report.verdict} - {report.action}",
        "",
        "**Why**",
    ]
    lines.extend(f"- {reason}" for reason in report.reasons)
    lines.extend(
        [
            "",
            "**Research Mode**",
            f"- operating cleanly: {report.research_operating_cleanly}",
            f"- supports trades: {report.research_supports_trades}",
            "",
            "**Metrics**",
            f"- decision_grade_candidate: {report.funnel.trade_candidates}",
            f"- live_cache_eligible: {report.funnel.live_cache_eligible}",
            f"- decision-grade candidates: {report.decision_grade.decision_grade_candidates}",
            f"- blocked by missing price: {report.decision_grade.blocked_by_missing_price}",
            "- blocked by market ineligible: "
            f"{report.decision_grade.blocked_by_market_ineligible}",
            "- blocked by no reliable source path: "
            f"{report.decision_grade.blocked_by_no_reliable_source_path}",
            "- blocked by official data pending: "
            f"{report.decision_grade.blocked_by_official_data_pending}",
            f"- blocked by provider error: {report.decision_grade.blocked_by_provider_error}",
            f"- blocked by neutral evidence: {report.decision_grade.blocked_by_neutral_evidence}",
            "- blocked by ambiguous direction: "
            f"{report.decision_grade.blocked_by_ambiguous_direction}",
            "- blocked by no counter-evidence: "
            f"{report.decision_grade.blocked_by_no_counter_evidence}",
            f"- blocked by generic summary: {report.decision_grade.blocked_by_generic_summary}",
            "- blocked by unresolved contradiction: "
            f"{report.decision_grade.blocked_by_unresolved_contradiction}",
            f"- stale but researchable: {report.decision_grade.stale_but_researchable}",
            f"- terminal untradeable: {report.decision_grade.terminal_untradeable}",
            "- terminal timeout exhausted: "
            f"{report.decision_grade.terminal_timeout_exhausted}",
            f"- research-backed opportunities: {report.funnel.research_backed_opportunities}",
            f"- research-backed trades: {report.funnel.research_backed_trades}",
            "- candidate -> opportunity delta vs 7d: "
            f"{_fmt_pct(report.funnel.candidate_to_opportunity_delta_vs_7d)}",
            "- candidate -> opportunity delta vs 30d: "
            f"{_fmt_pct(report.funnel.candidate_to_opportunity_delta_vs_30d)}",
            f"- net P&L: ${report.profit.net_pnl:.2f}",
            f"- profit factor: {_fmt_optional(report.profit.profit_factor)}",
            f"- ROI on deployed capital: {_fmt_pct(report.profit.roi_on_deployed_capital)}",
            f"- LIVE_ORDER: {report.runtime.live_order_count}",
            f"- PAPER_TRADE: {report.runtime.paper_trade_count}",
            f"- within_cooldown_repeats: {report.runtime.within_cooldown_repeats}",
            f"- bothealth: {report.workflow.bothealth_verdict or 'not run'}",
            f"- ERROR/CRITICAL: {report.runtime.error_critical_count}",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate whether research is converting into profitable trades."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--trades-log",
        type=Path,
        default=Path("logs/trades/live/trades.jsonl"),
    )
    parser.add_argument("--paper-db", type=Path, default=PAPER_TRADES_DB)
    parser.add_argument("--evidence-db", type=Path, default=EVIDENCE_STORE_DB)
    parser.add_argument("--replay-root", type=Path, default=RAW_EDGE_REPLAY_DIR)
    parser.add_argument("--app-log", type=Path, default=None)
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--baseline-days", type=int, default=7)
    parser.add_argument("--baseline-days-2", type=int, default=30)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--max-drawdown", type=float, default=0.20)
    parser.add_argument("--allow-live-orders", action="store_true")
    parser.add_argument("--skip-workflow-gates", action="store_true")
    parser.add_argument("--skip-bothealth", action="store_true")
    parser.add_argument("--skip-botcheck", action="store_true")
    parser.add_argument("--expected-version", type=str, default=None)
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    report = evaluate_research_profit_validation(
        repo_root,
        trades_log=_resolve(repo_root, args.trades_log),
        paper_db=_resolve(repo_root, args.paper_db),
        evidence_db=_resolve(repo_root, args.evidence_db),
        replay_root=_resolve(repo_root, args.replay_root),
        app_log=_resolve(repo_root, args.app_log) if args.app_log else None,
        now=_parse_ts(args.now) if args.now else None,
        window_hours=args.window_hours,
        baseline_days=args.baseline_days,
        baseline_days_2=args.baseline_days_2,
        min_edge=args.min_edge,
        max_drawdown=args.max_drawdown,
        allow_live_orders=args.allow_live_orders,
        run_workflow_gates=not args.skip_workflow_gates,
        run_bothealth=not args.skip_bothealth,
        expected_version=args.expected_version,
        run_botcheck=not args.skip_botcheck,
    )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report.ok else 1


def _runtime_evidence(
    records: list[dict[str, Any]],
    *,
    app_log: Path,
    since: datetime,
    window_hours: float,
    botcheck_output: str | None,
) -> RuntimeEvidence:
    counts = Counter(str(record.get("type") or "UNKNOWN") for record in records)
    research_status_counts = Counter(
        str(record.get("research_status") or "unknown")
        for record in records
        if record.get("research_attempted") is True
        or str(record.get("type") or "") == "RESEARCH_PREWARM_RESULT"
        or any(str(key).startswith("research_") for key in record)
    )
    latest = max((_parse_ts(record.get("ts")) for record in records), default=None)
    return RuntimeEvidence(
        window_hours=window_hours,
        live_order_count=counts.get("LIVE_ORDER", 0),
        paper_trade_count=counts.get("PAPER_TRADE", 0),
        paper_resolution_count=counts.get("PAPER_RESOLUTION", 0),
        within_cooldown_repeats=sum(
            1
            for record in records
            if record.get("within_cooldown") is True
            or record.get("within_cooldown_repeat") is True
        ),
        error_critical_count=len(error_samples := _log_error_samples(app_log, since=since)),
        error_critical_samples=error_samples[-5:],
        trade_log_event_counts=dict(sorted(counts.items())),
        research_status_counts=dict(sorted(research_status_counts.items())),
        latest_trade_log_ts=_iso(latest),
        botcheck_summary=_parse_botcheck_summary(botcheck_output),
    )


def _funnel_evidence(
    records: list[dict[str, Any]],
    candidate_proofs: dict[str, CandidateProof],
    *,
    min_edge: float,
    seven_day_records: list[dict[str, Any]],
    thirty_day_records: list[dict[str, Any]],
) -> FunnelEvidence:
    candidate_tickers = set(candidate_proofs)
    current_rates = _conversion_rates(
        records,
        candidate_tickers,
        fallback_candidate_count=len(candidate_tickers),
    )
    seven_day_rates = _conversion_rates(seven_day_records, candidate_tickers)
    thirty_day_rates = _conversion_rates(thirty_day_records, candidate_tickers)
    trade_candidate_events = [
        record
        for record in records
        if str(record.get("research_status") or "") in RESEARCH_TRADE_READY_STATUSES
    ]
    opportunities = [record for record in records if record.get("type") == "OPPORTUNITY"]
    trades = [
        record
        for record in records
        if record.get("type") in {"PAPER_TRADE", "LIVE_ORDER"}
    ]
    backed_opportunities = [
        record for record in opportunities if _record_ticker(record) in candidate_tickers
    ]
    backed_trades = [
        record for record in trades if _record_ticker(record) in candidate_tickers
    ]
    trade_tickers = {_record_ticker(record) for record in backed_trades}
    lost_positive = sum(
        1
        for record in backed_opportunities
        if (_as_float(record.get("edge")) or 0.0) > min_edge
        and _record_ticker(record) not in trade_tickers
    )
    skip_reasons = Counter(
        str(record.get("reason") or "unknown")
        for record in records
        if record.get("type") == "SKIPPED"
    )
    candidate_count = max(len(candidate_tickers), len(trade_candidate_events))
    return FunnelEvidence(
        trade_candidates=candidate_count,
        live_cache_eligible=sum(
            1 for proof in candidate_proofs.values() if proof.live_cache_eligible
        ),
        opportunities=len(opportunities),
        research_backed_opportunities=len(backed_opportunities),
        research_backed_trades=len(backed_trades),
        candidate_to_opportunity_rate=current_rates["candidate_to_opportunity"],
        opportunity_to_trade_rate=current_rates["opportunity_to_trade"],
        seven_day_candidate_to_opportunity_rate=seven_day_rates[
            "candidate_to_opportunity"
        ],
        seven_day_opportunity_to_trade_rate=seven_day_rates["opportunity_to_trade"],
        thirty_day_candidate_to_opportunity_rate=thirty_day_rates[
            "candidate_to_opportunity"
        ],
        thirty_day_opportunity_to_trade_rate=thirty_day_rates["opportunity_to_trade"],
        candidate_to_opportunity_delta_vs_7d=_delta(
            current_rates["candidate_to_opportunity"],
            seven_day_rates["candidate_to_opportunity"],
        ),
        candidate_to_opportunity_delta_vs_30d=_delta(
            current_rates["candidate_to_opportunity"],
            thirty_day_rates["candidate_to_opportunity"],
        ),
        opportunity_to_trade_delta_vs_7d=_delta(
            current_rates["opportunity_to_trade"],
            seven_day_rates["opportunity_to_trade"],
        ),
        opportunity_to_trade_delta_vs_30d=_delta(
            current_rates["opportunity_to_trade"],
            thirty_day_rates["opportunity_to_trade"],
        ),
        lost_positive_edge_candidates=lost_positive,
        top_skip_reasons=dict(skip_reasons.most_common(10)),
    )


def _profit_evidence(
    rows: list[PaperRow],
    *,
    candidate_tickers: set[str],
    since: datetime,
    seven_day_since: datetime,
    thirty_day_since: datetime,
    until: datetime,
    min_edge: float,
) -> ProfitEvidence:
    current = _filter_rows(
        rows,
        candidate_tickers=candidate_tickers,
        since=since,
        until=until,
    )
    seven_day = _filter_rows(
        rows,
        candidate_tickers=candidate_tickers,
        since=seven_day_since,
        until=until,
    )
    thirty_day = _filter_rows(
        rows,
        candidate_tickers=candidate_tickers,
        since=thirty_day_since,
        until=until,
    )
    resolved = [row for row in current if row.resolved]
    pnl_values = [row.pnl_dollars or 0.0 for row in resolved]
    wins = [pnl for pnl in pnl_values if pnl > 0]
    losses = [pnl for pnl in pnl_values if pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    deployed = sum(row.cost_dollars for row in resolved)
    edges = [row.edge for row in current if row.edge is not None]
    return ProfitEvidence(
        total_trades=len(current),
        resolved_trades=len(resolved),
        wins=len(wins),
        losses=len(losses),
        net_pnl=sum(pnl_values),
        win_rate=_ratio(len(wins), len(resolved)),
        profit_factor=(
            inf if gross_win > 0 and gross_loss == 0 else _ratio(gross_win, gross_loss)
        ),
        avg_edge_captured=(sum(edges) / len(edges) if edges else None),
        max_drawdown=_max_drawdown(current),
        unrealized_pnl=None,
        unrealized_pnl_unavailable_reason=(
            "open-position mark prices unavailable"
            if any(not row.resolved for row in current)
            else "open-position mark prices unavailable"
        ),
        deployed_capital=deployed,
        roi_on_deployed_capital=_ratio(sum(pnl_values), deployed),
        low_edge_overbet_rate=_ratio(
            sum(1 for row in current if (row.edge or 0.0) <= min_edge),
            len(current),
        ),
        seven_day_net_pnl=sum(row.pnl_dollars or 0.0 for row in seven_day if row.resolved),
        thirty_day_net_pnl=sum(
            row.pnl_dollars or 0.0 for row in thirty_day if row.resolved
        ),
    )


def _conversion_rates(
    records: list[dict[str, Any]],
    candidate_tickers: set[str],
    *,
    fallback_candidate_count: int = 0,
) -> dict[str, float | None]:
    candidate_events = [
        record
        for record in records
        if str(record.get("research_status") or "") in RESEARCH_TRADE_READY_STATUSES
        and _record_ticker(record) in candidate_tickers
    ]
    opportunities = [
        record
        for record in records
        if record.get("type") == "OPPORTUNITY"
        and _record_ticker(record) in candidate_tickers
    ]
    trades = [
        record
        for record in records
        if record.get("type") in {"PAPER_TRADE", "LIVE_ORDER"}
        and _record_ticker(record) in candidate_tickers
    ]
    candidates = max(len(candidate_events), fallback_candidate_count)
    return {
        "candidate_to_opportunity": _ratio(len(opportunities), candidates),
        "opportunity_to_trade": _ratio(len(trades), len(opportunities)),
    }


def _delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return current - baseline


def _research_quality_evidence(
    evidence_db: Path,
    candidate_proofs: dict[str, CandidateProof],
    *,
    fresh_since: datetime,
) -> ResearchQualityEvidence:
    if not evidence_db.exists() or not _has_table(evidence_db, "research_evidence"):
        return ResearchQualityEvidence(0, 0, {}, sorted(candidate_proofs))
    source_counts: Counter[str] = Counter()
    with _connect_ro(evidence_db) as conn:
        for row in conn.execute(
            """
            SELECT source_class, COALESCE(retrieved_at, inserted_at) AS ts
            FROM research_evidence
            """
        ):
            ts = _parse_ts(row["ts"])
            if ts is None or ts < fresh_since:
                continue
            source_counts[str(row["source_class"] or "unknown")] += 1
    official_rows = sum(
        count
        for source_class, count in source_counts.items()
        if source_class in OFFICIAL_SOURCE_CLASSES
    )
    stale_or_weak = sorted(
        ticker
        for ticker, proof in candidate_proofs.items()
        if not proof.live_cache_eligible
    )
    return ResearchQualityEvidence(
        evidence_rows=sum(source_counts.values()),
        official_evidence_rows=official_rows,
        source_class_counts=dict(sorted(source_counts.items())),
        stale_or_weak_candidate_tickers=stale_or_weak,
    )


def _decision_grade_evidence(
    evidence_db: Path,
    *,
    fresh_since: datetime,
    now: datetime,
) -> DecisionGradeEvidence:
    if not evidence_db.exists() or not _has_table(evidence_db, "research_runs"):
        return DecisionGradeEvidence(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    counts = Counter()
    with _connect_ro(evidence_db) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
        }
        created_expr = (
            "r.created_ts AS created_ts"
            if "created_ts" in columns
            else "NULL AS created_ts"
        )
        force_side_expr = (
            "r.force_side AS force_side"
            if "force_side" in columns
            else "NULL AS force_side"
        )
        probability_expr = (
            "r.estimated_probability AS estimated_probability"
            if "estimated_probability" in columns
            else "NULL AS estimated_probability"
        )
        market_price_expr = (
            "r.market_price AS market_price"
            if "market_price" in columns
            else "NULL AS market_price"
        )
        estimated_edge_expr = (
            "r.estimated_edge AS estimated_edge"
            if "estimated_edge" in columns
            else "NULL AS estimated_edge"
        )
        table_names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        dossier_columns = (
            {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(research_dossiers)"
                ).fetchall()
            }
            if "research_dossiers" in table_names
            else set()
        )
        required_dossier_columns = {
            "market_ticker",
            "last_research_run_id",
            "market_status",
            "market_close_time",
        }
        eligibility_schema_available = required_dossier_columns <= dossier_columns
        if eligibility_schema_available:
            run_source_sql = """
                FROM research_runs AS r
                LEFT JOIN research_dossiers AS d
                  ON d.market_ticker = r.market_ticker
                 AND d.last_research_run_id = r.research_run_id
                WHERE d.last_research_run_id IS NOT NULL
                   OR NOT EXISTS (
                        SELECT 1
                        FROM research_dossiers AS current_dossier
                        WHERE current_dossier.market_ticker = r.market_ticker
                   )
            """
            eligibility_select = "d.market_status, d.market_close_time"
        else:
            run_source_sql = "FROM research_runs AS r"
            eligibility_select = (
                "NULL AS market_status, NULL AS market_close_time"
            )
        rows = conn.execute(
            f"""
            SELECT
                r.market_ticker,
                r.research_run_id,
                r.verdict_status,
                r.skip_reason,
                {created_expr},
                {force_side_expr},
                {probability_expr},
                {market_price_expr},
                {estimated_edge_expr},
                {eligibility_select}
            {run_source_sql}
            """
        ).fetchall()
        task_rows = []
        if "research_tasks" in table_names:
            task_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_tasks)").fetchall()
            }
            task_created_expr = (
                "updated_ts" if "updated_ts" in task_columns else "NULL AS updated_ts"
            )
            task_terminal_reason_expr = (
                "terminal_reason"
                if "terminal_reason" in task_columns
                else "NULL AS terminal_reason"
            )
            task_rows = conn.execute(
                f"""
                SELECT market_ticker, state, {task_created_expr}, {task_terminal_reason_expr}
                FROM research_tasks
                """
            ).fetchall()
    terminal_untradeable_tasks = set()
    source_path_researchable_tasks = set()
    for row in task_rows:
        ts = _parse_ts(row["updated_ts"])
        if ts is not None and ts < fresh_since:
            continue
        terminal_reason = str(row["terminal_reason"] or "")
        if (
            str(row["state"] or "") == "untradeable"
            and terminal_reason == "no_reliable_source_path"
        ):
            source_path_researchable_tasks.add(str(row["market_ticker"]))
            counts["stale_but_researchable"] += 1
            continue
        if (
            str(row["state"] or "") == "untradeable"
            and terminal_reason in DECISION_GRADE_TASK_TERMINAL_REASONS
        ):
            terminal_untradeable_tasks.add(str(row["market_ticker"]))
        if terminal_reason == "research_timeout_exhausted":
            counts["terminal_timeout_exhausted"] += 1
    latest_rows_by_ticker: dict[str, sqlite3.Row] = {}
    for row in rows:
        ticker = str(row["market_ticker"] or "").strip()
        if not ticker:
            continue
        current = latest_rows_by_ticker.get(ticker)
        if current is None or _research_row_sort_key(row) > _research_row_sort_key(
            current
        ):
            latest_rows_by_ticker[ticker] = row
    for row in latest_rows_by_ticker.values():
        ticker = str(row["market_ticker"] or "")
        if ticker in terminal_untradeable_tasks:
            continue
        if ticker in source_path_researchable_tasks:
            continue
        ts = _parse_ts(row["created_ts"])
        if ts is not None and ts < fresh_since:
            continue
        status = str(row["verdict_status"] or "")
        reason = str(row["skip_reason"] or "")
        if status == "decision_grade_candidate":
            eligibility = evaluate_research_market_eligibility(
                status=row["market_status"],
                close_time=row["market_close_time"],
                now=now,
            )
            if not eligibility.eligible:
                counts["blocked_by_market_ineligible"] += 1
                continue
            if not _row_has_recomputable_edge(row):
                counts["blocked_by_missing_price"] += 1
                continue
            quality = _candidate_evidence_quality(
                conn,
                ticker=str(row["market_ticker"] or ""),
                research_run_id=str(row["research_run_id"] or ""),
                force_side=str(row["force_side"] or ""),
                fresh_since=fresh_since,
            )
            if not quality.has_reliable_source_path:
                counts["blocked_by_no_reliable_source_path"] += 1
                continue
            if not quality.has_directional_evidence:
                counts["blocked_by_neutral_evidence"] += 1
                continue
            if not quality.has_counter_query or not quality.has_counter_evidence:
                counts["blocked_by_no_counter_evidence"] += 1
                continue
            counts["decision_grade_candidates"] += 1
        if status == "needs_price_edge" or reason == "missing_market_price":
            counts["blocked_by_missing_price"] += 1
        if reason in {
            "missing_resolution_source",
            "no_research_hits",
            "insufficient_corroboration",
        }:
            counts["blocked_by_no_reliable_source_path"] += 1
        if reason == "official_data_pending":
            counts["blocked_by_official_data_pending"] += 1
        if status == "research_provider_error" or reason == "research_provider_error":
            counts["blocked_by_provider_error"] += 1
        if reason == "neutral_only_evidence":
            counts["blocked_by_neutral_evidence"] += 1
        if reason == "ambiguous_direction":
            counts["blocked_by_ambiguous_direction"] += 1
        if reason == "missing_counter_evidence":
            counts["blocked_by_no_counter_evidence"] += 1
        if reason == "generic_summary":
            counts["blocked_by_generic_summary"] += 1
        if reason == "unresolved_contradiction":
            counts["blocked_by_unresolved_contradiction"] += 1
        if reason == "source_freshness_ttl_exceeded":
            counts["stale_but_researchable"] += 1
        if status == "untradeable" and reason in DECISION_GRADE_NO_TRADE_SKIP_REASONS:
            counts["terminal_untradeable"] += 1
    if terminal_untradeable_tasks:
        counts["terminal_untradeable"] = max(
            counts["terminal_untradeable"],
            len(terminal_untradeable_tasks),
        )
    return DecisionGradeEvidence(
        decision_grade_candidates=counts["decision_grade_candidates"],
        blocked_by_missing_price=counts["blocked_by_missing_price"],
        blocked_by_no_reliable_source_path=counts[
            "blocked_by_no_reliable_source_path"
        ],
        blocked_by_official_data_pending=counts["blocked_by_official_data_pending"],
        blocked_by_provider_error=counts["blocked_by_provider_error"],
        blocked_by_neutral_evidence=counts["blocked_by_neutral_evidence"],
        blocked_by_ambiguous_direction=counts["blocked_by_ambiguous_direction"],
        blocked_by_no_counter_evidence=counts["blocked_by_no_counter_evidence"],
        blocked_by_generic_summary=counts["blocked_by_generic_summary"],
        blocked_by_unresolved_contradiction=counts[
            "blocked_by_unresolved_contradiction"
        ],
        stale_but_researchable=counts["stale_but_researchable"],
        terminal_untradeable=counts["terminal_untradeable"],
        terminal_timeout_exhausted=counts["terminal_timeout_exhausted"],
        blocked_by_market_ineligible=counts["blocked_by_market_ineligible"],
    )


def _research_row_sort_key(row: sqlite3.Row) -> tuple[datetime, str]:
    ts = _parse_ts(row["created_ts"])
    return (
        ts if ts is not None else datetime.min.replace(tzinfo=timezone.utc),
        str(row["research_run_id"] or ""),
    )


def _replay_evidence(replay_root: Path) -> ReplayEvidence:
    items = collect_replay_evidence(replay_root)
    scored = [item for item in items if item.status == "scored"]
    positive = [
        item
        for item in scored
        if (item.trade_count or 0) > 0
        and (item.realized_pnl or 0.0) > 0
        and (item.per_trade_ev or 0.0) > 0
        and (item.ev_ci_95_lo or 0.0) > 0
    ]
    best = max(positive, key=lambda item: item.per_trade_ev or 0.0, default=None)
    return ReplayEvidence(
        scored_items=len(scored),
        positive_items=len(positive),
        best_trade_count=best.trade_count if best else None,
        best_realized_pnl=best.realized_pnl if best else None,
        best_per_trade_ev=best.per_trade_ev if best else None,
        best_ev_ci_95_lo=best.ev_ci_95_lo if best else None,
    )


def _workflow_gate_evidence(
    repo_root: Path,
    *,
    trades_log: Path,
    app_log: Path,
    now: datetime,
    window_hours: float,
    run_workflow_gates: bool,
    run_bothealth: bool,
    bothealth_output: str | None,
    expected_version: str | None,
) -> WorkflowGateEvidence:
    normal_ok: bool | None = None
    strict_ok: bool | None = None
    failures: list[str] = []

    if run_workflow_gates:
        try:
            from scripts.research_multi_agent_workflow import (
                evaluate_research_multi_agent_workflow,
            )

            normal = evaluate_research_multi_agent_workflow(
                repo_root,
                trades_log,
                now=now,
                window_hours=window_hours,
                bot_log=app_log,
            )
            strict = evaluate_research_multi_agent_workflow(
                repo_root,
                trades_log,
                now=now,
                window_hours=window_hours,
                bot_log=app_log,
                require_live_cache=True,
                expected_version=expected_version,
            )
            normal_ok = normal.ok
            strict_ok = strict.ok
            failures.extend(f"normal gate: {item}" for item in normal.failures)
            failures.extend(f"strict gate: {item}" for item in strict.failures)
        except Exception as exc:  # pragma: no cover - defensive CLI guard
            failures.append(f"workflow gate error: {exc}")
            normal_ok = False
            strict_ok = False

    if bothealth_output is None and run_bothealth:
        bothealth_output = _run_bothealth(repo_root)

    bothealth_verdict, bothealth_summary = _parse_bothealth(bothealth_output)
    bothealth_capital_safe = (
        _bothealth_red_is_capital_safe(bothealth_summary)
        if bothealth_verdict == "RED"
        else (True if bothealth_verdict else None)
    )
    return WorkflowGateEvidence(
        normal_gate_ok=normal_ok,
        strict_live_cache_gate_ok=strict_ok,
        bothealth_verdict=bothealth_verdict,
        bothealth_capital_safe=bothealth_capital_safe,
        bothealth_summary=bothealth_summary,
        failures=failures,
    )


def _change_evidence(repo_root: Path) -> ChangeEvidence:
    head = _git(repo_root, "git rev-parse HEAD")
    origin = _git(repo_root, "git rev-parse origin/main")
    gitlab = _git(repo_root, "git rev-parse gitlab/main")
    remotes_synced = (
        None
        if head is None or origin is None or gitlab is None
        else head == origin == gitlab
    )
    recent_raw = _git(repo_root, "git log --since='24 hours ago' --oneline --decorate -20")
    return ChangeEvidence(
        head=head,
        branch=_git(repo_root, "git branch --show-current"),
        origin_main=origin,
        gitlab_main=gitlab,
        remotes_synced=remotes_synced,
        recent_commits=recent_raw.splitlines() if recent_raw else [],
        dirty_paths=[
            line.strip()
            for line in (_git(repo_root, "git status --short") or "").splitlines()
            if line.strip()
        ],
    )


def _decide(
    runtime: RuntimeEvidence,
    funnel: FunnelEvidence,
    profit: ProfitEvidence,
    research_quality: ResearchQualityEvidence,
    decision_grade: DecisionGradeEvidence,
    replay: ReplayEvidence,
    risk: RiskEvidence,
    workflow: WorkflowGateEvidence,
) -> tuple[Verdict, Action, list[str]]:
    hard_fail: list[str] = []
    if risk.unauthorized_live_orders:
        hard_fail.append(f"unauthorized LIVE_ORDER count {risk.unauthorized_live_orders}")
    if runtime.within_cooldown_repeats:
        hard_fail.append(f"within_cooldown_repeats {runtime.within_cooldown_repeats}")
    if runtime.error_critical_count:
        hard_fail.append(f"ERROR/CRITICAL count {runtime.error_critical_count}")
    if workflow.normal_gate_ok is False:
        hard_fail.append("normal research workflow gate failed")
    if (
        workflow.strict_live_cache_gate_ok is False
        and decision_grade.terminal_untradeable == 0
    ):
        hard_fail.append("strict live-cache research workflow gate failed")
    if workflow.bothealth_verdict == "RED" and not workflow.bothealth_capital_safe:
        hard_fail.append("bothealth RED without capital-safe explanation")
    if risk.drawdown_breach:
        hard_fail.append("max drawdown breached")
    if research_quality.official_evidence_rows == 0:
        hard_fail.append("fresh official/resolution evidence missing")
    if funnel.trade_candidates == 0 and decision_grade.terminal_untradeable == 0:
        hard_fail.append("no decision_grade_candidate evidence")
    if (
        funnel.trade_candidates > 0
        and funnel.live_cache_eligible == 0
        and decision_grade.terminal_untradeable == 0
    ):
        hard_fail.append("no live_cache_eligible research proof")
    if profit.net_pnl < 0:
        hard_fail.append("research-backed P&L is negative")
    if decision_grade.blocked_by_market_ineligible:
        hard_fail.append(
            "decision-grade candidates target an inactive, expired, or unverified market"
        )
    if (
        decision_grade.blocked_by_no_reliable_source_path
        and decision_grade.terminal_untradeable == 0
    ):
        hard_fail.append(
            "decision-grade candidates have no reliable source path; "
            "keep queued in needs_research until independent official/secondary sources exist"
        )
    if decision_grade.blocked_by_ambiguous_direction:
        hard_fail.append(
            "research has source coverage but no directional probability; "
            "keep queued for side/probability evidence"
        )
    if (
        decision_grade.blocked_by_no_counter_evidence
        and decision_grade.terminal_untradeable == 0
    ):
        hard_fail.append(
            "decision-grade candidates missing counter-evidence; run "
            "scripts/research_decision_grade_repair.py --format json first; "
            "operator may requeue with scripts/research_decision_grade_repair.py --apply"
        )
    if decision_grade.blocked_by_unresolved_contradiction:
        hard_fail.append(
            "decision-grade candidates have unresolved contradictions; "
            "keep queued in needs_counter_evidence until countercase is resolved"
        )
    if hard_fail:
        if (
            decision_grade.blocked_by_no_reliable_source_path
            and not any("no reliable source path" in reason for reason in hard_fail)
        ):
            hard_fail.append(
                "decision-grade candidates have no reliable source path; "
                "keep queued in needs_research until independent official/secondary sources exist"
            )
        if (
            decision_grade.blocked_by_no_counter_evidence
            and not any("counter-evidence" in reason for reason in hard_fail)
        ):
            hard_fail.append(
                "decision-grade candidates missing counter-evidence; run "
                "scripts/research_decision_grade_repair.py --format json first; "
                "operator may requeue with scripts/research_decision_grade_repair.py --apply"
            )
    if hard_fail and decision_grade.blocked_by_official_data_pending:
        hard_fail.append(
            "official settlement data is pending; keep queued until the source publishes"
        )
    if hard_fail:
        return "NOT_SUCCESSFUL", "ROLL_BACK_OR_PATCH", hard_fail

    reasons: list[str] = []
    if profit.total_trades == 0:
        reasons.append("no research-backed trades in current window")
    if funnel.research_backed_trades == 0:
        reasons.append("no research-backed trade events in current window")
    if profit.resolved_trades == 0:
        reasons.append("no resolved research-backed trades")
    if decision_grade.blocked_by_no_counter_evidence:
        reasons.append(
            "decision-grade candidates still need counter-evidence; keep queued in shadow research"
        )
    if decision_grade.blocked_by_ambiguous_direction:
        reasons.append(
            "research has source coverage but no directional probability; keep queued for side/probability evidence"
        )
    if decision_grade.blocked_by_official_data_pending:
        reasons.append(
            "official settlement data is pending; keep queued until the source publishes"
        )

    if (
        profit.total_trades > 0
        and profit.resolved_trades > 0
        and profit.net_pnl > 0
        and (profit.profit_factor == inf or (profit.profit_factor or 0.0) > 1.25)
    ):
        reasons.append("research-backed trades are profitable with profit factor > 1.25")
        return "STRONGLY_SUCCESSFUL", "KEEP_PAPER_RUNNING", reasons

    if (
        profit.total_trades == 0
        and replay.positive_items > 0
        and funnel.trade_candidates > 0
        and funnel.live_cache_eligible > 0
    ):
        reasons.append("positive counterfactual replay supports paper review")
        return "PROVISIONALLY_SUCCESSFUL", "PROMOTE_TO_PAPER_REVIEW", reasons

    if profit.total_trades > 0 and profit.net_pnl >= 0:
        reasons.append("paper trades observed but profit evidence is not decisive yet")
        return "PROVISIONALLY_SUCCESSFUL", "KEEP_PAPER_RUNNING", reasons

    if (
        profit.total_trades == 0
        and decision_grade.terminal_untradeable > 0
        and funnel.live_cache_eligible == 0
    ):
        reasons.append(
            "research reached terminal no-trade decisions; capital remains protected"
        )
        return "PROVISIONALLY_SUCCESSFUL", "CONTINUE_SHADOW", reasons

    if (
        profit.total_trades == 0
        and funnel.trade_candidates > 0
        and funnel.live_cache_eligible > 0
    ):
        reasons.append(
            "research loop is operating cleanly but has not produced trade support"
        )
        return "PROVISIONALLY_SUCCESSFUL", "CONTINUE_SHADOW", reasons

    return "NOT_SUCCESSFUL", "CONTINUE_SHADOW", reasons


def _load_candidate_proofs(
    evidence_db: Path,
    *,
    fresh_since: datetime,
    now: datetime,
) -> dict[str, CandidateProof]:
    if not evidence_db.exists() or not _has_table(evidence_db, "research_runs"):
        return {}
    proofs: dict[str, CandidateProof] = {}
    with _connect_ro(evidence_db) as conn:
        conn.row_factory = sqlite3.Row
        if not all(
            _has_table_conn(conn, table)
            for table in ("research_dossiers", "research_evidence")
        ):
            return {}
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
        }
        if not {"market_price", "estimated_edge"} <= columns:
            return {}
        dossier_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(research_dossiers)").fetchall()
        }
        required_dossier_columns = {
            "market_ticker",
            "last_research_run_id",
            "last_verdict_status",
            "market_status",
            "market_close_time",
        }
        if not required_dossier_columns <= dossier_columns:
            return {}
        for row in conn.execute(
            """
            SELECT
                r.market_ticker,
                r.research_run_id,
                r.created_ts,
                r.force_side,
                r.estimated_probability,
                r.market_price,
                r.estimated_edge,
                d.market_status,
                d.market_close_time
            FROM research_runs AS r
            JOIN research_dossiers AS d
              ON d.market_ticker = r.market_ticker
             AND d.last_research_run_id = r.research_run_id
            WHERE r.verdict_status = 'decision_grade_candidate'
              AND d.last_verdict_status = 'decision_grade_candidate'
              AND r.force_side IN ('yes', 'no')
              AND r.estimated_probability IS NOT NULL
              AND r.confidence IS NOT NULL
              AND r.market_price IS NOT NULL
              AND r.estimated_edge IS NOT NULL
            """
        ):
            eligibility = evaluate_research_market_eligibility(
                status=row["market_status"],
                close_time=row["market_close_time"],
                now=now,
            )
            if not eligibility.eligible:
                continue
            if not _row_has_recomputable_edge(row):
                continue
            quality = _candidate_evidence_quality(
                conn,
                ticker=str(row["market_ticker"] or ""),
                research_run_id=str(row["research_run_id"] or ""),
                force_side=str(row["force_side"] or ""),
                fresh_since=fresh_since,
            )
            if not quality.live_cache_eligible:
                continue
            ticker = str(row["market_ticker"])
            proofs[ticker] = CandidateProof(
                ticker=ticker,
                research_run_id=str(row["research_run_id"]),
                created_ts=_parse_ts(row["created_ts"]),
                evidence=quality.evidence,
            )
    return proofs


def _load_paper_rows(paper_db: Path) -> list[PaperRow]:
    if not paper_db.exists() or not _has_table(paper_db, "paper_trades"):
        return []
    with _connect_ro(paper_db) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_trades)").fetchall()
        }
        select = {
            "trade_id": "trade_id",
            "ts": "ts",
            "ticker": "ticker",
            "cost_dollars": (
                "cost_dollars" if "cost_dollars" in columns else "0.0 AS cost_dollars"
            ),
            "edge": "edge" if "edge" in columns else "NULL AS edge",
            "resolved": "resolved" if "resolved" in columns else "0 AS resolved",
            "pnl_dollars": (
                "pnl_dollars" if "pnl_dollars" in columns else "NULL AS pnl_dollars"
            ),
            "notional_bankroll_before": (
                "notional_bankroll_before"
                if "notional_bankroll_before" in columns
                else "NULL AS notional_bankroll_before"
            ),
            "notional_bankroll_after": (
                "notional_bankroll_after"
                if "notional_bankroll_after" in columns
                else "NULL AS notional_bankroll_after"
            ),
        }
        sql = "SELECT " + ", ".join(select.values()) + " FROM paper_trades"
        return [
            PaperRow(
                trade_id=str(row["trade_id"]),
                ts=_parse_ts(row["ts"]),
                ticker=str(row["ticker"]),
                cost_dollars=_as_float(row["cost_dollars"]) or 0.0,
                edge=_as_float(row["edge"]),
                resolved=bool(row["resolved"]),
                pnl_dollars=_as_float(row["pnl_dollars"]),
                notional_bankroll_before=_as_float(row["notional_bankroll_before"]),
                notional_bankroll_after=_as_float(row["notional_bankroll_after"]),
            )
            for row in conn.execute(sql)
        ]


def _filter_rows(
    rows: list[PaperRow],
    *,
    candidate_tickers: set[str],
    since: datetime,
    until: datetime,
) -> list[PaperRow]:
    return [
        row
        for row in rows
        if row.ticker in candidate_tickers
        and row.ts is not None
        and since <= row.ts <= until
    ]


def _max_drawdown(rows: list[PaperRow]) -> float | None:
    values: list[float] = []
    for row in rows:
        if row.notional_bankroll_before is not None:
            values.append(row.notional_bankroll_before)
        if row.notional_bankroll_after is not None:
            values.append(row.notional_bankroll_after)
    if not values:
        return None
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _log_error_samples(path: Path, *, since: datetime) -> list[str]:
    if not path.exists():
        return []
    samples: list[str] = []
    for line in path.read_text(errors="ignore").splitlines():
        ts = _parse_app_log_ts(line)
        if ts is not None and ts < since:
            continue
        if " ERROR " in line or " CRITICAL " in line:
            if _is_legacy_transient_provider_error(line):
                continue
            samples.append(line[:500])
    return samples


def _is_legacy_transient_provider_error(line: str) -> bool:
    if " CRITICAL " in line:
        return False
    text = line.lower()
    if " error " not in text or "kalshi_rest" not in text:
        return False
    transient_markers = (
        "too many 429",
        "too many 500",
        "too many 502",
        "too many 503",
        "too many 504",
        "http get",
        "http post",
        "request error get",
        "request error post",
        "max retries exceeded",
    )
    return any(marker in text for marker in transient_markers) and any(
        status in text for status in (" 429", " 500", " 502", " 503", " 504")
    )


def _parse_app_log_ts(line: str) -> datetime | None:
    if len(line) < 23:
        return None
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _latest_boot_version(path: Path) -> str | None:
    latest_ts: datetime | None = None
    latest_version: str | None = None
    if path.exists():
        for line in path.read_text(errors="ignore").splitlines():
            if "[BOOT]" not in line or "version=" not in line:
                continue
            version = line.split("version=", 1)[1].split()[0].strip()
            ts = _parse_app_log_ts(line)
            if ts is None or latest_ts is None or ts >= latest_ts:
                latest_ts = ts
                latest_version = version
    if latest_version:
        return latest_version
    version_path = path.parents[2] / "VERSION" if len(path.parents) >= 3 else None
    if version_path is not None and version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return None


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(path: Path, table: str) -> bool:
    try:
        with _connect_ro(path) as conn:
            return _has_table_conn(conn, table)
    except sqlite3.Error:
        return False


def _has_table_conn(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_ticker(record: dict[str, Any]) -> str:
    return str(record.get("ticker") or record.get("market_ticker") or "")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_has_recomputable_edge(row: Any) -> bool:
    side = str(row["force_side"] or "").lower()
    if side not in {"yes", "no"}:
        return False
    estimated_probability = _as_float(row["estimated_probability"])
    market_price = _as_float(row["market_price"])
    estimated_edge = _as_float(row["estimated_edge"])
    if estimated_probability is None or market_price is None or estimated_edge is None:
        return False
    if not (0.0 <= estimated_probability <= 1.0 and 0.0 < market_price < 1.0):
        return False
    side_probability = (
        estimated_probability if side == "yes" else 1.0 - estimated_probability
    )
    recomputed_edge = side_probability - market_price - 0.01
    return recomputed_edge > 0 and abs(recomputed_edge - estimated_edge) <= 0.005


def _candidate_evidence_quality(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    research_run_id: str,
    force_side: str,
    fresh_since: datetime,
) -> CandidateEvidenceQuality:
    if not _has_table_conn(conn, "research_evidence"):
        return CandidateEvidenceQuality(
            tuple(),
            frozenset(),
            False,
            False,
            force_side.strip().lower(),
        )
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(research_evidence)")
    }
    retrieved_expr = (
        "COALESCE(retrieved_at, inserted_at) AS ts"
        if {"retrieved_at", "inserted_at"} <= columns
        else (
            "retrieved_at AS ts"
            if "retrieved_at" in columns
            else "inserted_at AS ts"
            if "inserted_at" in columns
            else "NULL AS ts"
        )
    )
    claim_type_expr = "claim_type" if "claim_type" in columns else "NULL AS claim_type"
    direction_expr = (
        "supports_direction"
        if "supports_direction" in columns
        else "NULL AS supports_direction"
    )
    confidence_expr = (
        "supports_confidence"
        if "supports_confidence" in columns
        else "NULL AS supports_confidence"
    )
    metric_name_expr = "metric_name" if "metric_name" in columns else "NULL AS metric_name"
    metric_value_expr = "metric_value" if "metric_value" in columns else "NULL AS metric_value"
    extraction_confidence_expr = (
        "extraction_confidence"
        if "extraction_confidence" in columns
        else "NULL AS extraction_confidence"
    )
    source_name_expr = "source_name" if "source_name" in columns else "NULL AS source_name"
    source_url_expr = "source_url" if "source_url" in columns else "NULL AS source_url"
    title_expr = "title" if "title" in columns else "NULL AS title"
    snippet_expr = "snippet" if "snippet" in columns else "NULL AS snippet"
    evidence_payloads: list[dict[str, Any]] = []
    supports_directions: set[str] = set()
    has_counter_query = _has_recorded_counter_query(conn, research_run_id)
    has_counter_evidence = False
    structured_support_metrics: set[str] = set()
    structured_same_side_counter_metrics: set[str] = set()
    side = force_side.strip().lower()
    opposite = "no" if side == "yes" else "yes" if side == "no" else ""
    relevance_spec = build_contract_relevance_spec(
        ticker,
        _recorded_query_texts(conn, research_run_id),
    )
    for evidence in conn.execute(
        f"""
        SELECT source_class, {source_name_expr}, {source_url_expr},
               {claim_type_expr}, {direction_expr}, {confidence_expr},
               {metric_name_expr}, {metric_value_expr},
               {extraction_confidence_expr}, {title_expr}, {snippet_expr},
               {retrieved_expr}
        FROM research_evidence
        WHERE market_ticker = ?
          AND research_run_id = ?
        """,
        (ticker, research_run_id),
    ):
        ts = _parse_ts(evidence["ts"])
        if ts is not None and ts < fresh_since:
            continue
        source_class = str(evidence["source_class"] or "unknown")
        direction = str(evidence["supports_direction"] or "").strip().lower()
        evidence_text = f"{evidence['title'] or ''} {evidence['snippet'] or ''}"
        is_relevant = evidence_is_relevant_to_contract(
            evidence_text,
            relevance_spec,
        )
        effective_direction = direction if is_relevant else "neutral"
        evidence_payload = {
                "source_class": source_class,
                "source_name": evidence["source_name"],
                "source_url": evidence["source_url"],
                "claim_type": evidence["claim_type"],
                "supports_direction": effective_direction,
                "supports_confidence": evidence["supports_confidence"],
                "metric_name": evidence["metric_name"],
                "metric_value": evidence["metric_value"],
                "extraction_confidence": evidence["extraction_confidence"],
            }
        evidence_payloads.append(evidence_payload)
        claim_type = str(evidence["claim_type"] or "").strip().lower()
        confidence = float(evidence["supports_confidence"] or 0.0)
        if (
            is_relevant
            and claim_type in SETTLEMENT_CLAIM_TYPES
            and direction in {"yes", "no"}
            and confidence >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
        ):
            supports_directions.add(direction)
        metric_name = str(evidence["metric_name"] or "").strip()
        extraction_confidence = float(evidence["extraction_confidence"] or 0.0)
        effective_source_class = effective_research_source_class(evidence_payload)
        if (
            claim_type in SETTLEMENT_CLAIM_TYPES
            and direction in {"yes", "no"}
            and confidence >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
            and metric_name in STRUCTURED_SIGNAL_METRICS
            and effective_source_class in OFFICIAL_SOURCE_CLASSES
        ):
            if direction == side:
                structured_support_metrics.add(metric_name)
        if is_relevant and claim_type in {"disconfirming", "contradiction_check"}:
            if (
                direction == opposite
                and confidence >= MIN_COUNTER_EVIDENCE_CONFIDENCE
            ) or direction == "neutral":
                has_counter_evidence = True
            elif (
                direction == side
                and confidence >= 0.8
                and metric_name in STRUCTURED_SIGNAL_METRICS
                and effective_source_class in OFFICIAL_SOURCE_CLASSES
                and (
                    evidence["metric_value"] is not None
                    or extraction_confidence >= 0.8
                )
            ):
                structured_same_side_counter_metrics.add(metric_name)
    if structured_support_metrics & structured_same_side_counter_metrics:
        has_counter_evidence = True
    return CandidateEvidenceQuality(
        evidence=tuple(evidence_payloads),
        supports_directions=frozenset(supports_directions),
        has_counter_query=has_counter_query,
        has_counter_evidence=has_counter_evidence,
        force_side=side,
    )


def _recorded_query_texts(
    conn: sqlite3.Connection,
    research_run_id: str,
) -> tuple[str, ...]:
    if not _has_table_conn(conn, "research_run_queries"):
        return ()
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(research_run_queries)")
    }
    if "query" not in columns:
        return ()
    return tuple(
        str(row[0] or "")
        for row in conn.execute(
            """
            SELECT query
            FROM research_run_queries
            WHERE research_run_id = ?
            ORDER BY ordinal
            """,
            (research_run_id,),
        )
    )


def _has_recorded_counter_query(conn: sqlite3.Connection, research_run_id: str) -> bool:
    if not _has_table_conn(conn, "research_run_queries"):
        return False
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(research_run_queries)")
    }
    if "query_intent" not in columns:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM research_run_queries
        WHERE research_run_id = ?
          AND query_intent IN ('disconfirming', 'contradiction_check')
        LIMIT 1
        """,
        (research_run_id,),
    ).fetchone()
    return row is not None


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _resolve(repo_root: Path, value: Path) -> Path:
    return value if value.is_absolute() else repo_root / value


def _git(repo_root: Path, command: str) -> str | None:
    try:
        return subprocess.check_output(
            command,
            cwd=repo_root,
            shell=True,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except subprocess.SubprocessError:
        return None


def _run_bothealth(repo_root: Path) -> str:
    script = repo_root / "scripts" / "bothealth.sh"
    if not script.exists():
        return "BOTHEALTH_NOT_FOUND"
    try:
        return subprocess.check_output(
            ["bash", str(script)],
            cwd=repo_root,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        return exc.output or f"BOTHEALTH_EXIT_{exc.returncode}"
    except subprocess.SubprocessError as exc:
        return f"BOTHEALTH_ERROR {exc}"


def _run_botcheck(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            "zsh -lc 'source ~/.zshrc >/dev/null 2>&1; botcheck'",
            cwd=repo_root,
            shell=True,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        return exc.output or f"BOTCHECK_EXIT_{exc.returncode}"
    except subprocess.SubprocessError as exc:
        return f"BOTCHECK_ERROR {exc}"


def _parse_botcheck_summary(output: str | None) -> list[str]:
    if not output:
        return []
    wanted = (
        "Launchd PID",
        "Bot PID",
        "Boot UTC",
        "Version",
        "RESEARCH_PREWARM_RESULT",
        "PAPER_TRADE",
        "LIVE_ORDER",
        "activation",
        "prewarm",
        "research_rows",
        "statuses",
        "vetted",
    )
    return [
        line.strip()
        for line in output.splitlines()
        if any(token in line for token in wanted)
    ][:80]


def _parse_bothealth(output: str | None) -> tuple[str | None, str | None]:
    if not output:
        return None, None
    for line in output.splitlines():
        if "Verdict:" not in line:
            continue
        summary = line.strip()
        for label in ("GREEN", "YELLOW", "RED"):
            if label in summary:
                return label, summary
        return "UNKNOWN", summary
    lines = output.splitlines()
    return None, lines[0].strip() if lines else None


def _bothealth_red_is_capital_safe(summary: str | None) -> bool:
    if not summary:
        return False
    normalized = summary.lower()
    return (
        "post_fix_new readiness not_ready" in normalized
        and "min_trades" in normalized
        and "rows" in normalized
    )


def _json_number(value: Any) -> Any:
    if value == inf:
        return "Infinity"
    return value


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value == inf:
        return "inf"
    return f"{value:.3f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


if __name__ == "__main__":
    raise SystemExit(main())
