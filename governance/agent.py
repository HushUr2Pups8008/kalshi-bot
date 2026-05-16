"""Governance agent — orchestration loop.

CLI entry point:
    python -m governance --cadence fast|deep|weekly_review

Reads runtime overrides + checks kill switches; if clean, builds evidence
via the adapter, asks the LLM for decisions on candidates, validates and
either applies (real mode + above-threshold confidence) or proposes
(shadow mode or below-threshold) per spec §8.

Tasks 17-21 layer on top of this skeleton.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from governance.audit import AuditLogger
from governance.safety import KillSwitch, SafetyConfig
from utils.runtime_overrides import (
    OverridesState,
    RuntimeOverridesReader,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OVERRIDES_PATH = REPO_ROOT / "data" / "runtime_overrides.yaml"
DEFAULT_DECISIONS_LOG_DIR = REPO_ROOT / "logs" / "governance"
DEFAULT_TRADE_LOG_PATH = REPO_ROOT / "logs" / "trades"
DEFAULT_PAPER_DB_PATH = REPO_ROOT / "data" / "paper_trades.db"


class KillSwitchActive(RuntimeError):
    """Raised by load_state when GOVERNANCE_DISABLED is in effect."""


@dataclass(frozen=True)
class AgentLoadedState:
    """The agent's loaded state: parsed overrides + kill-switch status."""
    reader: RuntimeOverridesReader
    state: OverridesState
    mode: str  # "shadow" or "real"
    kill_switch_disabled: bool
    kill_switch_readonly: bool


def generate_decision_id(*, now: datetime, sequence: int) -> str:
    return f"gd_{now.strftime('%Y-%m-%d')}_{sequence:04d}"


def generate_batch_id(*, now: datetime, sequence: int) -> str:
    return f"gb_{now.strftime('%Y-%m-%d')}_{sequence:04d}"


def generate_cycle_id(*, now: datetime) -> str:
    return f"gc_{now.strftime('%Y-%m-%d_%H%M%S')}"


def load_state(*, overrides_path: Path) -> AgentLoadedState:
    """Load runtime overrides + check kill switches.

    Raises KillSwitchActive when GOVERNANCE_DISABLED is set; the caller
    is expected to log the event and exit. GOVERNANCE_READONLY is surfaced
    on the returned object — the caller decides what 'readonly' means
    in context (Phase 2: agent runs through the cycle, never writes
    `applied`; Phase 3+: agent runs but cannot promote shadow → real).
    """
    # KillSwitch's actual API: instance methods is_disabled() / is_readonly().
    # The plan referenced a from_env() factory that does not exist; the
    # zero-arg constructor + method calls is the supported path per
    # governance/safety.py. Recorded in PROFIT-LLM-001-style note at the
    # end of Task 16 in the plan.
    ks = KillSwitch()
    if ks.is_disabled():
        raise KillSwitchActive(
            "GOVERNANCE_DISABLED is set — agent refuses to run"
        )
    reader = RuntimeOverridesReader(path=overrides_path)
    reader.reload()
    # snapshot is a method on RuntimeOverridesReader (not a property).
    state_now = reader.snapshot()
    return AgentLoadedState(
        reader=reader,
        state=state_now,
        mode=state_now.mode,
        kill_switch_disabled=False,
        kill_switch_readonly=ks.is_readonly(),
    )


from typing import Sequence

from governance.adapter import GovernanceAdapter
from governance.evidence import (
    Candidate,
    select_candidates_for_cadence,
)
from governance.llm import LLMClient


