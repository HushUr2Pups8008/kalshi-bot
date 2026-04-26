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
import json
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


from typing import Iterable, Sequence

from governance.adapter import GovernanceAdapter
from governance.evidence import (
    Candidate,
    select_candidates_for_cadence,
)
from governance.llm import LLMClient


def run_cycle(
    *,
    cadence: str,
    loaded_state: AgentLoadedState,
    adapter: GovernanceAdapter,
    llm: LLMClient | None,
    audit_logger: AuditLogger,
    overrides_path: Path,
    candidate_override: Sequence[Candidate] | None = None,
    safety_config: SafetyConfig | None = None,
) -> int:
    """Run one governance cycle. Returns process exit code.

    Phase 2 Task 17: emits CYCLE_START / CYCLE_END only; no LLM iteration
    yet. Task 18 fills in the LLM-driven decision loop.
    """
    now = datetime.now(timezone.utc)
    cycle_id = generate_cycle_id(now=now)

    # AuditLogger's record-append method is .append(), not .write() (the
    # plan referenced .write throughout — Task 17 drift note in plan).
    audit_logger.append({
        "type": "GOVERNANCE_CYCLE_START",
        "cycle_id": cycle_id,
        "cadence": cadence,
        "mode": loaded_state.mode,
        "started_at": now.isoformat(),
    })

    cycle_start = now
    if candidate_override is not None:
        candidates: list[Candidate] = list(candidate_override)
    else:
        audit_data = adapter.collect_audit_data(window=_cadence_window(cadence))
        candidates = list(select_candidates_for_cadence(
            audit_data, cadence=cadence,
        ))

    decisions_made = 0
    decisions_applied = 0
    decisions_proposed = 0

    # Task 18 will fill in the per-candidate LLM iteration here.

    duration_sec = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    audit_logger.append({
        "type": "GOVERNANCE_CYCLE_END",
        "cycle_id": cycle_id,
        "duration_sec": duration_sec,
        "decisions_made": decisions_made,
        "decisions_applied": decisions_applied,
        "decisions_proposed": decisions_proposed,
        "batch_aborted": False,
    })
    return 0


def _cadence_window(cadence: str):
    from datetime import timedelta
    return {
        "fast": timedelta(hours=24),
        "deep": timedelta(days=7),
        "weekly_review": timedelta(days=30),
    }.get(cadence, timedelta(hours=24))
