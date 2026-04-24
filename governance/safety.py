"""Safety primitives for the governance agent.

Used by the agent in Phase 2+ to enforce decision-level and batch-level
safety bounds. Built and tested standalone in Phase 1.
"""

from __future__ import annotations

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
