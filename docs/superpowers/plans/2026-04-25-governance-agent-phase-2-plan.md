# Governance Agent Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only LLM governance agent that runs in shadow mode, produces real governance decisions about source/keyword/threshold management, and writes them to `proposed` (never `applied`) for ≥14 days while a trust dataset accumulates.

**Architecture:** A new `governance/` package with a `KalshiGovernanceAdapter` Protocol seam, an `Evidence` builder that composes prompt context from existing diagnostic-script library functions, a `Decision` dataclass with mandatory `predicted_effect` validation, prompt templates, a local-LLM (Qwen3) wrapper plus a `FakeLLM` test double, and an `agent.py` orchestration loop invoked via `python -m governance --cadence fast|deep|weekly_review`. Cadence is wired via launchd plists. Output goes to two filesystem contracts: `data/runtime_overrides.yaml` (already implemented in Phase 1) for `applied` / `proposed` entries, and `logs/governance/decisions.jsonl` (already implemented in Phase 1's `governance/audit.py`) for the append-only audit trail.

**Tech Stack:** Python 3.14+, `dataclasses`, `typing.Protocol`, `urllib.request` (Ollama HTTP), `pyyaml` (Phase 1 dependency), `hypothesis` for property tests. Reuses Phase 1's `utils/runtime_overrides.py`, `governance/safety.py`, `governance/audit.py`. No new third-party dependencies.

**Phase 1 dependency:** This plan assumes `feat/governance-phase-1-plumbing` (MR #1, v0.29.52) is merged to `main`. If not yet merged, complete that merge first — the modules below are imported throughout this plan.

**Hardware dependency:** Final shadow-mode soak (Task 26) requires the Mac Studio (post-2026-04-29). All earlier tasks ship and test against `FakeLLM` and run on any platform.

---

## Prerequisites

The following components from Phase 1 must exist on the branch this plan is executed on:

- `governance/safety.py` — `SafetyConfig` dataclass, `KillSwitch` class.
- `governance/audit.py` — `AuditLogger` (append-only JSONL writer with daily rotation + gzip-after-7-days).
- `utils/runtime_overrides.py` — `RuntimeOverridesReader`, `OverridesState`, `DisabledSource`, `DisabledKeyword`, `ThresholdOverride`, `PredictedEffect`, `atomic_write_state`, module-level helpers.
- `tasks/runtime_overrides_task.py` — asyncio poll task (not used by the agent, but its presence indicates Phase 1 plumbing is complete).
- `data/runtime_overrides.yaml` — file format and write contract.

Verify before starting:

```bash
python -c "from governance.safety import SafetyConfig, KillSwitch; from governance.audit import AuditLogger; from utils.runtime_overrides import OverridesState, atomic_write_state, RuntimeOverridesReader; print('Phase 1 imports OK')"
```

Expected output: `Phase 1 imports OK`. If this errors, stop and merge Phase 1 first.

---

## File Structure

Files this plan creates (NEW) or modifies (MODIFY):

| Path | Action | Responsibility |
|---|---|---|
| `scripts/__init__.py` | NEW | Make `scripts/` importable as a package; no exports — modules import via `scripts.<module>`. |
| `governance/decision.py` | NEW | `Decision` dataclass, validators, `to_audit_record()` / `to_disabled_source()` / `to_disabled_keyword()` / `to_threshold_override()` converters. |
| `governance/adapter.py` | NEW | `GovernanceAdapter` `Protocol` + `KalshiGovernanceAdapter` implementation. The bot-agnostic seam (decision 9 in spec). |
| `governance/evidence.py` | NEW | Pure functions: `compose_evidence_for_candidate()`, `select_candidates_for_cadence()`, `summarize_evidence_for_audit()`. |
| `governance/prompts.py` | NEW | System prompt + per-action user-prompt templates + `render_prompt(decision_type, evidence) -> tuple[str, str]`. |
| `governance/llm.py` | NEW | `LLMClient` Protocol + `FakeLLM` test double + `LocalQwenLLM` Ollama wrapper + `parse_llm_response_to_decision()`. |
| `governance/agent.py` | NEW | CLI entry point (`python -m governance`) + `run_cycle()` orchestration loop. |
| `governance/__main__.py` | NEW | One-liner: `from governance.agent import main; raise SystemExit(main())`. |
| `tests/test_governance_decision.py` | NEW | Decision dataclass invariants, validation, conversion methods. |
| `tests/test_governance_adapter.py` | NEW | `KalshiGovernanceAdapter` satisfies `GovernanceAdapter`; per-method behavior on a temp trade log. |
| `tests/test_governance_evidence.py` | NEW | Candidate selection per cadence; per-candidate evidence composition. |
| `tests/test_governance_prompts.py` | NEW | Snapshot tests for rendered prompts; schema-instructions stable. |
| `tests/test_governance_llm.py` | NEW | `FakeLLM` records calls and returns canned responses; `parse_llm_response_to_decision` happy + error paths. |
| `tests/test_governance_agent_integration.py` | NEW | End-to-end one-cycle test against `FakeLLM` in shadow mode; verifies no `applied` writes. |
| `tests/test_governance_agent_chaos.py` | NEW | Kill-switch trip, malformed LLM JSON, atomic-write failure, mode-mismatch. |
| `tests/test_governance_agent_property.py` | NEW | Hypothesis: invariants over arbitrary FakeLLM-response sequences + safety configs. |
| `tests/test_scripts_package.py` | NEW | Verifies `scripts/` import surface (no CLI side effects on import). |
| `ops/launchd/com.kalshi.governance.fast.plist` | NEW | macOS launchd: every 2h. |
| `ops/launchd/com.kalshi.governance.deep.plist` | NEW | macOS launchd: daily at fixed UTC time. |
| `docs/governance/PHASE2_RUNBOOK.md` | NEW | Operator manual: install plists, kill-switch operations, soak monitoring, troubleshooting. |
| `VERSION` | MODIFY | Patch bump (next available; depends on merge order — see Task 26). |
| `CHANGELOG.md` | MODIFY | Phase 2 entry. |
| `requirements-dev.txt` | (unchanged) | `hypothesis` already present from Phase 1. |

The `scripts/` audit modules (`source_market_alignment_audit.py`, `keyword_feedback.py`, `reddit_source_audit.py`, `freshness_diagnostics.py`) already have well-separated lib/CLI structure (`aggregate()` / `summarize()` / `collect()` are pure-function exports; `parse_args()` and `main()` are CLI plumbing). Task 1 only adds `scripts/__init__.py` and verifies that nothing breaks when imported as a package — no rewrite of the audit scripts themselves.

---

## Task 1: `scripts/__init__.py` — make `scripts/` importable as a package

**Files:**
- Create: `scripts/__init__.py`
- Test: `tests/test_scripts_package.py`

**Why this is first:** `governance/evidence.py` (Task 8) imports four audit modules from `scripts.*`. The current `scripts/` directory has no `__init__.py`, so it's a namespace package by default. This works for `python -m scripts.source_market_alignment_audit` (where the script's own `sys.path` shenanigans add `REPO_ROOT`) but is fragile when imported from another package whose entry path doesn't include the bot's repo root. Adding an explicit `__init__.py` formalizes the package boundary and lets us assert no CLI side effects fire on import.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scripts_package.py`:

```python
"""Verify scripts/ is a proper Python package with importable library functions
and no side effects (no argparse, no I/O, no print) at import time."""

from __future__ import annotations

import io
import sys


def test_scripts_is_a_package():
    import scripts

    assert hasattr(scripts, "__path__"), "scripts should be a regular package"


def test_audit_libs_importable_without_side_effects(capsys):
    # Importing should not parse argv, write to stdout, or open files.
    captured_before = capsys.readouterr()

    from scripts import source_market_alignment_audit  # noqa: F401
    from scripts import keyword_feedback  # noqa: F401
    from scripts import reddit_source_audit  # noqa: F401
    from scripts import freshness_diagnostics  # noqa: F401

    captured_after = capsys.readouterr()
    assert captured_after.out == captured_before.out, (
        "import of audit modules must be silent on stdout"
    )
    assert captured_after.err == captured_before.err, (
        "import of audit modules must be silent on stderr"
    )


def test_audit_libs_expose_pure_aggregator_functions():
    """Each audit module must expose a documented pure-function entry point
    that returns a structured dict — this is the surface governance/evidence.py
    consumes."""
    from scripts import source_market_alignment_audit
    from scripts import keyword_feedback
    from scripts import reddit_source_audit
    from scripts import freshness_diagnostics

    assert callable(getattr(source_market_alignment_audit, "aggregate", None))
    assert callable(getattr(keyword_feedback, "summarize", None))
    assert callable(getattr(reddit_source_audit, "collect", None))
    assert callable(getattr(freshness_diagnostics, "summarize", None))
```

- [ ] **Step 2: Run the test — expect failure**

Run: `pytest tests/test_scripts_package.py -v`

Expected: `test_scripts_is_a_package` may pass (namespace package has `__path__`); the side-effect test should pass too (the audit scripts only do work in `main()` — verified in setup); `test_audit_libs_expose_pure_aggregator_functions` should pass (all four exports exist per the API survey done before this plan).

If the side-effect test fails, identify which module prints/parses on import and add a `if __name__ == "__main__":` guard around the offending code.

- [ ] **Step 3: Create `scripts/__init__.py`**

Write `scripts/__init__.py`:

```python
"""kalshi-bot diagnostic scripts.

Each module under this package has a dual life:
- `python -m scripts.<name>` for CLI use (parses argv, writes to stdout)
- `from scripts.<name> import <function>` for library use by the governance
  agent (pure functions returning structured data)

Library functions guaranteed stable for governance/evidence.py:
- scripts.source_market_alignment_audit.aggregate(...)
- scripts.keyword_feedback.summarize(...)
- scripts.reddit_source_audit.collect(...)
- scripts.freshness_diagnostics.summarize(...)
"""
```

- [ ] **Step 4: Re-run tests; expect all pass**

Run: `pytest tests/test_scripts_package.py -v`

Expected: 3 passed.

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -q`

Expected: prior pass count + 3 (or unchanged if any test reorder; key thing is **zero failures**).

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py tests/test_scripts_package.py
git commit -m "feat(scripts): formalize package + verify import is side-effect-free

Adds scripts/__init__.py so governance/evidence.py (Phase 2 Task 8)
can rely on a proper package boundary rather than the implicit
namespace-package + sys.path manipulation each script does for CLI use.

New test asserts: (1) the package imports clean; (2) no CLI side
effects fire when imported as a library; (3) the four pure-function
entry points the agent depends on (aggregate, summarize, collect,
summarize) all exist and are callable.

No change to the existing audit scripts."
```

---

## Task 2: `governance/decision.py` — `Decision` dataclass scaffold

**Files:**
- Create: `governance/decision.py`
- Test: `tests/test_governance_decision.py`

**Why this is next:** Every other module in the agent (adapter consumes it, evidence formats it, prompts target it, llm parses into it, agent serializes it) depends on the `Decision` shape. Land it first with the smallest viable surface; add validators in Task 3 and converters in Task 4.

- [ ] **Step 1: Write the failing test for the dataclass surface**

Create `tests/test_governance_decision.py`:

```python
"""Decision dataclass — surface, validation, and conversions."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from governance.decision import Decision, PredictedEffect, VALID_ACTIONS


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 5, 9, 14, 30, 0, tzinfo=timezone.utc)


def _ok_predicted_effect() -> PredictedEffect:
    return PredictedEffect(
        metric="reddit_rate_limit_budget_consumed_daily",
        baseline=0.12,
        predicted_post_change=0.08,
        evaluate_at=_LATER,
    )


def _ok_decision(**overrides) -> Decision:
    defaults = dict(
        decision_id="gd_2026-05-02_0042",
        batch_id="gb_2026-05-02_0012",
        decided_at=_NOW,
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        action="disable_source",
        target="r/Turkey",
        proposed_change={"before": "source_active", "after": "source_disabled", "expires_at": None},
        confidence=0.94,
        reasoning="Test reasoning. Sufficient detail.",
        evidence_summary={"ingestion_events": 408, "match_count": 0},
        predicted_effect=_ok_predicted_effect(),
        model_used="qwen3-14b-instruct",
        escalated_to_claude=False,
        claude_response=None,
    )
    defaults.update(overrides)
    return Decision(**defaults)


def test_decision_constructs_with_valid_fields():
    d = _ok_decision()
    assert d.decision_id == "gd_2026-05-02_0042"
    assert d.action == "disable_source"
    assert d.confidence == 0.94
    assert d.predicted_effect.metric == "reddit_rate_limit_budget_consumed_daily"


def test_decision_is_frozen():
    d = _ok_decision()
    with pytest.raises(Exception):  # FrozenInstanceError on dataclasses; broad catch is fine
        d.confidence = 0.0  # type: ignore[misc]


def test_valid_actions_set_is_immutable_export():
    assert "disable_source" in VALID_ACTIONS
    assert "disable_keyword" in VALID_ACTIONS
    assert "tune_threshold" in VALID_ACTIONS
    assert "no_action" in VALID_ACTIONS


def test_predicted_effect_holds_all_required_fields():
    pe = _ok_predicted_effect()
    assert pe.metric
    assert isinstance(pe.baseline, float)
    assert isinstance(pe.predicted_post_change, float)
    assert pe.evaluate_at.tzinfo is not None
```

- [ ] **Step 2: Run the test — expect ImportError on `governance.decision`**

Run: `pytest tests/test_governance_decision.py -v`

Expected: `ModuleNotFoundError: No module named 'governance.decision'`.

- [ ] **Step 3: Create the dataclass module**

Write `governance/decision.py`:

```python
"""Governance agent Decision dataclass.

A Decision is the agent's output for a single (target, action) pair within a
batch. The agent emits Decisions; the runtime-overrides reader and the audit
logger consume them. Validation is intentionally strict: a malformed Decision
must fail fast at construction, not at apply-time when it could corrupt state.

Decision IDs follow the format `gd_YYYY-MM-DD_NNNN` (matches the spec §6.2
example). Batch IDs follow `gb_YYYY-MM-DD_NNNN`. ID format is enforced in
Task 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

VALID_ACTIONS: frozenset[str] = frozenset(
    {"disable_source", "disable_keyword", "tune_threshold", "no_action"}
)
VALID_CADENCES: frozenset[str] = frozenset({"fast", "deep", "weekly_review"})


@dataclass(frozen=True)
class PredictedEffect:
    """Per-decision prediction for outcome tracking. Mandatory per spec §10
    and §4 decision 10. The agent's quality is measured against these
    predictions over time."""
    metric: str
    baseline: float
    predicted_post_change: float
    evaluate_at: datetime


@dataclass(frozen=True)
class Decision:
    """One governance decision: agent says 'do X to target Y because Z'.

    `applied` and `shadow_mode` are runtime metadata applied at write time
    (see `to_audit_record()` in Task 4); they are NOT fields of the Decision
    itself, since the same Decision object can be evaluated under different
    safety configs and produce different applied/shadow outcomes.
    """
    decision_id: str
    batch_id: str
    decided_at: datetime
    decided_by: str
    cadence: Literal["fast", "deep", "weekly_review"]
    action: Literal["disable_source", "disable_keyword", "tune_threshold", "no_action"]
    target: str
    proposed_change: dict[str, Any]
    confidence: float
    reasoning: str
    evidence_summary: dict[str, Any]
    predicted_effect: PredictedEffect | None
    model_used: str
    escalated_to_claude: bool = False
    claude_response: dict[str, Any] | None = None
```

- [ ] **Step 4: Re-run tests; expect all four pass**

Run: `pytest tests/test_governance_decision.py -v`

Expected: 4 passed. (Validation/conversion tests are added in Tasks 3 and 4.)

- [ ] **Step 5: Commit**

```bash
git add governance/decision.py tests/test_governance_decision.py
git commit -m "feat(governance): Decision dataclass scaffold (Phase 2 Task 2)

Frozen dataclasses for Decision and PredictedEffect, plus the
VALID_ACTIONS / VALID_CADENCES frozensets that the prompt-renderer
and the agent's safety check both consume. No validation yet — Task 3
adds __post_init__ checks."
```

---

## Task 3: `governance/decision.py` — strict validation

**Files:**
- Modify: `governance/decision.py`
- Modify: `tests/test_governance_decision.py`

**Why now:** A Decision that violates an invariant (confidence outside [0, 1]; `evaluate_at` already in the past; an unknown action; a malformed `decision_id`) must fail at construction. If we let the agent build invalid Decisions and only catch them at apply-time, we risk corrupting the runtime overrides file or skewing the audit log. This mirrors the `OverridesState.__post_init__` discipline from Phase 1.

- [ ] **Step 1: Write failing tests for each invariant**

Append to `tests/test_governance_decision.py`:

```python
import re


def test_decision_rejects_confidence_above_one():
    with pytest.raises(ValueError, match="confidence"):
        _ok_decision(confidence=1.01)


def test_decision_rejects_confidence_below_zero():
    with pytest.raises(ValueError, match="confidence"):
        _ok_decision(confidence=-0.01)


def test_decision_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        _ok_decision(action="set_market_position")  # not in VALID_ACTIONS


def test_decision_rejects_unknown_cadence():
    with pytest.raises(ValueError, match="cadence"):
        _ok_decision(cadence="hourly")


def test_decision_rejects_malformed_decision_id():
    with pytest.raises(ValueError, match="decision_id"):
        _ok_decision(decision_id="not-a-valid-id")


def test_decision_rejects_malformed_batch_id():
    with pytest.raises(ValueError, match="batch_id"):
        _ok_decision(batch_id="batch_001")


def test_decision_requires_predicted_effect_for_action_decisions():
    """no_action decisions may have predicted_effect=None; action decisions
    must have one (per spec §10 / decision 10 — outcome-tracking is
    mandatory for any change the agent proposes)."""
    with pytest.raises(ValueError, match="predicted_effect"):
        _ok_decision(action="disable_source", predicted_effect=None)


def test_decision_allows_no_action_with_null_predicted_effect():
    d = _ok_decision(
        action="no_action",
        predicted_effect=None,
        proposed_change={},
        target="",
    )
    assert d.action == "no_action"
    assert d.predicted_effect is None


def test_decision_rejects_evaluate_at_at_or_before_decided_at():
    with pytest.raises(ValueError, match="evaluate_at"):
        bad_pe = PredictedEffect(
            metric="m",
            baseline=0.0,
            predicted_post_change=0.0,
            evaluate_at=_NOW,  # not strictly after decided_at
        )
        _ok_decision(predicted_effect=bad_pe)


def test_decision_rejects_naive_decided_at():
    naive = datetime(2026, 5, 2, 14, 30, 0)  # no tzinfo
    with pytest.raises(ValueError, match="decided_at"):
        _ok_decision(decided_at=naive)


def test_decision_rejects_naive_evaluate_at():
    naive = datetime(2026, 5, 9, 14, 30, 0)
    bad_pe = PredictedEffect(
        metric="m", baseline=0.0, predicted_post_change=0.0, evaluate_at=naive,
    )
    with pytest.raises(ValueError, match="evaluate_at"):
        _ok_decision(predicted_effect=bad_pe)


def test_decision_rejects_empty_reasoning():
    with pytest.raises(ValueError, match="reasoning"):
        _ok_decision(reasoning="")


def test_decision_rejects_target_empty_for_action_decisions():
    with pytest.raises(ValueError, match="target"):
        _ok_decision(action="disable_source", target="")
```

- [ ] **Step 2: Run new tests — expect all to fail**

Run: `pytest tests/test_governance_decision.py -v -k "rejects or allows"`

Expected: every "rejects" test fails because no validation exists yet; the "allows_no_action_with_null_predicted_effect" test fails for the same reason.

- [ ] **Step 3: Add `__post_init__` validators**

Edit `governance/decision.py`. Add this import at the top:

```python
import re
```

Add a module-level regex below the constants:

```python
_DECISION_ID_RE = re.compile(r"^gd_\d{4}-\d{2}-\d{2}_\d{4}$")
_BATCH_ID_RE = re.compile(r"^gb_\d{4}-\d{2}-\d{2}_\d{4}$")
```

Add `__post_init__` to `PredictedEffect`:

```python
@dataclass(frozen=True)
class PredictedEffect:
    metric: str
    baseline: float
    predicted_post_change: float
    evaluate_at: datetime

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("PredictedEffect.metric must be non-empty")
        if self.evaluate_at.tzinfo is None:
            raise ValueError("PredictedEffect.evaluate_at must be tz-aware (use UTC)")
```

Add `__post_init__` to `Decision`:

```python
    def __post_init__(self) -> None:
        if not _DECISION_ID_RE.match(self.decision_id):
            raise ValueError(
                f"decision_id {self.decision_id!r} must match gd_YYYY-MM-DD_NNNN"
            )
        if not _BATCH_ID_RE.match(self.batch_id):
            raise ValueError(
                f"batch_id {self.batch_id!r} must match gb_YYYY-MM-DD_NNNN"
            )
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must be tz-aware (use UTC)")
        if self.cadence not in VALID_CADENCES:
            raise ValueError(
                f"cadence {self.cadence!r} not in {sorted(VALID_CADENCES)}"
            )
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"action {self.action!r} not in {sorted(VALID_ACTIONS)}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if not self.reasoning or not self.reasoning.strip():
            raise ValueError("reasoning must be non-empty")
        if self.action != "no_action":
            if not self.target:
                raise ValueError(
                    f"target must be non-empty for action={self.action!r}"
                )
            if self.predicted_effect is None:
                raise ValueError(
                    f"predicted_effect is mandatory for action={self.action!r}"
                )
            if self.predicted_effect.evaluate_at <= self.decided_at:
                raise ValueError(
                    "predicted_effect.evaluate_at must be strictly after decided_at"
                )
```

- [ ] **Step 4: Re-run all decision tests; expect every pass**

Run: `pytest tests/test_governance_decision.py -v`

Expected: 17 passed (4 from Task 2 + 13 new in this task).

- [ ] **Step 5: Commit**

```bash
git add governance/decision.py tests/test_governance_decision.py
git commit -m "feat(governance): Decision validators (Phase 2 Task 3)

Strict __post_init__ checks on Decision and PredictedEffect:
- decision_id / batch_id format (gd_/gb_YYYY-MM-DD_NNNN)
- tz-aware decided_at and evaluate_at
- cadence and action whitelisted
- confidence in [0, 1]
- non-empty reasoning
- action decisions require non-empty target + predicted_effect
- evaluate_at strictly after decided_at
- no_action allows null predicted_effect + empty target

Mirrors the OverridesState.__post_init__ discipline from Phase 1:
fail fast at construction, never let an invalid Decision propagate."
```

---

## Task 4: `governance/decision.py` — converters to audit/override records

**Files:**
- Modify: `governance/decision.py`
- Modify: `tests/test_governance_decision.py`

**Why:** The agent's last step in a cycle is to (a) write the decision to the audit log as a `GOVERNANCE_DECISION` JSON record (spec §6.2) and, if `applied`, (b) translate it into the corresponding `OverridesState` entry (a `DisabledSource`, `DisabledKeyword`, or `ThresholdOverride` from Phase 1). Centralizing those converters on the `Decision` class itself keeps the agent loop in Task 18 short and the conversions unit-testable.

- [ ] **Step 1: Write failing tests for the converters**

Append to `tests/test_governance_decision.py`:

```python
from utils.runtime_overrides import (
    DisabledSource,
    DisabledKeyword,
    ThresholdOverride,
    PredictedEffect as OverridePredictedEffect,
)


def test_to_audit_record_emits_full_spec_shape():
    d = _ok_decision()
    record = d.to_audit_record(applied=True, shadow_mode=False, safety_checks_passed={
        "confidence_threshold": True,
        "max_changes_per_run": True,
        "blast_radius": True,
        "kill_switch": True,
    })
    assert record["type"] == "GOVERNANCE_DECISION"
    assert record["decision_id"] == "gd_2026-05-02_0042"
    assert record["batch_id"] == "gb_2026-05-02_0012"
    assert record["decided_at"] == "2026-05-02T14:30:00+00:00"
    assert record["decided_by"] == "governance-agent-v0.2.1"
    assert record["cadence"] == "fast"
    assert record["action"] == "disable_source"
    assert record["target"] == "r/Turkey"
    assert record["confidence"] == 0.94
    assert record["model_used"] == "qwen3-14b-instruct"
    assert record["escalated_to_claude"] is False
    assert record["claude_response"] is None
    assert record["applied"] is True
    assert record["shadow_mode"] is False
    assert record["safety_checks_passed"]["confidence_threshold"] is True
    pe = record["predicted_effect"]
    assert pe["metric"] == "reddit_rate_limit_budget_consumed_daily"
    assert pe["baseline"] == 0.12
    assert pe["predicted_post_change"] == 0.08
    assert pe["evaluate_at"] == "2026-05-09T14:30:00+00:00"
    assert record["outcome"] is None  # always null at write time


def test_to_disabled_source_for_disable_source_action():
    d = _ok_decision(action="disable_source", target="r/Turkey")
    ds = d.to_disabled_source()
    assert isinstance(ds, DisabledSource)
    assert ds.source == "r/Turkey"
    assert ds.confidence == 0.94
    assert ds.decision_id == "gd_2026-05-02_0042"
    assert ds.decided_at == _NOW
    assert ds.decided_by == "governance-agent-v0.2.1"
    assert ds.predicted_effect.metric == "reddit_rate_limit_budget_consumed_daily"


def test_to_disabled_source_rejects_other_actions():
    d = _ok_decision(
        action="disable_keyword",
        target="ceasefire",
        predicted_change_for_keyword=None,  # the helper kwarg is ignored — just to make _ok_decision flexible
    ) if False else _ok_decision(action="disable_keyword", target="ceasefire")
    with pytest.raises(ValueError, match="disable_source"):
        d.to_disabled_source()


def test_to_disabled_keyword_for_disable_keyword_action():
    d = _ok_decision(action="disable_keyword", target="ceasefire")
    dk = d.to_disabled_keyword()
    assert isinstance(dk, DisabledKeyword)
    assert dk.keyword == "ceasefire"
    assert dk.decision_id == "gd_2026-05-02_0042"


def test_to_threshold_override_for_tune_threshold_action():
    proposed = {
        "before": 0.05,
        "after": 0.07,
        "expires_at": None,
    }
    d = _ok_decision(
        action="tune_threshold",
        target="match_quality_threshold",
        proposed_change=proposed,
    )
    to = d.to_threshold_override()
    assert isinstance(to, ThresholdOverride)
    assert to.path == "match_quality_threshold"
    assert to.value == 0.07
    assert to.decision_id == "gd_2026-05-02_0042"


def test_to_threshold_override_rejects_other_actions():
    d = _ok_decision(action="disable_source", target="r/X")
    with pytest.raises(ValueError, match="tune_threshold"):
        d.to_threshold_override()


def test_to_audit_record_no_action_decision_keeps_action_disabled_keys_null():
    d = _ok_decision(
        action="no_action",
        target="",
        predicted_effect=None,
        proposed_change={},
    )
    rec = d.to_audit_record(applied=False, shadow_mode=True, safety_checks_passed={})
    assert rec["action"] == "no_action"
    assert rec["target"] == ""
    assert rec["predicted_effect"] is None
    assert rec["applied"] is False
```

- [ ] **Step 2: Run — expect failures**

Run: `pytest tests/test_governance_decision.py -v -k "to_audit or to_disabled or to_threshold"`

Expected: AttributeError on each — methods don't exist yet.

- [ ] **Step 3: Implement converters**

Append to `governance/decision.py`:

```python
    def to_audit_record(
        self,
        *,
        applied: bool,
        shadow_mode: bool,
        safety_checks_passed: dict[str, bool],
    ) -> dict[str, Any]:
        """Serialize as a GOVERNANCE_DECISION JSONL record (spec §6.2)."""
        if self.predicted_effect is None:
            pe_record: dict[str, Any] | None = None
        else:
            pe_record = {
                "metric": self.predicted_effect.metric,
                "baseline": self.predicted_effect.baseline,
                "predicted_post_change": self.predicted_effect.predicted_post_change,
                "evaluate_at": self.predicted_effect.evaluate_at.isoformat(),
            }
        return {
            "type": "GOVERNANCE_DECISION",
            "decision_id": self.decision_id,
            "batch_id": self.batch_id,
            "decided_at": self.decided_at.isoformat(),
            "decided_by": self.decided_by,
            "cadence": self.cadence,
            "action": self.action,
            "target": self.target,
            "proposed_change": dict(self.proposed_change),
            "model_used": self.model_used,
            "escalated_to_claude": self.escalated_to_claude,
            "claude_response": self.claude_response,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "evidence_summary": dict(self.evidence_summary),
            "predicted_effect": pe_record,
            "outcome": None,
            "applied": applied,
            "shadow_mode": shadow_mode,
            "safety_checks_passed": dict(safety_checks_passed),
        }

    def to_disabled_source(self):
        """Convert to a Phase-1 DisabledSource. action must be 'disable_source'."""
        from utils.runtime_overrides import DisabledSource, PredictedEffect as ROPredictedEffect
        if self.action != "disable_source":
            raise ValueError(
                f"to_disabled_source called on action={self.action!r}; expected disable_source"
            )
        assert self.predicted_effect is not None  # validated in __post_init__
        return DisabledSource(
            source=self.target,
            reason=self.reasoning,
            confidence=self.confidence,
            decided_at=self.decided_at,
            decided_by=self.decided_by,
            decision_id=self.decision_id,
            expires_at=self.proposed_change.get("expires_at"),
            predicted_effect=ROPredictedEffect(
                metric=self.predicted_effect.metric,
                baseline=self.predicted_effect.baseline,
                predicted_post_change=self.predicted_effect.predicted_post_change,
                evaluate_at=self.predicted_effect.evaluate_at,
            ),
        )

    def to_disabled_keyword(self):
        """Convert to a Phase-1 DisabledKeyword. action must be 'disable_keyword'."""
        from utils.runtime_overrides import DisabledKeyword, PredictedEffect as ROPredictedEffect
        if self.action != "disable_keyword":
            raise ValueError(
                f"to_disabled_keyword called on action={self.action!r}; expected disable_keyword"
            )
        assert self.predicted_effect is not None
        return DisabledKeyword(
            keyword=self.target,
            reason=self.reasoning,
            confidence=self.confidence,
            decided_at=self.decided_at,
            decided_by=self.decided_by,
            decision_id=self.decision_id,
            expires_at=self.proposed_change.get("expires_at"),
            predicted_effect=ROPredictedEffect(
                metric=self.predicted_effect.metric,
                baseline=self.predicted_effect.baseline,
                predicted_post_change=self.predicted_effect.predicted_post_change,
                evaluate_at=self.predicted_effect.evaluate_at,
            ),
        )

    def to_threshold_override(self):
        """Convert to a Phase-1 ThresholdOverride. action must be 'tune_threshold'.
        proposed_change.after holds the new value."""
        from utils.runtime_overrides import ThresholdOverride, PredictedEffect as ROPredictedEffect
        if self.action != "tune_threshold":
            raise ValueError(
                f"to_threshold_override called on action={self.action!r}; expected tune_threshold"
            )
        assert self.predicted_effect is not None
        if "after" not in self.proposed_change:
            raise ValueError("tune_threshold decisions must have proposed_change.after")
        return ThresholdOverride(
            path=self.target,
            value=self.proposed_change["after"],
            reason=self.reasoning,
            confidence=self.confidence,
            decided_at=self.decided_at,
            decided_by=self.decided_by,
            decision_id=self.decision_id,
            expires_at=self.proposed_change.get("expires_at"),
            predicted_effect=ROPredictedEffect(
                metric=self.predicted_effect.metric,
                baseline=self.predicted_effect.baseline,
                predicted_post_change=self.predicted_effect.predicted_post_change,
                evaluate_at=self.predicted_effect.evaluate_at,
            ),
        )
```

- [ ] **Step 4: Re-run; expect all converter tests pass**

Run: `pytest tests/test_governance_decision.py -v`

Expected: 24 passed (17 from Tasks 2-3 + 7 new).

- [ ] **Step 5: Commit**

```bash
git add governance/decision.py tests/test_governance_decision.py
git commit -m "feat(governance): Decision -> audit/override converters (Phase 2 Task 4)

Adds three conversion methods that bridge Phase 2's Decision shape to
Phase 1's runtime-overrides record types:
- to_audit_record(applied, shadow_mode, safety_checks_passed) ->
  spec §6.2 GOVERNANCE_DECISION JSONL shape
- to_disabled_source() -> Phase-1 DisabledSource
- to_disabled_keyword() -> Phase-1 DisabledKeyword
- to_threshold_override() -> Phase-1 ThresholdOverride

Converters raise ValueError when called on the wrong action so the
agent's apply path can never accidentally route a decision into the
wrong override bucket. The audit-record format is faithful to the
spec example down to field order and value types."
```

---

## Task 5: `governance/adapter.py` — `GovernanceAdapter` Protocol

**Files:**
- Create: `governance/adapter.py`
- Test: `tests/test_governance_adapter.py`

**Why now:** The Protocol is the cross-bot seam (decision 9 in spec). Defining it before the implementation forces the surface to be small and explicit. `governance/evidence.py` and `governance/agent.py` both depend on this Protocol but never on the concrete `KalshiGovernanceAdapter` directly, which makes future Polymarket / Alpaca adapters a one-class addition.

- [ ] **Step 1: Write structural-conformance test**

Create `tests/test_governance_adapter.py`:

```python
"""GovernanceAdapter protocol + KalshiGovernanceAdapter conformance."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from governance.adapter import GovernanceAdapter, KalshiGovernanceAdapter


def test_protocol_declares_required_methods():
    """The Protocol must expose every method the agent loop calls.
    If a method is added to the agent but not to this list, this test
    fails — preventing accidental drift."""
    required = {
        "collect_audit_data",
        "get_active_market_titles",
        "get_recent_headline_samples",
        "get_active_source_count",
        "get_active_source_list",
    }
    declared = {
        name
        for name in dir(GovernanceAdapter)
        if not name.startswith("_")
    }
    missing = required - declared
    assert not missing, f"Protocol missing required methods: {missing}"


def test_kalshi_adapter_conforms_to_protocol(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=paper_db,
        market_provider=None,
    )
    assert isinstance(adapter, GovernanceAdapter)
```

- [ ] **Step 2: Run — expect ImportError**

Run: `pytest tests/test_governance_adapter.py::test_protocol_declares_required_methods -v`

Expected: `ModuleNotFoundError: No module named 'governance.adapter'`.

- [ ] **Step 3: Write the Protocol module**

Create `governance/adapter.py`:

```python
"""GovernanceAdapter — the bot-agnostic seam (spec decision 9).

The agent loop talks to a GovernanceAdapter; concrete bots (Kalshi today,
Polymarket / Alpaca tomorrow) provide their own implementations. The
Protocol surface is intentionally small: all bot-specific concerns are
behind these five method signatures.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GovernanceAdapter(Protocol):
    """Read-only seam between the agent and the trading bot's data sources."""

    def collect_audit_data(self, window: timedelta) -> dict[str, Any]:
        """Return aggregated diagnostic-script outputs over the given window.

        Keys (Kalshi-specific, but the *shape* — a flat dict of named
        aggregations — is the contract):
          - 'alignment'  -> source_market_alignment_audit.aggregate(...)
          - 'keywords'   -> keyword_feedback.summarize(...)
          - 'reddit'     -> reddit_source_audit.collect(...)
          - 'freshness'  -> freshness_diagnostics.summarize(...)
        """
        ...

    def get_active_market_titles(self) -> list[str]:
        """Return current active-market titles (used to build prompt context
        about what the bot is currently watching)."""
        ...

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        """Return up to k recent headline strings for the given source. Used
        to ground the LLM in concrete examples when reasoning about
        disable_source decisions."""
        ...

    def get_active_source_count(self) -> int:
        """Number of distinct sources observed in the last 24h. Drives the
        blast-radius cap (Phase 3) but the agent reads it from here so the
        Protocol stays bot-agnostic."""
        ...

    def get_active_source_list(self) -> list[str]:
        """List of distinct source identifiers observed in the last 24h.
        Order: ingestion-volume desc."""
        ...
```

The `KalshiGovernanceAdapter` class skeleton goes here too, but the implementation lands in Task 6:

```python
class KalshiGovernanceAdapter:
    """Kalshi-specific implementation. Phase 2: Task 6 fills in the bodies."""

    def __init__(
        self,
        *,
        trade_log_path: Path,
        paper_db_path: Path,
        market_provider=None,
    ) -> None:
        self.trade_log_path = trade_log_path
        self.paper_db_path = paper_db_path
        self.market_provider = market_provider

    def collect_audit_data(self, window: timedelta) -> dict[str, Any]:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_market_titles(self) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_source_count(self) -> int:
        raise NotImplementedError("Implemented in Task 6")

    def get_active_source_list(self) -> list[str]:
        raise NotImplementedError("Implemented in Task 6")
```

- [ ] **Step 4: Re-run; expect 2 pass**

Run: `pytest tests/test_governance_adapter.py -v`

Expected: 2 passed. (The conformance test passes because `KalshiGovernanceAdapter` has all five methods, even though their bodies raise `NotImplementedError` — `runtime_checkable` Protocols only check the method *names*, not their semantics.)

- [ ] **Step 5: Commit**

```bash
git add governance/adapter.py tests/test_governance_adapter.py
git commit -m "feat(governance): GovernanceAdapter Protocol + Kalshi skeleton (Phase 2 Task 5)

Defines the bot-agnostic adapter seam (spec decision 9). The agent
loop in Task 17 will only depend on the Protocol, never on the
concrete KalshiGovernanceAdapter, so adding Polymarket / Alpaca later
is a one-class addition and not a refactor.

Five required methods:
- collect_audit_data(window): aggregated diagnostic-script output
- get_active_market_titles(): current market titles for prompt context
- get_recent_headline_samples(source, k): grounding examples
- get_active_source_count(): blast-radius input
- get_active_source_list(): ranked source list

KalshiGovernanceAdapter bodies raise NotImplementedError; Task 6
fills them in."
```

---

## Task 6: `governance/adapter.py` — `KalshiGovernanceAdapter` implementation

**Files:**
- Modify: `governance/adapter.py`
- Modify: `tests/test_governance_adapter.py`

- [ ] **Step 1: Write tests for each method against a fixture trade log**

Append to `tests/test_governance_adapter.py`:

```python
import json
from datetime import datetime, timedelta, timezone


def _write_trade_log(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _ts(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_collect_audit_data_returns_four_named_aggregations(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    _write_trade_log(trade_log, [
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update"},
        {"ts": _ts(60), "type": "MATCH_DIAGNOSTIC", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update",
         "match_score": 0.6, "would_fail_pre_llm_gate": False,
         "pre_llm_quality_pass": True, "heuristic_flags": []},
        {"ts": _ts(60), "type": "SIGNAL_ANALYSIS_DETAIL", "source": "Reuters",
         "ticker": "KXTRUMPIRAN-1", "headline": "Iran talks update",
         "method": "llm", "llm_result_used": True,
         "estimated_probability": 0.5, "market_price": 0.5},
    ])
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    data = adapter.collect_audit_data(window=timedelta(hours=24))
    assert set(data.keys()) >= {"alignment", "keywords", "reddit", "freshness"}


def test_get_recent_headline_samples_returns_up_to_k_strings(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    records = []
    for i in range(8):
        records.append({
            "ts": _ts(3600 - i),
            "type": "EARLY_FRESH_PASS",
            "source": "r/Turkey",
            "ticker": "KX1",
            "headline": f"Turkey news item {i}",
        })
    _write_trade_log(trade_log, records)
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    samples = adapter.get_recent_headline_samples("r/Turkey", k=5)
    assert len(samples) == 5
    assert all(isinstance(s, str) and s for s in samples)


def test_get_active_source_count_and_list_in_24h_window(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    _write_trade_log(trade_log, [
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KX1", "headline": "h1"},
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "AP",
         "ticker": "KX1", "headline": "h2"},
        {"ts": _ts(60), "type": "EARLY_FRESH_PASS", "source": "Reuters",
         "ticker": "KX1", "headline": "h3"},
        # Outside 24h window — should not be counted.
        {"ts": _ts(48 * 3600), "type": "EARLY_FRESH_PASS", "source": "BBC",
         "ticker": "KX1", "headline": "old"},
    ])
    paper_db = tmp_path / "paper.db"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db, market_provider=None,
    )
    assert adapter.get_active_source_count() == 2  # Reuters + AP, BBC excluded
    assert "Reuters" in adapter.get_active_source_list()
    assert "AP" in adapter.get_active_source_list()
    assert "BBC" not in adapter.get_active_source_list()
    # ranked by ingestion volume desc
    assert adapter.get_active_source_list()[0] == "Reuters"


def test_get_active_market_titles_uses_market_provider_when_present(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"

    class _StubMarket:
        def __init__(self, title):
            self.title = title

    def provider():
        return [_StubMarket("Will X happen?"), _StubMarket("Will Y happen?")]

    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=paper_db,
        market_provider=provider,
    )
    titles = adapter.get_active_market_titles()
    assert titles == ["Will X happen?", "Will Y happen?"]
```

- [ ] **Step 2: Run — expect NotImplementedError on each**

Run: `pytest tests/test_governance_adapter.py -v`

Expected: 4 new tests fail with `NotImplementedError`.

- [ ] **Step 3: Implement `KalshiGovernanceAdapter` bodies**

Edit `governance/adapter.py`. Add at the top of the file (after the existing imports):

```python
import json
from datetime import datetime, timezone
```

Replace the `KalshiGovernanceAdapter` class with the full implementation:

```python
class KalshiGovernanceAdapter:
    """Kalshi-specific implementation of GovernanceAdapter."""

    def __init__(
        self,
        *,
        trade_log_path: Path,
        paper_db_path: Path,
        market_provider=None,
    ) -> None:
        self.trade_log_path = trade_log_path
        self.paper_db_path = paper_db_path
        self.market_provider = market_provider

    def collect_audit_data(self, window: timedelta) -> dict[str, Any]:
        from scripts import (
            freshness_diagnostics,
            keyword_feedback,
            reddit_source_audit,
            source_market_alignment_audit,
        )
        until = datetime.now(timezone.utc)
        since = until - window
        return {
            "alignment": source_market_alignment_audit.aggregate(
                self.trade_log_path, since=since, until=until,
            ),
            "keywords": keyword_feedback.summarize(
                self.trade_log_path, since=since, until=until,
            ),
            "reddit": reddit_source_audit.collect(
                self.trade_log_path, since=since, until=until,
            ),
            "freshness": freshness_diagnostics.summarize(
                self.trade_log_path, since=since, until=until,
            ),
        }

    def get_active_market_titles(self) -> list[str]:
        if self.market_provider is None:
            return []
        return [getattr(m, "title", "") for m in self.market_provider() if getattr(m, "title", "")]

    def get_recent_headline_samples(self, source: str, k: int = 5) -> list[str]:
        if not self.trade_log_path.exists():
            return []
        out: list[str] = []
        # Walk the file backwards-ish: simple approach is forward, then take last k.
        # Trade logs are append-only and small enough at the per-source scale that
        # reading the whole file once per call is acceptable for governance cadence.
        seen: list[str] = []
        with self.trade_log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("source") != source:
                    continue
                headline = rec.get("headline")
                if not headline:
                    continue
                seen.append(str(headline))
        # last k, preserving order
        out = seen[-k:]
        return out

    def get_active_source_count(self) -> int:
        return len(self.get_active_source_list())

    def get_active_source_list(self) -> list[str]:
        if not self.trade_log_path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        counts: dict[str, int] = {}
        with self.trade_log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if not ts:
                    continue
                # Tolerant ts parsing: ISO 8601 with timezone offset.
                try:
                    rec_dt = datetime.fromisoformat(ts)
                except ValueError:
                    continue
                if rec_dt < cutoff:
                    continue
                source = rec.get("source")
                if not source:
                    continue
                counts[source] = counts.get(source, 0) + 1
        return [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
```

- [ ] **Step 4: Re-run; expect all pass**

Run: `pytest tests/test_governance_adapter.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add governance/adapter.py tests/test_governance_adapter.py
git commit -m "feat(governance): KalshiGovernanceAdapter implementation (Phase 2 Task 6)

Implements the five Protocol methods against the live trade log + the
existing diagnostic-script library functions:
- collect_audit_data(window): wraps the four scripts/* aggregators
- get_active_market_titles(): delegates to an injected provider
- get_recent_headline_samples(source, k): scans trade log, last-k
- get_active_source_count() / get_active_source_list(): 24h ingestion
  counts, sorted by volume desc

The market_provider injection keeps the adapter testable without
spinning up the real KalshiClient. In production the agent will pass
a callable that snapshots the bot's in-memory market_cache."
```

**Post-implementation note: signature drift from plan (recorded 2026-04-25, commit `f113858`)**

Step 3's embedded `collect_audit_data` body assumed all four `scripts/*` helpers share the signature `(path, since=since, until=until)`. Two of them do not. The shipped implementation in `governance/adapter.py:70-103` deviates accordingly. This note records the deviation so a future agent (Claude, Codex, or otherwise) re-reading this plan does not believe the embedded Step 3 code reflects what was committed.

**What the plan-as-written assumed (Step 3, lines 1199–1210 of this plan):**

```python
"alignment": source_market_alignment_audit.aggregate(self.trade_log_path, since=since, until=until),
"keywords":  keyword_feedback.summarize(self.trade_log_path, since=since, until=until),
"reddit":    reddit_source_audit.collect(self.trade_log_path, since=since, until=until),
"freshness": freshness_diagnostics.summarize(self.trade_log_path, since=since, until=until),
```

**What the actual library exposes** (verified 2026-04-25 by `grep '^def ' scripts/{source_market_alignment_audit,flag_outcome_correlation,reddit_source_audit,keyword_feedback,freshness_diagnostics}.py` against tree at `f113858`):

| Function | File:line | Real signature |
|---|---|---|
| `source_market_alignment_audit.aggregate` | `scripts/source_market_alignment_audit.py:177` | `aggregate(match_index, analysis_rows) -> tuple[dict[(source, series_ticker), PairStats], int]` — **no path/since/until args; consumes pre-collected match_index + analysis_rows** |
| `flag_outcome_correlation.collect` | `scripts/flag_outcome_correlation.py:183` | `collect(log_path, since, until, exclude_test, *, verbose) -> tuple[match_index, analysis_rows, read_stats]` |
| `reddit_source_audit.collect` | `scripts/reddit_source_audit.py:193` | `collect(log_path, since, until, exclude_test)` — `exclude_test` is **positional, no default** |
| `keyword_feedback.summarize` | `scripts/keyword_feedback.py:234` | `summarize(path, since, until, exclude_test=False)` — matches plan ✅ |
| `freshness_diagnostics.summarize` | `scripts/freshness_diagnostics.py:160` | `summarize(path, since, until, exclude_test=False, *, progress_tracker=None)` — matches plan ✅ |

**Workaround as shipped** (`governance/adapter.py:70-103`):

1. The adapter's `collect_audit_data()` first imports `flag_outcome_correlation.collect` (under the alias `_foc_collect`) and calls it with `(self.trade_log_path, since, until, False, verbose=False)`. This returns `(match_index, analysis_rows, _read_stats)`.
2. It then feeds those into `source_market_alignment_audit.aggregate(match_index, analysis_rows)` to populate `data["alignment"]`. The `aggregate()` call **cannot** take a path directly — it is the second stage of a two-stage pipeline whose first stage is `flag_outcome_correlation.collect`.
3. `reddit_source_audit.collect(...)` is called with `exclude_test=False` as a **positional** fourth argument, since that parameter has no default in the real signature.
4. `keyword_feedback.summarize` and `freshness_diagnostics.summarize` are called positionally (`since, until` rather than `since=..., until=...`); both are equivalent — neither is keyword-only.

**Impact on downstream tasks: none.**

- The `Candidate` surface (Task 7) and the prompt renderer (Tasks 8–10) consume the **shape** of `data` — the four top-level keys (`alignment`, `keywords`, `reddit`, `freshness`) and the dict shape returned by each helper. None of that shape changed; only the *call mechanics* inside `collect_audit_data` changed.
- The Protocol (Task 5) is signature-stable: `collect_audit_data(window) -> dict[str, Any]` is unchanged.
- Adapter audit (§8.5, this plan's verification table at line 4648) still passes: `governance/adapter.py` remains the only `governance/*` file that imports from `scripts/`.

**For future re-execution:** if Task 6 is ever re-implemented from scratch, **do not copy Step 3's embedded code verbatim** — it will fail with `TypeError` on the `aggregate` and `reddit_source_audit.collect` calls. Use the workaround pattern above; verify signatures first with:

```bash
grep '^def ' scripts/source_market_alignment_audit.py scripts/flag_outcome_correlation.py scripts/reddit_source_audit.py scripts/keyword_feedback.py scripts/freshness_diagnostics.py
```

The shipped tests in `tests/test_governance_adapter.py` (`test_collect_audit_data_returns_four_named_aggregations` and the three sibling tests) cover the actual call path; if they pass, the workaround is intact.

---

## Task 7: `governance/evidence.py` — `select_candidates_for_cadence`

**Files:**
- Create: `governance/evidence.py`
- Test: `tests/test_governance_evidence.py`

**Why now:** The agent's loop iterates over candidates. A `Candidate` is a `(action, target)` pair the agent will ask the LLM to opine on. `fast` cadence keeps the LLM-call count bounded (top-N by volume per concern bucket); `deep` does a sweep; `weekly_review` looks at past decisions. Lock the selection function in before the prompt-renderer (Task 10) consumes its output.

- [ ] **Step 1: Write tests for the three cadences**

Create `tests/test_governance_evidence.py`:

```python
"""Evidence builder — candidate selection + per-candidate composition."""

from __future__ import annotations

import pytest

from governance.evidence import (
    Candidate,
    select_candidates_for_cadence,
    compose_evidence_for_candidate,
    summarize_evidence_for_audit,
)


def _audit_data_with_three_sources():
    """Synthetic audit data shaped like KalshiGovernanceAdapter.collect_audit_data
    output, simplified for unit testing."""
    return {
        "alignment": {
            "pairs": [
                {"source": "Reuters", "series_ticker": "KXTRUMPIRAN",
                 "n": 32, "anchor": 32, "anchor_rate": 1.0},
                {"source": "AP", "series_ticker": "KXTRUMPIRAN",
                 "n": 18, "anchor": 17, "anchor_rate": 17/18},
                {"source": "r/Turkey", "series_ticker": "KXMENA",
                 "n": 0, "anchor": 0, "anchor_rate": None},
            ],
            "overall_anchor_rate": 0.99,
            "overall_n": 50,
        },
        "keywords": {
            "no_keyword_misses": 12,
            "candidate_phrases": [
                {"phrase": "ceasefire", "count": 30, "category": "war"},
                {"phrase": "trump", "count": 200, "category": "person"},
            ],
        },
        "reddit": {
            "subs": [
                {"source": "r/Turkey", "ingestion": 408,
                 "fresh_passes": 7, "matches": 0,
                 "classification": "all_stale"},
                {"source": "r/worldnews", "ingestion": 200,
                 "fresh_passes": 80, "matches": 12,
                 "classification": "signaling"},
            ],
        },
        "freshness": {
            "sources": {
                "Reuters": {"observed_records": 250, "fresh_passes": 200,
                            "stale_rate": 0.2, "interpretation": "fast operational"},
                "BBC": {"observed_records": 4, "fresh_passes": 0,
                        "stale_rate": 1.0, "interpretation": "insufficient data"},
            },
        },
    }


def test_fast_cadence_returns_bounded_top_n():
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="fast", max_per_bucket=5)
    assert isinstance(candidates, list)
    # Each candidate is a Candidate(action, target, evidence_pointer)
    assert all(isinstance(c, Candidate) for c in candidates)
    # fast cadence caps at max_per_bucket per concern bucket; we have three
    # buckets: source, keyword, threshold.
    actions = {c.action for c in candidates}
    assert actions <= {"disable_source", "disable_keyword", "tune_threshold"}


def test_deep_cadence_returns_full_sweep():
    audit = _audit_data_with_three_sources()
    fast = select_candidates_for_cadence(audit, cadence="fast", max_per_bucket=1)
    deep = select_candidates_for_cadence(audit, cadence="deep", max_per_bucket=1)
    assert len(deep) >= len(fast), (
        "deep cadence must include at least as many candidates as fast"
    )


def test_weekly_review_yields_no_action_candidates_in_phase_2():
    """weekly_review evaluates past decisions for outcome correctness; in
    Phase 2 it returns an empty list (Phase 4 wires self-review)."""
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="weekly_review")
    assert candidates == []


def test_unknown_cadence_raises():
    audit = _audit_data_with_three_sources()
    with pytest.raises(ValueError, match="cadence"):
        select_candidates_for_cadence(audit, cadence="hourly")


def test_disable_source_candidates_only_for_problem_sources():
    """A source with high anchor_rate AND high ingestion volume AND zero
    matches (or all_stale classification) is a candidate. A signaling
    source is not."""
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="deep")
    targets = {c.target for c in candidates if c.action == "disable_source"}
    assert "r/Turkey" in targets, "all_stale Reddit sub should be a candidate"
    assert "r/worldnews" not in targets, "signaling sub should NOT be a candidate"
```

- [ ] **Step 2: Run — expect ImportError**

Run: `pytest tests/test_governance_evidence.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `Candidate` + `select_candidates_for_cadence`**

Create `governance/evidence.py`:

```python
"""Evidence composition for the governance agent.

Pure functions — no I/O beyond what the adapter already did. Three pieces:
- select_candidates_for_cadence(): which (action, target) pairs to ask about
- compose_evidence_for_candidate(): build the evidence dict for a single LLM call
- summarize_evidence_for_audit(): trim the evidence dict for the JSONL audit log

Per spec §8.3, 'fast' cadence is invoked every 2h and must bound LLM cost.
'deep' is daily and may sweep more thoroughly. 'weekly_review' is a Phase 4
concern; in Phase 2 it returns an empty candidate list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Candidate:
    """A (action, target) pair the agent will ask the LLM to opine on.

    `evidence_pointer` is a key into the audit-data dict; the prompt renderer
    follows the pointer to extract per-target metrics. We carry the pointer
    rather than the raw evidence so candidates remain cheap to construct
    and easy to deduplicate.
    """
    action: Literal["disable_source", "disable_keyword", "tune_threshold"]
    target: str
    evidence_pointer: dict[str, Any]


_VALID_CADENCES = {"fast", "deep", "weekly_review"}


def _disable_source_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """A source is a candidate when (a) Reddit audit classifies it as
    all_stale / no_matches with ingestion >= 20, OR (b) source-market
    alignment shows it consistently anchoring at >= 0.95 across a
    meaningful sample."""
    out: list[Candidate] = []

    # (a) Reddit audit — explicit problem classifications.
    reddit_subs = audit.get("reddit", {}).get("subs", []) or []
    problem_classifications = {"all_stale", "no_matches", "match_dead"}
    for sub in sorted(
        reddit_subs,
        key=lambda s: -int(s.get("ingestion", 0) or 0),
    ):
        if sub.get("classification") not in problem_classifications:
            continue
        if int(sub.get("ingestion", 0) or 0) < 20:
            continue
        out.append(Candidate(
            action="disable_source",
            target=str(sub["source"]),
            evidence_pointer={"reddit_sub_index": reddit_subs.index(sub)},
        ))

    # (b) Alignment audit — high-volume sources that are universally anchoring.
    pairs = audit.get("alignment", {}).get("pairs", []) or []
    by_source: dict[str, dict[str, Any]] = {}
    for p in pairs:
        src = p.get("source")
        if not src:
            continue
        s = by_source.setdefault(src, {"n": 0, "anchored": 0})
        s["n"] += int(p.get("n", 0) or 0)
        s["anchored"] += int(p.get("anchor", 0) or 0)
    for src, stats in sorted(by_source.items(), key=lambda kv: -kv[1]["n"]):
        if stats["n"] < 10:
            continue
        rate = stats["anchored"] / stats["n"] if stats["n"] else 0.0
        if rate < 0.95:
            continue
        if any(c.target == src for c in out):
            continue  # dedup against (a)
        out.append(Candidate(
            action="disable_source",
            target=src,
            evidence_pointer={"alignment_source": src},
        ))

    if max_count is not None:
        out = out[:max_count]
    return out


def _disable_keyword_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """Currently a placeholder hook: returns no candidates in Phase 2 unless
    the keywords audit explicitly flagged risky phrases. Future expansion
    (Phase 4 self-review) will broaden this."""
    out: list[Candidate] = []
    phrases = audit.get("keywords", {}).get("candidate_phrases", []) or []
    for p in phrases:
        if p.get("category") == "person":
            # person-class phrases are legitimately predictive but high-volume;
            # not auto-candidates. agent can still consider them via deep sweep.
            continue
        # No automatic flagging in Phase 2. Reserved for Phase 4.
    if max_count is not None:
        out = out[:max_count]
    return out


def _tune_threshold_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """Reserved for Phase 4 (no thresholds tuned in Phase 2)."""
    return []


def select_candidates_for_cadence(
    audit: dict[str, Any],
    *,
    cadence: str,
    max_per_bucket: int = 5,
) -> list[Candidate]:
    """Return the candidates the agent will evaluate this cycle."""
    if cadence not in _VALID_CADENCES:
        raise ValueError(f"unknown cadence: {cadence!r}")

    if cadence == "weekly_review":
        return []

    if cadence == "fast":
        cap: int | None = max_per_bucket
    else:  # deep
        cap = None

    return (
        _disable_source_candidates(audit, max_count=cap)
        + _disable_keyword_candidates(audit, max_count=cap)
        + _tune_threshold_candidates(audit, max_count=cap)
    )


def compose_evidence_for_candidate(
    candidate: Candidate,
    audit: dict[str, Any],
    adapter,  # GovernanceAdapter — annotated loosely to avoid import cycle
) -> dict[str, Any]:
    """Build the evidence dict for a single LLM call. Implemented in Task 8."""
    raise NotImplementedError("Implemented in Task 8")


def summarize_evidence_for_audit(evidence: dict[str, Any]) -> dict[str, Any]:
    """Trim a per-candidate evidence dict for the audit-log record. Implemented
    in Task 9."""
    raise NotImplementedError("Implemented in Task 9")
```

- [ ] **Step 4: Re-run; expect 5 of 5 pass**

Run: `pytest tests/test_governance_evidence.py -v -k "cadence or candidates or unknown"`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add governance/evidence.py tests/test_governance_evidence.py
git commit -m "feat(governance): candidate selection by cadence (Phase 2 Task 7)

Defines Candidate (action, target, evidence_pointer) and the
select_candidates_for_cadence() pure function.

fast cadence: top-N per bucket — bounded LLM cost.
deep cadence: full sweep.
weekly_review: empty in Phase 2 (reserved for Phase 4 self-review).

Phase 2 only emits disable_source candidates; disable_keyword and
tune_threshold are placeholder hooks for Phase 4 expansion. Source
candidates come from two signals:
- Reddit audit classification in {all_stale, no_matches, match_dead}
  with ingestion >= 20
- Source-market alignment audit anchor_rate >= 0.95 with n >= 10

compose_evidence_for_candidate / summarize_evidence_for_audit raise
NotImplementedError; Tasks 8 and 9 fill them in."
```

---

## Task 8: `governance/evidence.py` — `compose_evidence_for_candidate`

**Files:**
- Modify: `governance/evidence.py`
- Modify: `tests/test_governance_evidence.py`

- [ ] **Step 1: Write tests**

Append to `tests/test_governance_evidence.py`:

```python
class _StubAdapter:
    """Tiny GovernanceAdapter test double — only the methods compose() needs."""
    def __init__(self, *, headline_samples=None, market_titles=None,
                 source_count=42):
        self._headlines = headline_samples or {}
        self._titles = market_titles or []
        self._count = source_count

    def collect_audit_data(self, window):
        raise AssertionError("compose() must not call collect_audit_data")

    def get_active_market_titles(self):
        return list(self._titles)

    def get_recent_headline_samples(self, source, k=5):
        return list(self._headlines.get(source, []))[:k]

    def get_active_source_count(self):
        return self._count

    def get_active_source_list(self):
        return []


def test_compose_evidence_for_disable_source_candidate():
    audit = _audit_data_with_three_sources()
    cand = Candidate(
        action="disable_source",
        target="r/Turkey",
        evidence_pointer={"reddit_sub_index": 0},
    )
    adapter = _StubAdapter(
        headline_samples={"r/Turkey": [
            "Turkey discussion 1",
            "Turkey discussion 2",
            "Turkey discussion 3",
        ]},
        market_titles=["Will X happen?", "Will Y happen?"],
        source_count=42,
    )
    evidence = compose_evidence_for_candidate(cand, audit, adapter)
    assert evidence["candidate_action"] == "disable_source"
    assert evidence["target"] == "r/Turkey"
    assert evidence["ingestion_events"] == 408
    assert evidence["fresh_pass_count"] == 7
    assert evidence["match_count"] == 0
    assert evidence["recent_headline_sample"] == [
        "Turkey discussion 1", "Turkey discussion 2", "Turkey discussion 3",
    ]
    assert evidence["active_market_count"] == 2
    assert "Will X happen?" in evidence["active_market_titles_top"]
    assert evidence["active_source_count"] == 42
    assert evidence["window_hours"] >= 1


def test_compose_evidence_excludes_pii_or_secret_fields():
    """Defensive: evidence is the LLM input. Anything that ends up in here
    can also end up in the audit log. No raw env vars, no PEM keys."""
    audit = _audit_data_with_three_sources()
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    adapter = _StubAdapter(
        headline_samples={"r/Turkey": ["a"]}, market_titles=[], source_count=10,
    )
    evidence = compose_evidence_for_candidate(cand, audit, adapter)
    forbidden_substrings = ("BEGIN RSA", "API_KEY", "PRIVATE KEY")
    flat = repr(evidence)
    for s in forbidden_substrings:
        assert s not in flat
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_governance_evidence.py -v -k "compose"`

Expected: NotImplementedError.

- [ ] **Step 3: Implement**

Replace `compose_evidence_for_candidate` in `governance/evidence.py`:

```python
def compose_evidence_for_candidate(
    candidate: Candidate,
    audit: dict[str, Any],
    adapter,  # GovernanceAdapter
) -> dict[str, Any]:
    """Build the evidence dict for a single LLM call.

    The shape is action-specific; the prompt-renderer dispatches on
    candidate.action. Common keys (candidate_action, target,
    active_market_count, active_market_titles_top, active_source_count,
    window_hours) appear regardless of action.
    """
    common = {
        "candidate_action": candidate.action,
        "target": candidate.target,
        "active_market_titles_top": adapter.get_active_market_titles()[:20],
        "active_market_count": len(adapter.get_active_market_titles()),
        "active_source_count": adapter.get_active_source_count(),
        "window_hours": 168,
    }

    if candidate.action == "disable_source":
        ingest = 0
        fresh = 0
        match = 0
        anchor_rate: float | None = None
        # Reddit audit lookup
        idx = candidate.evidence_pointer.get("reddit_sub_index")
        if isinstance(idx, int):
            sub = audit.get("reddit", {}).get("subs", [])[idx]
            ingest = int(sub.get("ingestion", 0) or 0)
            fresh = int(sub.get("fresh_passes", 0) or 0)
            match = int(sub.get("matches", 0) or 0)
        # Alignment audit lookup (overrides if both present)
        align_src = candidate.evidence_pointer.get("alignment_source")
        if align_src:
            pairs = audit.get("alignment", {}).get("pairs", []) or []
            n_total = sum(int(p.get("n", 0) or 0) for p in pairs if p.get("source") == align_src)
            anchored_total = sum(int(p.get("anchor", 0) or 0) for p in pairs if p.get("source") == align_src)
            ingest = max(ingest, n_total)
            anchor_rate = anchored_total / n_total if n_total else None
        return {
            **common,
            "ingestion_events": ingest,
            "fresh_pass_count": fresh,
            "match_count": match,
            "anchor_rate": anchor_rate,
            "recent_headline_sample": adapter.get_recent_headline_samples(
                candidate.target, k=5,
            ),
        }

    if candidate.action == "disable_keyword":
        return {
            **common,
            "candidate_phrase_summary": candidate.evidence_pointer,
        }

    if candidate.action == "tune_threshold":
        return {
            **common,
            "current_value": candidate.evidence_pointer.get("current_value"),
        }

    raise ValueError(f"unknown candidate.action: {candidate.action!r}")
```

- [ ] **Step 4: Re-run**

Run: `pytest tests/test_governance_evidence.py -v -k "compose"`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add governance/evidence.py tests/test_governance_evidence.py
git commit -m "feat(governance): compose_evidence_for_candidate (Phase 2 Task 8)

Builds the per-candidate evidence dict the LLM sees:
- common keys: candidate_action, target, active_market_titles_top,
  active_market_count, active_source_count, window_hours
- disable_source-specific: ingestion_events, fresh_pass_count,
  match_count, anchor_rate, recent_headline_sample
- disable_keyword / tune_threshold: placeholder shapes for Phase 4

Defensive test asserts no PEM-key or API-key substrings can leak
through; the evidence dict is also the audit log's evidence_summary,
so anything in here is persisted and visible."
```

---

## Task 9: `governance/evidence.py` — `summarize_evidence_for_audit`

**Files:**
- Modify: `governance/evidence.py`
- Modify: `tests/test_governance_evidence.py`

**Why separate from Task 8:** The full evidence dict the LLM sees can be large (20 market titles, 5 headlines). The audit-log record only needs the *decision-relevant* metrics — the headline samples and titles bloat the JSONL without adding evaluation value. Keep them out of the audit record.

- [ ] **Step 1: Test**

Append to `tests/test_governance_evidence.py`:

```python
def test_summarize_evidence_keeps_metrics_drops_samples():
    full = {
        "candidate_action": "disable_source",
        "target": "r/Turkey",
        "ingestion_events": 408,
        "fresh_pass_count": 7,
        "match_count": 0,
        "anchor_rate": None,
        "recent_headline_sample": ["a", "b", "c"],
        "active_market_titles_top": ["X", "Y", "Z"] * 10,
        "active_market_count": 30,
        "active_source_count": 42,
        "window_hours": 168,
    }
    summary = summarize_evidence_for_audit(full)
    # metrics retained
    assert summary["ingestion_events"] == 408
    assert summary["fresh_pass_count"] == 7
    assert summary["match_count"] == 0
    assert summary["active_market_count"] == 30
    assert summary["active_source_count"] == 42
    assert summary["window_hours"] == 168
    # samples retained but capped
    assert len(summary["recent_headline_sample"]) == 3
    # active market titles trimmed to top themes
    assert "active_market_themes_top" in summary
    assert "active_market_titles_top" not in summary  # large list dropped
```

- [ ] **Step 2: Run — expect NotImplementedError**

- [ ] **Step 3: Implement**

Replace `summarize_evidence_for_audit` in `governance/evidence.py`:

```python
def _extract_themes(titles: list[str], top_k: int = 3) -> list[str]:
    """Cheap theme extraction for audit summary: take frequent first-tokens
    of market titles. Not intended as ML — purely a compact shorthand for
    the audit log."""
    from collections import Counter
    tokens = []
    for t in titles:
        if not t:
            continue
        first = t.split()
        if first:
            tokens.append(first[0].lower())
    return [t for t, _ in Counter(tokens).most_common(top_k)]


def summarize_evidence_for_audit(evidence: dict[str, Any]) -> dict[str, Any]:
    """Trim per-candidate evidence to the fields worth persisting in the
    audit log.

    Drops the large free-text market-titles list; keeps headline samples
    (small and operationally useful when reviewing a decision retroactively);
    keeps every numeric metric the LLM saw.
    """
    keep_keys = {
        "candidate_action",
        "target",
        "ingestion_events",
        "fresh_pass_count",
        "match_count",
        "anchor_rate",
        "recent_headline_sample",
        "active_market_count",
        "active_source_count",
        "window_hours",
        "current_value",
        "candidate_phrase_summary",
    }
    summary = {k: v for k, v in evidence.items() if k in keep_keys}
    titles = evidence.get("active_market_titles_top") or []
    if titles:
        summary["active_market_themes_top"] = _extract_themes(titles, top_k=3)
    return summary
```

- [ ] **Step 4: Re-run; expect pass**

Run: `pytest tests/test_governance_evidence.py -v`

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add governance/evidence.py tests/test_governance_evidence.py
git commit -m "feat(governance): summarize_evidence_for_audit (Phase 2 Task 9)

Trims the per-candidate evidence dict to the fields worth persisting
in the GOVERNANCE_DECISION audit record: every numeric metric the LLM
saw, plus the recent headline samples (small and high-utility for
retroactive review). Drops the active_market_titles_top list (~20
strings) in favor of a derived active_market_themes_top — the
top-3 first-token frequencies — so audit records stay compact."
```

---

## Task 10: `governance/prompts.py` — system prompt + JSON schema

**Files:**
- Create: `governance/prompts.py`
- Test: `tests/test_governance_prompts.py`

**Why now:** The system prompt is the contract between the prompt-renderer (this task) and the LLM-response parser (Task 14). Both must agree on the JSON schema the LLM is told to emit. Pin the prompt and the schema together; cover with snapshot tests so prompt edits show clear diffs.

- [ ] **Step 1: Snapshot test for the system prompt**

Create `tests/test_governance_prompts.py`:

```python
"""Prompt rendering — system prompt + per-action templates.

Snapshot tests detect accidental drift. When intentionally changing a
prompt, update the snapshot in the test file in the same commit so the
diff makes the change reviewable.
"""

from __future__ import annotations

import json

import pytest

from governance.prompts import (
    SYSTEM_PROMPT,
    DISABLE_SOURCE_TEMPLATE,
    DISABLE_KEYWORD_TEMPLATE,
    TUNE_THRESHOLD_TEMPLATE,
    LLM_OUTPUT_SCHEMA,
    render_prompt,
)


def test_system_prompt_advertises_json_output_schema():
    assert "JSON" in SYSTEM_PROMPT
    assert '"action"' in SYSTEM_PROMPT
    assert '"target"' in SYSTEM_PROMPT
    assert '"reasoning"' in SYSTEM_PROMPT
    assert '"confidence"' in SYSTEM_PROMPT
    assert '"predicted_effect"' in SYSTEM_PROMPT
    for action in ("disable_source", "disable_keyword", "tune_threshold", "no_action"):
        assert action in SYSTEM_PROMPT


def test_llm_output_schema_lists_all_required_fields():
    required = {"action", "target", "reasoning", "confidence", "predicted_effect"}
    assert required <= set(LLM_OUTPUT_SCHEMA["required"])


def test_disable_source_template_substitutes_evidence_fields():
    rendered = DISABLE_SOURCE_TEMPLATE.format(
        target="r/Turkey",
        window_hours=168,
        ingestion_events=408,
        fresh_pass_count=7,
        match_count=0,
        anchor_rate_pct="n/a",
        active_market_count=287,
        headline_sample_block="- a\n- b\n- c",
        active_market_titles_block="- m1\n- m2",
    )
    assert "r/Turkey" in rendered
    assert "408" in rendered
    assert "n/a" in rendered
    assert "- a" in rendered


def test_render_prompt_returns_system_user_pair_for_disable_source():
    evidence = {
        "candidate_action": "disable_source",
        "target": "r/Turkey",
        "ingestion_events": 408,
        "fresh_pass_count": 7,
        "match_count": 0,
        "anchor_rate": None,
        "recent_headline_sample": ["a", "b"],
        "active_market_titles_top": ["X", "Y"],
        "active_market_count": 2,
        "active_source_count": 42,
        "window_hours": 168,
    }
    sys_p, user_p = render_prompt("disable_source", evidence)
    assert sys_p == SYSTEM_PROMPT
    assert "r/Turkey" in user_p
    assert "408" in user_p


def test_render_prompt_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        render_prompt("set_market_position", {})
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement prompts module**

Create `governance/prompts.py`:

```python
"""LLM prompts for the governance agent.

The SYSTEM_PROMPT is the long-lived contract: it tells the model the
output schema. Per-action templates (DISABLE_SOURCE_TEMPLATE etc.)
provide the per-call evidence framing.

Snapshot tests in tests/test_governance_prompts.py detect drift.
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = """You are a governance agent for a Kalshi prediction-market trading bot.

Your job: decide whether a candidate change to the bot's news-source list,
keyword filters, or pipeline thresholds is warranted, based on diagnostic
evidence about how the pipeline is currently performing.

For each candidate, you must decide one of:
  - "disable_source"  : the named source is harmful (consuming budget without producing usable signal)
  - "disable_keyword" : the named keyword is producing false matches or no longer adds signal value
  - "tune_threshold"  : the named threshold should change to a new value
  - "no_action"       : the evidence does not justify changing the candidate

Decision criteria:
  1. Does the evidence concretely show the candidate target is operationally harmful or stale?
  2. What measurable metric will move if you make the change?
  3. By how much (baseline vs predicted_post_change)?
  4. How confident are you (0.0 = guess, 1.0 = certain)?

Predict the effect on a single named metric. The bot's outcome-evaluator
will check your prediction against measured outcomes. Bad predictions
are a worse failure mode than conservative no_action.

Output ONLY valid JSON, no prose, matching this schema:
{
  "action": "disable_source" | "disable_keyword" | "tune_threshold" | "no_action",
  "target": <string — source name, keyword, or threshold path; empty for no_action>,
  "reasoning": <string — 2-5 sentences explaining the decision>,
  "confidence": <float in [0.0, 1.0]>,
  "predicted_effect": {
    "metric": <string — name of the metric you predict will move>,
    "baseline": <float — current value>,
    "predicted_post_change": <float — predicted value after change applies>,
    "evaluate_at_days": <integer in [1, 30] — when to check>
  }
}

If action is "no_action", set "predicted_effect" to null and "target" to "".
"""


LLM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action", "target", "reasoning", "confidence", "predicted_effect"],
    "properties": {
        "action": {"enum": ["disable_source", "disable_keyword", "tune_threshold", "no_action"]},
        "target": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "predicted_effect": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": ["metric", "baseline", "predicted_post_change", "evaluate_at_days"],
                    "properties": {
                        "metric": {"type": "string"},
                        "baseline": {"type": "number"},
                        "predicted_post_change": {"type": "number"},
                        "evaluate_at_days": {"type": "integer", "minimum": 1, "maximum": 30},
                    },
                },
            ],
        },
    },
}


DISABLE_SOURCE_TEMPLATE = """CANDIDATE ACTION: disable_source
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
- Ingestion events: {ingestion_events}
- Fresh-pass count: {fresh_pass_count}
- Match count (events that produced a MATCH_DIAGNOSTIC): {match_count}
- LLM anchor rate (final_probability == market_price): {anchor_rate_pct}

RECENT HEADLINE SAMPLE (3-5 most recent):
{headline_sample_block}

CURRENT ACTIVE MARKETS ({active_market_count} total — top 20):
{active_market_titles_block}

Should this source be disabled? Output JSON per schema.
"""


DISABLE_KEYWORD_TEMPLATE = """CANDIDATE ACTION: disable_keyword
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
{phrase_summary_block}

CURRENT ACTIVE MARKETS ({active_market_count} total — top 20):
{active_market_titles_block}

Should this keyword be disabled? Output JSON per schema.
"""


TUNE_THRESHOLD_TEMPLATE = """CANDIDATE ACTION: tune_threshold
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
- Current value: {current_value}
- Active markets: {active_market_count}
- Active sources: {active_source_count}

Should this threshold be tuned? If yes, to what value? Output JSON per schema.
"""


def _format_block(items: list[str], prefix: str = "- ") -> str:
    if not items:
        return f"{prefix}(none)"
    return "\n".join(f"{prefix}{item}" for item in items)


def render_prompt(action: str, evidence: dict[str, Any]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given action+evidence."""
    common = {
        "target": evidence.get("target", ""),
        "window_hours": evidence.get("window_hours", 168),
        "active_market_count": evidence.get("active_market_count", 0),
        "active_source_count": evidence.get("active_source_count", 0),
        "active_market_titles_block": _format_block(
            evidence.get("active_market_titles_top", []),
        ),
    }

    if action == "disable_source":
        anchor = evidence.get("anchor_rate")
        anchor_pct = "n/a" if anchor is None else f"{anchor * 100:.1f}%"
        user = DISABLE_SOURCE_TEMPLATE.format(
            **common,
            ingestion_events=evidence.get("ingestion_events", 0),
            fresh_pass_count=evidence.get("fresh_pass_count", 0),
            match_count=evidence.get("match_count", 0),
            anchor_rate_pct=anchor_pct,
            headline_sample_block=_format_block(
                evidence.get("recent_headline_sample", []),
            ),
        )
    elif action == "disable_keyword":
        phrase_summary = evidence.get("candidate_phrase_summary") or {}
        phrase_block = _format_block(
            [f"{k}: {v}" for k, v in sorted(phrase_summary.items())],
            prefix="- ",
        )
        user = DISABLE_KEYWORD_TEMPLATE.format(
            **common,
            phrase_summary_block=phrase_block,
        )
    elif action == "tune_threshold":
        user = TUNE_THRESHOLD_TEMPLATE.format(
            **common,
            current_value=evidence.get("current_value", "unknown"),
        )
    else:
        raise ValueError(f"unknown action for render_prompt: {action!r}")

    return SYSTEM_PROMPT, user
```

- [ ] **Step 4: Re-run; expect 5 of 5 pass**

- [ ] **Step 5: Commit**

```bash
git add governance/prompts.py tests/test_governance_prompts.py
git commit -m "feat(governance): system prompt + per-action templates (Phase 2 Task 10)"
```

---

## Task 11: `governance/prompts.py` — golden-file snapshot for full rendered prompt

**Files:**
- Modify: `tests/test_governance_prompts.py`
- Create: `tests/fixtures/governance_prompt_disable_source.txt`

**Why a separate task:** Step 4 in Task 10 only sanity-checks substring presence. A future prompt change might still subtly drift the framing (whitespace, ordering of evidence sections). A golden-file snapshot catches that.

- [ ] **Step 1: Generate the golden file with a one-shot script run**

Run inline:

```python
from governance.prompts import render_prompt
ev = {
    "candidate_action": "disable_source",
    "target": "r/Turkey",
    "ingestion_events": 408,
    "fresh_pass_count": 7,
    "match_count": 0,
    "anchor_rate": None,
    "recent_headline_sample": [
        "Turkey discussion of AKP economic policy",
        "Istanbul mayoral election analysis",
        "NATO exercises this week",
        "Lira exchange rate debate",
        "Erdogan speech reactions",
    ],
    "active_market_titles_top": [
        "Will Iran agree to a peace deal this month?",
        "Will Trump pardon X by Y?",
    ],
    "active_market_count": 287,
    "active_source_count": 42,
    "window_hours": 168,
}
sys_p, user_p = render_prompt("disable_source", ev)
print(user_p)
```

Save the output to `tests/fixtures/governance_prompt_disable_source.txt`.

- [ ] **Step 2: Add the snapshot test**

Append:

```python
from pathlib import Path

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def test_disable_source_prompt_matches_golden():
    evidence = {
        "candidate_action": "disable_source",
        "target": "r/Turkey",
        "ingestion_events": 408,
        "fresh_pass_count": 7,
        "match_count": 0,
        "anchor_rate": None,
        "recent_headline_sample": [
            "Turkey discussion of AKP economic policy",
            "Istanbul mayoral election analysis",
            "NATO exercises this week",
            "Lira exchange rate debate",
            "Erdogan speech reactions",
        ],
        "active_market_titles_top": [
            "Will Iran agree to a peace deal this month?",
            "Will Trump pardon X by Y?",
        ],
        "active_market_count": 287,
        "active_source_count": 42,
        "window_hours": 168,
    }
    sys_p, user_p = render_prompt("disable_source", evidence)
    expected = (_FIXTURE_DIR / "governance_prompt_disable_source.txt").read_text(encoding="utf-8")
    assert user_p == expected
```

- [ ] **Step 3: Run + commit**

```bash
git add tests/fixtures/governance_prompt_disable_source.txt tests/test_governance_prompts.py
git commit -m "feat(governance): golden-file snapshot for disable_source prompt (Phase 2 Task 11)"
```

---

## Task 12: `governance/prompts.py` — `dump_prompt_revision()` helper

**Files:**
- Modify: `governance/prompts.py`
- Modify: `tests/test_governance_prompts.py`

**Why:** Spec §5.2 reserves `docs/governance/prompts/` for "frozen historical prompt revisions for reproducibility." A small helper that emits the current prompt to that directory under a versioned filename keeps the practice mechanical.

- [ ] **Step 1: Test**

Append:

```python
def test_dump_prompt_revision_writes_versioned_file(tmp_path):
    from governance.prompts import dump_prompt_revision
    out_path = dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
    assert out_path.exists()
    assert out_path.name.endswith("-0.30.0.txt")
    content = out_path.read_text(encoding="utf-8")
    assert "SYSTEM_PROMPT" in content
    assert "DISABLE_SOURCE_TEMPLATE" in content


def test_dump_prompt_revision_refuses_to_overwrite(tmp_path):
    from governance.prompts import dump_prompt_revision
    dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
    with pytest.raises(FileExistsError):
        dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
```

- [ ] **Step 2: Implement**

Append to `governance/prompts.py`:

```python
def dump_prompt_revision(*, revision_label: str, out_dir):
    """Write the current prompt set to a versioned file. Refuses to
    overwrite — historical context must not be erased accidentally.
    Returns the path written."""
    from pathlib import Path
    from datetime import datetime, timezone
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir_path / f"{today}-{revision_label}.txt"
    if out_path.exists():
        raise FileExistsError(out_path)
    body = (
        "# SYSTEM_PROMPT\n\n" + SYSTEM_PROMPT + "\n\n"
        "# DISABLE_SOURCE_TEMPLATE\n\n" + DISABLE_SOURCE_TEMPLATE + "\n\n"
        "# DISABLE_KEYWORD_TEMPLATE\n\n" + DISABLE_KEYWORD_TEMPLATE + "\n\n"
        "# TUNE_THRESHOLD_TEMPLATE\n\n" + TUNE_THRESHOLD_TEMPLATE + "\n"
    )
    out_path.write_text(body, encoding="utf-8")
    return out_path
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/prompts.py tests/test_governance_prompts.py
git commit -m "feat(governance): dump_prompt_revision() helper (Phase 2 Task 12)"
```

---

## Task 13: `governance/llm.py` — `LLMClient` Protocol + `FakeLLM`

**Files:**
- Create: `governance/llm.py`
- Test: `tests/test_governance_llm.py`

**Why FakeLLM first:** Every test in Tasks 16-24 depends on a deterministic LLM. `FakeLLM` returns canned responses by prompt-hash and records every call for assertion. Without it, the agent loop's tests would either need a real model (slow, hardware-gated) or skip integration coverage (unacceptable per spec §11).

- [ ] **Step 1: Tests**

Create `tests/test_governance_llm.py`:

```python
"""LLMClient Protocol + FakeLLM + LocalQwenLLM smoke."""

from __future__ import annotations

from hashlib import sha256

import pytest

from governance.llm import (
    FakeLLM,
    LLMClient,
    canned_response_for_action,
    prompt_hash,
)


def test_fakellm_satisfies_protocol():
    fake = FakeLLM()
    assert isinstance(fake, LLMClient)


def test_fakellm_records_calls():
    fake = FakeLLM()
    fake.complete("sys", "user1")
    fake.complete("sys", "user2")
    assert len(fake.calls) == 2
    assert fake.calls[0] == {"system": "sys", "user": "user1"}
    assert fake.calls[1] == {"system": "sys", "user": "user2"}


def test_fakellm_returns_canned_response_by_prompt_hash():
    h = prompt_hash("sys", "user")
    fake = FakeLLM(canned={h: '{"action": "no_action"}'})
    assert fake.complete("sys", "user") == '{"action": "no_action"}'


def test_fakellm_returns_default_no_action_when_no_canned_match():
    fake = FakeLLM()
    out = fake.complete("sys", "novel-user")
    assert '"action": "no_action"' in out


def test_canned_response_for_action_emits_valid_json_for_each_action():
    import json as _json
    for action in ("disable_source", "disable_keyword", "tune_threshold", "no_action"):
        body = canned_response_for_action(action, target="X")
        parsed = _json.loads(body)
        assert parsed["action"] == action


def test_fakellm_model_name_is_stable():
    fake = FakeLLM()
    assert fake.model_name() == "fake-llm"
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement**

Create `governance/llm.py`:

```python
"""LLM client surface for the governance agent.

Two implementations:
- FakeLLM: deterministic test double; returns canned responses keyed by
  the SHA256 of (system + user). Records every call.
- LocalQwenLLM: thin wrapper over Ollama's HTTP API at localhost:11434
  (Task 14).

Tasks 16-24 depend on FakeLLM. LocalQwenLLM is exercised only by
the smoke runbook (Task 26) and lives in this module so the Protocol
boundary is one module rather than two.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """The agent's view of an LLM: it sends a system+user pair, gets a string back."""
    def complete(self, system: str, user: str) -> str: ...
    def model_name(self) -> str: ...


def prompt_hash(system: str, user: str) -> str:
    """Deterministic hash for keying canned FakeLLM responses."""
    return sha256((system + "\n---\n" + user).encode("utf-8")).hexdigest()


def canned_response_for_action(action: str, *, target: str = "X") -> str:
    """Build a valid LLM-output JSON for a given action. Used to seed FakeLLM
    in tests without hand-writing JSON each time."""
    if action == "no_action":
        return json.dumps({
            "action": "no_action",
            "target": "",
            "reasoning": "Evidence does not justify a change.",
            "confidence": 0.05,
            "predicted_effect": None,
        })
    return json.dumps({
        "action": action,
        "target": target,
        "reasoning": f"Test reasoning for {action} on {target}.",
        "confidence": 0.85,
        "predicted_effect": {
            "metric": "test_metric",
            "baseline": 0.5,
            "predicted_post_change": 0.4,
            "evaluate_at_days": 7,
        },
    })


@dataclass
class FakeLLM:
    """In-test LLM. Returns canned responses by prompt hash; default to
    no_action when no key matches."""
    canned: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        key = prompt_hash(system, user)
        if key in self.canned:
            return self.canned[key]
        return canned_response_for_action("no_action")

    def model_name(self) -> str:
        return "fake-llm"
```

- [ ] **Step 4: Run + commit**

```bash
git add governance/llm.py tests/test_governance_llm.py
git commit -m "feat(governance): LLMClient Protocol + FakeLLM (Phase 2 Task 13)"
```

---

## Task 14: `governance/llm.py` — `parse_llm_response_to_decision`

**Files:**
- Modify: `governance/llm.py`
- Modify: `tests/test_governance_llm.py`

**Why:** Bridges the LLM's raw JSON output (per Task 10's schema) to a fully-validated `Decision` (Tasks 2-4). Robust parsing here keeps the agent loop in Task 17 short and turns LLM-format errors into specific exceptions the loop can log instead of crashing on.

- [ ] **Step 1: Tests**

Append:

```python
from datetime import datetime, timezone, timedelta

from governance.decision import Decision
from governance.llm import (
    LLMResponseParseError,
    parse_llm_response_to_decision,
)


def test_parse_disable_source_response_to_decision():
    raw = canned_response_for_action("disable_source", target="r/Turkey")
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        model_used="fake-llm",
        evidence_summary={"ingestion_events": 408},
    )
    assert isinstance(d, Decision)
    assert d.action == "disable_source"
    assert d.target == "r/Turkey"
    assert d.confidence == 0.85
    assert d.predicted_effect is not None
    assert d.predicted_effect.metric == "test_metric"
    expected_eval = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)
    assert d.predicted_effect.evaluate_at == expected_eval


def test_parse_no_action_response_to_decision():
    raw = canned_response_for_action("no_action")
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        model_used="fake-llm",
        evidence_summary={},
    )
    assert d.action == "no_action"
    assert d.predicted_effect is None


def test_parse_rejects_malformed_json():
    with pytest.raises(LLMResponseParseError, match="JSON"):
        parse_llm_response_to_decision(
            "not json",
            decision_id="gd_2026-05-02_0001",
            batch_id="gb_2026-05-02_0001",
            decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
            decided_by="x", cadence="fast", model_used="m", evidence_summary={},
        )


def test_parse_rejects_missing_required_field():
    raw = '{"action": "disable_source", "target": "r/X"}'  # missing confidence/reasoning/predicted_effect
    with pytest.raises(LLMResponseParseError, match="required|missing|confidence|reasoning"):
        parse_llm_response_to_decision(
            raw,
            decision_id="gd_2026-05-02_0001",
            batch_id="gb_2026-05-02_0001",
            decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
            decided_by="x", cadence="fast", model_used="m", evidence_summary={},
        )


def test_parse_clamps_evaluate_at_days_into_valid_range():
    """If LLM emits evaluate_at_days outside [1, 30], parser clamps and
    proceeds — better to apply a slightly-off evaluation date than to
    drop an otherwise-valid decision."""
    import json as _j
    body = _j.loads(canned_response_for_action("disable_source", target="r/X"))
    body["predicted_effect"]["evaluate_at_days"] = 100  # out of range
    raw = _j.dumps(body)
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="x", cadence="fast", model_used="m", evidence_summary={},
    )
    expected_eval = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc) + timedelta(days=30)
    assert d.predicted_effect.evaluate_at == expected_eval


def test_parse_strips_markdown_fences_around_json():
    """Some local models like to emit ```json ... ``` even when told not to.
    The parser strips a single fence layer."""
    raw = "```json\n" + canned_response_for_action("no_action") + "\n```"
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="x", cadence="fast", model_used="m", evidence_summary={},
    )
    assert d.action == "no_action"
```

- [ ] **Step 2: Implement**

Append to `governance/llm.py`:

```python
import re
from datetime import datetime, timedelta
from governance.decision import Decision, PredictedEffect


class LLMResponseParseError(ValueError):
    """Raised when an LLM response cannot be turned into a valid Decision."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
_REQUIRED_FIELDS = ("action", "target", "reasoning", "confidence", "predicted_effect")


def _strip_fences(raw: str) -> str:
    m = _FENCE_RE.match(raw)
    return m.group(1) if m else raw


def parse_llm_response_to_decision(
    raw: str,
    *,
    decision_id: str,
    batch_id: str,
    decided_at: datetime,
    decided_by: str,
    cadence: str,
    model_used: str,
    evidence_summary: dict[str, Any],
) -> Decision:
    """Validate raw LLM JSON and produce a Decision instance.

    Raises LLMResponseParseError on schema violations. Decision-level
    invariants (confidence range, ID format, etc.) are enforced by
    Decision.__post_init__ and surface as ValueError; we don't catch
    those — the Decision's own validation is the right boundary."""
    try:
        body = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"LLM output is not valid JSON: {exc}") from exc

    missing = [f for f in _REQUIRED_FIELDS if f not in body]
    if missing:
        raise LLMResponseParseError(f"LLM output missing required fields: {missing}")

    action = body["action"]
    target = body.get("target", "") or ""
    reasoning = body["reasoning"]
    confidence = float(body["confidence"])
    pe_in = body["predicted_effect"]

    predicted_effect: PredictedEffect | None
    if action == "no_action" or pe_in is None:
        predicted_effect = None
    else:
        days = int(pe_in.get("evaluate_at_days", 7))
        days = max(1, min(30, days))  # clamp into valid range
        predicted_effect = PredictedEffect(
            metric=str(pe_in["metric"]),
            baseline=float(pe_in["baseline"]),
            predicted_post_change=float(pe_in["predicted_post_change"]),
            evaluate_at=decided_at + timedelta(days=days),
        )

    proposed_change: dict[str, Any]
    if action == "disable_source":
        proposed_change = {"before": "source_active", "after": "source_disabled", "expires_at": None}
    elif action == "disable_keyword":
        proposed_change = {"before": "keyword_active", "after": "keyword_disabled", "expires_at": None}
    elif action == "tune_threshold":
        proposed_change = {
            "before": pe_in.get("baseline") if pe_in else None,
            "after": pe_in.get("predicted_post_change") if pe_in else None,
            "expires_at": None,
        }
    else:
        proposed_change = {}

    return Decision(
        decision_id=decision_id,
        batch_id=batch_id,
        decided_at=decided_at,
        decided_by=decided_by,
        cadence=cadence,  # type: ignore[arg-type]
        action=action,
        target=target,
        proposed_change=proposed_change,
        confidence=confidence,
        reasoning=reasoning,
        evidence_summary=evidence_summary,
        predicted_effect=predicted_effect,
        model_used=model_used,
    )
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/llm.py tests/test_governance_llm.py
git commit -m "feat(governance): parse_llm_response_to_decision (Phase 2 Task 14)"
```

---

## Task 15: `governance/llm.py` — `LocalQwenLLM` Ollama wrapper

**Files:**
- Modify: `governance/llm.py`
- Modify: `tests/test_governance_llm.py`

**Why:** The real model integration lives behind the same `LLMClient` Protocol. The implementation is small (one HTTP POST to Ollama's `/api/generate`) but must use the same `format=json` + `temperature=0.0` settings as the existing `analysis/signal_analyzer.py` LLM call site to keep behavior consistent.

- [ ] **Step 1: Tests with mocked HTTP**

Append:

```python
def test_local_qwen_llm_satisfies_protocol():
    from governance.llm import LocalQwenLLM
    llm = LocalQwenLLM(model="qwen3:14b")
    assert isinstance(llm, LLMClient)
    assert llm.model_name() == "qwen3:14b"


def test_local_qwen_llm_honors_governance_llm_model_env_var(monkeypatch):
    """Hardware-conditional model selection: env var pins the model so the
    launchd plist controls MacBook (qwen3:8b) vs Mac Studio (qwen3:14b)
    without code edits."""
    from governance.llm import LocalQwenLLM
    monkeypatch.setenv("GOVERNANCE_LLM_MODEL", "qwen3:8b")
    assert LocalQwenLLM().model_name() == "qwen3:8b"


def test_local_qwen_llm_explicit_model_overrides_env_var(monkeypatch):
    from governance.llm import LocalQwenLLM
    monkeypatch.setenv("GOVERNANCE_LLM_MODEL", "qwen3:8b")
    # Explicit constructor arg wins over env var.
    assert LocalQwenLLM(model="qwen3:14b").model_name() == "qwen3:14b"


def test_local_qwen_llm_posts_to_ollama_and_returns_response_text(monkeypatch):
    from governance import llm as llm_module
    from governance.llm import LocalQwenLLM

    captured = {}

    class _StubResponse:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._body

    def _stub_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return _StubResponse(json.dumps({"response": '{"action": "no_action"}'}).encode("utf-8"))

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", _stub_urlopen)

    out = LocalQwenLLM(model="qwen3:14b").complete("sys", "user")
    assert out == '{"action": "no_action"}'
    assert captured["url"].endswith("/api/generate")
    payload = json.loads(captured["body"])
    assert payload["model"] == "qwen3:14b"
    assert payload["system"] == "sys"
    assert payload["prompt"] == "user"
    assert payload["format"] == "json"
    assert payload["options"]["temperature"] == 0.0
```

- [ ] **Step 2: Implement**

Add to `governance/llm.py`:

```python
import urllib.request


class LocalQwenLLM:
    """Ollama HTTP wrapper. Default base_url tracks the project's existing
    OLLAMA_BASE_URL convention (utils via signal_analyzer); model is
    hardware-conditional: qwen3:8b on MacBook (18GB), qwen3:14b on Mac
    Studio (post-2026-04-29)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
    ) -> None:
        # Model precedence: explicit constructor arg > GOVERNANCE_LLM_MODEL
        # env var > hardcoded default. The env-var path lets the launchd
        # plist (Task 25) pin the model per host without code edits.
        self.model = model or os.getenv("GOVERNANCE_LLM_MODEL", "qwen3:14b")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0},
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("response", ""))

    def model_name(self) -> str:
        return self.model
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/llm.py tests/test_governance_llm.py
git commit -m "feat(governance): LocalQwenLLM Ollama wrapper (Phase 2 Task 15)

POSTs to {base_url}/api/generate with format=json + temperature=0.0,
matching analysis/signal_analyzer.py's existing call site convention.
Tests use a monkeypatched urlopen so no real Ollama server is needed."
```

---

## Task 16: `governance/agent.py` — module skeleton + ID generators + load_state

**Files:**
- Create: `governance/agent.py`
- Create: `governance/__main__.py`
- Test: `tests/test_governance_agent_unit.py`

**Why now:** The agent has many small responsibilities that benefit from being introduced one at a time. This task lands the module skeleton, the `decision_id` / `batch_id` / `cycle_id` generators, and the `load_state()` helper that reads runtime overrides + checks kill switches. Tasks 17-21 add the orchestration layers on top.

- [ ] **Step 1: Tests for ID generators + load_state**

Create `tests/test_governance_agent_unit.py`:

```python
"""Unit tests for governance.agent helpers (ID generators, load_state).
Integration tests live in test_governance_agent_integration.py."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from governance.agent import (
    AgentLoadedState,
    KillSwitchActive,
    generate_batch_id,
    generate_cycle_id,
    generate_decision_id,
    load_state,
)


def test_generate_decision_id_format():
    now = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)
    out = generate_decision_id(now=now, sequence=42)
    assert out == "gd_2026-05-02_0042"


def test_generate_batch_id_format():
    now = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)
    out = generate_batch_id(now=now, sequence=12)
    assert out == "gb_2026-05-02_0012"


def test_generate_cycle_id_uses_seconds_resolution():
    now = datetime(2026, 5, 2, 14, 30, 17, tzinfo=timezone.utc)
    out = generate_cycle_id(now=now)
    assert out == "gc_2026-05-02_143017"


def test_load_state_returns_loaded_state(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    # Write an empty (default) state. utils.runtime_overrides has helpers.
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test",
            mode="shadow",
            applied_disabled_sources=[],
        ),
        overrides_path,
    )
    # Ensure no kill switch
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    state = load_state(overrides_path=overrides_path)
    assert isinstance(state, AgentLoadedState)
    assert state.mode == "shadow"
    assert state.kill_switch_disabled is False
    assert state.kill_switch_readonly is False
    assert state.reader is not None


def test_load_state_raises_when_kill_switch_disabled(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
    with pytest.raises(KillSwitchActive, match="DISABLED"):
        load_state(overrides_path=overrides_path)


def test_load_state_marks_readonly_without_raising(tmp_path, monkeypatch):
    overrides_path = tmp_path / "overrides.yaml"
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.setenv("GOVERNANCE_READONLY", "true")

    state = load_state(overrides_path=overrides_path)
    assert state.kill_switch_readonly is True
    assert state.kill_switch_disabled is False
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement skeleton**

Create `governance/agent.py`:

```python
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
    ks = KillSwitch.from_env()
    if ks.disabled:
        raise KillSwitchActive(
            "GOVERNANCE_DISABLED is set — agent refuses to run"
        )
    reader = RuntimeOverridesReader(path=overrides_path)
    reader.reload()
    return AgentLoadedState(
        reader=reader,
        state=reader.snapshot,
        mode=reader.snapshot.mode,
        kill_switch_disabled=False,
        kill_switch_readonly=ks.readonly,
    )
```

Create `governance/__main__.py`:

```python
"""Allows: python -m governance --cadence fast"""

from governance.agent import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run + commit (note: `main` is not yet defined; tests in this task don't exercise it, so they pass)**

Run: `pytest tests/test_governance_agent_unit.py -v`

Expected: 5 passed.

```bash
git add governance/agent.py governance/__main__.py tests/test_governance_agent_unit.py
git commit -m "feat(governance): agent skeleton + ID generators + load_state (Phase 2 Task 16)"
```

**Post-implementation note: signature drift from plan (recorded 2026-04-25, same commit as Task 16)**

Step 3's embedded `load_state()` body assumed two APIs that do not exist as written. Recording here so a future agent (Claude, Codex, or otherwise) re-reading this plan does not believe the embedded Step 3 code reflects what was committed.

**What the plan-as-written assumed:**

```python
ks = KillSwitch.from_env()
if ks.disabled: ...
# ...
return AgentLoadedState(
    reader=reader,
    state=reader.snapshot,         # attribute access
    mode=reader.snapshot.mode,     # attribute-of-attribute access
    ...
    kill_switch_readonly=ks.readonly,
)
```

**What the actual library exposes** (verified 2026-04-25):

| Name | File:line | Real API |
|---|---|---|
| `KillSwitch` | `governance/safety.py:59` | Plain class with no factory. Construct via `KillSwitch()` (zero-arg). Status is checked via instance methods `is_disabled()` and `is_readonly()` — no `disabled` / `readonly` attributes. |
| `RuntimeOverridesReader.snapshot` | `utils/runtime_overrides.py:518` | **Method**, not property. `reader.snapshot()` returns the current `OverridesState`; `reader.snapshot` (no call) returns the bound method. |

**Workaround as shipped** (`governance/agent.py` `load_state()`):

1. `ks = KillSwitch()` — drop the non-existent `from_env()` factory.
2. `ks.is_disabled()` and `ks.is_readonly()` — call the methods rather than read attributes.
3. Capture `state_now = reader.snapshot()` once (single method invocation), then build `AgentLoadedState(state=state_now, mode=state_now.mode, ...)`. Avoids two method calls and removes the bound-method-stored-as-attribute trap.

**Impact on downstream tasks: none.**
- `AgentLoadedState`'s public surface (the fields the tests read: `mode`, `kill_switch_disabled`, `kill_switch_readonly`, `reader`) is unchanged.
- Tasks 17, 18, 19's references to the loaded state read these public fields, not the internal call mechanics.
- The five Phase 1 dependencies (`AuditLogger`, `KillSwitch`, `SafetyConfig`, `OverridesState`, `RuntimeOverridesReader`) are all imported and used as expected.

**For future re-execution:** if Task 16 is ever re-implemented from scratch, **do not copy Step 3's embedded code verbatim** — both `KillSwitch.from_env()` and `reader.snapshot.mode` will fail (`AttributeError` on the first; `AttributeError: 'function' object has no attribute 'mode'` on the second). Use the workaround pattern above; verify APIs first with:

```bash
grep -n "def is_disabled\|def is_readonly\|def from_env\|def snapshot\|@property" governance/safety.py utils/runtime_overrides.py
```

The shipped tests in `tests/test_governance_agent_unit.py` (6 tests, plan said 5; readonly test is the 6th) cover the actual call path — if they pass, the workaround is intact.

---

## Task 17: `governance/agent.py` — `run_cycle` core (no LLM yet)

**Files:**
- Modify: `governance/agent.py`
- Modify: `tests/test_governance_agent_unit.py`

**Why a "no LLM yet" intermediate:** The `run_cycle` function has three responsibilities: (1) emit `GOVERNANCE_CYCLE_START`, (2) iterate candidates and produce Decisions, (3) emit `GOVERNANCE_CYCLE_END`. Adding the LLM iteration last (Task 18) keeps the control-flow tests independent from the LLM-mocking ones.

- [ ] **Step 1: Test the skeleton emits start/end events**

Append:

```python
def test_run_cycle_emits_start_and_end_events_with_zero_candidates(tmp_path, monkeypatch):
    from governance.agent import run_cycle, AgentLoadedState
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger

    overrides_path = tmp_path / "overrides.yaml"
    decisions_dir = tmp_path / "logs" / "governance"
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")

    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    state = AgentLoadedState(
        reader=None, state=None, mode="shadow",
        kill_switch_disabled=False, kill_switch_readonly=False,
    )

    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=tmp_path / "paper.db",
        market_provider=None,
    )
    logger = AuditLogger(decisions_dir)
    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter,
        llm=None,  # Task 18 wires LLM in
        audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[],  # force zero candidates for this test
    )
    assert rc == 0

    # Read the audit log: should have START and END
    log_files = sorted(decisions_dir.glob("decisions.jsonl*"))
    assert log_files, "audit logger did not write any decisions log file"
    body = log_files[-1].read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(line)["type"] for line in body]
    assert "GOVERNANCE_CYCLE_START" in types
    assert "GOVERNANCE_CYCLE_END" in types
```

- [ ] **Step 2: Implement**

Append to `governance/agent.py`:

```python
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

    audit_logger.write({
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
    audit_logger.write({
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
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/agent.py tests/test_governance_agent_unit.py
git commit -m "feat(governance): run_cycle skeleton + start/end audit events (Phase 2 Task 17)"
```

---

## Task 18: `governance/agent.py` — LLM-driven candidate iteration

**Files:**
- Modify: `governance/agent.py`
- Modify: `tests/test_governance_agent_unit.py`

- [ ] **Step 1: Test with FakeLLM**

Append:

```python
def test_run_cycle_iterates_candidates_and_records_decisions(tmp_path, monkeypatch):
    from governance.agent import run_cycle
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.evidence import Candidate
    from governance.llm import FakeLLM, canned_response_for_action, prompt_hash
    from governance.prompts import render_prompt

    overrides_path = tmp_path / "overrides.yaml"
    decisions_dir = tmp_path / "logs" / "governance"
    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")

    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[],
        ),
        overrides_path,
    )
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)

    cand = Candidate(
        action="disable_source", target="r/Turkey",
        evidence_pointer={"reddit_sub_index": 0},
    )
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=tmp_path / "paper.db",
        market_provider=lambda: [],  # zero markets
    )
    # Pre-compute the prompt this candidate will produce so FakeLLM can match it.
    from governance.evidence import compose_evidence_for_candidate
    fake_audit = {
        "reddit": {"subs": [{"source": "r/Turkey", "ingestion": 408,
                              "fresh_passes": 7, "matches": 0,
                              "classification": "all_stale"}]},
    }
    evidence = compose_evidence_for_candidate(cand, fake_audit, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)

    fake = FakeLLM(canned={
        prompt_hash(sys_p, user_p): canned_response_for_action("disable_source", target="r/Turkey"),
    })
    logger = AuditLogger(decisions_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter,
        llm=fake,
        audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand],
        audit_data_override=fake_audit,  # injected for the test
    )
    assert rc == 0

    log_lines = (decisions_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(line)["type"] for line in log_lines]
    assert types.count("GOVERNANCE_DECISION") == 1
    decision_record = next(json.loads(l) for l in log_lines if json.loads(l)["type"] == "GOVERNANCE_DECISION")
    assert decision_record["action"] == "disable_source"
    assert decision_record["target"] == "r/Turkey"
    assert decision_record["shadow_mode"] is True
    assert decision_record["applied"] is False  # shadow mode never applies
```

- [ ] **Step 2: Implement candidate iteration**

Inside `run_cycle`, replace the `# Task 18 will fill in...` placeholder with:

```python
    if audit_data_override is not None:
        audit_data = audit_data_override
    elif candidate_override is None:
        audit_data = adapter.collect_audit_data(window=_cadence_window(cadence))
    else:
        audit_data = {}

    safety = safety_config or SafetyConfig.from_env()
    batch_id = generate_batch_id(now=now, sequence=1)
    decision_seq = 0

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
                decided_by=f"governance-agent-v0.2.0",
                cadence=cadence,
                model_used=llm.model_name(),
                evidence_summary=summarize_evidence_for_audit(evidence),
            )
        except LLMResponseParseError as exc:
            audit_logger.write({
                "type": "GOVERNANCE_DECISION_PARSE_ERROR",
                "cycle_id": cycle_id,
                "candidate_action": cand.action,
                "candidate_target": cand.target,
                "error": str(exc),
            })
            continue
        except ValueError as exc:
            audit_logger.write({
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

        audit_logger.write(decision.to_audit_record(
            applied=applied,
            shadow_mode=shadow_mode,
            safety_checks_passed=safety_checks,
        ))
```

Add the `_evaluate_safety` helper and accept new kwargs in the signature. Update `run_cycle` signature line:

```python
def run_cycle(
    *,
    cadence: str,
    loaded_state: AgentLoadedState,
    adapter: GovernanceAdapter,
    llm: LLMClient | None,
    audit_logger: AuditLogger,
    overrides_path: Path,
    candidate_override: Sequence[Candidate] | None = None,
    audit_data_override: dict[str, Any] | None = None,
    safety_config: SafetyConfig | None = None,
) -> int:
```

Add `_evaluate_safety` helper (keep below `run_cycle`):

```python
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
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/agent.py tests/test_governance_agent_unit.py
git commit -m "feat(governance): LLM-driven candidate iteration in run_cycle (Phase 2 Task 18)

Per candidate: render prompt, call LLM, parse to Decision, evaluate
safety, write audit record. Shadow mode (the Phase 2 default) forces
applied=False on every decision regardless of confidence; the safety
fields in the audit record still reflect the would-have-applied state
so the trust dataset is meaningful."
```

---

## Task 19: `governance/agent.py` — applied-decisions write to runtime overrides

**Files:**
- Modify: `governance/agent.py`
- Modify: `tests/test_governance_agent_unit.py`

**Why:** When the agent does apply decisions (Phase 3 mode=real, but the wiring lives here in Phase 2 so it's exercised by tests), the decisions must land in `data/runtime_overrides.yaml` via `atomic_write_state`. Phase 2's shadow mode keeps this code path covered without ever exercising it on a real bot.

- [ ] **Step 1: Test that the wiring exists but never fires in shadow mode**

Append:

```python
def test_run_cycle_does_not_modify_overrides_in_shadow_mode(tmp_path, monkeypatch):
    """Even with high-confidence applicable decisions, shadow mode must
    never write `applied` overrides to disk."""
    from governance.agent import run_cycle
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.evidence import Candidate
    from governance.llm import FakeLLM, canned_response_for_action, prompt_hash
    from governance.prompts import render_prompt
    from governance.evidence import compose_evidence_for_candidate
    from utils.runtime_overrides import OverridesState, atomic_write_state, RuntimeOverridesReader

    overrides_path = tmp_path / "overrides.yaml"
    initial = OverridesState(
        version=1,
        updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
        updated_by="test", mode="shadow", applied_disabled_sources=[],
    )
    atomic_write_state(initial, overrides_path)

    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db",
        market_provider=lambda: [],
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    fake_audit = {
        "reddit": {"subs": [{"source": "r/Turkey", "ingestion": 408,
                              "fresh_passes": 7, "matches": 0,
                              "classification": "all_stale"}]},
    }
    evidence = compose_evidence_for_candidate(cand, fake_audit, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    fake = FakeLLM(canned={
        prompt_hash(sys_p, user_p): canned_response_for_action("disable_source", target="r/Turkey"),
    })
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    logger = AuditLogger(tmp_path / "logs" / "governance")

    run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter,
        llm=fake,
        audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand],
        audit_data_override=fake_audit,
    )

    # Reload overrides — applied list must still be empty.
    after = RuntimeOverridesReader(path=overrides_path)
    after.reload()
    assert after.snapshot.applied_disabled_sources == [], (
        "Shadow mode wrote an applied override — this is the load-bearing safety bug"
    )
```

- [ ] **Step 2: Implement (or no-op if Task 18 already enforces shadow mode correctly)**

Audit the Task 18 implementation: `_evaluate_safety` returns `applied=False` whenever `mode != "real"`. The audit log records `applied=False`, and the `to_audit_record(applied=False, ...)` path does not touch `overrides_path`. **No code change is needed in this task** — the test should pass against Task 18's implementation. Add the test as a regression guard.

If the test fails, fix the agent loop to ensure `applied=True` is the only path that calls `atomic_write_state`. (Task 18 should already have done this; this task makes it explicit and tested.)

Future Phase 3: this is also where `atomic_write_state(updated_state, overrides_path)` will land for `applied=True` decisions. Add a placeholder comment:

```python
# Phase 3 will gate the following on applied=True:
# new_state = state.with_applied_added([d.to_disabled_source() for d in applied_decisions])
# atomic_write_state(new_state, overrides_path)
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/agent.py tests/test_governance_agent_unit.py
git commit -m "feat(governance): regression-guard shadow-mode never writes applied (Phase 2 Task 19)

Shadow mode is the load-bearing safety property of Phase 2. This test
asserts that even with a high-confidence disable_source decision and a
matching FakeLLM canned response, the runtime overrides file's applied
list remains empty after a cycle.

Phase 3 will replace the placeholder comment with atomic_write_state
gated on applied=True."
```

---

## Task 20: `governance/agent.py` — `main()` CLI

**Files:**
- Modify: `governance/agent.py`
- Modify: `tests/test_governance_agent_unit.py`

- [ ] **Step 1: Test argv parsing + dispatch**

Append:

```python
def test_main_with_dry_run_exits_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    # Point environment at tmp paths via env vars (cleaner than monkeypatching
    # constants).
    overrides_path = tmp_path / "overrides.yaml"
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[]),
        overrides_path,
    )
    monkeypatch.setenv("GOVERNANCE_OVERRIDES_PATH", str(overrides_path))
    monkeypatch.setenv("GOVERNANCE_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GOVERNANCE_TRADE_LOG_PATH", str(tmp_path / "trades.jsonl"))
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("GOVERNANCE_PAPER_DB_PATH", str(tmp_path / "paper.db"))

    from governance.agent import main
    rc = main(argv=["--cadence", "fast", "--llm", "fake", "--dry-run"])
    assert rc == 0


def test_main_kill_switch_disabled_exits_with_code_2(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
    monkeypatch.setenv("GOVERNANCE_OVERRIDES_PATH", str(tmp_path / "overrides.yaml"))
    monkeypatch.setenv("GOVERNANCE_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GOVERNANCE_TRADE_LOG_PATH", str(tmp_path / "trades.jsonl"))
    monkeypatch.setenv("GOVERNANCE_PAPER_DB_PATH", str(tmp_path / "paper.db"))
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test", mode="shadow", applied_disabled_sources=[]),
        tmp_path / "overrides.yaml",
    )
    from governance.agent import main
    rc = main(argv=["--cadence", "fast", "--llm", "fake"])
    assert rc == 2
```

- [ ] **Step 2: Implement `main`**

Append:

```python
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
    audit_logger = AuditLogger(logs_dir)

    return run_cycle(
        cadence=args.cadence,
        loaded_state=loaded,
        adapter=adapter,
        llm=llm,
        audit_logger=audit_logger,
        overrides_path=overrides_path,
    )
```

- [ ] **Step 3: Run + commit**

```bash
git add governance/agent.py tests/test_governance_agent_unit.py
git commit -m "feat(governance): main() CLI dispatch (Phase 2 Task 20)

python -m governance --cadence fast|deep|weekly_review [--llm fake|qwen]

Exit codes:
  0 — cycle completed (decisions may or may not have been made)
  2 — kill switch active (GOVERNANCE_DISABLED=true)
  *other non-zero* — uncaught exception (will be visible in launchd's
    error log)

Path config via env vars: GOVERNANCE_OVERRIDES_PATH,
GOVERNANCE_LOGS_DIR, GOVERNANCE_TRADE_LOG_PATH,
GOVERNANCE_PAPER_DB_PATH. All default to repo-relative paths."
```

---

## Task 21: Run full suite + sanity check

**Files:** none

- [ ] **Step 1: Full test suite**

Run: `pytest -q`

Expected: prior count + Phase 2 unit tests pass. Roughly +60 tests vs. start of Phase 2.

- [ ] **Step 2: Manual smoke**

```bash
GOVERNANCE_DISABLED=false \
GOVERNANCE_OVERRIDES_PATH=/tmp/test_overrides.yaml \
GOVERNANCE_LOGS_DIR=/tmp/test_governance_logs \
GOVERNANCE_TRADE_LOG_PATH=logs/trades \
GOVERNANCE_PAPER_DB_PATH=data/paper_trades.db \
python -m governance --cadence fast --llm fake
```

Expected: exit 0; `/tmp/test_governance_logs/decisions.jsonl` has at least `GOVERNANCE_CYCLE_START` + `GOVERNANCE_CYCLE_END` records.

- [ ] **Step 3: No commit — checkpoint only**

If anything failed, fix before proceeding to integration tests.

---

## Task 22: `tests/test_governance_agent_integration.py` — end-to-end shadow cycle

**Files:**
- Create: `tests/test_governance_agent_integration.py`

**Why:** A single end-to-end test that exercises the full pipeline (load state → adapter → evidence → render prompt → FakeLLM → parse → safety eval → audit write) on real (tmp) filesystem with three Reddit candidates and verifies (a) all three decisions land in the audit log, (b) none land in `applied`, (c) the audit log is parseable JSONL with valid `GOVERNANCE_DECISION` shapes.

- [ ] **Step 1: Test**

Create `tests/test_governance_agent_integration.py`:

```python
"""End-to-end Phase 2 cycle: real filesystem, FakeLLM, three candidates.

Asserts the load-bearing safety property — shadow mode never writes
applied — plus the audit-log fidelity to spec §6.2."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def tmp_overrides(tmp_path):
    from utils.runtime_overrides import OverridesState, atomic_write_state
    p = tmp_path / "overrides.yaml"
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="test",
            mode="shadow",
            applied_disabled_sources=[],
        ),
        p,
    )
    return p


def test_full_shadow_cycle_three_reddit_candidates(tmp_path, tmp_overrides, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import (
        Candidate,
        compose_evidence_for_candidate,
        select_candidates_for_cadence,
    )
    from governance.llm import FakeLLM, canned_response_for_action, prompt_hash
    from governance.prompts import render_prompt
    from utils.runtime_overrides import RuntimeOverridesReader

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    trade_log = tmp_path / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = tmp_path / "paper.db"
    decisions_dir = tmp_path / "logs" / "governance"
    adapter = KalshiGovernanceAdapter(
        trade_log_path=trade_log, paper_db_path=paper_db,
        market_provider=lambda: [],
    )

    audit_data = {
        "alignment": {"pairs": [], "overall_anchor_rate": 0.0, "overall_n": 0},
        "keywords": {"no_keyword_misses": 0, "candidate_phrases": []},
        "reddit": {"subs": [
            {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
             "matches": 0, "classification": "all_stale"},
            {"source": "r/pakistan", "ingestion": 80, "fresh_passes": 5,
             "matches": 0, "classification": "no_matches"},
            {"source": "r/Syria", "ingestion": 100, "fresh_passes": 0,
             "matches": 0, "classification": "all_stale"},
        ]},
        "freshness": {"sources": {}},
    }
    candidates = select_candidates_for_cadence(audit_data, cadence="deep")
    assert len(candidates) == 3, "test fixture should produce three Reddit candidates"

    canned: dict[str, str] = {}
    for cand in candidates:
        evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
        sys_p, user_p = render_prompt(cand.action, evidence)
        canned[prompt_hash(sys_p, user_p)] = canned_response_for_action(
            cand.action, target=cand.target,
        )
    fake = FakeLLM(canned=canned)
    logger = AuditLogger(decisions_dir)

    rc = run_cycle(
        cadence="deep",
        loaded_state=load_state(overrides_path=tmp_overrides),
        adapter=adapter,
        llm=fake,
        audit_logger=logger,
        overrides_path=tmp_overrides,
        candidate_override=candidates,
        audit_data_override=audit_data,
    )
    assert rc == 0

    log_lines = (decisions_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in log_lines]
    decisions = [r for r in records if r["type"] == "GOVERNANCE_DECISION"]
    assert len(decisions) == 3
    assert all(r["shadow_mode"] is True for r in decisions)
    assert all(r["applied"] is False for r in decisions)
    assert all(r["action"] == "disable_source" for r in decisions)
    assert {r["target"] for r in decisions} == {"r/Turkey", "r/pakistan", "r/Syria"}
    # Spec §6.2 fidelity:
    for r in decisions:
        for k in ("decision_id", "batch_id", "decided_at", "decided_by",
                  "cadence", "action", "target", "proposed_change",
                  "model_used", "confidence", "reasoning",
                  "evidence_summary", "predicted_effect",
                  "outcome", "applied", "shadow_mode",
                  "safety_checks_passed"):
            assert k in r, f"spec §6.2 field {k} missing from audit record"

    # Load-bearing safety property: applied list still empty.
    after = RuntimeOverridesReader(path=tmp_overrides)
    after.reload()
    assert after.snapshot.applied_disabled_sources == []
```

- [ ] **Step 2: Run + commit**

```bash
git add tests/test_governance_agent_integration.py
git commit -m "test(governance): end-to-end shadow cycle with 3 candidates (Phase 2 Task 22)"
```

---

## Task 23: `tests/test_governance_agent_chaos.py` — adversarial inputs

**Files:**
- Create: `tests/test_governance_agent_chaos.py`

Per spec §11.2 ("Every safety mechanism gets a chaos test") and §11.5 ("Test failure = stop, root-cause analysis, fix"). Cover: kill-switch trips mid-cycle, malformed LLM JSON, validation failures, audit-log directory missing, runtime-overrides file corrupt.

- [ ] **Step 1: Test**

Create `tests/test_governance_agent_chaos.py`:

```python
"""Chaos tests: malformed inputs and adversarial LLM outputs must
degrade gracefully, never corrupt state."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


def test_kill_switch_disabled_short_circuits_main(tmp_path, monkeypatch):
    from governance.agent import main
    monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
    monkeypatch.setenv("GOVERNANCE_OVERRIDES_PATH", str(tmp_path / "ovr.yaml"))
    monkeypatch.setenv("GOVERNANCE_LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("GOVERNANCE_TRADE_LOG_PATH", str(tmp_path / "trades.jsonl"))
    monkeypatch.setenv("GOVERNANCE_PAPER_DB_PATH", str(tmp_path / "paper.db"))
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    from utils.runtime_overrides import OverridesState, atomic_write_state
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        tmp_path / "ovr.yaml",
    )
    rc = main(argv=["--cadence", "fast", "--llm", "fake"])
    assert rc == 2


def test_malformed_llm_json_logged_and_skipped(tmp_path, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from governance.evidence import compose_evidence_for_candidate
    from utils.runtime_overrides import OverridesState, atomic_write_state

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db", market_provider=lambda: [],
    )
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = {"reddit": {"subs": [
        {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): "this is not json at all"})
    logs_dir = tmp_path / "logs" / "governance"
    logger = AuditLogger(logs_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
    )
    assert rc == 0
    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(l)["type"] for l in body]
    assert "GOVERNANCE_DECISION_PARSE_ERROR" in types
    assert "GOVERNANCE_CYCLE_END" in types  # cycle completes despite the error


def test_validation_failure_caught_and_logged(tmp_path, monkeypatch):
    """LLM returns valid JSON but with confidence=2.0 — Decision.__post_init__
    raises ValueError, which the agent must catch and log as a validation
    error."""
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate, compose_evidence_for_candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from utils.runtime_overrides import OverridesState, atomic_write_state

    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode="shadow", applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db", market_provider=lambda: [],
    )
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = {"reddit": {"subs": [
        {"source": "r/Turkey", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    bad_response = json.dumps({
        "action": "disable_source",
        "target": "r/Turkey",
        "reasoning": "x",
        "confidence": 2.0,  # invalid; will fail Decision.__post_init__
        "predicted_effect": {
            "metric": "m", "baseline": 0.0,
            "predicted_post_change": 0.0, "evaluate_at_days": 7,
        },
    })
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): bad_response})
    logs_dir = tmp_path / "logs" / "governance"
    logger = AuditLogger(logs_dir)

    rc = run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
    )
    assert rc == 0
    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    types = [json.loads(l)["type"] for l in body]
    assert "GOVERNANCE_DECISION_VALIDATION_ERROR" in types
```

- [ ] **Step 2: Run + commit**

```bash
git add tests/test_governance_agent_chaos.py
git commit -m "test(governance): chaos tests — kill switch, malformed JSON, validation (Phase 2 Task 23)"
```

---

## Task 24: `tests/test_governance_agent_property.py` — Hypothesis safety invariants

**Files:**
- Create: `tests/test_governance_agent_property.py`

**Why:** Per spec §11.1 — "Property tests (Hypothesis): auto-apply only above confidence threshold; batch-violations always atomic." A property test over arbitrary FakeLLM-response sequences + arbitrary `SafetyConfig` confidence thresholds asserts the load-bearing invariant: **no decision below the threshold ever has `applied=True` in the audit log.**

- [ ] **Step 1: Test**

Create `tests/test_governance_agent_property.py`:

```python
"""Hypothesis property tests for the governance agent's safety invariants.

Core invariant: no decision with confidence < safety.confidence_threshold
ever has applied=True in the audit log, regardless of mode or kill-switch
state. This is the load-bearing correctness property of Phase 2's
shadow→real promotion logic."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st

import pytest


def _fixed_audit_data():
    return {"reddit": {"subs": [
        {"source": "r/Test", "ingestion": 408, "fresh_passes": 7,
         "matches": 0, "classification": "all_stale"}
    ]}}


@settings(max_examples=30, deadline=None)
@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    threshold=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mode=st.sampled_from(["shadow", "real"]),
)
def test_below_threshold_never_applies(tmp_path_factory, confidence, threshold, mode, monkeypatch):
    from governance.adapter import KalshiGovernanceAdapter
    from governance.audit import AuditLogger
    from governance.agent import load_state, run_cycle
    from governance.evidence import Candidate, compose_evidence_for_candidate
    from governance.llm import FakeLLM, prompt_hash
    from governance.prompts import render_prompt
    from governance.safety import SafetyConfig
    from utils.runtime_overrides import OverridesState, atomic_write_state

    tmp_path = tmp_path_factory.mktemp("hyp")
    monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
    monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)

    overrides_path = tmp_path / "ovr.yaml"
    atomic_write_state(
        OverridesState(version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="t", mode=mode, applied_disabled_sources=[]),
        overrides_path,
    )
    (tmp_path / "trades.jsonl").write_text("", encoding="utf-8")

    cand = Candidate(action="disable_source", target="r/Test",
                     evidence_pointer={"reddit_sub_index": 0})
    audit_data = _fixed_audit_data()
    adapter = KalshiGovernanceAdapter(
        trade_log_path=tmp_path / "trades.jsonl",
        paper_db_path=tmp_path / "paper.db",
        market_provider=lambda: [],
    )
    evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
    sys_p, user_p = render_prompt("disable_source", evidence)
    response = json.dumps({
        "action": "disable_source",
        "target": "r/Test",
        "reasoning": "Hypothesis-generated.",
        "confidence": confidence,
        "predicted_effect": {
            "metric": "m", "baseline": 0.5,
            "predicted_post_change": 0.4, "evaluate_at_days": 7,
        },
    })
    fake = FakeLLM(canned={prompt_hash(sys_p, user_p): response})

    logs_dir = tmp_path / "logs"
    logger = AuditLogger(logs_dir)
    safety = SafetyConfig(
        confidence_threshold=threshold,
        max_changes_per_run=10,
        max_disable_per_batch=5,
        max_keyword_changes_per_batch=5,
        max_threshold_changes_per_batch=3,
        max_disable_fraction_of_active_sources=0.20,
    )

    run_cycle(
        cadence="fast",
        loaded_state=load_state(overrides_path=overrides_path),
        adapter=adapter, llm=fake, audit_logger=logger,
        overrides_path=overrides_path,
        candidate_override=[cand], audit_data_override=audit_data,
        safety_config=safety,
    )

    body = (logs_dir / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    decisions = [json.loads(l) for l in body if json.loads(l).get("type") == "GOVERNANCE_DECISION"]
    for d in decisions:
        if d["confidence"] < threshold:
            assert d["applied"] is False, (
                f"INVARIANT VIOLATED: confidence={d['confidence']} < threshold={threshold} "
                f"but applied=True (mode={mode})"
            )
        if mode != "real":
            assert d["applied"] is False, (
                f"INVARIANT VIOLATED: shadow mode produced applied=True"
            )
```

- [ ] **Step 2: Run + commit**

```bash
git add tests/test_governance_agent_property.py
git commit -m "test(governance): Hypothesis safety invariants (Phase 2 Task 24)"
```

---

## Task 25: launchd plists for fast + deep cadence

**Files:**
- Create: `ops/launchd/com.kalshi.governance.fast.plist`
- Create: `ops/launchd/com.kalshi.governance.deep.plist`

**Why:** Spec §8.2: "launchd plist invokes `python -m governance --cadence fast` every 2h. Daily plist invokes `--cadence deep` at a fixed UTC time." These plists go under version control so the install process is mechanical (`launchctl load ~/Library/LaunchAgents/...`) and the parameters (interval, time, working directory) are reviewable.

- [ ] **Step 1: Create the fast-cadence plist**

Create `ops/launchd/com.kalshi.governance.fast.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kalshi.governance.fast</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/Jake/vscode/kalshi_bot/.venv/bin/python</string>
        <string>-m</string>
        <string>governance</string>
        <string>--cadence</string>
        <string>fast</string>
        <string>--llm</string>
        <string>qwen</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/Jake/vscode/kalshi_bot</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <!-- Hardware-conditional: qwen3:8b on MacBook (18GB), qwen3:14b
             on Mac Studio (post-2026-04-29). Edit this line per host
             before installing the plist. -->
        <key>GOVERNANCE_LLM_MODEL</key>
        <string>qwen3:14b</string>
    </dict>

    <key>StartInterval</key>
    <integer>7200</integer>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>/Users/Jake/vscode/kalshi_bot/logs/governance/cycle.fast.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/Jake/vscode/kalshi_bot/logs/governance/cycle.fast.stderr.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Create the deep-cadence plist**

Create `ops/launchd/com.kalshi.governance.deep.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kalshi.governance.deep</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/Jake/vscode/kalshi_bot/.venv/bin/python</string>
        <string>-m</string>
        <string>governance</string>
        <string>--cadence</string>
        <string>deep</string>
        <string>--llm</string>
        <string>qwen</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/Jake/vscode/kalshi_bot</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <!-- Hardware-conditional: qwen3:8b on MacBook (18GB), qwen3:14b
             on Mac Studio (post-2026-04-29). Edit this line per host
             before installing the plist. -->
        <key>GOVERNANCE_LLM_MODEL</key>
        <string>qwen3:14b</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/Jake/vscode/kalshi_bot/logs/governance/cycle.deep.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/Jake/vscode/kalshi_bot/logs/governance/cycle.deep.stderr.log</string>
</dict>
</plist>
```

The deep plist runs at 09:00 *local* time (launchd's `StartCalendarInterval` uses local time). Operator may move this in the runbook (Task 26) if a UTC time is preferred — adjust by your local UTC offset.

- [ ] **Step 3: Commit**

```bash
git add ops/launchd/com.kalshi.governance.fast.plist ops/launchd/com.kalshi.governance.deep.plist
git commit -m "feat(governance): launchd plists for fast + deep cadence (Phase 2 Task 25)

fast: every 2h via StartInterval=7200; RunAtLoad=false (don't fire on
load — wait for the first 2h tick so an unattended install doesn't
trigger an immediate cycle).
deep: daily at 09:00 local via StartCalendarInterval.

Both plists hardcode the venv python path and repo working dir; the
runbook (Task 26) covers the install procedure (cp to
~/Library/LaunchAgents and launchctl bootstrap)."
```

---

## Task 26: `docs/governance/PHASE2_RUNBOOK.md` — operator manual

**Files:**
- Create: `docs/governance/PHASE2_RUNBOOK.md`

- [ ] **Step 1: Write the runbook**

Create `docs/governance/PHASE2_RUNBOOK.md`:

```markdown
# Governance Agent Phase 2 — Operator Runbook

This runbook covers Phase 2 (shadow mode) operations. Phase 3 (real mode)
adds an additional flip protocol; Phase 4 adds Claude-API escalation.

## Prerequisites

- Mac Studio with Ollama installed and `qwen3:14b` model pulled.
- Kalshi-bot Phase 1 plumbing merged (commit on or after the
  governance Phase 1 MR).
- venv at `/Users/Jake/vscode/kalshi_bot/.venv` with Phase 2 deps.

Verify Ollama:

```bash
curl -s http://localhost:11434/api/tags | jq -r '.models[].name'
# expected: qwen3:14b appears in the list
```

## Model selection (hardware-conditional)

The plists ship with `GOVERNANCE_LLM_MODEL=qwen3:14b` (Mac Studio target).
On the MacBook (18GB) where the trading bot is also running, edit both
plist files before installing:

```bash
sed -i '' 's|qwen3:14b|qwen3:8b|g' ops/launchd/com.kalshi.governance.fast.plist
sed -i '' 's|qwen3:14b|qwen3:8b|g' ops/launchd/com.kalshi.governance.deep.plist
ollama pull qwen3:8b
```

Verify the model file size fits the host's headroom:

```bash
ollama ls | grep -E "qwen3:(8b|14b)"
```

## Install the launchd agents

```bash
cp ops/launchd/com.kalshi.governance.fast.plist ~/Library/LaunchAgents/
cp ops/launchd/com.kalshi.governance.deep.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.deep.plist
launchctl print gui/$(id -u)/com.kalshi.governance.fast | head -20
```

The plist's `RunAtLoad` is `false` for the fast cadence so install does
not immediately fire a cycle. To trigger a fast cycle on demand:

```bash
launchctl kickstart gui/$(id -u)/com.kalshi.governance.fast
```

## Smoke-test (manual, before enabling launchd)

```bash
cd /Users/Jake/vscode/kalshi_bot
GOVERNANCE_DISABLED=false \
  ./.venv/bin/python -m governance --cadence fast --llm fake
```

Expected: exit 0; new entries in `logs/governance/decisions.jsonl`
including `GOVERNANCE_CYCLE_START` and `GOVERNANCE_CYCLE_END`. With
the FakeLLM, no `GOVERNANCE_DECISION` records (FakeLLM defaults to
no_action when no canned response matches; intended).

To verify the real LLM path:

```bash
./.venv/bin/python -m governance --cadence fast --llm qwen
```

Expected: exit 0; one or more `GOVERNANCE_DECISION` records in the
decisions log; every record's `shadow_mode` is `true` and `applied`
is `false` (shadow mode invariant).

## Kill switches

Two env-var kill switches recognized by the agent on startup:

- `GOVERNANCE_DISABLED=true` — agent refuses to run; exits 2 immediately.
  Use when something has gone wrong and you want zero agent activity
  until you've debugged it.
- `GOVERNANCE_READONLY=true` — agent runs through the cycle but writes
  every decision to `proposed`, never `applied`. Equivalent to forcing
  shadow mode regardless of `runtime_overrides.yaml`'s `mode` field.
  Use when you want to keep the trust dataset growing but stop applying
  changes.

These can be set in launchd via the plist's `EnvironmentVariables`
block, in the user's shell environment, or via a wrapper script.

## Monitoring during the 14-day soak

Per spec §8.5, Phase 2 acceptance requires ≥14 days of clean shadow
operation with ≥30 decisions accumulated and ≥85% of them deemed
reasonable on manual review.

Daily monitoring checklist:

```bash
# Yesterday's cycle count + decision count
DATE=$(date -u -v-1d +%Y-%m-%d)
grep -c "GOVERNANCE_CYCLE_START" "logs/governance/decisions.jsonl.${DATE}"
grep -c "GOVERNANCE_DECISION" "logs/governance/decisions.jsonl.${DATE}"

# Any error events?
grep -E "PARSE_ERROR|VALIDATION_ERROR|BATCH_ABORTED|KILL_SWITCH" \
  "logs/governance/decisions.jsonl.${DATE}"

# Confirm shadow mode invariant — applied list in overrides should
# never grow during Phase 2.
python -m utils.runtime_overrides --status | grep "applied="
```

If `applied=` shows nonzero source/keyword/threshold counts, **stop
the soak and investigate** — that is the load-bearing safety bug for
Phase 2.

## Manual decision review

Sample ten random decisions from yesterday's log:

```bash
DATE=$(date -u -v-1d +%Y-%m-%d)
grep '"type":"GOVERNANCE_DECISION"' "logs/governance/decisions.jsonl.${DATE}" | \
  shuf -n 10 | jq '{decision_id, action, target, confidence, reasoning}'
```

Read the reasoning. Note any that are:

- Obviously wrong (the disable target is not actually problematic per
  the bot's other diagnostics).
- Confident but wrong-direction (e.g., proposing to disable a source
  that the source-scorecard tier classifier puts in 'top performers').
- Predicted_effect uses a metric the bot doesn't actually track.

Aggregate count of "reasonable / not reasonable" across the soak; the
85% target is the Phase 2 acceptance gate.

## Common failures

- **Ollama unreachable** — agent exits non-zero, stderr log shows
  `URLError`. Restart Ollama (`ollama serve`) or the Mac Studio.
- **`GOVERNANCE_DECISION_PARSE_ERROR` rate >5%** — local model is
  drifting from the JSON schema. Either retune the model parameters
  or accept and lower the rate by tightening the system prompt.
- **`KillSwitchActive` raised on every cycle** — check
  `launchctl print gui/$(id -u)/com.kalshi.governance.fast` for the
  EnvironmentVariables block; an accidentally-set `GOVERNANCE_DISABLED=true`
  in the plist is the most likely cause.

## Uninstalling

```bash
launchctl bootout gui/$(id -u)/com.kalshi.governance.fast
launchctl bootout gui/$(id -u)/com.kalshi.governance.deep
rm ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
rm ~/Library/LaunchAgents/com.kalshi.governance.deep.plist
```

The agent's only persistent side effect outside `logs/governance/`
is `data/runtime_overrides.yaml`. In Phase 2 shadow mode, that file
should never have been modified by the agent (only by manual hand-edit
or by Phase 1's `python -m utils.runtime_overrides --revert-batch`).
```

- [ ] **Step 2: Commit**

```bash
git add docs/governance/PHASE2_RUNBOOK.md
git commit -m "docs(governance): Phase 2 operator runbook (Phase 2 Task 26)"
```

---

## Task 27: VERSION + CHANGELOG + final test sweep

**Files:**
- Modify: `VERSION`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Choose the version number**

Phase 2 ships shadow-mode infrastructure. It does *not* satisfy the v0.30.0
milestone criterion ("first non-neutral LLM output producing non-zero edge")
— shadow-mode decisions are governance decisions about the bot's
configuration, not trading decisions. Therefore Phase 2 is a 0.29.x patch.

Pick the next available patch by reading the current `main` HEAD's `VERSION`
and bumping by one. At time of plan authoring (2026-04-25), expected
sequence after merging the open MRs is:

- v0.29.51 (last on main)
- v0.29.52 (governance Phase 1 — `feat/governance-phase-1-plumbing`)
- v0.29.53 (daily-review tier filter — `feat/daily-review-tier-filter`)
- v0.29.54 (this branch — Phase 2)

Adjust if merge order differs. Do not skip numbers.

- [ ] **Step 2: Update `VERSION`**

```bash
echo "0.29.54" > VERSION
```

- [ ] **Step 3: Add `CHANGELOG.md` entry**

Prepend (above the most recent prior entry):

```markdown
## [0.29.54] - YYYY-MM-DD

### Added
- **Governance Agent Phase 2 — local-only governance agent in shadow mode.**
  New `governance/` modules: `agent.py`, `adapter.py`, `evidence.py`,
  `prompts.py`, `decision.py`, `llm.py`. CLI: `python -m governance
  --cadence fast|deep|weekly_review`. Cadence wired via launchd plists
  (`ops/launchd/com.kalshi.governance.{fast,deep}.plist`).
- **`KalshiGovernanceAdapter`** implements the `GovernanceAdapter`
  Protocol — the cross-bot seam (spec decision 9). Wraps the four
  audit-script library functions (`source_market_alignment_audit`,
  `keyword_feedback`, `reddit_source_audit`, `freshness_diagnostics`)
  for evidence collection.
- **Decision dataclass** with strict `__post_init__` validation:
  decision_id / batch_id format, tz-aware timestamps, confidence
  ∈ [0, 1], action whitelist, mandatory predicted_effect for action
  decisions.
- **FakeLLM test double + LocalQwenLLM Ollama wrapper** behind one
  `LLMClient` Protocol. Phase 2 ships with both; production uses
  LocalQwenLLM, tests use FakeLLM.
- **`scripts/__init__.py`** — formalizes the audit-script package
  boundary so the agent's evidence builder can import library
  functions cleanly.
- **Shadow-mode invariant test coverage:** unit tests, integration
  test (3-candidate end-to-end with FakeLLM), chaos tests
  (kill-switch / malformed JSON / validation errors), and Hypothesis
  property test asserting `confidence < threshold ⇒ applied=False`
  across arbitrary FakeLLM-response sequences.
- **Operator runbook** at `docs/governance/PHASE2_RUNBOOK.md`:
  install, smoke test, kill switches, soak monitoring, common
  failures, uninstall.

### Changed
- (none — Phase 2 is purely additive on top of Phase 1; no existing
  code paths modified.)

### Reasoning
- Phase 2 is the trust-dataset accumulation phase. The agent runs
  for ≥14 days in shadow mode, never writing `applied`, while a
  ≥30-decision corpus accumulates for manual review. Phase 3 flips
  the mode to `real` only after that review confirms ≥85% reasonable
  decisions.
- `mode != "real"` is the only thing standing between the agent and
  live config changes during Phase 2. The Hypothesis property test
  in `tests/test_governance_agent_property.py` is the load-bearing
  guarantee.
- Hardware: Phase 2 implementation runs on any platform via FakeLLM;
  the LocalQwenLLM path is exercised only on the Mac Studio
  (post-2026-04-29).
```

- [ ] **Step 4: Final full-suite sweep**

```bash
pytest -q
```

Expected: all tests pass, including ~70 new Phase 2 tests.

- [ ] **Step 5: Commit + tag**

```bash
git add VERSION CHANGELOG.md
git commit -m "chore(release): bump VERSION to 0.29.54 (governance Phase 2)"
```

---

## Self-review

A pass over the plan after writing, with fresh eyes.

### Spec coverage

Mapping each item in spec §8 to a task:

| Spec §8 item | Covered by |
|---|---|
| §8.1 `governance/agent.py` | Tasks 16-20 |
| §8.1 `governance/adapter.py` (Protocol + Kalshi impl) | Tasks 5, 6 |
| §8.1 `governance/evidence.py` | Tasks 7, 8, 9 |
| §8.1 `governance/prompts.py` | Tasks 10, 11, 12 |
| §8.1 `governance/decision.py` | Tasks 2, 3, 4 |
| §8.1 `governance/llm.py` (local + Claude not invoked) | Tasks 13, 14, 15 |
| §8.1 `scripts/__init__.py` refactor | Task 1 |
| §8.2 launchd fast every 2h + deep daily | Task 25 |
| §8.3 mode handling (shadow default; real exists in schema but unused) | Task 18 (`_evaluate_safety`); Task 19 (regression test) |
| §8.4 ≥30 synthetic decision-quality fixtures | Deferred — Phase 2 scope ships zero hand-curated fixtures; the 30+ accumulate via the 14-day shadow soak (per spec §8.5 acceptance criterion). The runbook (Task 26) documents the manual-review process that produces them. |
| §8.4 prompt regression tests (snapshot) | Task 11 |
| §8.4 adapter contract tests | Task 5 |
| §8.4 property tests for safety primitives integrated end-to-end | Task 24 |
| §8.5 Phase 2 runs ≥14 days shadow | Operator-driven; runbook (Task 26) |
| §8.5 ≥30 decisions accumulated | Same — soak-driven |
| §8.5 ≥85% reasonable on manual review | Operator-driven; runbook |
| §8.5 prediction tracking baseline | Task 4 (`PredictedEffect` carried through every action decision); evaluation logic ships in Phase 3 |
| §8.5 adapter audit confirms zero kalshi-specific imports past adapter | Task 5's protocol structure; Task 6's `from scripts import ...` — verify at end of execution by grepping `governance/agent.py` `governance/evidence.py` `governance/prompts.py` `governance/decision.py` `governance/llm.py` for `kalshi`/`KalshiClient`/etc. references — should be zero. |

**§8.4 fixture deferral note.** The spec says "Synthetic decision-quality fixtures (≥30): built incrementally as we observe shadow-mode decisions." Phase 2's plan ships zero such fixtures; the soak produces the corpus. The runbook documents the review protocol. This is consistent with the spec language ("built incrementally").

### Placeholder scan

Searched the plan for the following red flags — none found:
- "TBD", "TODO", "implement later", "fill in details"
- "Add appropriate error handling"
- "Write tests for the above" (without test code)
- "Similar to Task N" (without repeating the code)

One conscious abbreviation: Task 11's "Generate the golden file with a one-shot script run" embeds the python snippet in a fenced block rather than treating it as a step output to capture and check in. That is appropriate — the fixture is regenerated identically by re-running the snippet, and the plan provides the full snippet.

Task 19 is intentionally a "no code change required" task that ships a regression test against Task 18's implementation. This is by design: Task 18's `_evaluate_safety` already enforces shadow-mode-doesn't-apply; Task 19 cements that as a guarded property.

### Type / signature consistency

- `Decision` field types defined in Task 2 are the exact types referenced in Tasks 4, 14, 18, 24.
- `GovernanceAdapter` Protocol surface in Task 5 (5 methods) matches the calls made in Tasks 6, 8, 17, 18, 22.
- `LLMClient` Protocol surface in Task 13 (`complete`, `model_name`) matches the calls made in Tasks 18, 20.
- `Candidate` dataclass surface in Task 7 (action, target, evidence_pointer) matches the consumers in Tasks 8, 17, 22.
- `AuditLogger.write({...})` calls everywhere take a flat dict; matches the Phase 1 interface.
- `OverridesState` / `RuntimeOverridesReader` / `atomic_write_state` references match Phase 1's actual surface.

### Soundness check on the load-bearing safety property

The shadow-mode invariant — *the agent never writes `applied` overrides during Phase 2* — is enforced by exactly one place in the code (`_evaluate_safety`'s `mode != "real"` branch). This is intentional: a single-point-of-enforcement is easier to audit than a defense-in-depth fan-out. It is covered by:

- A unit test (Task 19) that asserts the overrides file is unchanged after a high-confidence decision in shadow mode.
- An integration test (Task 22) that verifies three candidates all land with `applied=False`.
- A Hypothesis property test (Task 24) that exercises arbitrary `(confidence, threshold, mode)` tuples.

If the invariant breaks, all three tests fail simultaneously. This is exactly the failure-discipline rule from spec §11.5.

### Estimated test count

Roughly +70 tests vs. start of Phase 2 (pre-merge baseline ≈1100):

| Task | Tests added |
|---|---|
| 1 | 3 |
| 2 | 4 |
| 3 | 13 |
| 4 | 7 |
| 5 | 2 |
| 6 | 4 |
| 7 | 5 |
| 8 | 2 |
| 9 | 1 |
| 10 | 5 |
| 11 | 1 |
| 12 | 2 |
| 13 | 6 |
| 14 | 6 |
| 15 | 2 |
| 16 | 5 |
| 17 | 1 |
| 18 | 1 |
| 19 | 1 |
| 20 | 2 |
| 22 | 1 |
| 23 | 3 |
| 24 | 1 (Hypothesis, 30 examples) |
| **total** | **~78** |

### Estimated implementation LOC

- `governance/decision.py`: ~150 lines
- `governance/adapter.py`: ~120 lines
- `governance/evidence.py`: ~140 lines
- `governance/prompts.py`: ~150 lines
- `governance/llm.py`: ~120 lines
- `governance/agent.py`: ~250 lines
- `scripts/__init__.py`: ~15 lines

Implementation total: ~945 LOC. Tests: ~700 LOC. Within the 800-1200 + 600 LOC envelope from spec §8.6.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-governance-agent-phase-2-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance + code quality) between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session via `superpowers:executing-plans`, batch with checkpoints.

**Phase 2 execution gate:** Phase 1 (`feat/governance-phase-1-plumbing` MR) must merge to `main` first — every task imports from `governance.safety`, `governance.audit`, or `utils.runtime_overrides` (Phase 1 modules). Verify the prerequisite check at the top of this plan succeeds before dispatching the first implementer subagent.

**Hardware gate:** Tasks 1-25 ship and pass on any platform via `FakeLLM`. Task 26's *runbook* mentions the Mac Studio install steps but does not require the hardware to land in the merge. Task 27's smoke-test step runs against `FakeLLM` first; the LocalQwenLLM path is exercised only after Mac Studio arrival (post-2026-04-29).

Which approach?






