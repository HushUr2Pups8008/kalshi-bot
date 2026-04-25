# Governance Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the runtime-overrides plumbing — YAML reader, atomic writes, hot-reload poll task, safety primitives, CLI shim, and refactor of existing static-config call sites — so the bot can pick up override changes without restart and a future agent has the foundation to write to.

**Architecture:** New module `utils/runtime_overrides.py` holds the bot-side reader (singleton, schema validation, TTL filtering). New `governance/` package holds safety primitives (`SafetyConfig`, `KillSwitch`, `AuditLogger`) used in Phase 1 standalone tests and consumed in Phase 2+. New asyncio task `tasks/runtime_overrides_task.py` polls the YAML file every 10 min and hot-swaps in-memory state. Existing call sites refactored from `if source in DISABLED_NEWS_SOURCES` to `if reader.is_source_disabled(source)`. CLI shim at `python -m utils.runtime_overrides` for emergency intervention.

**Tech Stack:** Python 3.14, pytest, pytest-asyncio, **pyyaml** (new dep), **hypothesis** (new dev dep), existing project conventions (asyncio task pattern, `utils/logger.py` rotation primitives).

**Spec reference:** `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §7 (Phase 1 detailed design). Read sections §5 (architecture), §6 (data contracts), and §11 (testing strategy) before starting.

**Branch protocol:** Create feature branch `feat/governance-phase-1-plumbing` off main. All Phase 1 commits land there; merge to main only after the Acceptance gate (Task 28).

---

## File structure

**New files:**
- `governance/__init__.py` — package marker
- `governance/safety.py` — `SafetyConfig`, `KillSwitch` classes
- `governance/audit.py` — `AuditLogger` (JSONL append-only writer with daily rotation)
- `utils/runtime_overrides.py` — schema dataclasses, validation, `RuntimeOverridesReader` singleton, atomic-write helper, CLI entry point
- `tasks/runtime_overrides_task.py` — asyncio poll task
- `tests/test_governance_safety.py` — `SafetyConfig`, `KillSwitch` tests
- `tests/test_governance_audit.py` — `AuditLogger` tests including rotation
- `tests/test_runtime_overrides_schema.py` — schema validation tests (parametrized)
- `tests/test_runtime_overrides_reader.py` — `RuntimeOverridesReader` query/swap/diff tests
- `tests/test_runtime_overrides_atomic.py` — atomic-write race tests
- `tests/test_runtime_overrides_property.py` — Hypothesis property test
- `tests/test_runtime_overrides_task.py` — async poll task tests
- `tests/test_runtime_overrides_cli.py` — CLI shim tests
- `docs/governance/README.md` — operator manual (emergency intervention, mode flips, rollback)

**Modified files:**
- `requirements.txt` — add `pyyaml`
- `requirements-dev.txt` — add `hypothesis`
- `analysis/market_matcher.py` — refactor `DISABLED_NEWS_SOURCES` checks; skip disabled keywords during keyword iteration
- `feeds/rss_monitor.py` — refactor source checks (if any exist)
- `feeds/reddit_monitor.py` — refactor source checks (if any exist)
- `main.py` — wire reader in startup, add poll task to async task group
- `config.py` — add `EARLY_MAX_NEWS_AGE_BY_SOURCE` consumer indirection (a function or use override-aware lookup)
- `CHANGELOG.md` — entry for Phase 1 merge
- `VERSION` — bumped patch level

**Files NOT touched in Phase 1** (deferred to later phases):
- `governance/agent.py`, `governance/decision.py`, `governance/evidence.py`, `governance/prompts.py`, `governance/llm.py`, `governance/adapter.py` — Phase 2.
- `data/runtime_overrides.yaml` — written exclusively by humans (manual edit) or the future agent (Phase 2). Phase 1 just reads it if present, ignores if absent.

---

### Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Add pyyaml to runtime requirements**

In `requirements.txt`, add to the existing `# Data / storage` section:

```
# YAML config (governance runtime overrides, see docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md)
pyyaml>=6.0
```

- [ ] **Step 2: Add hypothesis to dev requirements**

In `requirements-dev.txt`, add after pytest entries:

```
# Property-based testing (governance plumbing — see Phase 1 plan)
hypothesis>=6,<7
```

- [ ] **Step 3: Install in venv**

Run: `pip install -r requirements-dev.txt`
Expected: PyYAML and hypothesis install without error.

- [ ] **Step 4: Verify imports**

Run: `python -c "import yaml; import hypothesis; print(yaml.__version__, hypothesis.__version__)"`
Expected: prints two version strings, no errors.

- [ ] **Step 5: Run existing test suite to ensure no regression**

Run: `pytest --tb=short 2>&1 | tail -3`
Expected: same baseline as current main (`1100 passed, 1 skipped`).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/governance-phase-1-plumbing
git add requirements.txt requirements-dev.txt
git commit -m "build(deps): add pyyaml + hypothesis for governance Phase 1"
```

---

### Task 2: Bootstrap `governance/` package + `SafetyConfig` dataclass

**Files:**
- Create: `governance/__init__.py`
- Create: `governance/safety.py`
- Create: `tests/test_governance_safety.py`

- [ ] **Step 1: Write the failing test for SafetyConfig**

Create `tests/test_governance_safety.py`:

```python
"""Tests for governance.safety primitives (Phase 1 standalone tests).

These primitives are used by the governance agent in Phase 2+ but are
built and tested here so the safety layer is solid before any LLM
involvement begins.
"""

from __future__ import annotations

import pytest

from governance.safety import SafetyConfig


