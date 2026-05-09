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

    # PLANNED: Task 4 governance interface. Call site at agent.py:235 currently
    # commented out. See PROFIT-GOV-002 / Task 4 roadmap for activation criteria.
    def to_disabled_source(self):
        """Convert to a Phase-1 DisabledSource. action must be 'disable_source'."""
        from utils.runtime_overrides import DisabledSource, PredictedEffect as ROPredictedEffect
        if self.action != "disable_source":
            raise ValueError(
                f"to_disabled_source called on action={self.action!r}; expected disable_source"
            )
        if self.predicted_effect is None:
            raise ValueError(
                "predicted_effect must not be None for an action decision; "
                "this should have been caught by Decision.__post_init__"
            )
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

    # PLANNED: Task 4 governance interface. Call site at agent.py:235 currently
    # commented out. See PROFIT-GOV-002 / Task 4 roadmap for activation criteria.
    def to_disabled_keyword(self):
        """Convert to a Phase-1 DisabledKeyword. action must be 'disable_keyword'."""
        from utils.runtime_overrides import DisabledKeyword, PredictedEffect as ROPredictedEffect
        if self.action != "disable_keyword":
            raise ValueError(
                f"to_disabled_keyword called on action={self.action!r}; expected disable_keyword"
            )
        if self.predicted_effect is None:
            raise ValueError(
                "predicted_effect must not be None for an action decision; "
                "this should have been caught by Decision.__post_init__"
            )
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

    # PLANNED: Task 4 governance interface. Call site at agent.py:235 currently
    # commented out. See PROFIT-GOV-002 / Task 4 roadmap for activation criteria.
    def to_threshold_override(self):
        """Convert to a Phase-1 ThresholdOverride. action must be 'tune_threshold'.
        proposed_change.after holds the new value."""
        from utils.runtime_overrides import ThresholdOverride, PredictedEffect as ROPredictedEffect
        if self.action != "tune_threshold":
            raise ValueError(
                f"to_threshold_override called on action={self.action!r}; expected tune_threshold"
            )
        if self.predicted_effect is None:
            raise ValueError(
                "predicted_effect must not be None for an action decision; "
                "this should have been caught by Decision.__post_init__"
            )
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
