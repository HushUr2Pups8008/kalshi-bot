# Gate-6 manual-review capacity — close-day resolution plan

**Type:** operator decision plan for close-day fire (2026-05-07/08).
**Drafted:** 2026-05-06 (cycle 11), in response to Codex's `manual_review_capacity_audit.py` Day-4 finding.
**Companion:** `PROFIT-PHASE2-001-early-close-attestation.md` (gate 6 row).
**Authority:** `PROFIT-PHASE2-001-early-close-criteria.md` §8.5.1 gate 6 + spec wording: "Manual review pass on **all** decisions for gate 6."

## TL;DR

Codex's mid-soak (Day-4) capacity audit FAILed at default 80/day budget: 286/383 = 0.747 reviewable, below 0.85. Close-day plan: **try Path 3 first (re-eval at close-time); fall back to Path 1 (operator-day burn)**. Path 2 (sample-based amendment) is **explicitly deferred to Phase-3 spec work** — see PROFIT-GOV-004 in `docs/profit_path_debt_log.md`.

## Context

Gate 6 of §8.5.1 requires "≥ 85 % reasonable on manual review." Spec body further mandates "Manual review pass on **all** decisions for gate 6" — exhaustive, not sample-based.

Mid-soak distribution (Day-4 audit):

| date | decisions | reviewable @ 80/day |
|---|---:|---:|
| 2026-05-02 | 46 | 46 |
| 2026-05-03 | 82 | 80 |
| 2026-05-04 | 109 | 80 |
| 2026-05-05 | 146 | 80 |

Total: 383; reviewable: 286; fraction: 0.747. **Below 0.85.**

Day-5 peak (146) is the dominant pressure on the per-day-cap math. By Day-7 close, total may be ~600+; reviewable cap = 80 × 7 = 560 = ~93 % if pattern moderates.

## Resolution sequence

### Path 3 (PRIMARY) — Re-eval at close-time

**Action at close-day fire:**

```bash
.venv/bin/python scripts/manual_review_capacity_audit.py --json
```

If `status == "pass"` (reviewable_fraction ≥ 0.85): **gate 6 cleared by capacity**; operator proceeds to actual review per existing `governance_decision_review.py --bulk-mode` flow. Sign attestation.

If `status == "fail"`: drop to Path 1.

**Why primary:**
- Empirical, cheap (single command).
- Day-4 punitive: peak Day-5 inflates per-day-cap denominator; close-day window resets daily caps for Days 6 + 7.
- Avoids both operator-time pre-commitment AND mid-soak spec amendment.

### Path 1 (FALLBACK) — Increase daily review budget

**Action at close-day if Path 3 fails:**

1. Compute the day with the highest decision count from the audit `per_day` field.
2. Set `--daily-budget` ≥ that peak:

```bash
.venv/bin/python scripts/manual_review_capacity_audit.py --json --daily-budget 200
```

3. If `status == "pass"`: operator commits to reviewing 200 (or whatever peak) decisions on the heaviest day, proceeds with bulk-review.
4. Sign attestation gate 6 at completion.

**Why fallback (not primary):**
- Operator-time burn (200+ decisions ≈ 1-2 hours of focused review).
- Honors spec literal exhaustive-review requirement without amendment risk.
- Cycle-9 manual review of 67 day-1-to-day-3 decisions (commit `9f8deef`) returned 100 % reasonable — establishes that bulk review is operationally tractable.

### Path 2 (DEFERRED) — Sample-based review amendment

**NOT invoked at close-day.** Reasons:

- Mid-soak amendment of a §8.5.1 gate is process violation: relaxing a safety gate during incident response sets the precedent that gates can be re-spec'd when inconvenient.
- Sample-based review is the better statistical primitive long-term (gate-6's "%reasonable" IS a population statistic), but implementing it correctly requires real statistical work (sample size determination, stratification across decision types, expected error rate). Quick mid-soak amendment without that work is fake rigor.
- The pattern of decisions (mostly `disable_source` against persistent low-engagement Reddit; few distinct targets per day) suggests sampling would work — but proving it requires Phase-3 design work, not close-day expedience.

**Filed for Phase-3 spec work:** `PROFIT-GOV-004` in `docs/profit_path_debt_log.md`.

## Per-path effort estimate

| path | effort at close-day | risk profile |
|---|---|---|
| 3 | 0 (just rerun) | LOW — empirical |
| 1 | 1-2 h operator review | LOW — honors spec literal |
| 2 | (deferred; 4-8 h spec authoring + sample-size analysis) | (Phase-3) |

## Decision authority

Path selection is the operator's call at close-day — this doc captures the recommended sequence + rationale only. Operator may override (e.g., choose Path 1 directly without trying Path 3) without consulting this doc.

## Cross-links

- `scripts/manual_review_capacity_audit.py` — capacity audit (Codex cycle-11)
- `scripts/governance_decision_review.py` — bulk-review tool (gate 6 mechanism)
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — §8.5.1 gates definition
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` — close-day attestation (gate 6 row)
- `docs/profit_path_debt_log.md` `PROFIT-GOV-004` — Phase-3 spec work (sample-based gate-6 review)