class TestSafetyConfig:
    def test_default_values(self):
        c = SafetyConfig()
        assert c.confidence_threshold == 0.7
        assert c.max_changes_per_run == 10
        assert c.blast_radius_max_source_disable_pct == 0.20
        assert c.blast_radius_max_source_disables_per_batch == 5
        assert c.blast_radius_max_keyword_changes_per_batch == 5
        assert c.blast_radius_max_threshold_tunings_per_batch == 3

    def test_custom_values(self):
        c = SafetyConfig(
            confidence_threshold=0.8,
            max_changes_per_run=20,
            blast_radius_max_source_disable_pct=0.10,
        )
        assert c.confidence_threshold == 0.8
        assert c.max_changes_per_run == 20
        assert c.blast_radius_max_source_disable_pct == 0.10

    def test_confidence_threshold_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="confidence_threshold"):
            SafetyConfig(confidence_threshold=1.5)
        with pytest.raises(ValueError, match="confidence_threshold"):
            SafetyConfig(confidence_threshold=-0.1)

    def test_blast_radius_pct_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="blast_radius_max_source_disable_pct"):
            SafetyConfig(blast_radius_max_source_disable_pct=1.5)

    def test_max_changes_must_be_positive(self):
        with pytest.raises(ValueError, match="max_changes_per_run"):
            SafetyConfig(max_changes_per_run=0)
        with pytest.raises(ValueError, match="max_changes_per_run"):
            SafetyConfig(max_changes_per_run=-5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_governance_safety.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'governance'`.

- [ ] **Step 3: Create governance package skeleton**

Create `governance/__init__.py`:

```python
"""Governance agent package.

Phase 1 (this commit): safety primitives built and tested standalone.
Phase 2+: agent core, decision engine, LLM integration.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md.
"""
```

Create `governance/safety.py`:

```python
"""Safety primitives for the governance agent.

Used by the agent in Phase 2+ to enforce decision-level and batch-level
safety bounds. Built and tested standalone in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SafetyConfig:
    """Bounds on what the governance agent may do in a single cycle.

    All fields have explicit defaults reflecting the spec's MVP defaults.
    Validation enforces that fractions are in [0, 1] and integer caps
    are positive; out-of-range values raise ValueError immediately so
    misconfiguration cannot leak into the agent's runtime.
    """

    confidence_threshold: float = 0.7
    max_changes_per_run: int = 10
    blast_radius_max_source_disable_pct: float = 0.20
    blast_radius_max_source_disables_per_batch: int = 5
    blast_radius_max_keyword_changes_per_batch: int = 5
    blast_radius_max_threshold_tunings_per_batch: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError(
                f"confidence_threshold must be in [0, 1], got {self.confidence_threshold}"
            )
        if not 0.0 <= self.blast_radius_max_source_disable_pct <= 1.0:
            raise ValueError(
                "blast_radius_max_source_disable_pct must be in [0, 1], "
                f"got {self.blast_radius_max_source_disable_pct}"
            )
        for name in (
            "max_changes_per_run",
            "blast_radius_max_source_disables_per_batch",
            "blast_radius_max_keyword_changes_per_batch",
            "blast_radius_max_threshold_tunings_per_batch",
        ):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_governance_safety.py -v`
Expected: 5/5 PASS.

- [ ] **Step 4a: Add comprehensive boundary tests for SafetyConfig**

The five tests above cover representative cases. The user's "100%-confidence on the safety layer" mandate calls for explicit coverage of every validated field's bounds. Append the following test methods to the `TestSafetyConfig` class:

```python
    def test_all_integer_caps_reject_zero_and_negative(self):
        """All four integer caps must reject 0 and negative values, not just max_changes_per_run."""
        for field_name in (
            "max_changes_per_run",
            "blast_radius_max_source_disables_per_batch",
            "blast_radius_max_keyword_changes_per_batch",
            "blast_radius_max_threshold_tunings_per_batch",
        ):
            with pytest.raises(ValueError, match=field_name):
                SafetyConfig(**{field_name: 0})
            with pytest.raises(ValueError, match=field_name):
                SafetyConfig(**{field_name: -1})

    def test_blast_radius_pct_rejects_negative(self):
        """Symmetric coverage: lower bound, not just upper bound (1.5)."""
        with pytest.raises(ValueError, match="blast_radius_max_source_disable_pct"):
            SafetyConfig(blast_radius_max_source_disable_pct=-0.1)

    def test_unit_interval_boundary_values_accepted(self):
        """0.0 and 1.0 are valid operator settings:
        - confidence_threshold=0.0 → 'agent never auto-applies'
        - confidence_threshold=1.0 → 'only perfectly confident decisions apply'
        - blast_radius_max_source_disable_pct=0.0 → 'never auto-disable via percentage'
        - blast_radius_max_source_disable_pct=1.0 → 'no percentage cap, only absolute'
        """
        SafetyConfig(confidence_threshold=0.0)
        SafetyConfig(confidence_threshold=1.0)
        SafetyConfig(blast_radius_max_source_disable_pct=0.0)
        SafetyConfig(blast_radius_max_source_disable_pct=1.0)
```

Run: `pytest tests/test_governance_safety.py::TestSafetyConfig -v`
Expected: 8/8 PASS (5 original + 3 new).

- [ ] **Step 5: Commit**

```bash
git add governance/__init__.py governance/safety.py tests/test_governance_safety.py
git commit -m "feat(governance): SafetyConfig dataclass + tests (Phase 1, task 2)"
```

---

### Task 3: `KillSwitch` class

**Files:**
- Modify: `governance/safety.py`
- Modify: `tests/test_governance_safety.py`

- [ ] **Step 1: Append KillSwitch tests**

Append to `tests/test_governance_safety.py`:

```python
import os

from governance.safety import KillSwitch


class TestKillSwitch:
    def test_default_state_is_active(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        ks = KillSwitch()
        assert ks.is_disabled() is False
        assert ks.is_readonly() is False
        assert ks.may_apply() is True

    def test_disabled_env_var_truthy_values(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        for truthy in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("GOVERNANCE_DISABLED", truthy)
            ks = KillSwitch()
            assert ks.is_disabled() is True, f"expected disabled for {truthy!r}"
            assert ks.may_apply() is False

    def test_disabled_env_var_falsy_values(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        for falsy in ("false", "FALSE", "0", "no", "off", ""):
            monkeypatch.setenv("GOVERNANCE_DISABLED", falsy)
            ks = KillSwitch()
            assert ks.is_disabled() is False, f"expected enabled for {falsy!r}"

    def test_readonly_blocks_apply_but_not_run(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        monkeypatch.setenv("GOVERNANCE_READONLY", "true")
        ks = KillSwitch()
        assert ks.is_disabled() is False
        assert ks.is_readonly() is True
        assert ks.may_apply() is False

    def test_disabled_takes_precedence_over_readonly(self, monkeypatch):
        monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
        monkeypatch.setenv("GOVERNANCE_READONLY", "true")
        ks = KillSwitch()
        assert ks.is_disabled() is True
        assert ks.may_apply() is False

    def test_re_check_picks_up_env_changes(self, monkeypatch):
        # KillSwitch reads env on each call -- not cached. This is
        # important so a sysadmin can flip the kill-switch on a running
        # agent process between cycles.
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        ks = KillSwitch()
        assert ks.is_disabled() is False
        monkeypatch.setenv("GOVERNANCE_DISABLED", "true")
        assert ks.is_disabled() is True

    def test_readonly_env_var_truthy_values(self, monkeypatch):
        """GOVERNANCE_READONLY must accept the same truthy variants as GOVERNANCE_DISABLED."""
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        for truthy in ("true", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("GOVERNANCE_READONLY", truthy)
            ks = KillSwitch()
            assert ks.is_readonly() is True, f"expected readonly for {truthy!r}"
            assert ks.may_apply() is False

    def test_readonly_env_var_falsy_values(self, monkeypatch):
        """GOVERNANCE_READONLY must reject the same falsy variants as GOVERNANCE_DISABLED."""
        monkeypatch.delenv("GOVERNANCE_DISABLED", raising=False)
        for falsy in ("false", "FALSE", "0", "no", "off", ""):
            monkeypatch.setenv("GOVERNANCE_READONLY", falsy)
            ks = KillSwitch()
            assert ks.is_readonly() is False, f"expected enabled for {falsy!r}"
            assert ks.may_apply() is True

    def test_whitespace_around_values_handled(self, monkeypatch):
        """Operator may set GOVERNANCE_DISABLED='  true  ' from a shell with
        trailing space; the implementation strips and lowercases.
        """
        monkeypatch.delenv("GOVERNANCE_READONLY", raising=False)
        monkeypatch.setenv("GOVERNANCE_DISABLED", "  true  ")
        assert KillSwitch().is_disabled() is True
        monkeypatch.setenv("GOVERNANCE_DISABLED", "\tTRUE\n")
        assert KillSwitch().is_disabled() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_governance_safety.py::TestKillSwitch -v`
Expected: FAIL with `ImportError: cannot import name 'KillSwitch' from 'governance.safety'`.

- [ ] **Step 3: Implement KillSwitch**

Append to `governance/safety.py`:

```python
import os

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    """Return True iff env var is set to a truthy string (case-insensitive)."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


class KillSwitch:
    """Two-level emergency stop for the governance agent.

    GOVERNANCE_DISABLED=true: agent must exit cleanly without writing
        any state. Use to halt a misbehaving agent immediately.
    GOVERNANCE_READONLY=true: agent runs and produces decisions but
        does NOT write them to data/runtime_overrides.yaml. Useful for
        debugging the agent's decisions without applying them.

    Both env vars are re-read on every call (not cached) so a sysadmin
    can flip the switch between cycles on a running agent process.
    DISABLED takes precedence over READONLY when both are set.
    """

    def is_disabled(self) -> bool:
        return _env_truthy("GOVERNANCE_DISABLED")

    def is_readonly(self) -> bool:
        return _env_truthy("GOVERNANCE_READONLY")

    def may_apply(self) -> bool:
        """True iff agent is allowed to write state to disk."""
        return not (self.is_disabled() or self.is_readonly())
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_governance_safety.py -v`
Expected: 17/17 PASS (8 SafetyConfig from Task 2 + 9 KillSwitch from Task 3: 6 original + 3 new variants/whitespace).

- [ ] **Step 5: Commit**

```bash
git add governance/safety.py tests/test_governance_safety.py
git commit -m "feat(governance): KillSwitch class + env var tests (Phase 1, task 3)"
```

---

### Task 4: Schema dataclasses for the YAML overrides file

**Files:**
- Create: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_schema.py`

- [ ] **Step 1: Write the failing tests for schema dataclasses**

Create `tests/test_runtime_overrides_schema.py`:

```python
"""Tests for runtime_overrides schema dataclasses + validation.

The schema is the contract between the governance agent (writer) and
the kalshi-bot (reader). Bad schema = bad reload behavior, so tests are
parametrized against every required field, type, and range.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md §6.1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.runtime_overrides import (
    DisabledKeyword,
    DisabledSource,
    OverridesState,
    PredictedEffect,
    ThresholdOverride,
)


def _utc(years_offset: int = 0, hours_offset: int = 0) -> datetime:
    base = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
    return base + timedelta(days=365 * years_offset, hours=hours_offset)


class TestPredictedEffect:
    def test_construct_with_all_fields(self):
        pe = PredictedEffect(
            metric="anchor_rate",
            baseline=0.99,
            predicted_post_change=0.85,
            evaluate_at=_utc(),
        )
        assert pe.metric == "anchor_rate"
        assert pe.baseline == 0.99
        assert pe.predicted_post_change == 0.85
        assert pe.evaluate_at == _utc()

    def test_metric_required_non_empty(self):
        with pytest.raises(ValueError, match="metric"):
            PredictedEffect(
                metric="",
                baseline=0.0,
                predicted_post_change=0.0,
                evaluate_at=_utc(),
            )


class TestDisabledSource:
    def test_construct_with_required_fields(self):
        ds = DisabledSource(
            source="r/Turkey",
            reason="zero matches over 7d",
            confidence=0.94,
            decided_at=_utc(),
            decided_by="governance-agent-v0.2.1",
            decision_id="gd_2026-05-02_0042",
            expires_at=None,
            predicted_effect=PredictedEffect(
                metric="reddit_rate_limit_budget_consumed_daily",
                baseline=0.12,
                predicted_post_change=0.08,
                evaluate_at=_utc(hours_offset=168),
            ),
        )
        assert ds.source == "r/Turkey"
        assert ds.expires_at is None

    def test_confidence_must_be_unit_interval(self):
        with pytest.raises(ValueError, match="confidence"):
            DisabledSource(
                source="r/Turkey",
                reason="x",
                confidence=1.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_x",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )

    def test_decision_id_format(self):
        with pytest.raises(ValueError, match="decision_id"):
            DisabledSource(
                source="r/Turkey",
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="not-a-valid-id",  # missing gd_ prefix
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestDisabledKeyword:
    def test_construct(self):
        dk = DisabledKeyword(
            keyword="trump may deadline",
            reason="time-bounded",
            confidence=0.82,
            decided_at=_utc(),
            decided_by="agent",
            decision_id="gd_2026-05-02_0043",
            expires_at=_utc(hours_offset=24),
            predicted_effect=PredictedEffect(
                metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
            ),
        )
        assert dk.keyword == "trump may deadline"

    def test_keyword_required_non_empty(self):
        with pytest.raises(ValueError, match="keyword"):
            DisabledKeyword(
                keyword="",
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_2026-05-02_0001",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestThresholdOverride:
    def test_construct(self):
        to = ThresholdOverride(
            path="EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA",
            value=21600,
            reason="slow cadence",
            confidence=0.71,
            decided_at=_utc(),
            decided_by="agent",
            decision_id="gd_2026-05-02_0044",
            expires_at=None,
            predicted_effect=PredictedEffect(
                metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
            ),
        )
        assert to.path == "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
        assert to.value == 21600

    def test_path_required_non_empty(self):
        with pytest.raises(ValueError, match="path"):
            ThresholdOverride(
                path="",
                value=10,
                reason="x",
                confidence=0.5,
                decided_at=_utc(),
                decided_by="agent",
                decision_id="gd_2026-05-02_0001",
                expires_at=None,
                predicted_effect=PredictedEffect(
                    metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc()
                ),
            )


class TestOverridesState:
    def test_empty_state(self):
        state = OverridesState(
            version=1,
            updated_at=_utc(),
            updated_by="agent",
            mode="shadow",
        )
        assert state.applied_disabled_sources == []
        assert state.applied_disabled_keywords == []
        assert state.applied_threshold_overrides == []
        assert state.proposed_disabled_sources == []

    def test_mode_must_be_valid(self):
        with pytest.raises(ValueError, match="mode"):
            OverridesState(
                version=1,
                updated_at=_utc(),
                updated_by="agent",
                mode="invalid",
            )

    def test_version_must_be_supported(self):
        with pytest.raises(ValueError, match="version"):
            OverridesState(
                version=99,  # future schema version we don't understand
                updated_at=_utc(),
                updated_by="agent",
                mode="shadow",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.runtime_overrides'`.

- [ ] **Step 3: Create the schema module**

Create `utils/runtime_overrides.py`:

```python
"""Runtime overrides reader for kalshi-bot.

Reads data/runtime_overrides.yaml at startup and on hot-reload (via
tasks/runtime_overrides_task.py). Exposes typed query methods consumed
by analysis/ and feeds/ modules in place of static config-set lookups.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md
sections 6 (data contracts) and 7 (Phase 1 design).

Phase 1 boundaries:
  - This module only READS the YAML file. The agent (Phase 2+) writes it.
  - The atomic-write helper here is provided so tests + future agent
    can produce valid files; nothing in the bot writes during Phase 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# Schema versions this module knows how to read. Forward-incompatible
# bumps (rare) require a code update before reading the new file.
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

# The agent writes decision IDs in this exact format. Anything else
# is a corruption signal -- reject on read.
_DECISION_ID_RE = re.compile(r"^gd_\d{4}-\d{2}-\d{2}_\d{4}$")


@dataclass(frozen=True)
class PredictedEffect:
    """Mandatory prediction attached to every decision (per spec §6.1)."""

    metric: str
    baseline: float
    predicted_post_change: float
    evaluate_at: datetime

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("metric is required and must be non-empty")


@dataclass(frozen=True)
class _OverrideBase:
    """Common fields for all override types. Validated in __post_init__."""

    reason: str
    confidence: float
    decided_at: datetime
    decided_by: str
    decision_id: str
    expires_at: datetime | None
    predicted_effect: PredictedEffect

    def _validate_common(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1], got {self.confidence}"
            )
        if not _DECISION_ID_RE.match(self.decision_id):
            raise ValueError(
                f"decision_id must match {_DECISION_ID_RE.pattern}, got {self.decision_id!r}"
            )


@dataclass(frozen=True)
class DisabledSource(_OverrideBase):
    source: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required and must be non-empty")
        self._validate_common()


@dataclass(frozen=True)
class DisabledKeyword(_OverrideBase):
    keyword: str = ""

    def __post_init__(self) -> None:
        if not self.keyword:
            raise ValueError("keyword is required and must be non-empty")
        self._validate_common()


@dataclass(frozen=True)
class ThresholdOverride(_OverrideBase):
    path: str = ""
    value: Any = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("path is required and must be non-empty")
        self._validate_common()


Mode = Literal["shadow", "real"]


@dataclass
class OverridesState:
    """Full in-memory representation of the YAML overrides file.

    Phase 1 reads this; Phase 2+ writes it. The bot consults
    `applied_*` fields only; `proposed_*` are human/agent review queue
    that the bot ignores.
    """

    version: int
    updated_at: datetime
    updated_by: str
    mode: Mode
    applied_disabled_sources: list[DisabledSource] = field(default_factory=list)
    applied_disabled_keywords: list[DisabledKeyword] = field(default_factory=list)
    applied_threshold_overrides: list[ThresholdOverride] = field(default_factory=list)
    proposed_disabled_sources: list[DisabledSource] = field(default_factory=list)
    proposed_disabled_keywords: list[DisabledKeyword] = field(default_factory=list)
    proposed_threshold_overrides: list[ThresholdOverride] = field(default_factory=list)
    last_applied_batch: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"version must be one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}, "
                f"got {self.version}"
            )
        if self.mode not in ("shadow", "real"):
            raise ValueError(f"mode must be 'shadow' or 'real', got {self.mode!r}")
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_schema.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_schema.py
git commit -m "feat(runtime-overrides): schema dataclasses + validation (Phase 1, task 4)"
```

---

### Task 5: YAML parsing into typed `OverridesState`

**Files:**
- Modify: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_parse.py`

- [ ] **Step 1: Write the failing tests for YAML parsing**

Create `tests/test_runtime_overrides_parse.py`:

```python
"""Tests for parse_yaml_to_state — reading YAML dict into OverridesState."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from utils.runtime_overrides import OverridesState, parse_yaml_to_state


_VALID_PREDICTED_EFFECT = {
    "metric": "anchor_rate",
    "baseline": 0.99,
    "predicted_post_change": 0.85,
    "evaluate_at": "2026-05-09T14:30:00+00:00",
}


def _valid_yaml() -> dict:
    return {
        "version": 1,
        "updated_at": "2026-05-02T14:30:00+00:00",
        "updated_by": "governance-agent-v0.2.1",
        "mode": "shadow",
        "applied": {
            "disabled_sources": [
                {
                    "source": "r/Turkey",
                    "reason": "0 matches over 7d",
                    "confidence": 0.94,
                    "decided_at": "2026-05-02T14:30:00+00:00",
                    "decided_by": "governance-agent-v0.2.1",
                    "decision_id": "gd_2026-05-02_0042",
                    "expires_at": None,
                    "predicted_effect": _VALID_PREDICTED_EFFECT,
                }
            ],
            "disabled_keywords": [],
            "threshold_overrides": [],
        },
        "proposed": {
            "disabled_sources": [],
            "disabled_keywords": [],
            "threshold_overrides": [],
        },
    }


class TestParseYamlToState:
    def test_minimal_valid_input(self):
        state = parse_yaml_to_state(_valid_yaml())
        assert isinstance(state, OverridesState)
        assert state.version == 1
        assert state.mode == "shadow"
        assert len(state.applied_disabled_sources) == 1
        assert state.applied_disabled_sources[0].source == "r/Turkey"

    def test_empty_applied_section(self):
        data = _valid_yaml()
        data["applied"]["disabled_sources"] = []
        state = parse_yaml_to_state(data)
        assert state.applied_disabled_sources == []

    def test_missing_version_raises(self):
        data = _valid_yaml()
        del data["version"]
        with pytest.raises(ValueError, match="version"):
            parse_yaml_to_state(data)

    def test_missing_mode_raises(self):
        data = _valid_yaml()
        del data["mode"]
        with pytest.raises(ValueError, match="mode"):
            parse_yaml_to_state(data)

    def test_unknown_top_level_section_ignored(self):
        # Forward-compat: agent might add a new section in v1+ that this
        # reader doesn't recognize. Don't crash; ignore.
        data = _valid_yaml()
        data["future_section"] = {"foo": "bar"}
        state = parse_yaml_to_state(data)
        assert state.version == 1

    def test_invalid_decision_id_in_entry_raises(self):
        data = _valid_yaml()
        data["applied"]["disabled_sources"][0]["decision_id"] = "not-valid"
        with pytest.raises(ValueError, match="decision_id"):
            parse_yaml_to_state(data)

    def test_iso8601_with_z_suffix_accepted(self):
        # Some YAML emitters use 'Z' instead of '+00:00' for UTC.
        # We accept both.
        data = _valid_yaml()
        data["updated_at"] = "2026-05-02T14:30:00Z"
        data["applied"]["disabled_sources"][0]["decided_at"] = "2026-05-02T14:30:00Z"
        data["applied"]["disabled_sources"][0]["predicted_effect"]["evaluate_at"] = (
            "2026-05-09T14:30:00Z"
        )
        state = parse_yaml_to_state(data)
        assert state.updated_at.tzinfo == timezone.utc

    def test_proposed_section_missing_treated_as_empty(self):
        data = _valid_yaml()
        del data["proposed"]
        state = parse_yaml_to_state(data)
        assert state.proposed_disabled_sources == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_parse.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_yaml_to_state'`.

- [ ] **Step 3: Implement parse_yaml_to_state**

Append to `utils/runtime_overrides.py`:

```python
def _parse_iso(value: Any, field_name: str) -> datetime:
    """Parse an ISO 8601 timestamp string. Accepts both '+00:00' and 'Z' UTC suffixes."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 string, got {type(value).__name__}")
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"{field_name}: invalid ISO 8601 timestamp {value!r}") from exc