def run_cycle(
    *,
    cadence: str,
    run_source: str = "manual",
    loaded_state: AgentLoadedState,
    adapter: GovernanceAdapter,
    llm: LLMClient | None,
    audit_logger: AuditLogger,
    overrides_path: Path,
    candidate_override: Sequence[Candidate] | None = None,
    audit_data_override: dict[str, Any] | None = None,
    safety_config: SafetyConfig | None = None,
) -> int:
    """Run one governance cycle. Returns process exit code.

    Phase 2 Task 18: full LLM-driven candidate iteration. The agent
    composes evidence per candidate, renders a prompt, calls the LLM,
    parses the response into a Decision, evaluates safety, and writes
    a GOVERNANCE_DECISION audit record. Shadow mode forces applied=False
    on every decision; the safety_checks fields still reflect the
    would-have-applied state.
    """
    now = datetime.now(timezone.utc)
    cycle_id = generate_cycle_id(now=now)

    # AuditLogger's record-append method is .append(), not .write() (the
    # plan referenced .write throughout — Task 17 drift note in plan).
    audit_logger.append({
        "type": "GOVERNANCE_CYCLE_START",
        "cycle_id": cycle_id,
        "cadence": cadence,
        "run_source": run_source,
        "mode": loaded_state.mode,
        "started_at": now.isoformat(),
    })

    cycle_start = now
    if audit_data_override is not None:
        audit_data = audit_data_override
    elif candidate_override is None:
        audit_data = adapter.collect_audit_data(window=_cadence_window(cadence))
    else:
        audit_data = {}

    if candidate_override is not None:
        candidates: list[Candidate] = list(candidate_override)
    else:
        candidates = list(select_candidates_for_cadence(
            audit_data, cadence=cadence,
        ))

    # SafetyConfig.from_env() is referenced in the plan but does not exist;
    # SafetyConfig() with built-in defaults is the supported instantiation
    # per governance/safety.py. Documented in the Task 18 drift note.
    safety = safety_config or SafetyConfig()
    batch_id = generate_batch_id(now=now, sequence=1)
    decision_seq = 0

    decisions_made = 0
    decisions_applied = 0
    decisions_proposed = 0

    from governance.evidence import (
        compose_evidence_for_candidate,
        summarize_evidence_for_audit,
    )
    from governance.llm import (
        LLMResponseParseError,
        parse_llm_response_to_decision,
    )
    from governance.prompts import render_prompt

    for cand in candidates:
        if llm is None:
            break  # cycle without an LLM is a no-op shape-only test path
        decision_seq += 1
        evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
        sys_p, user_p = render_prompt(cand.action, evidence)
        try:
            raw = llm.complete(sys_p, user_p)
            decided_at = datetime.now(timezone.utc)
            decision = parse_llm_response_to_decision(
                raw,
                decision_id=generate_decision_id(now=decided_at, sequence=decision_seq),
                batch_id=batch_id,
                decided_at=decided_at,
                decided_by="governance-agent-v0.2.0",
                cadence=cadence,
                model_used=llm.model_name(),
                evidence_summary=summarize_evidence_for_audit(evidence),
            )
        except LLMResponseParseError as exc:
            audit_logger.append({
                "type": "GOVERNANCE_DECISION_PARSE_ERROR",
                "cycle_id": cycle_id,
                "candidate_action": cand.action,
                "candidate_target": cand.target,
                "error": str(exc),
            })
            continue
        except ValueError as exc:
            audit_logger.append({
                "type": "GOVERNANCE_DECISION_VALIDATION_ERROR",
                "cycle_id": cycle_id,
                "candidate_action": cand.action,
                "candidate_target": cand.target,
                "error": str(exc),
            })
            continue

        decisions_made += 1

        applied, shadow_mode, safety_checks = _evaluate_safety(
            decision=decision,
            mode=loaded_state.mode,
            kill_switch_readonly=loaded_state.kill_switch_readonly,
            safety=safety,
            applied_so_far=decisions_applied,
        )
        if applied:
            decisions_applied += 1
        else:
            decisions_proposed += 1

        audit_logger.append(decision.to_audit_record(
            applied=applied,
            shadow_mode=shadow_mode,
            safety_checks_passed=safety_checks,
        ))

        # Phase 3 will gate the following on applied=True:
        # new_state = state.with_applied_added([d.to_disabled_source() for d in applied_decisions])
        # atomic_write_state(new_state, overrides_path)
        # Phase 2: load-bearing safety property is `applied` is False everywhere
        # in shadow mode (mode != "real" or kill_switch_readonly), so this
        # branch never fires. The Task 19 regression test in
        # tests/test_governance_agent_unit.py guards that invariant.

    duration_sec = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    audit_logger.append({
        "type": "GOVERNANCE_CYCLE_END",
        "cycle_id": cycle_id,
        "cadence": cadence,
        "run_source": run_source,
        "duration_sec": duration_sec,
        "decisions_made": decisions_made,
        "decisions_applied": decisions_applied,
        "decisions_proposed": decisions_proposed,
        "batch_aborted": False,
    })
    return 0


