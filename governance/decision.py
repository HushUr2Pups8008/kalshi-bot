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