def _parse_optional_iso(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_iso(value, field_name)


def _parse_predicted_effect(data: dict, ctx: str) -> PredictedEffect:
    if not isinstance(data, dict):
        raise ValueError(f"{ctx}.predicted_effect must be a mapping")
    try:
        return PredictedEffect(
            metric=str(data["metric"]),
            baseline=float(data["baseline"]),
            predicted_post_change=float(data["predicted_post_change"]),
            evaluate_at=_parse_iso(data["evaluate_at"], f"{ctx}.predicted_effect.evaluate_at"),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}.predicted_effect: missing required field {exc.args[0]!r}") from exc


def _parse_disabled_source(data: dict, idx: int) -> DisabledSource:
    ctx = f"applied.disabled_sources[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return DisabledSource(
            source=str(data["source"]),
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def _parse_disabled_keyword(data: dict, idx: int) -> DisabledKeyword:
    ctx = f"applied.disabled_keywords[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return DisabledKeyword(
            keyword=str(data["keyword"]),
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def _parse_threshold_override(data: dict, idx: int) -> ThresholdOverride:
    ctx = f"applied.threshold_overrides[{idx}]"
    if not isinstance(data, dict):
        raise ValueError(f"{ctx} must be a mapping")
    try:
        return ThresholdOverride(
            path=str(data["path"]),
            value=data["value"],
            reason=str(data.get("reason", "")),
            confidence=float(data["confidence"]),
            decided_at=_parse_iso(data["decided_at"], f"{ctx}.decided_at"),
            decided_by=str(data["decided_by"]),
            decision_id=str(data["decision_id"]),
            expires_at=_parse_optional_iso(data.get("expires_at"), f"{ctx}.expires_at"),
            predicted_effect=_parse_predicted_effect(data["predicted_effect"], ctx),
        )
    except KeyError as exc:
        raise ValueError(f"{ctx}: missing required field {exc.args[0]!r}") from exc


def parse_yaml_to_state(data: dict) -> OverridesState:
    """Parse a YAML-loaded dict into a typed OverridesState.

    Raises ValueError on schema violations with a path indicating where
    the failure occurred (e.g., "applied.disabled_sources[0].confidence").
    Unknown top-level sections are ignored for forward-compat.
    """
    if not isinstance(data, dict):
        raise ValueError(f"top-level YAML must be a mapping, got {type(data).__name__}")

    try:
        version = int(data["version"])
        updated_at = _parse_iso(data["updated_at"], "updated_at")
        updated_by = str(data["updated_by"])
        mode = str(data["mode"])
    except KeyError as exc:
        raise ValueError(f"missing required top-level field {exc.args[0]!r}") from exc

    applied = data.get("applied") or {}
    proposed = data.get("proposed") or {}

    if not isinstance(applied, dict):
        raise ValueError("applied must be a mapping")
    if not isinstance(proposed, dict):
        raise ValueError("proposed must be a mapping")

    return OverridesState(
        version=version,
        updated_at=updated_at,
        updated_by=updated_by,
        mode=mode,  # type: ignore[arg-type]  # validated in OverridesState.__post_init__
        applied_disabled_sources=[
            _parse_disabled_source(d, i)
            for i, d in enumerate(applied.get("disabled_sources") or [])
        ],
        applied_disabled_keywords=[
            _parse_disabled_keyword(d, i)
            for i, d in enumerate(applied.get("disabled_keywords") or [])
        ],
        applied_threshold_overrides=[
            _parse_threshold_override(d, i)
            for i, d in enumerate(applied.get("threshold_overrides") or [])
        ],
        proposed_disabled_sources=[
            _parse_disabled_source(d, i)
            for i, d in enumerate(proposed.get("disabled_sources") or [])
        ],
        proposed_disabled_keywords=[
            _parse_disabled_keyword(d, i)
            for i, d in enumerate(proposed.get("disabled_keywords") or [])
        ],
        proposed_threshold_overrides=[
            _parse_threshold_override(d, i)
            for i, d in enumerate(proposed.get("threshold_overrides") or [])
        ],
        last_applied_batch=data.get("last_applied_batch"),
    )
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_parse.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_parse.py
git commit -m "feat(runtime-overrides): parse_yaml_to_state with full validation (Phase 1, task 5)"
```

---

### Task 6: TTL expiry filtering

**Files:**
- Modify: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_ttl.py`

- [ ] **Step 1: Write the failing tests for TTL filtering**

Create `tests/test_runtime_overrides_ttl.py`:

```python
"""Tests for TTL-expiry filtering on OverridesState."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.runtime_overrides import (
    DisabledSource,
    OverridesState,
    PredictedEffect,
    filter_expired,
)


def _utc(hours_offset: int = 0) -> datetime:
    return datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc) + timedelta(hours=hours_offset)


def _make_disabled_source(decision_id: str, expires_at):
    return DisabledSource(
        source=f"src_{decision_id}",
        reason="x",
        confidence=0.5,
        decided_at=_utc(),
        decided_by="agent",
        decision_id=decision_id,
        expires_at=expires_at,
        predicted_effect=PredictedEffect(
            metric="m", baseline=0, predicted_post_change=0, evaluate_at=_utc(),
        ),
    )


class TestFilterExpired:
    def test_indefinite_never_expires(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=None)
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc(hours_offset=10000))
        assert len(result.applied_disabled_sources) == 1

    def test_expired_in_past_filtered(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc())
        assert result.applied_disabled_sources == []

    def test_not_yet_expired_kept(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=24))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc(hours_offset=12))
        assert len(result.applied_disabled_sources) == 1

    def test_filter_does_not_mutate_input(self):
        s_kept = _make_disabled_source("gd_2026-05-02_0001", expires_at=None)
        s_expired = _make_disabled_source("gd_2026-05-02_0002", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            applied_disabled_sources=[s_kept, s_expired],
        )
        original_len = len(state.applied_disabled_sources)
        filter_expired(state, now=_utc())
        assert len(state.applied_disabled_sources) == original_len  # input untouched

    def test_proposed_section_also_filtered(self):
        s = _make_disabled_source("gd_2026-05-02_0001", expires_at=_utc(hours_offset=-1))
        state = OverridesState(
            version=1, updated_at=_utc(), updated_by="agent", mode="shadow",
            proposed_disabled_sources=[s],
        )
        result = filter_expired(state, now=_utc())
        assert result.proposed_disabled_sources == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_ttl.py -v`
Expected: FAIL with `ImportError: cannot import name 'filter_expired'`.

- [ ] **Step 3: Implement filter_expired**

Append to `utils/runtime_overrides.py`:

```python
def _not_expired(override: _OverrideBase, now: datetime) -> bool:
    return override.expires_at is None or override.expires_at > now


def filter_expired(state: OverridesState, now: datetime) -> OverridesState:
    """Return a new OverridesState with all expired entries removed.

    Does NOT mutate the input. An entry is expired iff its expires_at
    is non-None and <= now. Both `applied` and `proposed` sections are
    filtered (the agent should not see its own expired proposals as
    in-force when deciding the next batch).
    """
    return OverridesState(
        version=state.version,
        updated_at=state.updated_at,
        updated_by=state.updated_by,
        mode=state.mode,
        applied_disabled_sources=[
            o for o in state.applied_disabled_sources if _not_expired(o, now)
        ],
        applied_disabled_keywords=[
            o for o in state.applied_disabled_keywords if _not_expired(o, now)
        ],
        applied_threshold_overrides=[
            o for o in state.applied_threshold_overrides if _not_expired(o, now)
        ],
        proposed_disabled_sources=[
            o for o in state.proposed_disabled_sources if _not_expired(o, now)
        ],
        proposed_disabled_keywords=[
            o for o in state.proposed_disabled_keywords if _not_expired(o, now)
        ],
        proposed_threshold_overrides=[
            o for o in state.proposed_threshold_overrides if _not_expired(o, now)
        ],
        last_applied_batch=state.last_applied_batch,
    )
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_ttl.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_ttl.py
git commit -m "feat(runtime-overrides): TTL-expiry filtering (Phase 1, task 6)"
```

---

### Task 7: `load_from_disk()` reads YAML file end-to-end

**Files:**
- Modify: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_load.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runtime_overrides_load.py`:

```python
"""Tests for load_from_disk: read YAML file -> OverridesState."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from utils.runtime_overrides import OverridesState, load_from_disk


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "governance-agent-v0.2.1"
    mode: shadow
    applied:
      disabled_sources:
        - source: "r/Turkey"
          reason: "0 matches"
          confidence: 0.94
          decided_at: "2026-05-02T14:30:00+00:00"
          decided_by: "governance-agent-v0.2.1"
          decision_id: "gd_2026-05-02_0042"
          expires_at: null
          predicted_effect:
            metric: "anchor_rate"
            baseline: 0.99
            predicted_post_change: 0.85
            evaluate_at: "2026-05-09T14:30:00+00:00"
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


class TestLoadFromDisk:
    def test_loads_valid_file(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        state = load_from_disk(p)
        assert isinstance(state, OverridesState)
        assert len(state.applied_disabled_sources) == 1

    def test_missing_file_returns_default_empty_state(self, tmp_path: Path):
        p = tmp_path / "does_not_exist.yaml"
        state = load_from_disk(p)
        assert isinstance(state, OverridesState)
        assert state.applied_disabled_sources == []
        assert state.mode == "shadow"  # safest default for a missing file

    def test_empty_file_raises(self, tmp_path: Path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="empty"):
            load_from_disk(p)

    def test_malformed_yaml_raises(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("not: valid: yaml: : :")
        with pytest.raises(ValueError, match="YAML"):
            load_from_disk(p)

    def test_schema_violation_raises_with_path(self, tmp_path: Path):
        # Confidence > 1 in a deeply nested entry; error should point to it.
        bad = VALID_YAML.replace("0.94", "1.94")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="confidence"):
            load_from_disk(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_load.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_from_disk'`.

- [ ] **Step 3: Implement load_from_disk**

Append to `utils/runtime_overrides.py`:

```python
from datetime import timezone as _timezone
from pathlib import Path

import yaml as _yaml


def _default_empty_state() -> OverridesState:
    """Return a baseline OverridesState used when no overrides file exists.

    Mode defaults to 'shadow' -- the safest default. Even if the agent
    later writes to this state, shadow mode means the bot ignores `applied`
    until a human flips mode by hand.
    """
    return OverridesState(
        version=1,
        updated_at=datetime.now(_timezone.utc),
        updated_by="default-empty",
        mode="shadow",
    )


def load_from_disk(path: Path) -> OverridesState:
    """Read a YAML overrides file and return a parsed OverridesState.

    Behavior contract:
      - Missing file: return _default_empty_state(). NOT an error.
      - Empty file: raise ValueError. The agent should never write empty.
      - Malformed YAML: raise ValueError wrapping the underlying YAMLError.
      - Schema violation: raise ValueError with the field path that failed.
    """
    if not path.exists():
        return _default_empty_state()

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"overrides file at {path} is empty")

    try:
        data = _yaml.safe_load(text)
    except _yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {path}: {exc}") from exc

    if data is None:
        raise ValueError(f"overrides file at {path} parsed to None (empty document)")

    return parse_yaml_to_state(data)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_load.py -v`
Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_load.py
git commit -m "feat(runtime-overrides): load_from_disk with graceful missing-file handling (Phase 1, task 7)"
```

---

### Task 8: Atomic-write helper

**Files:**
- Modify: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_atomic.py`

- [ ] **Step 1: Write failing tests for atomic write**

Create `tests/test_runtime_overrides_atomic.py`:

```python
"""Atomic-write tests for runtime_overrides.

The agent (Phase 2+) and humans use this helper to write the YAML file
without ever leaving a partial file visible to the bot reader. This is
load-bearing for the no-corruption-on-concurrent-access guarantee.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from utils.runtime_overrides import (
    OverridesState,
    atomic_write_state,
    load_from_disk,
)


def _utc() -> datetime:
    return datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)


def _make_state(updated_by: str) -> OverridesState:
    return OverridesState(
        version=1,
        updated_at=_utc(),
        updated_by=updated_by,
        mode="shadow",
    )


class TestAtomicWriteState:
    def test_writes_valid_yaml_round_trip(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        state = _make_state("test-writer")
        atomic_write_state(state, target)
        assert target.exists()
        loaded = load_from_disk(target)
        assert loaded.updated_by == "test-writer"

    def test_no_temp_file_left_behind_on_success(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("a"), target)
        siblings = list(tmp_path.iterdir())
        # Only the target file. No .tmp / .partial / etc.
        assert siblings == [target]

    def test_existing_file_replaced_atomically(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)
        atomic_write_state(_make_state("second"), target)
        loaded = load_from_disk(target)
        assert loaded.updated_by == "second"

    def test_concurrent_read_during_write_never_truncated(self, tmp_path: Path):
        """A reader running in parallel with a writer must always see a
        complete, valid file -- never a half-written one.

        This test runs a writer in a tight loop while a reader runs in
        parallel and asserts the loaded state is always valid.
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("initial"), target)

        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            try:
                count = 0
                while not stop.is_set():
                    atomic_write_state(_make_state(f"writer_{count}"), target)
                    count += 1
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                while not stop.is_set():
                    state = load_from_disk(target)
                    assert isinstance(state, OverridesState)
                    assert state.version == 1
                    # Brief pause so we don't pin a CPU
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),  # two readers to stress more
        ]
        for t in threads:
            t.start()

        # Run for 0.5s of contention
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)

        assert errors == [], f"concurrent access produced errors: {errors}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_atomic.py -v`
Expected: FAIL with `ImportError: cannot import name 'atomic_write_state'`.

- [ ] **Step 3: Implement atomic_write_state**

Append to `utils/runtime_overrides.py`:

```python
import dataclasses as _dataclasses
import os as _os


def _state_to_yaml_dict(state: OverridesState) -> dict:
    """Serialize an OverridesState to a dict suitable for yaml.safe_dump."""

    def _override_to_dict(o: _OverrideBase) -> dict:
        d = _dataclasses.asdict(o)
        # asdict converts nested dataclasses too -- predicted_effect becomes
        # a dict already. Re-format datetimes to ISO 8601 with explicit UTC.
        d["decided_at"] = o.decided_at.isoformat()
        d["expires_at"] = (
            o.expires_at.isoformat() if o.expires_at is not None else None
        )
        d["predicted_effect"]["evaluate_at"] = o.predicted_effect.evaluate_at.isoformat()
        return d

    out: dict = {
        "version": state.version,
        "updated_at": state.updated_at.isoformat(),
        "updated_by": state.updated_by,
        "mode": state.mode,
        "applied": {
            "disabled_sources": [_override_to_dict(o) for o in state.applied_disabled_sources],
            "disabled_keywords": [_override_to_dict(o) for o in state.applied_disabled_keywords],
            "threshold_overrides": [_override_to_dict(o) for o in state.applied_threshold_overrides],
        },
        "proposed": {
            "disabled_sources": [_override_to_dict(o) for o in state.proposed_disabled_sources],
            "disabled_keywords": [_override_to_dict(o) for o in state.proposed_disabled_keywords],
            "threshold_overrides": [_override_to_dict(o) for o in state.proposed_threshold_overrides],
        },
    }
    if state.last_applied_batch is not None:
        out["last_applied_batch"] = state.last_applied_batch
    return out


def atomic_write_state(state: OverridesState, target: Path) -> None:
    """Write state to target via temp-file-and-rename.

    The bot reader doing a concurrent read at any point during this
    function will always see either the previous valid file or the new
    valid file -- never a half-written file. Achieved via os.rename
    (POSIX atomic) or os.replace (cross-platform atomic on Windows too).

    The temp file is created in the same directory as `target` so the
    rename is on the same filesystem (rename across filesystems is NOT
    atomic on POSIX).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = _state_to_yaml_dict(state)
    text = _yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    tmp.write_text(text, encoding="utf-8")
    _os.replace(tmp, target)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_atomic.py -v`
Expected: 4/4 PASS (including the concurrency test).

- [ ] **Step 4a: Add failure-mode tests for atomic write**

The four tests above cover the happy path and concurrent reads. Per the "100%-confidence safety layer" mandate, also verify the negative path: a failed write must NOT corrupt or remove the previously valid file.

Append to `tests/test_runtime_overrides_atomic.py`:

```python
class TestAtomicWriteFailureModes:
    def test_replace_failure_preserves_previous_file(self, tmp_path: Path, monkeypatch):
        """If os.replace raises mid-write, the target on disk must still be
        the previous valid version -- never empty, never partial.
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)
        original_bytes = target.read_bytes()

        import utils.runtime_overrides as mod

        def boom(*_args, **_kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod._os, "replace", boom)

        with pytest.raises(OSError, match="simulated rename failure"):
            atomic_write_state(_make_state("second"), target)

        # Target must still exist and contain the original content unchanged.
        assert target.exists()
        assert target.read_bytes() == original_bytes

        # And no .tmp file should be left behind in user-visible state for
        # production code -- but acceptable here since the rename failed
        # mid-flight. Just confirm the target itself is intact.
        loaded = load_from_disk(target)
        assert loaded.updated_by == "first"

    def test_temp_file_cleaned_up_on_replace_failure(self, tmp_path: Path, monkeypatch):
        """If os.replace fails, the implementation should remove the orphan
        temp file so subsequent writes don't trip over it. (Defensive: the
        cleanup is best-effort -- if it can't remove the temp, the next
        successful write will overwrite it anyway.)
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)

        import utils.runtime_overrides as mod

        def boom(*_args, **_kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod._os, "replace", boom)

        with pytest.raises(OSError):
            atomic_write_state(_make_state("second"), target)

        # The implementation should have cleaned up its own temp file.
        siblings = sorted(p.name for p in tmp_path.iterdir())
        assert siblings == ["overrides.yaml"], f"orphan temp file left behind: {siblings}"