def _evaluate_safety(
    *,
    decision,
    mode: str,
    kill_switch_readonly: bool,
    safety: SafetyConfig,
    applied_so_far: int,
) -> tuple[bool, bool, dict[str, bool]]:
    """Returns (applied, shadow_mode, safety_checks_passed_dict).

    Phase 2: shadow_mode is True iff mode != 'real'. Even in real mode,
    GOVERNANCE_READONLY=true demotes to shadow. no_action decisions never
    apply. confidence below threshold never applies. max_changes_per_run
    caps the number of applied decisions per cycle.
    """
    if decision.action == "no_action":
        return False, mode != "real", {
            "confidence_threshold": True,
            "max_changes_per_run": True,
            "blast_radius": True,
            "kill_switch": True,
        }
    confidence_ok = decision.confidence >= safety.confidence_threshold
    cap_ok = applied_so_far < safety.max_changes_per_run
    eligible_for_apply = (
        mode == "real" and not kill_switch_readonly and confidence_ok and cap_ok
    )
    safety_checks = {
        "confidence_threshold": confidence_ok,
        "max_changes_per_run": cap_ok,
        "blast_radius": True,  # Phase 3 enforces; in Phase 2 it always passes
        "kill_switch": not kill_switch_readonly,
    }
    shadow_mode = (mode != "real") or kill_switch_readonly
    return eligible_for_apply, shadow_mode, safety_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="governance",
        description="Run a governance agent cycle.",
    )
    parser.add_argument(
        "--cadence",
        choices=["fast", "deep", "weekly_review"],
        required=True,
    )
    parser.add_argument(
        "--llm",
        choices=["fake", "qwen"],
        default="qwen",
        help="Which LLM to use (fake for tests, qwen for production).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run cycle but skip writing the audit log final flush "
             "(used by smoke tests).",
    )
    parser.add_argument(
        "--run-source",
        choices=["launchd", "manual", "smoke"],
        default=os.getenv("GOVERNANCE_RUN_SOURCE", "manual"),
        help=(
            "Operational source for cycle audit records. launchd cycles are "
            "eligible for Gate 5 cadence metrics; manual/smoke cycles are "
            "operator evidence only."
        ),
    )
    args = parser.parse_args(argv)

    overrides_path = Path(os.getenv("GOVERNANCE_OVERRIDES_PATH", str(DEFAULT_OVERRIDES_PATH)))
    logs_dir = Path(os.getenv("GOVERNANCE_LOGS_DIR", str(DEFAULT_DECISIONS_LOG_DIR)))
    trade_log_path = Path(os.getenv("GOVERNANCE_TRADE_LOG_PATH", str(DEFAULT_TRADE_LOG_PATH)))
    paper_db_path = Path(os.getenv("GOVERNANCE_PAPER_DB_PATH", str(DEFAULT_PAPER_DB_PATH)))

    try:
        loaded = load_state(overrides_path=overrides_path)
    except KillSwitchActive:
        return 2

    from governance.adapter import KalshiGovernanceAdapter
    from governance.llm import FakeLLM, LocalQwenLLM
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log_path,
        paper_db_path=paper_db_path,
        market_provider=None,
    )
    llm = FakeLLM() if args.llm == "fake" else LocalQwenLLM()
    # AuditLogger uses keyword-only log_dir per Task 17 drift note.
    audit_logger = AuditLogger(log_dir=logs_dir)

    return run_cycle(
        cadence=args.cadence,
        run_source=args.run_source,
        loaded_state=loaded,
        adapter=adapter,
        llm=llm,
        audit_logger=audit_logger,
        overrides_path=overrides_path,
    )


def _cadence_window(cadence: str):
    from datetime import timedelta
    return {
        "fast": timedelta(hours=24),
        "deep": timedelta(days=7),
        "weekly_review": timedelta(days=30),
    }.get(cadence, timedelta(hours=24))
