from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.research_decision_grade_repair import (
    InvalidDecisionGradeCandidate,
    RepairableResearchTask,
    find_invalid_decision_grade_candidates,
    find_repairable_research_tasks,
    repair_invalid_decision_grade_candidates,
    repair_research_task_blockers,
)
from scripts.research_profit_validation_loop import (
    ResearchProfitValidationReport,
    evaluate_research_profit_validation,
)
from utils.output_paths import EVIDENCE_STORE_DB, PAPER_TRADES_DB, RAW_EDGE_REPLAY_DIR

DecisionLoopStatus = Literal[
    "READY_FOR_TRADE_REVIEW",
    "CONTINUE_RESEARCH",
    "TERMINAL_NO_TRADE",
    "FAILED_NO_DECISION",
]

RESEARCH_BLOCKER_FIELDS = (
    "blocked_by_missing_price",
    "blocked_by_market_ineligible",
    "blocked_by_no_reliable_source_path",
    "blocked_by_official_data_pending",
    "blocked_by_provider_error",
    "blocked_by_neutral_evidence",
    "blocked_by_ambiguous_direction",
    "blocked_by_no_counter_evidence",
    "blocked_by_generic_summary",
    "blocked_by_unresolved_contradiction",
    "stale_but_researchable",
    "terminal_timeout_exhausted",
)


@dataclass(frozen=True)
class DecisionLoopReport:
    status: DecisionLoopStatus
    ok: bool
    action: str
    reasons: list[str]
    validation_verdict: str
    validation_action: str
    research_operating_cleanly: bool
    research_supports_trades: bool
    decision_grade_candidates: int
    live_cache_eligible: int
    terminal_untradeable: int
    research_blockers: dict[str, int]
    invalid_decision_grade_candidates: int
    repairable_research_tasks: int
    repair_applied: bool = False
    repaired_decision_grade_candidates: int = 0
    repaired_research_tasks: int = 0
    repair_candidates: list[dict[str, str]] = field(default_factory=list)
    task_repair_candidates: list[dict[str, str]] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    action_results: list[dict[str, Any]] = field(default_factory=list)
    action_errors: list[str] = field(default_factory=list)
    simulation_mode: bool = False
    simulation_source_evidence_db: str | None = None
    simulation_evidence_db: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_research_decision_loop(
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
    run_workflow_gates: bool = True,
    run_bothealth: bool = True,
    expected_version: str | None = None,
    run_botcheck: bool = True,
    apply_repair: bool = False,
) -> DecisionLoopReport:
    repaired_decision_grade_candidates = 0
    repaired_research_tasks = 0
    if apply_repair:
        repaired_decision_grade_candidates = len(
            repair_invalid_decision_grade_candidates(evidence_db)
        )
        repaired_research_tasks = len(repair_research_task_blockers(evidence_db))
    validation = evaluate_research_profit_validation(
        repo_root,
        trades_log=trades_log,
        paper_db=paper_db,
        evidence_db=evidence_db,
        replay_root=replay_root,
        app_log=app_log,
        now=now,
        window_hours=window_hours,
        baseline_days=baseline_days,
        baseline_days_2=baseline_days_2,
        min_edge=min_edge,
        max_drawdown=max_drawdown,
        allow_live_orders=allow_live_orders,
        run_workflow_gates=run_workflow_gates,
        run_bothealth=run_bothealth,
        expected_version=expected_version,
        run_botcheck=run_botcheck,
    )
    invalid_candidates = find_invalid_decision_grade_candidates(evidence_db)
    task_candidates = find_repairable_research_tasks(evidence_db)
    return classify_decision_loop(
        validation,
        invalid_candidates,
        task_candidates,
        repair_applied=apply_repair,
        repaired_decision_grade_candidates=repaired_decision_grade_candidates,
        repaired_research_tasks=repaired_research_tasks,
    )