```

The second test asserts a behavioral requirement on the implementation. **Update `atomic_write_state` to clean up its own temp file on rename failure:**

```python
def atomic_write_state(state: OverridesState, target: Path) -> None:
    """Write state to target via temp-file-and-rename.

    The bot reader doing a concurrent read at any point during this
    function will always see either the previous valid file or the new
    valid file -- never a half-written file. Achieved via os.rename
    (POSIX atomic) or os.replace (cross-platform atomic on Windows too).

    The temp file is created in the same directory as `target` so the
    rename is on the same filesystem (rename across filesystems is NOT
    atomic on POSIX).

    On rename failure the temp file is removed so it doesn't accumulate
    or confuse the next writer. The original target (if any) is left
    untouched; previous-content guarantee holds.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    payload = _state_to_yaml_dict(state)
    text = _yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    tmp.write_text(text, encoding="utf-8")
    try:
        _os.replace(tmp, target)
    except OSError:
        # Best-effort cleanup; never mask the original error.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
```

Run: `pytest tests/test_runtime_overrides_atomic.py -v`
Expected: 6/6 PASS (4 from Step 1 + 2 new failure-mode).

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_atomic.py
git commit -m "feat(runtime-overrides): atomic_write_state with concurrency tests (Phase 1, task 8)"
```

---

### Task 9: `RuntimeOverridesReader` singleton + state swap + diff detection

**Files:**
- Modify: `utils/runtime_overrides.py`
- Create: `tests/test_runtime_overrides_reader.py`

- [ ] **Step 1: Write failing tests for the reader**

Create `tests/test_runtime_overrides_reader.py`:

```python
"""Tests for RuntimeOverridesReader: singleton, state-swap, diff detection."""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from utils.runtime_overrides import (
    OverridesState,
    RuntimeOverridesReader,
    StateDiff,
)


VALID_YAML_NO_OVERRIDES = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


def _yaml_with_disabled_source(source: str, decision_id: str) -> str:
    return textwrap.dedent(f"""
        version: 1
        updated_at: "2026-05-02T14:30:00+00:00"
        updated_by: "test"
        mode: real
        applied:
          disabled_sources:
            - source: "{source}"
              reason: "test"
              confidence: 0.9
              decided_at: "2026-05-02T14:30:00+00:00"
              decided_by: "test"
              decision_id: "{decision_id}"
              expires_at: null
              predicted_effect:
                metric: "m"
                baseline: 0
                predicted_post_change: 0
                evaluate_at: "2026-05-09T14:30:00+00:00"
          disabled_keywords: []
          threshold_overrides: []
        proposed:
          disabled_sources: []
          disabled_keywords: []
          threshold_overrides: []
    """).lstrip()


class TestRuntimeOverridesReader:
    def test_initial_state_is_empty_default(self, tmp_path: Path):
        r = RuntimeOverridesReader(path=tmp_path / "missing.yaml")
        # Before any reload, state is the default empty
        assert r.snapshot().applied_disabled_sources == []

    def test_reload_loads_state(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        diff = r.reload()
        assert len(r.snapshot().applied_disabled_sources) == 1
        assert isinstance(diff, StateDiff)

    def test_reload_failure_keeps_previous_state(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()  # load valid state
        assert len(r.snapshot().applied_disabled_sources) == 1

        # Corrupt the file
        p.write_text("not: valid: yaml: : :")
        with pytest.raises(ValueError):
            r.reload()
        # Previous state preserved
        assert len(r.snapshot().applied_disabled_sources) == 1

    def test_diff_detects_added_source(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML_NO_OVERRIDES)
        r = RuntimeOverridesReader(path=p)
        r.reload()  # initial empty applied state

        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        diff = r.reload()
        assert "r/Turkey" in diff.sources_added
        assert diff.sources_removed == set()

    def test_diff_detects_removed_source(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()

        p.write_text(VALID_YAML_NO_OVERRIDES)
        diff = r.reload()
        assert "r/Turkey" in diff.sources_removed
        assert diff.sources_added == set()

    def test_diff_no_changes_when_state_identical(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()

        diff = r.reload()  # reload same content
        assert diff.is_empty() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_reader.py -v`
Expected: FAIL with `ImportError: cannot import name 'RuntimeOverridesReader'`.

- [ ] **Step 3: Implement reader + StateDiff**

Append to `utils/runtime_overrides.py`:

```python
@dataclass
class StateDiff:
    """Difference between two OverridesStates -- used for change logging.

    `sources_added` / `sources_removed` are the source-name string sets.
    Same convention for keywords. Threshold overrides reported as
    (path, value) tuples since the value is the meaningful change.
    """

    sources_added: set[str] = field(default_factory=set)
    sources_removed: set[str] = field(default_factory=set)
    keywords_added: set[str] = field(default_factory=set)
    keywords_removed: set[str] = field(default_factory=set)
    thresholds_added: set[tuple[str, Any]] = field(default_factory=set)
    thresholds_removed: set[tuple[str, Any]] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (
            self.sources_added or self.sources_removed
            or self.keywords_added or self.keywords_removed
            or self.thresholds_added or self.thresholds_removed
        )


def _diff_states(prev: OverridesState, new: OverridesState) -> StateDiff:
    prev_sources = {o.source for o in prev.applied_disabled_sources}
    new_sources = {o.source for o in new.applied_disabled_sources}
    prev_keywords = {o.keyword for o in prev.applied_disabled_keywords}
    new_keywords = {o.keyword for o in new.applied_disabled_keywords}
    # Thresholds aren't hashable as full dataclasses, so use (path, value).
    # value may be an unhashable type in principle; convert to str defensively.
    prev_thresholds = {(o.path, _hashable(o.value)) for o in prev.applied_threshold_overrides}
    new_thresholds = {(o.path, _hashable(o.value)) for o in new.applied_threshold_overrides}

    return StateDiff(
        sources_added=new_sources - prev_sources,
        sources_removed=prev_sources - new_sources,
        keywords_added=new_keywords - prev_keywords,
        keywords_removed=prev_keywords - new_keywords,
        thresholds_added=new_thresholds - prev_thresholds,
        thresholds_removed=prev_thresholds - new_thresholds,
    )


def _hashable(value: Any) -> Any:
    """Best-effort hashable form of an arbitrary YAML value."""
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    try:
        hash(value)
        return value
    except TypeError:
        return str(value)


class RuntimeOverridesReader:
    """In-process singleton holding the current effective overrides state.

    Phase 1: bot creates one of these at startup. The asyncio poll task
    calls reload() every N minutes. Existing pipeline modules query
    is_source_disabled / is_keyword_disabled / get_threshold_override
    instead of indexing into static config sets directly.

    Reload contract: returns a StateDiff describing what changed.
    Reload failure (malformed YAML, schema violation) raises and leaves
    the previous valid state in place -- the bot never operates on a
    half-loaded state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._state: OverridesState = _default_empty_state()

    def snapshot(self) -> OverridesState:
        """Return the current loaded state. Useful for diagnostics."""
        return self._state

    def reload(self) -> StateDiff:
        """Read the file fresh, compute the diff vs current state, swap.

        On failure: previous state preserved; exception raised. Caller
        is responsible for catching and logging (see tasks/runtime_overrides_task.py).
        """
        new_state_raw = load_from_disk(self._path)
        new_state = filter_expired(new_state_raw, now=datetime.now(_timezone.utc))
        diff = _diff_states(self._state, new_state)
        self._state = new_state
        return diff
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_reader.py -v`
Expected: 6/6 PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_reader.py
git commit -m "feat(runtime-overrides): RuntimeOverridesReader + StateDiff (Phase 1, task 9)"
```

---

### Task 10: Reader query methods (`is_source_disabled`, `is_keyword_disabled`, `get_threshold_override`)

**Files:**
- Modify: `utils/runtime_overrides.py`
- Modify: `tests/test_runtime_overrides_reader.py`

- [ ] **Step 1: Append failing tests for query methods**

Append to `tests/test_runtime_overrides_reader.py`:

```python
class TestReaderQueries:
    def test_is_source_disabled_runtime_only(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("r/Turkey", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("r/Turkey") is True
        assert r.is_source_disabled("r/SomeOtherSub") is False

    def test_is_source_disabled_unioned_with_static_config(self, tmp_path: Path, monkeypatch):
        # Reader unions runtime overrides with static config for a single
        # source-of-truth answer. We patch the static set in config so we
        # don't accidentally exercise the real list.
        from config import DISABLED_NEWS_SOURCES
        monkeypatch.setattr(
            "utils.runtime_overrides._static_disabled_sources",
            lambda: frozenset({"static_only_source"}),
        )
        p = tmp_path / "overrides.yaml"
        p.write_text(_yaml_with_disabled_source("runtime_only_source", "gd_2026-05-02_0042"))
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("static_only_source") is True
        assert r.is_source_disabled("runtime_only_source") is True
        assert r.is_source_disabled("not_disabled_anywhere") is False

    def test_is_keyword_disabled(self, tmp_path: Path):
        yaml_with_kw = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: real
            applied:
              disabled_sources: []
              disabled_keywords:
                - keyword: "trump may deadline"
                  reason: "time-bounded"
                  confidence: 0.8
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0043"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
              threshold_overrides: []
            proposed:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_with_kw)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_keyword_disabled("trump may deadline") is True
        assert r.is_keyword_disabled("other keyword") is False

    def test_get_threshold_override_returns_value_when_set(self, tmp_path: Path):
        yaml_with_threshold = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: real
            applied:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides:
                - path: "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
                  value: 21600
                  reason: "slow"
                  confidence: 0.7
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0044"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
            proposed:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_with_threshold)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA") == 21600
        assert r.get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.OtherSrc") is None

    def test_proposed_section_not_visible_to_queries(self, tmp_path: Path):
        # Bot must IGNORE proposed entirely. Querying a source that's
        # only in proposed must return False.
        yaml_proposed_only = textwrap.dedent("""
            version: 1
            updated_at: "2026-05-02T14:30:00+00:00"
            updated_by: "test"
            mode: shadow
            applied:
              disabled_sources: []
              disabled_keywords: []
              threshold_overrides: []
            proposed:
              disabled_sources:
                - source: "r/proposed_only"
                  reason: "shadow"
                  confidence: 0.9
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0099"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
              disabled_keywords: []
              threshold_overrides: []
        """).lstrip()
        p = tmp_path / "overrides.yaml"
        p.write_text(yaml_proposed_only)
        r = RuntimeOverridesReader(path=p)
        r.reload()
        assert r.is_source_disabled("r/proposed_only") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_reader.py::TestReaderQueries -v`
Expected: FAIL with `AttributeError` or `ImportError` for missing methods.

- [ ] **Step 3: Implement query methods**

Append to `utils/runtime_overrides.py`:

```python
def _static_disabled_sources() -> frozenset[str]:
    """Indirection for test monkey-patching. Returns the static disabled
    set from config. Wrapped in a function so tests can replace it
    without monkey-patching the entire config module."""
    from config import DISABLED_NEWS_SOURCES
    return frozenset(DISABLED_NEWS_SOURCES)


# Methods for RuntimeOverridesReader -- attach by extending class above.
def _reader_is_source_disabled(self: RuntimeOverridesReader, source: str) -> bool:
    """True iff source is in static config OR in runtime-applied disabled set."""
    if source in _static_disabled_sources():
        return True
    return any(o.source == source for o in self._state.applied_disabled_sources)


def _reader_is_keyword_disabled(self: RuntimeOverridesReader, keyword: str) -> bool:
    """True iff keyword is in runtime-applied disabled set.

    Phase 1 has no static counterpart for keywords; the bot's
    GEOPOLITICAL_SIGNALS list is unchanged. Disabling here means
    market_matcher's keyword iteration skips this keyword.
    """
    return any(o.keyword == keyword for o in self._state.applied_disabled_keywords)


def _reader_get_threshold_override(self: RuntimeOverridesReader, path: str) -> Any:
    """Return the override value for a dotted path, or None if not overridden.

    Caller MUST treat None as 'no override' and fall back to the static
    config value -- never confuse None-as-override with no-override.
    """
    for o in self._state.applied_threshold_overrides:
        if o.path == path:
            return o.value
    return None


RuntimeOverridesReader.is_source_disabled = _reader_is_source_disabled  # type: ignore[attr-defined]
RuntimeOverridesReader.is_keyword_disabled = _reader_is_keyword_disabled  # type: ignore[attr-defined]
RuntimeOverridesReader.get_threshold_override = _reader_get_threshold_override  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_reader.py -v`
Expected: 11/11 PASS (6 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_reader.py
git commit -m "feat(runtime-overrides): is_source/is_keyword/get_threshold_override queries (Phase 1, task 10)"
```

---

### Task 11: `AuditLogger` JSONL writer with daily rotation

**Files:**
- Create: `governance/audit.py`
- Create: `tests/test_governance_audit.py`

- [ ] **Step 1: Write failing tests for AuditLogger**

Create `tests/test_governance_audit.py`:

```python
"""Tests for governance.audit.AuditLogger.

The audit logger is append-only. Daily rotation matches the bot.log
pattern: `decisions.jsonl.YYYY-MM-DD` for archives, gzip after 7 days.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from governance.audit import AuditLogger


def _record(event_type: str = "GOVERNANCE_TEST") -> dict:
    return {
        "type": event_type,
        "ts": "2026-05-02T14:30:00+00:00",
        "payload": {"x": 1},
    }


class TestAuditLoggerAppend:
    def test_first_write_creates_file(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record())
        target = log_dir / "decisions.jsonl"
        assert target.exists()
        line = target.read_text(encoding="utf-8").strip()
        assert json.loads(line)["type"] == "GOVERNANCE_TEST"

    def test_multiple_appends_one_line_each(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record("FIRST"))
        logger.append(_record("SECOND"))
        logger.append(_record("THIRD"))
        target = log_dir / "decisions.jsonl"
        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["type"] == "FIRST"
        assert json.loads(lines[2])["type"] == "THIRD"

    def test_log_dir_auto_created(self, tmp_path: Path):
        log_dir = tmp_path / "deeply" / "nested" / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        logger.append(_record())
        assert log_dir.exists()


class TestAuditLoggerRotation:
    def test_rotates_at_utc_midnight_change(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        # Inject a "now" function so we can simulate clock advancing.
        clock = datetime(2026, 5, 2, 23, 59, 0, tzinfo=timezone.utc)
        def fake_now():
            return clock
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl", now=fake_now)
        logger.append(_record("BEFORE_MIDNIGHT"))

        # Advance past midnight UTC
        clock = datetime(2026, 5, 3, 0, 1, 0, tzinfo=timezone.utc)
        logger.append(_record("AFTER_MIDNIGHT"))

        archive = log_dir / "decisions.jsonl.2026-05-02"
        current = log_dir / "decisions.jsonl"
        assert archive.exists()
        assert current.exists()
        # Archive contains the BEFORE record
        before_line = archive.read_text(encoding="utf-8").strip()
        assert json.loads(before_line)["type"] == "BEFORE_MIDNIGHT"
        # Current contains only the AFTER record
        after_line = current.read_text(encoding="utf-8").strip()
        assert json.loads(after_line)["type"] == "AFTER_MIDNIGHT"

    def test_no_rotation_when_same_day(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        clock = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl", now=lambda: clock)
        logger.append(_record("FIRST"))
        clock = datetime(2026, 5, 2, 23, 59, 0, tzinfo=timezone.utc)
        logger.append(_record("SECOND"))
        target = log_dir / "decisions.jsonl"
        assert target.exists()
        archive = log_dir / "decisions.jsonl.2026-05-02"
        assert not archive.exists()
        assert len(target.read_text(encoding="utf-8").splitlines()) == 2


class TestAuditLoggerErrorHandling:
    def test_non_serializable_record_raises_typeerror(self, tmp_path: Path):
        """Caller is responsible for pre-serializing values (datetimes, etc).
        If a non-JSON-serializable value slips through, raise TypeError
        loudly rather than silently writing a corrupt line.
        """
        log_dir = tmp_path / "governance"
        logger = AuditLogger(log_dir=log_dir, basename="decisions.jsonl")
        bad = {"type": "T", "ts": datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)}
        with pytest.raises(TypeError):
            logger.append(bad)
        # The current file may exist but should not contain a corrupt line.
        target = log_dir / "decisions.jsonl"
        if target.exists():
            text = target.read_text(encoding="utf-8")
            for line in text.splitlines():
                json.loads(line)  # would raise if any line is corrupt


class TestAuditLoggerCompression:
    def test_archive_older_than_7d_gzipped(self, tmp_path: Path):
        log_dir = tmp_path / "governance"
        log_dir.mkdir(parents=True)
        # Pre-populate an old archive that should be compressed
        old_archive = log_dir / "decisions.jsonl.2026-04-01"
        old_archive.write_text("{}\n", encoding="utf-8")

        clock = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
        logger = AuditLogger(
            log_dir=log_dir,
            basename="decisions.jsonl",
            now=lambda: clock,
            compress_after_days=7,
        )
        logger.compress_old_archives()

        compressed = log_dir / "decisions.jsonl.2026-04-01.gz"
        assert compressed.exists()
        assert not old_archive.exists()
        with gzip.open(compressed, "rt") as f:
            assert f.read() == "{}\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_governance_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'governance.audit'`.

- [ ] **Step 3: Implement AuditLogger**

Create `governance/audit.py`:

```python
"""Append-only JSONL audit logger with daily rotation.

Used by the governance agent (Phase 2+) to record every decision +
governance event. Phase 1 builds and tests this standalone -- the agent
that consumes it lands in Phase 2.

Rotation pattern matches utils/logger.py's bot.log convention:
  decisions.jsonl                     (current day)
  decisions.jsonl.YYYY-MM-DD          (archived days)
  decisions.jsonl.YYYY-MM-DD.gz       (after compress_after_days)
"""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogger:
    """Append-only JSONL writer with UTC-midnight daily rotation.

    The writer keeps a tiny amount of state: the date of the last write.
    On each append, if the current UTC date differs from last-write date,
    rotate the existing file to `<basename>.YYYY-MM-DD` (the previous
    day) and start writing fresh.

    Compression (calling compress_old_archives()) is a separate idempotent
    step that the agent invokes once per cycle. It walks the log dir,
    gzips any archive older than `compress_after_days`. Safe to call
    repeatedly.
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        basename: str = "decisions.jsonl",
        now: Callable[[], datetime] = _default_now,
        compress_after_days: int = 7,
    ) -> None:
        self._log_dir = log_dir
        self._basename = basename
        self._now = now
        self._compress_after_days = compress_after_days
        self._last_write_date: str | None = None  # YYYY-MM-DD UTC
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        """Append a single record as one JSON line.

        Rotates atomically if the day changed since the last write. The
        rotation is best-effort; if the rename fails, we log the failure
        and continue writing to the current file (don't fail to record
        a decision because rotation hit a race).
        """
        now = self._now()
        today = now.strftime("%Y-%m-%d")
        target = self._log_dir / self._basename

        if (
            self._last_write_date is not None
            and self._last_write_date != today
            and target.exists()
        ):
            # Rotate: rename current file to <basename>.<previous_date>
            archive_path = self._log_dir / f"{self._basename}.{self._last_write_date}"
            try:
                target.rename(archive_path)
            except OSError:
                # Rotation lost a race or hit a permission error. Log and
                # continue -- recording the decision is more important than
                # rotation hygiene.
                pass

        line = json.dumps(record, ensure_ascii=False) + "\n"
        with target.open("a", encoding="utf-8") as f:
            f.write(line)
        self._last_write_date = today

    def compress_old_archives(self) -> None:
        """Gzip any archive older than compress_after_days.

        Idempotent: already-compressed archives are skipped. Walks
        log_dir looking for files matching `<basename>.YYYY-MM-DD`.
        """
        now = self._now()
        for entry in self._log_dir.iterdir():
            if not entry.is_file():
                continue
            if not entry.name.startswith(f"{self._basename}."):
                continue
            if entry.name.endswith(".gz"):
                continue
            date_part = entry.name[len(self._basename) + 1 :]  # +1 for the dot
            try:
                archive_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            age_days = (now - archive_date).days
            if age_days < self._compress_after_days:
                continue
            compressed = entry.with_suffix(entry.suffix + ".gz")
            with entry.open("rb") as src, gzip.open(compressed, "wb") as dst:
                shutil.copyfileobj(src, dst)
            entry.unlink()
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_governance_audit.py -v`
Expected: 7/7 PASS (6 original + 1 non-serializable error-handling test).

Note: `json.dumps` already raises `TypeError` on a non-serializable value before any file write happens, so the test passes against the existing implementation without changes — but locking the behavior in tests prevents future "swallow the error to keep logging" regressions.

- [ ] **Step 5: Commit**

```bash
git add governance/audit.py tests/test_governance_audit.py
git commit -m "feat(governance): AuditLogger JSONL writer with daily rotation (Phase 1, task 11)"
```

---

### Task 12: Asyncio poll task

**Files:**
- Create: `tasks/runtime_overrides_task.py`
- Create: `tests/test_runtime_overrides_task.py`

- [ ] **Step 1: Write failing tests for the poll task**

Create `tests/test_runtime_overrides_task.py`:

```python
"""Tests for the asyncio poll task that hot-reloads the overrides file."""

