# 2026-05-05 Gate-6 Manual-Review Tool Status

**Task:** run Claude's proposed `scripts/governance_decision_review.py` against the governance decisions and produce an aggregate reasonable-rate report.

**Status:** blocked. Tool absent in this checkout.

## Findings

`scripts/governance_decision_review.py` is not present, so Codex could not run the structured review flow or emit a defensible `>= 85 %` gate-6 aggregate.

The early-close attestation can still be filled manually, but that keeps the highest-friction part of §8.5.1 outside the repo and makes reviewer disagreement tracking harder.

## Required Artifact Before Gate 6 Closes

| Field | Requirement |
|---|---|
| Denominator | all `GOVERNANCE_DECISION` rows in scope |
| Numerator | rows marked reasonable |
| Rate | numerator / denominator, must be `>= 85 %` |
| Reviewer identity | operator / Claude / Codex split recorded |
| Disagreement set | decision IDs needing tie-break |
| Source file | `logs/governance/decisions.jsonl` commit or timestamp recorded |

## Next Action

Run this task after `scripts/governance_decision_review.py` lands, or commit a review ledger with the same fields.
