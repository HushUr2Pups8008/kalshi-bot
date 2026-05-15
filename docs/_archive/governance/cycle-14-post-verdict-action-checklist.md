# Cycle-14 post-verdict action checklist

**Type:** consolidated skeleton for verification + post-verdict actions. Items 2/5/8/9/10 from Claude's Cycle-14 task list, merged so they fire as a single coordinated action when Codex's verdict lands.
**Drafted:** 2026-05-06 cycle 14 prep.
**Triggered by:** Codex's `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` numerical-findings draft landing.

## Pre-execution: Item 2 — Audit threshold lock verification (RUN BEFORE diagnostic fires)

Before Codex's calibration diagnostic runs, Claude verifies Codex's audit implementations match the locked thresholds from `docs/_archive/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md`. Specifically:

```
✓ movement_rate threshold uses |delta| > 0.01 (movement_floor=0.01)
✓ direction-correctness denominator EXCLUDES rows where new_estimate ∈ [0.49, 0.51]
✓ direction-correctness reports excluded_count separately from numerator/denominator
✓ Brier/log-loss output flagged with "n=24, do not use as primary verdict" caveat
✓ sized-bet subset (3 paper trades) reported separately from full corpus
✓ moved-vs-unmoved EV/P&L split reported
✓ synthetic Lane A and Lane B output direction-correctness AND magnitude
```

Verification mechanism: code review of `scripts/edge_replay/calibration_audit.py` (and sibling scripts) against this checklist BEFORE the diagnostic invocation. Findings written to `docs/governance/2026-05-06-cycle-14-pre-execution-threshold-verification.md` (LATER, when Codex's audit code lands).

**If a threshold drifts:** flag to Codex. Diagnostic does NOT run until thresholds match charter.

## Item 5 — Diagnosis-doc co-authoring (POST Codex's numerical findings)

Codex authors `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` with numerical findings per the locked schema. Claude appends:

1. **Verdict** in one of: `sign_error`, `extraction_broken`, `information_frontier`, `model_fine`, `sample_noise`, `redesign`. Verdict derives from Codex's findings against pre-stated criteria; no new criteria invented.

2. **Cycle-15 scope recommendation** matching the verdict-to-skeleton map in `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` §"Verdict-to-skeleton map." Identifies which skeleton (§A/§B/§C/§D/§E/§F) instantiates.

3. **Independent voice** on what the numbers mean. If Claude reads the data differently from Codex, both perspectives recorded. Operator picks.

4. **What's RULED OUT.** Per `2026-05-06-cycle-14-sign-error-candidate-trace.md`: which sign-error candidate sites are confirmed/eliminated by the synthetic Lane A/B results.

## Item 8 — ROADMAP refresh post-verdict

`docs/ROADMAP.md` Wave-2/3/Branch-D rows: change "HALTED AND POTENTIALLY OBSOLETE PENDING CYCLE-14 DIAGNOSIS" to one of:

| verdict | new ROADMAP wording |
|---|---|
| `sign_error` (fixable) | "HALTED PENDING CYCLE-15A SIGN-ERROR FIX + REPLAY VALIDATION" |
| `extraction_broken` | "HALTED PENDING CYCLE-15B EXTRACTION REBUILD + REPLAY VALIDATION" |
| `information_frontier` | "HALTED PENDING CYCLE-15C SOURCE-ONBOARDING + REPLAY VALIDATION" |
| `model_fine` | "HALTED INDEFINITELY (paper-only continuation; reassess at +30 trades milestone)" |
| `sample_noise` | "HALTED PENDING CYCLE-15E EVIDENCE-STORE EXTENSION + REPLAY VALIDATION" |
| `redesign` | "OBSOLETED PER CYCLE-14 VERDICT (strategic pivot to Cycle-15F)" |

Cycle-14 row itself: "DELIVERED <DATE>; verdict = <verdict>; Cycle-15<X> active."

Capital-posture row: stays "PAPER-ONLY" regardless of verdict (until Cycle-15X delivers replay-validated fix per IC §16).

## Item 9 — EDGE_STATUS dashboard refresh

`docs/EDGE_STATUS.md` updates in 3 places:

1. **TL;DR replay verdict line** updated to: `Cycle-14 verdict: <verdict>. Cycle-15<X> active per matching skeleton.`
2. **Wave deploy status table** rows updated per ROADMAP wording above.
3. **Replay verdict log** appended with cycle-14-diagnosis row including Brier point estimate, direction-correctness rate, synthetic Lane A/B pass/fail.

## Item 10 — Debt log: PROFIT-EDGE-007 closure + PROFIT-EDGE-008 file

`docs/profit_path_debt_log.md`:

**PROFIT-EDGE-007 status:**
- Status: COMPLETE
- Verdict (from Cycle-14 diagnosis-doc) recorded in §"Notes"
- Recommended Cycle-15 scope reference

**PROFIT-EDGE-008 (NEW):**
- Title: matches verdict — "Cycle-15<X> <scope>" (e.g., "Cycle-15A sign-error fix + replay validation")
- Category: Profit-Path Integrity / <fix-type-per-verdict>
- Severity: HIGH (gates Wave-2 deploy decision)
- Status: ACTIVE
- Priority: NOW (sign_error/extraction_broken/sample_noise verdicts) OR LATER (information_frontier/model_fine/redesign verdicts)
- Owner: Codex (implementation; verdict-conditional) OR Operator-decision-only (redesign verdict)
- Depends On: PROFIT-EDGE-007 (delivered)
- Blocks: All Wave-2/Wave-3 deploys per IC §16

Acceptance criteria match the matching Cycle-15<X> skeleton acceptance section.

## Cross-links

- `docs/_archive/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` — Cycle-14 charter
- `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` — Cycle-15 skeletons
- `docs/_archive/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md` — Site-by-site code trace
- `docs/_archive/governance/edge-replay-cycle14-diagnosis.md` — Cycle-14 diagnosis-doc (FUTURE)
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-15)