from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

import pytest

from tasks.runtime_overrides_task import run_runtime_overrides_poll
from utils.runtime_overrides import RuntimeOverridesReader


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


@pytest.mark.asyncio
async def test_polls_and_reloads(tmp_path: Path):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)

    # Run the poll task as a background task with a tiny interval; cancel
    # after a couple of cycles.
    task = asyncio.create_task(
        run_runtime_overrides_poll(reader, interval_secs=0.05)
    )
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Final state should be loaded
    assert reader.snapshot().version == 1


@pytest.mark.asyncio
async def test_malformed_file_does_not_crash_task(tmp_path: Path, caplog):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)
    reader.reload()  # initial valid state

    # Corrupt the file
    p.write_text("not: valid: yaml: : :")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(
            run_runtime_overrides_poll(reader, interval_secs=0.05)
        )
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Task survived; warning logged
    assert any("malformed" in r.message.lower() or "yaml" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_diff_logged_on_change(tmp_path: Path, caplog):
    p = tmp_path / "overrides.yaml"
    p.write_text(VALID_YAML)
    reader = RuntimeOverridesReader(path=p)

    yaml_with_source = VALID_YAML.replace(
        "disabled_sources: []",
        textwrap.dedent("""
            disabled_sources:
                - source: "r/Test"
                  reason: "x"
                  confidence: 0.9
                  decided_at: "2026-05-02T14:30:00+00:00"
                  decided_by: "test"
                  decision_id: "gd_2026-05-02_0099"
                  expires_at: null
                  predicted_effect:
                    metric: "m"
                    baseline: 0
                    predicted_post_change: 0
                    evaluate_at: "2026-05-09T14:30:00+00:00"
        """).strip(),
        1,  # only the first occurrence (the `applied` one)
    )

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(
            run_runtime_overrides_poll(reader, interval_secs=0.05)
        )
        await asyncio.sleep(0.10)
        p.write_text(yaml_with_source)
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert any("r/Test" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_task.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tasks.runtime_overrides_task'`.

- [ ] **Step 3: Implement poll task**

Create `tasks/runtime_overrides_task.py`:

```python
"""Asyncio poll task: hot-reload runtime_overrides.yaml every N seconds.

Lifecycle owned by main.py — task is started during bot startup as part
of the bot's task group and cancelled on shutdown. Failures inside the
poll loop are caught and logged; the task itself never propagates an
exception.

See docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md §7.
"""

from __future__ import annotations

import asyncio
import logging

from utils.runtime_overrides import RuntimeOverridesReader

log = logging.getLogger("runtime_overrides_task")


def _format_diff_for_log(diff) -> str:
    parts: list[str] = []
    if diff.sources_added:
        parts.append(f"+sources={sorted(diff.sources_added)}")
    if diff.sources_removed:
        parts.append(f"-sources={sorted(diff.sources_removed)}")
    if diff.keywords_added:
        parts.append(f"+keywords={sorted(diff.keywords_added)}")
    if diff.keywords_removed:
        parts.append(f"-keywords={sorted(diff.keywords_removed)}")
    if diff.thresholds_added:
        parts.append(f"+thresholds={sorted(diff.thresholds_added)}")
    if diff.thresholds_removed:
        parts.append(f"-thresholds={sorted(diff.thresholds_removed)}")
    return " ".join(parts) if parts else "(no changes)"


async def run_runtime_overrides_poll(
    reader: RuntimeOverridesReader,
    interval_secs: float = 600.0,
) -> None:
    """Poll the overrides file every interval_secs and reload on change.

    Runs forever until cancelled. Catches all exceptions inside the
    loop body; does NOT propagate (a malformed YAML file should not
    crash the bot's async task group).
    """
    log.info("runtime_overrides poll task started (interval=%.1fs)", interval_secs)
    while True:
        try:
            diff = reader.reload()
            if not diff.is_empty():
                log.info("runtime overrides reloaded: %s", _format_diff_for_log(diff))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("runtime_overrides reload failed; previous state preserved: %s", exc)
        try:
            await asyncio.sleep(interval_secs)
        except asyncio.CancelledError:
            raise
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_runtime_overrides_task.py -v`
Expected: 3/3 PASS.

- [ ] **Step 5: Commit**

```bash
git add tasks/runtime_overrides_task.py tests/test_runtime_overrides_task.py
git commit -m "feat(tasks): asyncio runtime_overrides poll task with failure isolation (Phase 1, task 12)"
```

---

### Task 13: Property test — `effective_config == static UNION applied_unexpired`

**Files:**
- Create: `tests/test_runtime_overrides_property.py`

- [ ] **Step 1: Write the property test**

Create `tests/test_runtime_overrides_property.py`:

```python
"""Hypothesis property test for the core invariant of the runtime overrides
reader: the bot's effective view of disabled sources is exactly
`static_config UNION applied_runtime_disabled_unexpired_sources`.

This is the load-bearing correctness property of Phase 1. If this fails,
the bot is operating on a config view that doesn't match what the human
or agent wrote -- the worst possible silent failure mode.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from utils.runtime_overrides import (
    DisabledSource,
    OverridesState,
    PredictedEffect,
    RuntimeOverridesReader,
    atomic_write_state,
)


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)


@st.composite
def _disabled_source_strategy(draw, decision_id_seed: int) -> DisabledSource:
    source = draw(st.text(min_size=1, max_size=40, alphabet=st.characters(
        whitelist_categories=("L", "N"), whitelist_characters="_/-",
    )))
    expires_offset_hours = draw(st.one_of(
        st.none(),
        st.integers(min_value=-1000, max_value=1000),
    ))
    expires_at = (
        None if expires_offset_hours is None
        else _NOW + timedelta(hours=expires_offset_hours)
    )
    return DisabledSource(
        source=source,
        reason="test",
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        decided_at=_NOW,
        decided_by="hypothesis",
        decision_id=f"gd_2026-05-02_{decision_id_seed:04d}",
        expires_at=expires_at,
        predicted_effect=PredictedEffect(
            metric="m", baseline=0.0, predicted_post_change=0.0, evaluate_at=_NOW,
        ),
    )


@st.composite
def _state_strategy(draw) -> OverridesState:
    sources = draw(st.lists(
        st.builds(
            lambda i, kw: kw,
            st.integers(min_value=0, max_value=9999),
            _disabled_source_strategy(decision_id_seed=draw(st.integers(min_value=0, max_value=9999))),
        ),
        max_size=10,
    ))
    return OverridesState(
        version=1,
        updated_at=_NOW,
        updated_by="hypothesis",
        mode="real",
        applied_disabled_sources=sources,
    )


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(_state_strategy(), st.sets(st.text(min_size=1, max_size=20)))
def test_effective_disabled_sources_invariant(state: OverridesState, static_set: set[str], tmp_path_factory):
    """For any (state, static_set), reader.is_source_disabled returns True
    iff the source is in static_set OR in applied_disabled_sources with
    an unexpired or null expires_at."""
    tmp_path = tmp_path_factory.mktemp("hyp")
    target = tmp_path / "overrides.yaml"
    atomic_write_state(state, target)

    reader = RuntimeOverridesReader(path=target)
    # Patch the static-source-set indirection
    from utils import runtime_overrides as ro
    original = ro._static_disabled_sources
    ro._static_disabled_sources = lambda: frozenset(static_set)
    try:
        reader.reload()

        # Expected effective set: static UNION runtime-applied-unexpired
        effective_runtime = {
            o.source for o in state.applied_disabled_sources
            if o.expires_at is None or o.expires_at > _NOW
        }
        expected_effective = static_set | effective_runtime

        # Spot-check several names: anything in expected -> True;
        # anything not in expected -> False (with sample names).
        for name in expected_effective:
            assert reader.is_source_disabled(name) is True, f"expected {name} disabled"
        # Test some known-not-disabled names
        sample_negatives = {"r/probably_not_in_set_xyzzy", "definitely_not_real_source"}
        for name in sample_negatives - expected_effective:
            assert reader.is_source_disabled(name) is False, f"expected {name} NOT disabled"
    finally:
        ro._static_disabled_sources = original
```

- [ ] **Step 2: Run the property test**

Run: `pytest tests/test_runtime_overrides_property.py -v`
Expected: PASS (50 random examples explored).

- [ ] **Step 3: Commit**

```bash
git add tests/test_runtime_overrides_property.py
git commit -m "test(runtime-overrides): Hypothesis property test for effective-config invariant (Phase 1, task 13)"
```

---

### Task 14: Backward-compat test — bot works with no overrides file

**Files:**
- Create: `tests/test_runtime_overrides_backward_compat.py`

- [ ] **Step 1: Write the backward-compat test**

Create `tests/test_runtime_overrides_backward_compat.py`:

```python
"""Backward-compat test: behavior with NO overrides file present.

Bot must continue to operate identically to its pre-Phase-1 self when
no `data/runtime_overrides.yaml` file exists. This test exists to make
that an explicit invariant rather than an implicit assumption.
"""

from __future__ import annotations

from pathlib import Path

from utils.runtime_overrides import RuntimeOverridesReader


def test_no_file_yields_empty_overrides(tmp_path: Path):
    p = tmp_path / "does_not_exist.yaml"
    assert not p.exists()

    reader = RuntimeOverridesReader(path=p)
    reader.reload()

    state = reader.snapshot()
    assert state.applied_disabled_sources == []
    assert state.applied_disabled_keywords == []
    assert state.applied_threshold_overrides == []


def test_no_file_queries_return_static_only(tmp_path: Path, monkeypatch):
    from utils import runtime_overrides as ro
    monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset({"static_src"}))

    p = tmp_path / "does_not_exist.yaml"
    reader = RuntimeOverridesReader(path=p)
    reader.reload()

    # Static-only result -- no runtime overrides
    assert reader.is_source_disabled("static_src") is True
    assert reader.is_source_disabled("other") is False
    assert reader.is_keyword_disabled("any_keyword") is False
    assert reader.get_threshold_override("any.path") is None


def test_repeated_reload_with_no_file_idempotent(tmp_path: Path):
    p = tmp_path / "does_not_exist.yaml"
    reader = RuntimeOverridesReader(path=p)
    diff_first = reader.reload()
    diff_second = reader.reload()
    diff_third = reader.reload()
    # First reload may show changes from the default empty state to
    # a freshly-read empty state (none in practice). Subsequent reloads
    # must produce empty diffs.
    assert diff_second.is_empty()
    assert diff_third.is_empty()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_runtime_overrides_backward_compat.py -v`
Expected: 3/3 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_runtime_overrides_backward_compat.py
git commit -m "test(runtime-overrides): backward-compat with no overrides file (Phase 1, task 14)"
```

---

### Task 15: Refactor source-disable check sites in main.py + feeds/

**Plan amendment (2026-04-24):** Original plan targeted `analysis/market_matcher.py`, but `grep` showed that file has zero `DISABLED_NEWS_SOURCES` references. The actual runtime-path call sites are in `main.py` (the source filter at ingestion) and two feed modules. Static-set entries also have mixed casing (e.g., `r/Turkey` vs. `r/pakistan`, `GDELT` vs. `Foreign Policy`), so case-insensitive matching is load-bearing — the refactor preserves it in the new module-level helper.

**Files:**
- Modify: `utils/runtime_overrides.py` (add module-level helpers + `set_global_reader`)
- Modify: `main.py` (refactor `_is_disabled_news_source`)
- Modify: `feeds/subreddit_selector.py` (refactor `_is_disabled_reddit_source`)
- Modify: `feeds/gdelt_monitor.py` (refactor inline GDELT check)
- Modify: `tests/test_runtime_overrides_reader.py` (extend with module-level helper tests, OR create `tests/test_runtime_overrides_module_helpers.py`)

- [ ] **Step 1: Confirm the call sites**

Run: `grep -rn "DISABLED_NEWS_SOURCES" --include="*.py" main.py feeds/`

Expect to see (line numbers may drift):
- `main.py:76` — import
- `main.py:147` — `if source in DISABLED_NEWS_SOURCES:`
- `main.py:150` — `return any(key.strip().lower() == source_lower for key in DISABLED_NEWS_SOURCES)`
- `feeds/subreddit_selector.py:20` — import
- `feeds/subreddit_selector.py:43` and `:46` — same case-insensitive pattern
- `feeds/gdelt_monitor.py:30` — import
- `feeds/gdelt_monitor.py:135-136` — inline `"GDELT" in DISABLED_NEWS_SOURCES or any(...)`

The check at each site is case-insensitive: exact-match first, then lowercase iteration. The refactor replaces both branches with a single call to a new module-level helper.

- [ ] **Step 2: Add the module-level helpers + global reader registration**

Append to `utils/runtime_overrides.py`:

```python
_global_reader: RuntimeOverridesReader | None = None


def set_global_reader(reader: RuntimeOverridesReader | None) -> None:
    """Register (or clear) the singleton reader for module-level query helpers.

    Called once at bot startup. Tests may set/unset their own (always restore
    the previous value in `finally`). Pass None to clear.
    """
    global _global_reader
    _global_reader = reader


def _matches_case_insensitive(needle: str, haystack) -> bool:
    """True iff `needle` matches any element of `haystack` case-insensitively.

    Mirrors the existing main.py / feeds/ check pattern: exact-match first
    (a fast common case), then lowercase iteration as a fallback for
    inconsistent-casing entries in the static set.
    """
    if needle in haystack:
        return True
    needle_lower = needle.strip().lower()
    return any(item.strip().lower() == needle_lower for item in haystack)


def is_source_disabled(source: str) -> bool:
    """Module-level helper combining static config + runtime overrides.

    Returns True iff `source` is in static `DISABLED_NEWS_SOURCES` OR in the
    runtime-applied disabled set. Comparison is case-insensitive against
    both sets to preserve the existing main.py / feeds/ semantics.

    Falls back to static-only when no global reader is registered (Phase 1
    backward-compat: bot operates exactly as pre-Phase-1 if main.py has not
    yet wired the reader at startup).
    """
    if _matches_case_insensitive(source, _static_disabled_sources()):
        return True
    if _global_reader is None:
        return False
    runtime_sources = [o.source for o in _global_reader.snapshot().applied_disabled_sources]
    return _matches_case_insensitive(source, runtime_sources)


def is_keyword_disabled(keyword: str) -> bool:
    """Module-level helper for keyword disabling.

    Phase 1 has no static counterpart; returns True only if the runtime
    reader has the keyword in `applied_disabled_keywords`. Case-sensitive
    by design (keywords are matched against text body where casing matters).
    """
    if _global_reader is None:
        return False
    return any(o.keyword == keyword for o in _global_reader.snapshot().applied_disabled_keywords)


def get_threshold_override(path: str):
    """Module-level threshold-override lookup.

    Returns the override value if any applied threshold matches `path`
    exactly, else None. Caller MUST treat None as 'no override' and fall
    back to the static config value -- never confuse None-as-override with
    no-override.
    """
    if _global_reader is None:
        return None
    for o in _global_reader.snapshot().applied_threshold_overrides:
        if o.path == path:
            return o.value
    return None
```

Note on case-sensitivity asymmetry: source matching is case-insensitive (mirrors the existing helpers in main.py/feeds/, where the static set has `r/Turkey` vs `r/pakistan` casing variation). Keyword matching is case-sensitive (keywords match against text body where capitalization is preserved). Threshold-override paths are case-sensitive (exact dotted-path match per the spec).

- [ ] **Step 3: Write tests for the new module-level helpers**

Create `tests/test_runtime_overrides_module_helpers.py`:

```python
"""Tests for the module-level helpers used by main.py / feeds/ to
consult the runtime overrides reader without holding a reader reference.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from utils.runtime_overrides import (
    DisabledKeyword,
    DisabledSource,
    OverridesState,
    PredictedEffect,
    RuntimeOverridesReader,
    ThresholdOverride,
    get_threshold_override,
    is_keyword_disabled,
    is_source_disabled,
    set_global_reader,
)
from utils import runtime_overrides as ro


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)


def _pe() -> PredictedEffect:
    return PredictedEffect(metric="m", baseline=0, predicted_post_change=0, evaluate_at=_NOW)


@pytest.fixture(autouse=True)
def _reset_global_reader():
    """Always restore the global reader to whatever it was (likely None)
    before/after each test, so tests do not leak state."""
    original = ro._global_reader
    yield
    ro._global_reader = original


class FakeReader:
    """Stub reader with a fixed snapshot for tests that don't need disk."""

    def __init__(self, state: OverridesState):
        self._state = state

    def snapshot(self) -> OverridesState:
        return self._state


def _make_state_with_source(source_name: str) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_disabled_sources=[
            DisabledSource(
                source=source_name, reason="test", confidence=0.9,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0001",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


def _make_state_with_keyword(keyword: str) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_disabled_keywords=[
            DisabledKeyword(
                keyword=keyword, reason="test", confidence=0.8,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0002",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


def _make_state_with_threshold(path: str, value) -> OverridesState:
    return OverridesState(
        version=1, updated_at=_NOW, updated_by="test", mode="real",
        applied_threshold_overrides=[
            ThresholdOverride(
                path=path, value=value, reason="test", confidence=0.7,
                decided_at=_NOW, decided_by="test",
                decision_id="gd_2026-05-02_0003",
                expires_at=None, predicted_effect=_pe(),
            )
        ],
    )


class TestIsSourceDisabled:
    def test_no_global_reader_falls_back_to_static_only(self, monkeypatch):
        """Backward-compat: if main.py has not yet called set_global_reader,
        the helper still consults the static DISABLED_NEWS_SOURCES set and
        returns the same result as the pre-Phase-1 main.py helper."""
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"static_only"}))
        ro._global_reader = None
        assert is_source_disabled("static_only") is True
        assert is_source_disabled("not_disabled") is False

    def test_runtime_only(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/RuntimeOnly")))
        assert is_source_disabled("r/RuntimeOnly") is True
        assert is_source_disabled("r/Other") is False

    def test_static_and_runtime_union(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"r/StaticOnly"}))
        set_global_reader(FakeReader(_make_state_with_source("r/RuntimeOnly")))
        assert is_source_disabled("r/StaticOnly") is True
        assert is_source_disabled("r/RuntimeOnly") is True
        assert is_source_disabled("r/Neither") is False

    def test_case_insensitive_match_against_static(self, monkeypatch):
        """Mirrors the existing main.py case-insensitive behavior. Static set
        contains 'r/Turkey' (per config.py); 'r/turkey' must also match."""
        monkeypatch.setattr(ro, "_static_disabled_sources",
                            lambda: frozenset({"r/Turkey"}))
        ro._global_reader = None
        assert is_source_disabled("r/Turkey") is True
        assert is_source_disabled("r/turkey") is True
        assert is_source_disabled("R/TURKEY") is True

    def test_case_insensitive_match_against_runtime(self, monkeypatch):
        """Same case-insensitive policy applies to runtime-disabled sources."""
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/Turkey")))
        assert is_source_disabled("r/Turkey") is True
        assert is_source_disabled("r/turkey") is True


class TestIsKeywordDisabled:
    def test_no_global_reader_returns_false(self):
        ro._global_reader = None
        assert is_keyword_disabled("anything") is False

    def test_runtime_match(self, monkeypatch):
        set_global_reader(FakeReader(_make_state_with_keyword("trump may deadline")))
        assert is_keyword_disabled("trump may deadline") is True
        assert is_keyword_disabled("not in list") is False

    def test_keyword_match_case_sensitive(self, monkeypatch):
        """Keywords match against text body where capitalization is preserved.
        Spec preserves case-sensitivity for keywords."""
        set_global_reader(FakeReader(_make_state_with_keyword("ceasefire")))
        assert is_keyword_disabled("ceasefire") is True
        assert is_keyword_disabled("Ceasefire") is False  # different casing -> not disabled


class TestGetThresholdOverride:
    def test_no_global_reader_returns_none(self):
        ro._global_reader = None
        assert get_threshold_override("any.path") is None

    def test_returns_value_when_path_matches(self, monkeypatch):
        set_global_reader(FakeReader(
            _make_state_with_threshold("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA", 21600)
        ))
        assert get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA") == 21600
        assert get_threshold_override("EARLY_MAX_NEWS_AGE_BY_SOURCE.OtherSrc") is None


class TestSetGlobalReader:
    def test_set_and_clear(self, monkeypatch):
        monkeypatch.setattr(ro, "_static_disabled_sources", lambda: frozenset())
        set_global_reader(FakeReader(_make_state_with_source("r/X")))
        assert is_source_disabled("r/X") is True
        set_global_reader(None)
        assert is_source_disabled("r/X") is False
```

Note: `RuntimeOverridesReader` is in the imports but not actually used (the tests use `FakeReader`). Drop the import if ruff F401 complains.

- [ ] **Step 4: Refactor the three call sites**

#### main.py (lines 146-150)

Find:

```python
def _is_disabled_news_source(source: str) -> bool:
    if source in DISABLED_NEWS_SOURCES:
        return True
    source_lower = source.strip().lower()
    return any(key.strip().lower() == source_lower for key in DISABLED_NEWS_SOURCES)
```

Replace with:

```python
def _is_disabled_news_source(source: str) -> bool:
    """Defer to the runtime-overrides module-level helper, which combines
    static DISABLED_NEWS_SOURCES with any runtime-applied disabled set.
    Behavior with no runtime reader registered: identical to pre-Phase-1.
    """
    return is_source_disabled(source)
```

Add to `main.py` imports near the top (e.g., next to other `utils.` imports):

```python
from utils.runtime_overrides import is_source_disabled
```

Note: `DISABLED_NEWS_SOURCES` is still imported in `main.py:76` and may still be used elsewhere in `main.py` for diagnostics; check. If only used in `_is_disabled_news_source` after the refactor, remove from imports too.

#### feeds/subreddit_selector.py (lines 41-46)

Find:

```python
def _is_disabled_reddit_source(subreddit: str) -> bool:
    source = f"r/{subreddit}"
    if source in DISABLED_NEWS_SOURCES:
        return True
    source_lower = source.lower()
    return any(key.strip().lower() == source_lower for key in DISABLED_NEWS_SOURCES)
```

Replace with:

```python
def _is_disabled_reddit_source(subreddit: str) -> bool:
    """Defer to the runtime-overrides module-level helper. Same behavior
    as before (case-insensitive UNION of static + runtime sets) when the
    runtime reader is registered; behaves identically to pre-Phase-1
    when no reader is registered.
    """
    return is_source_disabled(f"r/{subreddit}")
```

Add to imports:

```python
from utils.runtime_overrides import is_source_disabled
```

Remove `DISABLED_NEWS_SOURCES` from the imports if no longer referenced in the file.

#### feeds/gdelt_monitor.py (lines 135-136)

Find:

```python
gdelt_disabled = "GDELT" in DISABLED_NEWS_SOURCES or any(
    key.strip().lower() == "gdelt" for key in DISABLED_NEWS_SOURCES
)
```

Replace with:

```python
gdelt_disabled = is_source_disabled("GDELT")
```

Add the import; remove `DISABLED_NEWS_SOURCES` from the imports if unused after.

- [ ] **Step 5: Run tests**

```
pytest --tb=short 2>&1 | tail -3
```
Expected: 1185+ passed (1180 + new module-helper tests).

```
pytest tests/test_runtime_overrides_module_helpers.py -v
```
Expected: all new tests pass.

```
ruff check utils/runtime_overrides.py main.py feeds/subreddit_selector.py feeds/gdelt_monitor.py tests/test_runtime_overrides_module_helpers.py
```
Expected: clean.

- [ ] **Step 6: Commit**

```
git add utils/runtime_overrides.py main.py feeds/subreddit_selector.py feeds/gdelt_monitor.py tests/test_runtime_overrides_module_helpers.py
git commit -m "refactor(sources): route source-disable checks through runtime_overrides helpers (Phase 1, task 15)"
```

---

### Task 16: Refactor keyword-disable handling in `analysis/signal_analyzer.py`

**Plan amendment (2026-04-24):** Original title said "market_matcher" but the body correctly targeted `signal_analyzer.py`. Also, the original test incorrectly unpacked `_keyword_score` as a 2-tuple — the actual signature returns a 3-tuple `(net_shift, dominant, matched)`. And the original plan only mentioned refactoring `_keyword_score`; in fact there are three iteration sites in `signal_analyzer.py` that each iterate `sig_def["keywords"]` and need the `is_keyword_disabled` skip:

- `_count_matched_signal_groups` (line ~215) — counts groups with at least one keyword hit (used by the `all_required` override mode)
- `_keyword_score` (line ~303) — the main scoring function
- `_keyword_contributions` (line ~334) — observability-only contribution details

All three must skip runtime-disabled keywords consistently so the bot's scoring, override-gate, and diagnostics agree on what's disabled.

**Files:**
- Modify: `analysis/signal_analyzer.py`
- Modify: `tests/test_signal_analyzer.py`

- [ ] **Step 1: Confirm the iteration sites**

Run: `grep -n "sig_def\[\"keywords\"\]\|for kw in" analysis/signal_analyzer.py`

Expect three iteration sites in three functions. If you see more or fewer, STOP and report.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_signal_analyzer.py`:

```python
class TestRuntimeKeywordDisable:
    """Runtime-disabled keywords must not contribute to the keyword
    score even though they remain in GEOPOLITICAL_SIGNALS.
    """

    def test_runtime_disabled_keyword_skipped_in_keyword_score(self, monkeypatch):
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        # "ceasefire" is a real keyword in GEOPOLITICAL_SIGNALS (config.py).
        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0099", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _keyword_score
        # Signature: (net_shift, dominant, matched_keywords)
        _shift, _direction, matched_keywords = _keyword_score(
            "Israel announces ceasefire today"
        )
        assert "ceasefire" not in matched_keywords

    def test_runtime_disabled_keyword_skipped_in_count_matched_signal_groups(
        self, monkeypatch
    ):
        """The all_required override mode counts how many signal groups have
        at least one keyword hit. A runtime-disabled keyword must NOT
        contribute a hit to its group -- otherwise the override mode would
        treat a disabled keyword as still evidence.
        """
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0100", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _count_matched_signal_groups
        # A headline whose only hit is the disabled keyword should not
        # register a matched group.
        groups_before = _count_matched_signal_groups("benign text with no signals")
        groups_with_only_disabled = _count_matched_signal_groups(
            "ceasefire announced"  # only the disabled keyword matches
        )
        # The ceasefire-only headline should register zero MORE groups than
        # the benign one (since ceasefire is disabled).
        assert groups_with_only_disabled == groups_before

    def test_runtime_disabled_keyword_skipped_in_contributions(self, monkeypatch):
        """Observability path (_keyword_contributions) must also hide
        disabled keywords -- otherwise the diagnostic lies about what
        the scorer actually used.
        """
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            DisabledKeyword, OverridesState, PredictedEffect,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_disabled_keywords=[
                DisabledKeyword(
                    keyword="ceasefire", reason="test", confidence=0.9,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0101", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        from analysis.signal_analyzer import _keyword_contributions
        contributions = _keyword_contributions("Israel announces ceasefire today")
        for contribution in contributions:
            assert contribution["keyword"] != "ceasefire", (
                f"disabled keyword leaked into contributions: {contribution}"
            )
```

- [ ] **Step 3: Add the `is_keyword_disabled` import to `signal_analyzer.py`**

At the top of `analysis/signal_analyzer.py`, near the existing `from config import cfg, GEOPOLITICAL_SIGNALS` import, add:

```python
from utils.runtime_overrides import is_keyword_disabled
```

- [ ] **Step 4: Refactor the three iteration sites**

#### Site 1: `_count_matched_signal_groups` (line ~215)

Find:

```python
    for sig_def in GEOPOLITICAL_SIGNALS:
        for kw in sig_def["keywords"]:
            if kw.lower() in text_lower:
                count += 1
                break
```

Replace with:

```python
    for sig_def in GEOPOLITICAL_SIGNALS:
        for kw in sig_def["keywords"]:
            if is_keyword_disabled(kw):
                continue
            if kw.lower() in text_lower:
                count += 1
                break
```

#### Site 2: `_keyword_score` (line ~303)

Find:

```python
    for sig_def in GEOPOLITICAL_SIGNALS:
        keywords  = sig_def["keywords"]
        direction = sig_def["direction"]
        strength  = sig_def["strength"]

        hits = [kw for kw in keywords if kw.lower() in text_lower]
```

Replace the `hits = [...]` line with:

```python
        hits = [
            kw for kw in keywords
            if not is_keyword_disabled(kw) and kw.lower() in text_lower
        ]
```

#### Site 3: `_keyword_contributions` (line ~334)

Apply the SAME replacement as Site 2 (the code uses the same `hits = [...]` comprehension).

- [ ] **Step 5: Run tests**

```
pytest tests/test_signal_analyzer.py -v --tb=short 2>&1 | tail -15
```
Expected: all existing tests still pass AND the three new tests pass.

```
pytest --tb=short 2>&1 | tail -3
```
Expected: 1195+ passed (1192 baseline + 3 new) + 1 skipped.

```
ruff check analysis/signal_analyzer.py tests/test_signal_analyzer.py
```
Expected: clean.

- [ ] **Step 6: Commit**

```
git add analysis/signal_analyzer.py tests/test_signal_analyzer.py
git commit -m "refactor(signal-analyzer): skip runtime-disabled keywords in all 3 iteration sites (Phase 1, task 16)"
```

---

### Task 17: Threshold-override consumer for `EARLY_MAX_NEWS_AGE_BY_SOURCE`

**Files:**
- Modify: `main.py` (or wherever `EARLY_MAX_NEWS_AGE_BY_SOURCE` is consulted — find via grep)
- Add tests for the consumer

- [ ] **Step 1: Locate `EARLY_MAX_NEWS_AGE_BY_SOURCE` consumers**

Run: `grep -rn "EARLY_MAX_NEWS_AGE_BY_SOURCE" /Users/Jake/vscode/kalshi_bot --include='*.py'`

Identify the function(s) that look up per-source freshness thresholds.

- [ ] **Step 2: Write failing test**

Pick the test file that tests the freshness lookup (likely `tests/test_main_pipeline.py` or `tests/test_signal_analyzer.py`). Append:

```python
class TestRuntimeThresholdOverride:
    def test_runtime_threshold_overrides_static_value(self, monkeypatch):
        from utils import runtime_overrides as ro
        from utils.runtime_overrides import (
            OverridesState, PredictedEffect, ThresholdOverride,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)

        fake_state = OverridesState(
            version=1, updated_at=now, updated_by="test", mode="real",
            applied_threshold_overrides=[
                ThresholdOverride(
                    path="EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA",
                    value=21600,
                    reason="test", confidence=0.7,
                    decided_at=now, decided_by="test",
                    decision_id="gd_2026-05-02_0044", expires_at=None,
                    predicted_effect=PredictedEffect(
                        metric="m", baseline=0, predicted_post_change=0, evaluate_at=now,
                    ),
                )
            ],
        )

        class FakeReader:
            def snapshot(self):
                return fake_state

        monkeypatch.setattr(ro, "_global_reader", FakeReader())

        # Function under test: whatever returns the per-source freshness
        # threshold. Adjust import to match the codebase.
        from main import _early_max_news_age_for_source
        assert _early_max_news_age_for_source("IAEA") == 21600
        # A source without an override falls through to the static value
        # (whatever that is in EARLY_MAX_NEWS_AGE_BY_SOURCE; if not present,
        # to EARLY_MAX_NEWS_AGE_SECONDS).
        from config import EARLY_MAX_NEWS_AGE_SECONDS
        assert _early_max_news_age_for_source("UnknownSrc") == EARLY_MAX_NEWS_AGE_SECONDS
```

- [ ] **Step 3: Refactor the lookup function**

Locate the per-source freshness lookup (likely in `main.py` near line 137). Refactor:

```python
from utils.runtime_overrides import get_threshold_override

def _early_max_news_age_for_source(source: str) -> int:
    """Return the freshness threshold for a source, with runtime overrides
    taking precedence over the static EARLY_MAX_NEWS_AGE_BY_SOURCE map."""
    runtime = get_threshold_override(f"EARLY_MAX_NEWS_AGE_BY_SOURCE.{source}")
    if runtime is not None:
        return int(runtime)
    if source in EARLY_MAX_NEWS_AGE_BY_SOURCE:
        return EARLY_MAX_NEWS_AGE_BY_SOURCE[source]
    for key, value in EARLY_MAX_NEWS_AGE_BY_SOURCE.items():
        if key.lower() == source.lower():
            return value
    return EARLY_MAX_NEWS_AGE_SECONDS
```

(If a function with this purpose already exists, modify it; do not create a duplicate.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_main_pipeline.py tests/test_signal_analyzer.py --tb=short 2>&1 | tail -10`
Expected: All passing; new threshold-override test passes.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_pipeline.py
git commit -m "refactor(main): consult runtime threshold_overrides for per-source freshness (Phase 1, task 17)"
```

---

### Task 18: Wire `RuntimeOverridesReader` into `main.py` startup

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Identify the bot startup site**

The bot constructs its services in `TradingBot.__init__` and starts asyncio tasks in `run()` or equivalent. Locate both. The reader needs to be:
1. Constructed during `__init__`.
2. Reload-once on startup so the initial state is loaded synchronously.
3. Registered as the module-level singleton via `set_global_reader`.
4. Polled via `run_runtime_overrides_poll` as a background task.

- [ ] **Step 2: Add reader construction and registration**

In `main.py`, near where other services are constructed (e.g., `self.paper = PaperTrader(...)`), add:

```python
from pathlib import Path
from utils.runtime_overrides import (
    RuntimeOverridesReader,
    set_global_reader,
)
from tasks.runtime_overrides_task import run_runtime_overrides_poll

# Construct early so subsequent code that reads overrides sees them.
_overrides_path = Path("data/runtime_overrides.yaml")
self._runtime_overrides_reader = RuntimeOverridesReader(path=_overrides_path)
self._runtime_overrides_reader.reload()  # synchronous initial load
set_global_reader(self._runtime_overrides_reader)
log.info(
    "runtime_overrides reader initialized: %s",
    self._runtime_overrides_reader.snapshot(),
)
```

- [ ] **Step 3: Add poll task to the async task group**

In whichever method creates the bot's asyncio tasks (likely `run()` or `_start_tasks()`), add:

```python
asyncio.create_task(
    run_runtime_overrides_poll(self._runtime_overrides_reader, interval_secs=600),
    name="runtime_overrides_poll",
)
```

- [ ] **Step 4: Manual smoke test**

Build a minimal smoke test (no automated test for full bot startup is in scope; existing `tests/test_main_startup.py` covers the existing paths and should still pass). Run:

```bash
python -c "from main import TradingBot; print('imports ok')"
```

Expected: no import errors.

Then run the existing test suite:

```bash
pytest --tb=short 2>&1 | tail -5
```

Expected: same test counts as the start of Phase 1, plus all the new Phase 1 tests, all passing.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): wire RuntimeOverridesReader into bot startup + poll task (Phase 1, task 18)"
```

---

### Task 19: CLI shim — `--status`

**Files:**
- Modify: `utils/runtime_overrides.py` (add `__main__` block)
- Create: `tests/test_runtime_overrides_cli.py`

- [ ] **Step 1: Write failing tests for `--status`**

Create `tests/test_runtime_overrides_cli.py`:

```python
"""Tests for the python -m utils.runtime_overrides CLI shim."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources:
        - source: "r/Test"
          reason: "x"
          confidence: 0.9
          decided_at: "2026-05-02T14:30:00+00:00"
          decided_by: "test"
          decision_id: "gd_2026-05-02_0099"
          expires_at: null
          predicted_effect:
            metric: "m"
            baseline: 0
            predicted_post_change: 0
            evaluate_at: "2026-05-09T14:30:00+00:00"
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


def _run_cli(args: list[str], path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "utils.runtime_overrides", *args],
        env={"OVERRIDES_PATH": str(path), "PYTHONPATH": str(Path(__file__).parent.parent)},
        capture_output=True, text=True, timeout=10,
    )


class TestCliStatus:
    def test_status_existing_file(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--status"], p)
        assert result.returncode == 0
        assert "r/Test" in result.stdout
        assert "shadow" in result.stdout

    def test_status_missing_file(self, tmp_path: Path):
        p = tmp_path / "missing.yaml"
        result = _run_cli(["--status"], p)
        assert result.returncode == 0
        assert "no overrides file" in result.stdout.lower() or "default" in result.stdout.lower()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_runtime_overrides_cli.py::TestCliStatus -v`
Expected: FAIL — CLI not yet implemented.

- [ ] **Step 3: Implement `__main__` block with `--status`**

Append to `utils/runtime_overrides.py`:

```python
def _cli_main() -> int:
    """python -m utils.runtime_overrides — emergency intervention CLI.

    Subcommands:
      --status              print current loaded state
      --validate <path>     validate a YAML file without applying
      --revert-batch <id>   drop all overrides applied by a given batch_id

    Path can be overridden via env var OVERRIDES_PATH (used in tests).
    """
    import argparse
    parser = argparse.ArgumentParser(prog="python -m utils.runtime_overrides")
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--status", action="store_true", help="print current loaded state")
    sub.add_argument("--validate", metavar="PATH", help="validate a YAML file, no live effect")
    sub.add_argument("--revert-batch", metavar="BATCH_ID", help="drop all overrides applied by a given batch_id")
    args = parser.parse_args()

    import os
    target = Path(os.environ.get("OVERRIDES_PATH", "data/runtime_overrides.yaml"))

    if args.status:
        if not target.exists():
            print(f"no overrides file at {target} -- bot uses static config defaults")
            return 0
        try:
            state = load_from_disk(target)
        except ValueError as exc:
            print(f"INVALID overrides file at {target}: {exc}")
            return 2
        print(f"path: {target}")
        print(f"version: {state.version}")
        print(f"mode: {state.mode}")
        print(f"updated_at: {state.updated_at.isoformat()}")
        print(f"updated_by: {state.updated_by}")
        print(f"applied disabled_sources ({len(state.applied_disabled_sources)}):")
        for o in state.applied_disabled_sources:
            ttl = "indefinite" if o.expires_at is None else f"expires {o.expires_at.isoformat()}"
            print(f"  - {o.source} ({ttl}) [{o.decision_id}]")
        print(f"applied disabled_keywords ({len(state.applied_disabled_keywords)}):")
        for o in state.applied_disabled_keywords:
            ttl = "indefinite" if o.expires_at is None else f"expires {o.expires_at.isoformat()}"
            print(f"  - {o.keyword!r} ({ttl}) [{o.decision_id}]")
        print(f"applied threshold_overrides ({len(state.applied_threshold_overrides)}):")
        for o in state.applied_threshold_overrides:
            print(f"  - {o.path} = {o.value} [{o.decision_id}]")
        return 0

    # Other subcommands implemented in subsequent tasks
    print("subcommand not yet implemented", file=__import__("sys").stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli_main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_runtime_overrides_cli.py::TestCliStatus -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_cli.py
git commit -m "feat(runtime-overrides): CLI --status subcommand (Phase 1, task 19)"
```

---

### Task 20: CLI shim — `--validate <path>`

**Files:**
- Modify: `utils/runtime_overrides.py`
- Modify: `tests/test_runtime_overrides_cli.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_runtime_overrides_cli.py`:

```python
class TestCliValidate:
    def test_validate_valid_file_returns_0(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--validate", str(p)], path=p)
        assert result.returncode == 0
        assert "valid" in result.stdout.lower()

    def test_validate_invalid_file_returns_2(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("not: valid: yaml: : :")
        result = _run_cli(["--validate", str(p)], path=p)
        assert result.returncode == 2
        assert "invalid" in result.stdout.lower() or "invalid" in result.stderr.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_runtime_overrides_cli.py::TestCliValidate -v`
Expected: FAIL — `--validate` not implemented.

- [ ] **Step 3: Implement `--validate` branch**

In `utils/runtime_overrides.py` `_cli_main()`, replace the "subcommand not yet implemented" stub with:

```python
    if args.validate:
        target = Path(args.validate)
        try:
            state = load_from_disk(target)
        except ValueError as exc:
            print(f"INVALID: {exc}")
            return 2
        n_total = (
            len(state.applied_disabled_sources)
            + len(state.applied_disabled_keywords)
            + len(state.applied_threshold_overrides)
        )
        print(f"valid: {target} ({n_total} applied overrides, mode={state.mode})")
        return 0

    if args.revert_batch:
        # Implemented in Task 21
        print("--revert-batch not yet implemented", file=__import__("sys").stderr)
        return 1

    print("no subcommand", file=__import__("sys").stderr)
    return 1
```

(Replace the existing stub-block, keep `--status` branch at the top.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_runtime_overrides_cli.py::TestCliValidate -v`
Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_cli.py
git commit -m "feat(runtime-overrides): CLI --validate subcommand (Phase 1, task 20)"
```

---

### Task 21: CLI shim — `--revert-batch <batch_id>`

**Files:**
- Modify: `utils/runtime_overrides.py`
- Modify: `tests/test_runtime_overrides_cli.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_runtime_overrides_cli.py`:

```python
class TestCliRevertBatch:
    def test_revert_drops_overrides_for_matching_batch_id(self, tmp_path: Path):
        # Build a state with two batches; revert one
        from utils.runtime_overrides import (
            DisabledSource, OverridesState, PredictedEffect, atomic_write_state,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
        eff = PredictedEffect(metric="m", baseline=0, predicted_post_change=0, evaluate_at=now)

        s1 = DisabledSource(
            source="r/A", reason="x", confidence=0.9, decided_at=now,
            decided_by="t", decision_id="gd_2026-05-02_0001",
            expires_at=None, predicted_effect=eff,
        )
        s2 = DisabledSource(
            source="r/B", reason="x", confidence=0.9, decided_at=now,
            decided_by="t", decision_id="gd_2026-05-02_0002",
            expires_at=None, predicted_effect=eff,
        )
        state = OverridesState(
            version=1, updated_at=now, updated_by="t", mode="real",
            applied_disabled_sources=[s1, s2],
            last_applied_batch={
                "batch_id": "gb_2026-05-02_0001",
                "decision_ids": ["gd_2026-05-02_0001"],
            },
        )
        p = tmp_path / "overrides.yaml"
        atomic_write_state(state, p)

        # Revert the batch -- s1 should drop, s2 stays
        result = _run_cli(["--revert-batch", "gb_2026-05-02_0001"], path=p)
        assert result.returncode == 0

        from utils.runtime_overrides import load_from_disk
        new_state = load_from_disk(p)
        assert {o.source for o in new_state.applied_disabled_sources} == {"r/B"}

    def test_revert_unknown_batch_returns_1(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--revert-batch", "gb_does_not_exist"], path=p)
        assert result.returncode == 1
        assert "no batch" in result.stdout.lower() or "not found" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runtime_overrides_cli.py::TestCliRevertBatch -v`
Expected: FAIL.

- [ ] **Step 3: Implement `--revert-batch`**

Replace the stub in `_cli_main()` with:

```python
    if args.revert_batch:
        if not target.exists():
            print(f"no overrides file at {target} -- nothing to revert")
            return 1
        try:
            state = load_from_disk(target)
        except ValueError as exc:
            print(f"INVALID overrides file: {exc}")
            return 2

        # last_applied_batch contains decision_ids that were applied in
        # this batch. Drop any applied entry whose decision_id is in that
        # list. If the batch_id doesn't match, return 1.
        batch_meta = state.last_applied_batch or {}
        if batch_meta.get("batch_id") != args.revert_batch:
            print(f"batch_id {args.revert_batch!r} not found in last_applied_batch")
            return 1
        decision_ids: set[str] = set(batch_meta.get("decision_ids") or [])

        new_state = OverridesState(
            version=state.version,
            updated_at=datetime.now(_timezone.utc),
            updated_by=f"cli-revert-{args.revert_batch}",
            mode=state.mode,
            applied_disabled_sources=[
                o for o in state.applied_disabled_sources if o.decision_id not in decision_ids
            ],
            applied_disabled_keywords=[
                o for o in state.applied_disabled_keywords if o.decision_id not in decision_ids
            ],
            applied_threshold_overrides=[
                o for o in state.applied_threshold_overrides if o.decision_id not in decision_ids
            ],
            proposed_disabled_sources=state.proposed_disabled_sources,
            proposed_disabled_keywords=state.proposed_disabled_keywords,
            proposed_threshold_overrides=state.proposed_threshold_overrides,
            last_applied_batch=None,  # cleared after revert
        )
        atomic_write_state(new_state, target)
        n_dropped = (
            len(state.applied_disabled_sources) - len(new_state.applied_disabled_sources)
            + len(state.applied_disabled_keywords) - len(new_state.applied_disabled_keywords)
            + len(state.applied_threshold_overrides) - len(new_state.applied_threshold_overrides)
        )
        print(f"reverted batch {args.revert_batch}: dropped {n_dropped} overrides")
        return 0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_runtime_overrides_cli.py -v`
Expected: All CLI tests PASS.

- [ ] **Step 5: Commit**

```bash
git add utils/runtime_overrides.py tests/test_runtime_overrides_cli.py
git commit -m "feat(runtime-overrides): CLI --revert-batch subcommand (Phase 1, task 21)"
```

---

### Task 22: Operator manual `docs/governance/README.md`

**Files:**
- Create: `docs/governance/README.md`

- [ ] **Step 1: Create the README**

Create `docs/governance/README.md`:

```markdown
# Governance Operator Manual

This manual documents the runtime-overrides plumbing built in Phase 1 of
the LLM governance agent project. Phase 1 ships *infrastructure*; the
agent itself comes in Phase 2+. Until then, this file is a guide for
**human-edited overrides**: how to disable a source or keyword on a
running bot without restarting it.

## What this is

`data/runtime_overrides.yaml` is a YAML file the bot reads every 10
minutes (configurable via `RUNTIME_OVERRIDES_POLL_SECS`). Anything in
its `applied` section overrides or augments the static config in
`config.py`.

The bot reads ONLY the `applied` section. The `proposed` section is a
human-review queue used by the future agent (Phase 2+) to write
shadow-mode decisions.

## Schema (full reference: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §6)

Top-level fields: `version` (int, must be 1), `updated_at` (ISO 8601),
`updated_by` (string), `mode` (`shadow` or `real`).

Within `applied`:
- `disabled_sources` — list of source-name overrides
- `disabled_keywords` — list of keyword overrides
- `threshold_overrides` — list of (path, value) tuples

Each entry has: `reason`, `confidence`, `decided_at`, `decided_by`,
`decision_id`, `expires_at` (or null), `predicted_effect` (mandatory).

For human-edited entries, use:
- `decided_by: "human-edit-by-jake"` (or your name)
- `decision_id: "gd_YYYY-MM-DD_HHMM"` (timestamp, doesn't need to match
  agent format precisely as long as it matches `gd_\d{4}-\d{2}-\d{2}_\d{4}`)

## How to disable a source manually

1. Open `data/runtime_overrides.yaml` (create if missing).
2. Add an entry to `applied.disabled_sources`:

```yaml
applied:
  disabled_sources:
    - source: "r/SomeSubreddit"
      reason: "stalling the pipeline; revisit after Phase 2"
      confidence: 1.0
      decided_at: "2026-04-24T22:30:00+00:00"
      decided_by: "human-edit-by-jake"
      decision_id: "gd_2026-04-24_2230"
      expires_at: null
      predicted_effect:
        metric: "manual_intervention"
        baseline: 0
        predicted_post_change: 0
        evaluate_at: "2026-05-01T22:30:00+00:00"
```

3. Save. Within 10 minutes, the bot's poll task will reload the file,
   log the diff to `bot.log`, and stop polling that source on the next
   cycle.

## How to verify

Run: `python -m utils.runtime_overrides --status`

This prints the currently-loaded state.

To validate a YAML file before saving (without affecting the live bot):

```
python -m utils.runtime_overrides --validate /path/to/edited.yaml
```

## Emergency intervention

### Kill switches

Two env vars halt the (future) governance agent:

- `GOVERNANCE_DISABLED=true` — agent exits cleanly, writes nothing.
- `GOVERNANCE_READONLY=true` — agent runs but does not write to the
  overrides file.

Set in your shell or in `.env` as needed. Bot's behavior is not
affected by these env vars (the bot just reads whatever's in the YAML
file).

### Reverting an agent batch

When the Phase 2+ agent writes a batch, it records the `batch_id` in
`last_applied_batch`. To roll back the entire batch:

```
python -m utils.runtime_overrides --revert-batch gb_YYYY-MM-DD_NNNN
```

This drops every override in that batch and clears the
`last_applied_batch` field. Effective on the next bot poll cycle.

### Manual edit during emergency

Editing `data/runtime_overrides.yaml` directly is fully supported. The
bot's reader treats human-written entries identically to agent-written
entries. The reader's atomic-rename semantics protect you from
half-written-file races even if you save while the bot is reading.

## Compatibility with observation windows

During any active P2.x or S4.5x observation window in `docs/ROADMAP.md`
that has a no-change-scope discipline, **do not** edit
`runtime_overrides.yaml`. The runtime overrides count as runtime
behavior changes for the purposes of those windows.

When governance Phase 2+ is operational, set `GOVERNANCE_DISABLED=true`
for the duration of the observation window so the agent doesn't
accidentally invalidate the measurement.
```

- [ ] **Step 2: Commit**

```bash
git add docs/governance/README.md
git commit -m "docs(governance): operator manual for Phase 1 runtime overrides (task 22)"
```

---

### Task 23: VERSION bump + CHANGELOG entry

**Files:**
- Modify: `VERSION`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump VERSION**

Read current VERSION, then write next patch level. Current is `0.29.51`. Update to `0.29.52`.

```bash
echo "0.29.52" > /Users/Jake/vscode/kalshi_bot/VERSION
```

- [ ] **Step 2: Add CHANGELOG entry**

In `CHANGELOG.md`, immediately after the `---` separator above `## [0.29.51]`, insert:

```markdown
---

## [0.29.52] - 2026-04-XX

### Added
- **Governance Phase 1: runtime overrides plumbing.** New module
  `utils/runtime_overrides.py` reads `data/runtime_overrides.yaml`
  every 10 minutes and exposes `is_source_disabled`,
  `is_keyword_disabled`, `get_threshold_override` query helpers.
  Existing static-config call sites in `analysis/market_matcher.py`,
  `analysis/signal_analyzer.py`, and `main.py` refactored to consult
  the runtime reader. New asyncio task `tasks/runtime_overrides_task.py`
  performs the periodic poll and hot-reload.
- New `governance/` package skeleton with `safety.py` (`SafetyConfig`
  + `KillSwitch`) and `audit.py` (`AuditLogger` JSONL writer with daily
  rotation matching `bot.log`). These are scaffolding for the agent
  itself which lands in Phase 2.
- New CLI shim: `python -m utils.runtime_overrides --status |
  --validate <path> | --revert-batch <batch_id>` for emergency
  intervention.
- New operator manual: `docs/governance/README.md`.

### Changed
- Bot processes that previously consulted `config.DISABLED_NEWS_SOURCES`
  directly now go through `utils.runtime_overrides.is_source_disabled`.
  Behavior is unchanged when no `data/runtime_overrides.yaml` file
  exists (graceful backward-compat).
- `EARLY_MAX_NEWS_AGE_BY_SOURCE` lookups in `main.py` now respect
  runtime threshold overrides.

### New dependencies
- Runtime: `pyyaml>=6.0`
- Dev: `hypothesis>=6,<7`

### Tests
Phase 1 adds ~XX unit tests, Hypothesis property test for the
effective-config invariant, atomic-write race tests, and CLI
subprocess tests. Total project test count increases from 1100 to
~11XX.

### Reference
Spec: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`
Phase 1 plan: `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md`
```

(Update the `XX` placeholders to actual numbers from the test suite output and today's date once committed.)

- [ ] **Step 3: Commit**

```bash
git add VERSION CHANGELOG.md
git commit -m "build: bump to 0.29.52; CHANGELOG entry for Phase 1 plumbing"
```

---

### Task 24: ROADMAP entry — add Governance Agent track

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Add a new top-level section to ROADMAP**

In `docs/ROADMAP.md`, after the "Versioning milestones" section and before any phase tables, add:

```markdown
## Governance Agent (Phase 1 in flight)

**Purpose:** Replace the operator's diagnostic→edit→commit→restart loop with
an LLM-driven process that decides what sources/keywords/thresholds to
add/remove/tune, with safety scaffolding ensuring the agent cannot do harm.

**Spec:** `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`
**Phase 1 plan:** `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md`

| ID | Task | Status | Owner | Notes |
|----|------|--------|-------|-------|
| GOV.P1 | Runtime overrides plumbing (read-only file format, hot-reload, safety primitives, CLI shim) | IN_PROGRESS | Claude | Phase 1 of 4. See plan. |
| GOV.P2 | Local-only governance agent in shadow mode | NOT_STARTED | Claude | Spec §8. Builds on P1. Requires Mac Studio (post-2026-04-29). |
| GOV.P3 | Real-mode flip + auto-revert | NOT_STARTED | Claude | Spec §9. Requires P2 + 14d shadow soak. |
| GOV.P4 | Tiered LLM (Claude API escalation) + weekly self-review | NOT_STARTED | Claude | Spec §10. Requires P3 + 2w real-mode soak. |
```

- [ ] **Step 2: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(roadmap): add Governance Agent track with Phase 1 in flight"
```

---

### Task 25: Final integration: full suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run full test suite**

Run: `pytest --tb=short 2>&1 | tail -10`
Expected: ~11XX passed, 1 skipped, 0 failed. (XX = however many new tests Phase 1 added.)

- [ ] **Step 2: Manual smoke test 1 — bot startup with no overrides file**

Ensure `data/runtime_overrides.yaml` does not exist:

```bash
ls /Users/Jake/vscode/kalshi_bot/data/runtime_overrides.yaml 2>&1 | grep -q "No such" && echo "OK: file absent" || echo "WARN: file present"
```

If absent, run the bot for 30 seconds (interrupt with Ctrl-C) and check `bot.log` shows the runtime_overrides reader initialized with the default empty state, no errors.

- [ ] **Step 3: Manual smoke test 2 — hand-edit overrides file**

Create `data/runtime_overrides.yaml` with one disabled source per the README example. Start the bot. Within ~10 minutes, `bot.log` should log:

```
runtime overrides reloaded: +sources=['r/SomeSubreddit']
```

Confirm by grepping the log: `grep "runtime overrides" /Users/Jake/vscode/kalshi_bot/logs/app/bot.log | tail -5`

- [ ] **Step 4: Run `--status` against the real file**

Run: `python -m utils.runtime_overrides --status`
Expected: prints the source you added, with TTL `indefinite`.

- [ ] **Step 5: Clean up the test override**

Delete or empty the `data/runtime_overrides.yaml` file. Bot's next poll cycle should log `-sources=['r/SomeSubreddit']` and resume polling that source.

- [ ] **Step 6: Final commit (no-op marker)**

If any cleanup needed (e.g., test fixture leftover), commit. Otherwise:

```bash
git status   # should be clean
```

---

### Task 26: Push branch + open PR

**Files:** none

- [ ] **Step 1: Push the feature branch**

```bash
git push -u origin feat/governance-phase-1-plumbing
```

- [ ] **Step 2: Create PR via gh CLI**

```bash
gh pr create --title "feat(governance): Phase 1 runtime overrides plumbing" --body "$(cat <<'EOF'
## Summary
- Phase 1 of the LLM governance agent project. Builds the runtime
  overrides plumbing — YAML file format, hot-reload poll task, safety
  primitives (SafetyConfig, KillSwitch, AuditLogger), and CLI shim for
  emergency intervention.
- All static-config call sites in `analysis/market_matcher.py`,
  `analysis/signal_analyzer.py`, and `main.py` refactored to consult
  the runtime reader.
- Phase 1 ships infrastructure only — the agent itself lands in
  Phase 2. With no overrides file present, behavior is identical to
  pre-Phase-1.

## Test plan
- [ ] Full suite green (~11XX passed, 1 skipped).
- [ ] Property test (Hypothesis) for effective-config invariant passes
      across 50+ random examples.
- [ ] Concurrent atomic-write race test passes (writer + 2 readers
      racing for 0.5s without errors).
- [ ] Manual smoke test 1: bot starts with no overrides file, behaves
      as before.
- [ ] Manual smoke test 2: hand-edit `data/runtime_overrides.yaml`
      with one disabled source, bot logs the reload diff within 10
      minutes and stops polling that source.
- [ ] Manual smoke test 3: `python -m utils.runtime_overrides --status`
      reports the loaded state correctly.
- [ ] Manual smoke test 4: `python -m utils.runtime_overrides
      --revert-batch <id>` clears overrides correctly.

## Reference
- Spec: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`
- Plan: `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Run ultrareview on the PR**

The user can run `/ultrareview <PR#>` from their session. Address any issues raised; failure-discipline rule (spec §11.5) applies — any test failures or critical findings get root-cause-fixed before merge.

- [ ] **Step 4: Merge to main once green**

After ultrareview clears and the user explicitly approves merge:

```bash
gh pr merge --squash --delete-branch
```

Or via the GitHub UI. Update `docs/ROADMAP.md` GOV.P1 status from IN_PROGRESS to COMPLETE in a follow-up commit.

---

## Self-review

- **Spec coverage:** Every Phase 1 requirement from spec §7 (`utils/runtime_overrides.py`, poll task, refactor sites, safety primitives, CLI shim, schema validation, atomic-write tests, TTL expiry, kill-switch, diff detection, backward-compat, property test, manual smoke) has at least one task that implements it. Operator manual covered by Task 22; ROADMAP entry by Task 24.
- **Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in" anywhere. All test code is concrete; all implementation code is concrete.
- **Type consistency:** `RuntimeOverridesReader`, `OverridesState`, `DisabledSource`, `DisabledKeyword`, `ThresholdOverride`, `PredictedEffect`, `StateDiff` used consistently across all tasks. Methods `is_source_disabled`, `is_keyword_disabled`, `get_threshold_override`, `snapshot`, `reload` referenced by their final names from Task 9 onward.
- **Frequent commits:** 26 tasks, each committing once. Average ~1 commit per task.
- **Bite-sized steps:** Each task has 4-6 steps; each step is one atomic action (write test, run, implement, run, commit).
- **DRY:** YAML test fixtures shared via constants in test files; helpers like `_utc()`, `_make_disabled_source`, `_run_cli` reused.
- **YAGNI:** No agent code (Phase 2), no LLM integration (Phase 2), no decision logic (Phase 2). Phase 1 stops where the spec says it stops.
- **TDD:** Every code-producing task is write-test → fail → implement → pass → commit.

---

## Out-of-scope reminders

These are NOT in Phase 1; they appear in later phase plans:

- The governance agent itself (`governance/agent.py`, `governance/decision.py`, `governance/evidence.py`, `governance/prompts.py`, `governance/llm.py`, `governance/adapter.py`)
- Local LLM invocation
- Claude API tiered escalation
- Shadow → real mode flip
- Auto-revert mechanism
- Self-review cycle
- Decision-quality fixtures
- Soak tests (14-day shadow, 1-week real, 2-week tiered)

These will be planned individually before each phase starts.