def classify_decision_loop(
    validation: ResearchProfitValidationReport,
    invalid_candidates: list[InvalidDecisionGradeCandidate],
    task_candidates: list[RepairableResearchTask] | None = None,
    *,
    repair_applied: bool = False,
    repaired_decision_grade_candidates: int = 0,
    repaired_research_tasks: int = 0,
) -> DecisionLoopReport:
    task_candidates = task_candidates or []
    blockers = {
        field_name: int(getattr(validation.decision_grade, field_name, 0))
        for field_name in RESEARCH_BLOCKER_FIELDS
        if int(getattr(validation.decision_grade, field_name, 0))
    }
    invalid_count = len(invalid_candidates)
    repairable_task_count = len(task_candidates)
    safety_failures = _safety_failures(validation)
    reasons: list[str] = []
    next_commands: list[str] = []
    if repair_applied:
        reasons.append(
            _repair_applied_reason(
                repaired_decision_grade_candidates,
                repaired_research_tasks,
            )
        )

    if invalid_count:
        reasons.append(
            f"{invalid_count} persisted decision-grade candidates fail strict evidence checks"
        )
        next_commands.append(
            "scripts/research_decision_loop.py --simulate-repair-prewarm --format json"
        )
        next_commands.append(
            "scripts/research_decision_loop.py --run-prewarm --max-cycles 2 --sleep-seconds 0 --format json"
        )
        next_commands.append(
            "scripts/research_decision_grade_repair.py --format json"
        )
        next_commands.append(
            "operator-approved only: scripts/research_decision_grade_repair.py --apply"
        )
    if repairable_task_count:
        reasons.append(
            f"{repairable_task_count} repairable research task(s) need queue-state cleanup"
        )
        next_commands.append(
            "scripts/research_decision_loop.py --simulate-repair-prewarm --format json"
        )
        next_commands.append(
            "scripts/research_decision_grade_repair.py --format json"
        )
        next_commands.append(
            "operator-approved only: scripts/research_decision_grade_repair.py --apply"
        )

    for failure in safety_failures:
        reasons.append(failure)

    ready_for_trade_review = (
        validation.decision_grade.decision_grade_candidates > 0
        and validation.funnel.live_cache_eligible > 0
        and invalid_count == 0
        and repairable_task_count == 0
        and not blockers
        and not safety_failures
        and validation.workflow.strict_live_cache_gate_ok is not False
    )
    if ready_for_trade_review:
        reasons.append(
            "decision-grade dossier has price, edge, direction, counter-evidence, and reliable independent sources"
        )
        return _report(
            "READY_FOR_TRADE_REVIEW",
            True,
            "HOLD_SHADOW_AND_REVIEW_TRADE_DECISION",
            reasons,
            validation,
            blockers,
            invalid_candidates,
            task_candidates,
            repair_applied,
            repaired_decision_grade_candidates,
            repaired_research_tasks,
            next_commands,
        )

    terminal_no_trade = (
        validation.decision_grade.terminal_untradeable > 0
        and validation.decision_grade.decision_grade_candidates == 0
        and validation.funnel.live_cache_eligible == 0
        and invalid_count == 0
        and repairable_task_count == 0
        and not blockers
        and not safety_failures
    )
    if terminal_no_trade:
        reasons.append(
            "research reached an explicit untradeable terminal decision; no trade is the decision"
        )
        return _report(
            "TERMINAL_NO_TRADE",
            True,
            "NO_TRADE",
            reasons,
            validation,
            blockers,
            invalid_candidates,
            task_candidates,
            repair_applied,
            repaired_decision_grade_candidates,
            repaired_research_tasks,
            next_commands,
        )

    if blockers or invalid_count or repairable_task_count:
        reasons.extend(_blocker_reasons(blockers))
        next_commands.extend(_research_commands(blockers))
        return _report(
            "CONTINUE_RESEARCH",
            False,
            "CONTINUE_SHADOW_RESEARCH",
            reasons or validation.reasons,
            validation,
            blockers,
            invalid_candidates,
            task_candidates,
            repair_applied,
            repaired_decision_grade_candidates,
            repaired_research_tasks,
            _dedupe(next_commands),
        )

    reasons.extend(validation.reasons)
    if not reasons:
        reasons.append(
            "research produced neither a trade-supporting dossier nor an explicit no-trade decision"
        )
    next_commands.append(
        "run shadow research prewarm until a dossier reaches decision_grade_candidate or untradeable"
    )
    return _report(
        "FAILED_NO_DECISION",
        False,
        "FAIL_CLOSED_NO_DECISION",
        reasons,
        validation,
        blockers,
        invalid_candidates,
        task_candidates,
        repair_applied,
        repaired_decision_grade_candidates,
        repaired_research_tasks,
        next_commands,
    )


