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

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

VALID_ACTIONS: frozenset[str] = frozenset(
    {"disable_source", "disable_keyword", "tune_threshold", "no_action"}
)
VALID_CADENCES: frozenset[str] = frozenset({"fast", "deep", "weekly_review"})

_DECISION_ID_RE = re.compile(r"^gd_\d{4}-\d{2}-\d{2}_\d{4}$")
_BATCH_ID_RE = re.compile(r"^gb_\d{4}-\d{2}-\d{2}_\d{4}$")


@dataclass(frozen=True)
class PredictedEffect:
    """Per-decision prediction for outcome tracking. Mandatory per spec §10
    and §4 decision 10. The agent's quality is measured against these
    predictions over time.

    Note: this is the agent-internal representation. A structurally-identical
    `PredictedEffect` exists in `utils.runtime_overrides` for the persisted
    runtime-overrides YAML schema. The two classes are intentionally distinct
    types: this one is what the LLM emits and what the agent reasons over;
    the runtime-overrides one is locked into the YAML schema with its own
    validators. The bridge between them lives in the `Decision.to_disabled_*`
    / `Decision.to_threshold_override` converters added in Task 4, which
    explicitly reconstruct a `utils.runtime_overrides.PredictedEffect` from
    this one's fields. Do not collapse the two classes — the decoupling is
    deliberate so each can evolve independently.
    """
    metric: str
    baseline: float
    predicted_post_change: float
    evaluate_at: datetime

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("PredictedEffect.metric must be non-empty")
        if self.evaluate_at.tzinfo is None:
            raise ValueError("PredictedEffect.evaluate_at must be tz-aware (use UTC)")


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
