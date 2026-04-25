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