def render_text(report: DecisionLoopReport) -> str:
    lines = [
        f"status: {report.status}",
        f"action: {report.action}",
        f"validation: {report.validation_verdict} / {report.validation_action}",
        (
            "decision_grade: "
            f"{report.decision_grade_candidates}, "
            f"live_cache_eligible: {report.live_cache_eligible}, "
            f"terminal_untradeable: {report.terminal_untradeable}"
        ),
    ]
    if report.research_blockers:
        lines.append(f"blockers: {json.dumps(report.research_blockers, sort_keys=True)}")
    if report.invalid_decision_grade_candidates or report.repairable_research_tasks:
        lines.append(
            "repairable: "
            f"invalid_decision_grade={report.invalid_decision_grade_candidates}, "
            f"research_tasks={report.repairable_research_tasks}"
        )
    if report.repair_applied:
        lines.append(
            "repair_applied: "
            f"invalid_decision_grade={report.repaired_decision_grade_candidates}, "
            f"research_tasks={report.repaired_research_tasks}"
        )
    if report.simulation_mode:
        lines.append(
            "simulation: "
            f"source={report.simulation_source_evidence_db}, "
            f"evidence_db={report.simulation_evidence_db}"
        )
    if report.reasons:
        lines.append("reasons:")
        lines.extend(f"- {reason}" for reason in report.reasons)
    if report.next_commands:
        lines.append("next:")
        lines.extend(f"- {command}" for command in report.next_commands)
    if report.actions_taken:
        lines.append("actions:")
        lines.extend(f"- {action}" for action in report.actions_taken)
    if report.action_errors:
        lines.append("action_errors:")
        lines.extend(f"- {error}" for error in report.action_errors)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Watch the shadow research loop until it yields a trade-review dossier "
            "or an explicit no-trade terminal decision."
        )
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
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument("--sleep-seconds", type=float, default=60.0)
    parser.add_argument(
        "--apply-repair",
        action="store_true",
        help=(
            "Mutate research_tasks to requeue invalid decision-grade and timeout "
            "task blockers before each evaluation cycle."
        ),
    )
    parser.add_argument(
        "--run-prewarm",
        action="store_true",
        help=(
            "After a non-terminal evaluation, run one shadow research prewarm "
            "cycle before the next evaluation. Requires --max-cycles > 1."
        ),
    )
    parser.add_argument(
        "--simulate-repair-prewarm",
        action="store_true",
        help=(
            "Copy the evidence DB to a temp path, then apply repair and run "
            "shadow prewarm on the copy only. Leaves runtime DB state untouched."
        ),
    )
    parser.add_argument("--prewarm-max-markets", type=int, default=10)
    parser.add_argument("--prewarm-max-pages", type=int, default=3)
    parser.add_argument("--prewarm-max-queries", type=int, default=7)
    parser.add_argument("--prewarm-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    simulation_mode = bool(args.simulate_repair_prewarm)
    simulation_source_evidence_db: Path | None = None
    simulation_evidence_db: Path | None = None
    if simulation_mode:
        simulation_source_evidence_db = _resolve(repo_root, args.evidence_db)
        simulation_evidence_db = _copy_evidence_db_for_simulation(
            simulation_source_evidence_db
        )
        args.evidence_db = simulation_evidence_db
        args.apply_repair = True
        args.run_prewarm = True
        args.sleep_seconds = 0.0
        args.max_cycles = max(2, int(args.max_cycles))
    report: DecisionLoopReport | None = None
    actions_taken: list[str] = []
    action_results: list[dict[str, Any]] = []
    action_errors: list[str] = []
    repair_applied = False
    repaired_decision_grade_candidates = 0
    repaired_research_tasks = 0
    max_cycles = max(1, args.max_cycles)
    for cycle in range(max_cycles):
        report = evaluate_research_decision_loop(
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
            apply_repair=args.apply_repair,
        )
        repair_applied = repair_applied or report.repair_applied
        repaired_decision_grade_candidates += report.repaired_decision_grade_candidates
        repaired_research_tasks += report.repaired_research_tasks
        if report.ok or cycle == max_cycles - 1:
            break
        if args.run_prewarm:
            prewarm_result = _run_shadow_prewarm(repo_root, args)
            actions_taken.append(prewarm_result["command"])
            action_results.append(_summarize_shadow_prewarm_result(prewarm_result))
            if prewarm_result["returncode"] != 0:
                action_errors.append(
                    "shadow prewarm failed "
                    f"exit={prewarm_result['returncode']} "
                    f"stderr={prewarm_result['stderr'][:500]}"
                )
                break
        time.sleep(max(0.0, args.sleep_seconds))

    assert report is not None
    if actions_taken or action_errors or repair_applied:
        report = replace(
            report,
            actions_taken=actions_taken,
            action_results=action_results,
            action_errors=action_errors,
            repair_applied=repair_applied,
            repaired_decision_grade_candidates=repaired_decision_grade_candidates,
            repaired_research_tasks=repaired_research_tasks,
            reasons=_replace_repair_applied_reason(
                report.reasons,
                repaired_decision_grade_candidates,
                repaired_research_tasks,
            )
            if repair_applied
            else report.reasons,
            ok=report.ok and not action_errors,
        )
    if simulation_mode:
        report = replace(
            report,
            simulation_mode=True,
            simulation_source_evidence_db=(
                str(simulation_source_evidence_db)
                if simulation_source_evidence_db is not None
                else None
            ),
            simulation_evidence_db=(
                str(simulation_evidence_db) if simulation_evidence_db is not None else None
            ),
        )
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report.ok else 1


def _report(
    status: DecisionLoopStatus,
    ok: bool,
    action: str,
    reasons: list[str],
    validation: ResearchProfitValidationReport,
    blockers: dict[str, int],
    invalid_candidates: list[InvalidDecisionGradeCandidate],
    task_candidates: list[RepairableResearchTask],
    repair_applied: bool,
    repaired_decision_grade_candidates: int,
    repaired_research_tasks: int,
    next_commands: list[str],
) -> DecisionLoopReport:
    return DecisionLoopReport(
        status=status,
        ok=ok,
        action=action,
        reasons=_dedupe(reasons),
        validation_verdict=validation.verdict,
        validation_action=validation.action,
        research_operating_cleanly=validation.research_operating_cleanly,
        research_supports_trades=validation.research_supports_trades,
        decision_grade_candidates=validation.decision_grade.decision_grade_candidates,
        live_cache_eligible=validation.funnel.live_cache_eligible,
        terminal_untradeable=validation.decision_grade.terminal_untradeable,
        research_blockers=blockers,
        invalid_decision_grade_candidates=len(invalid_candidates),
        repairable_research_tasks=len(task_candidates),
        repair_applied=repair_applied,
        repaired_decision_grade_candidates=repaired_decision_grade_candidates,
        repaired_research_tasks=repaired_research_tasks,
        repair_candidates=[
            asdict(candidate) for candidate in invalid_candidates[:20]
        ],
        task_repair_candidates=[
            asdict(candidate) for candidate in task_candidates[:20]
        ],
        next_commands=_dedupe(next_commands),
    )


def _blocker_reasons(blockers: dict[str, int]) -> list[str]:
    reasons: list[str] = []
    for name, count in sorted(blockers.items()):
        if name == "stale_but_researchable":
            reasons.append(
                f"{count} dossier(s) stale but researchable; keep queued for refresh"
            )
            continue
        if name == "blocked_by_ambiguous_direction":
            reasons.append(
                f"{count} dossier(s) have source coverage but no side/probability decision"
            )
            continue
        reasons.append(
            f"{count} dossier(s) blocked by {name.removeprefix('blocked_by_')}"
        )
    return reasons


def _research_commands(blockers: dict[str, int]) -> list[str]:
    commands: list[str] = []
    if any(
        blockers.get(name, 0)
        for name in (
            "blocked_by_no_counter_evidence",
            "blocked_by_neutral_evidence",
            "blocked_by_ambiguous_direction",
            "blocked_by_unresolved_contradiction",
        )
    ):
        commands.append(
            "queue disconfirming and independent-source queries for blocked dossiers"
        )
    if blockers.get("blocked_by_ambiguous_direction", 0):
        commands.append(
            "queue side/probability adjudication queries for ambiguous-direction dossiers"
        )
    if any(
        blockers.get(name, 0)
        for name in (
            "blocked_by_missing_price",
            "stale_but_researchable",
        )
    ):
        commands.append("refresh market_price and staleness_check evidence")
    if blockers.get("blocked_by_no_reliable_source_path", 0):
        commands.append(
            "refresh official/rules/resolution plus reputable secondary source paths"
        )
    if blockers.get("blocked_by_official_data_pending", 0):
        commands.append("refresh official settlement source after publication delay")
    if blockers.get("blocked_by_provider_error", 0):
        commands.append("rerun shadow research after provider cooldown")
    if blockers.get("blocked_by_generic_summary", 0):
        commands.append("regenerate evidence-specific reasoning summaries")
    if blockers.get("terminal_timeout_exhausted", 0):
        commands.append(
            "requeue timeout-exhausted tasks; timeout is not a trade decision"
        )
    return commands


def _run_shadow_prewarm(repo_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    command = _shadow_prewarm_command(repo_root, args)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=max(30.0, float(args.prewarm_timeout_seconds) * int(args.prewarm_max_markets) + 30.0),
    )
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _summarize_shadow_prewarm_result(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "command": result.get("command", ""),
        "returncode": int(result.get("returncode", 0) or 0),
    }
    stdout = str(result.get("stdout", "") or "").strip()
    if not stdout:
        summary["stdout_empty"] = True
        return summary
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        summary["stdout_parse_error"] = str(exc)
        summary["stdout_preview"] = stdout[:500]
        return summary
    if not isinstance(parsed, dict):
        summary["stdout_parse_error"] = "expected JSON object"
        return summary
    for key in ("markets", "attempted", "queries", "evidence", "statuses", "results"):
        if key in parsed:
            summary[key] = parsed[key]
    return summary


def _repair_applied_reason(
    repaired_decision_grade_candidates: int,
    repaired_research_tasks: int,
) -> str:
    return (
        "applied repair: "
        f"requeued {repaired_decision_grade_candidates} invalid decision-grade "
        f"candidate(s) and cleaned {repaired_research_tasks} research task(s)"
    )


def _replace_repair_applied_reason(
    reasons: list[str],
    repaired_decision_grade_candidates: int,
    repaired_research_tasks: int,
) -> list[str]:
    replacement = _repair_applied_reason(
        repaired_decision_grade_candidates,
        repaired_research_tasks,
    )
    updated: list[str] = []
    replaced = False
    for reason in reasons:
        if reason.startswith("applied repair: "):
            if not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(reason)
    if not replaced:
        updated.insert(0, replacement)
    return updated


def _shadow_prewarm_command(repo_root: Path, args: argparse.Namespace) -> list[str]:
    evidence_db = _resolve(repo_root, args.evidence_db)
    return [
        sys.executable,
        str(repo_root / "scripts" / "research_prewarm.py"),
        "--db-path",
        str(evidence_db),
        "--max-markets",
        str(max(1, int(args.prewarm_max_markets))),
        "--max-pages",
        str(max(1, int(args.prewarm_max_pages))),
        "--max-queries",
        str(max(1, int(args.prewarm_max_queries))),
        "--timeout-seconds",
        str(max(1.0, float(args.prewarm_timeout_seconds))),
        "--json",
        "--include-results",
        "--no-trade-log",
    ]


def _copy_evidence_db_for_simulation(evidence_db: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="research-decision-loop-"))
    copied_db = temp_dir / evidence_db.name
    if evidence_db.exists():
        shutil.copy2(evidence_db, copied_db)
    return copied_db


def _safety_failures(validation: ResearchProfitValidationReport) -> list[str]:
    failures: list[str] = []
    if validation.risk.unauthorized_live_orders:
        failures.append(
            f"unauthorized live orders observed: {validation.risk.unauthorized_live_orders}"
        )
    if validation.runtime.error_critical_count:
        failures.append(
            f"runtime ERROR/CRITICAL observed: {validation.runtime.error_critical_count}"
        )
    if validation.runtime.within_cooldown_repeats:
        failures.append(
            f"cooldown repeats observed: {validation.runtime.within_cooldown_repeats}"
        )
    if validation.workflow.normal_gate_ok is False:
        failures.append("normal workflow gate failed")
    return failures


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
