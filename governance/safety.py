"""Safety primitives for the governance agent.

Used by the agent in Phase 2+ to enforce decision-level and batch-level
safety bounds. Built and tested standalone in Phase 1.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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
