# Cycle-15B post-verdict action checklist (L9)

**Type:** consolidated post-verdict execution plan. Mirrors `cycle-14-post-verdict-action-checklist.md` pattern.
**Drafted:** 2026-05-06 cycle-14 verdict landing (filed pre-Codex C1 per locked sequencing).
**Triggered by:** Cycle-15B C10 replay output landing (`docs/governance/edge-replay-cycle15b-report.md`).

## TL;DR

Cycle-15B produces a verdict in one of 3 categories:

| verdict | trigger | Cycle-16 path |
|---|---|---|
| `extraction_fixed_with_positive_ev_slice` | Lane B post-fix ≥ 6/10 AND ≥ 1 IC §16 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` | Cycle-16 §A — Wave-2 candidate authoring |
| `extraction_fixed_but_information_frontier_holds` | Lane B post-fix ≥ 6/10 AND 0 IC §16 slices | Cycle-16 §B (source-onboarding) OR §C (redesign) per operator decision |
| `extraction_rebuild_failed` | Lane B post-fix < 6/10 | Cycle-15B-extension OR Cycle-16 §C redesign per operator decision |

Pre-staging this checklist prevents improvisation when verdict lands. Verdict is consumed via the table above; no new criteria invented.

## Pre-execution: Item L2 — sub-fix-criteria-lock verification (RUN BETWEEN C2 trace AND C6 selection)

Before Codex's C6 sub-fix selection runs, Claude verifies Codex's C2 zero-collapse-step identification matches the locked criterion from `docs/_archive/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` §"Zero-collapse-step identification" (ARCHIVED Stream G R15):

```
✓ step input has |signal_magnitude| > 0.05
✓ step output has |signal_magnitude| < 0.01
✓ if multiple steps each meet the criterion, FIRST step recorded (root-cause-tracing rule)
✓ if no single step zeros alone but cumulative effect collapses, flagged as SECOND-CLASS multi-step finding
✓ multi-step finding triggers operator scope-extension conversation BEFORE C7
```

Verification mechanism: review of `logs/edge_replay/cycle15b/zero_collapse_step.json` + `per_step_trace.json` against this checklist. Findings written to `docs/_archive/governance/2026-05-06-cycle-15b-pre-execution-criteria-verification.md` (ARCHIVED Stream G R15) (LATER, when C2 lands).

**If criterion drift:** flag to Codex. C6 sub-fix selection does NOT run until criteria match charter.

## Item L4 + L5 — Independent Lane B trace read + same-class pathology cross-check (POST C2-C5)

After Codex C2-C5 outputs land, Claude reads them WITHOUT consulting Codex's C6 proposal. Claude:

1. Independently identifies the zero-collapse step.
2. Cross-checks against PROFIT-GOV-001 (qwen3 thinking-consumed-by-JSON-grammar; fixed via `think=False`) and PROFIT-GOV-002 (rubber-stamp bias) pathology classes. If C3 LLM audit shows `magnitude="none"` on directional fixtures, audit whether root cause is the same LLM-layer pathology surface or a different signal-analyzer-prompt pathology.

Output: `docs/_archive/governance/2026-05-06-cycle-15b-claude-independent-trace-read.md` (ARCHIVED Stream G R15) (or §section in cycle-15B report).

**If Claude's identification differs from Codex's:** both perspectives recorded; operator picks before C6 commits to a sub-fix path.

## Item L6 — sub-fix verdict appendix (POST C6)

Codex authors `docs/governance/edge-replay-cycle15b-sub-fix-proposal.md` per task split. Claude appends:

1. **Verdict** in one of: `concur` / `disagree-with-alternate-proposal` / `multi-step-needed-operator-scope-extension`.
2. **Match against L4 independent read.** If sub-fix path matches Claude's L4 identification, concur. If not, disagree with rationale + alternate proposal.
3. **Match against locked acceptance criteria** (charter §"Sub-fix selection acceptance"). Single-step + named file:line + before/after pseudocode + cited C2-C5 evidence.
4. **What's RULED OUT.** Per `2026-05-06-cycle-14-sign-error-candidate-trace.md`: which of sites 2/3/6/7 are confirmed by C2-C5 evidence vs eliminated.

## Item L7 — re-ingestion atomicity review (POST C9, BEFORE C10 consumes)

Claude reviews Codex C9 re-ingestion pipeline for:
- Idempotence: re-run twice produces byte-identical `dossier_updates_post_fix.db`.
- Determinism: no wall-clock-dependent ordering.
- Atomicity: re-run failure does not corrupt mid-state. Reference CLAUDE.md "DB transaction atomicity in `resolve_market()`" gotcha.
- PRE_FIX preservation: pre-fix `dossier_updates` recoverable per `docs/_archive/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` (ARCHIVED Stream G R18).

**If atomicity review fails:** C10 replay run does NOT proceed until C9 re-shipped. C10 consuming a non-atomic re-ingestion produces non-reproducible results → IC §16 Rule 4 violation.

## Item L8 — paper_trades cohort note (FILED 2026-05-06; reference only at this point)

Already landed. `docs/_archive/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` (ARCHIVED Stream G R18) defines PRE_FIX / POST_FIX_REBUILT / POST_FIX_NEW cohorts. Cross-linked from IC §16 Rule 6.

## Items 5/8/9/10 — post-verdict refresh (POST C10)

Mirror cycle-14 post-verdict pattern.

### Item 5 — diagnosis-doc co-authoring

Codex authors `docs/governance/edge-replay-cycle15b-report.md` with C10 numerical findings. Claude appends:

1. **Verdict** in one of: `extraction_fixed_with_positive_ev_slice`, `extraction_fixed_but_information_frontier_holds`, `extraction_rebuild_failed`. Verdict derives from C10 output against locked charter criteria; no new criteria invented.
2. **Cycle-16 scope recommendation** per verdict-to-skeleton map in `cycle-16-conditional-charter-skeletons.md` (L10).
3. **Independent voice** on what the numbers mean. If Claude reads the data differently from Codex, both perspectives recorded. Operator picks.
4. **What's RULED OUT.** Which Cycle-15B-extension or Cycle-16 paths are eliminated by C8/C10 evidence.

### Item 8 — ROADMAP refresh

`docs/ROADMAP.md` Wave-2/3/Branch-D rows: change "HALTED PENDING CYCLE-15B EXTRACTION REBUILD + REPLAY VALIDATION" to one of:

| verdict | new ROADMAP wording |
|---|---|
| `extraction_fixed_with_positive_ev_slice` | "AUTHORIZED PER CYCLE-15B IC §16 ACCEPTANCE — Wave-2 candidate slice = <slice>; Wave-2 deploy gated on slice-specific replay validation" |
| `extraction_fixed_but_information_frontier_holds` | "HALTED PENDING CYCLE-16B SOURCE ONBOARDING + REPLAY VALIDATION" or "HALTED PENDING CYCLE-16C STRATEGIC REDESIGN DECISION" per operator pick |
| `extraction_rebuild_failed` | "HALTED PENDING CYCLE-15B-EXTENSION SECOND SUB-FIX ATTEMPT" or "HALTED PENDING CYCLE-16C REDESIGN DECISION" per operator pick |

Cycle-15B row itself: "DELIVERED <DATE>; verdict = <verdict>; Cycle-16<X> active."

Cycle-14 row stays "DELIVERED 2026-05-06; verdict = `extraction_broken`."

Capital-posture row: stays "PAPER-ONLY" UNLESS verdict = `extraction_fixed_with_positive_ev_slice` AND operator explicitly authorizes live-trading-enabled flip with Cycle-15B replay-report citation in the commit message. Even then, live-trading flip is a separate operator action, not automatic.

### Item 9 — EDGE_STATUS dashboard refresh

`docs/EDGE_STATUS.md` updates in 3 places:

1. **TL;DR replay verdict line** updated to: `Cycle-15B verdict: <verdict>. Cycle-16<X> active per matching skeleton.`
2. **Wave deploy status table** rows updated per ROADMAP wording above.
3. **Replay verdict log** appended with cycle-15B row including Lane B post-fix pass rate, IC §16 slice count, ev_ci_95_lo of best slice (if any).

### Item 10 — Debt log: PROFIT-EDGE-008 closure + PROFIT-EDGE-009 file

`docs/profit_path_debt_log.md`:

**PROFIT-EDGE-008 status:**
- Status: COMPLETE (regardless of verdict; the cycle ran).
- Verdict (from Cycle-15B C10) recorded in Notes.
- Recommended Cycle-16 scope reference.

**PROFIT-EDGE-009 (NEW):**
- Title matches verdict — e.g., "Cycle-16A Wave-2 candidate slice deploy + replay validation" (positive-EV verdict) OR "Cycle-16B source onboarding scope" (frontier-holds verdict) OR "Cycle-16C strategic redesign decision" (rebuild-failed or operator picks redesign).
- Category: matches verdict path.
- Severity: HIGH (gates Wave-2/Wave-3 deploy decision OR strategic-direction decision).
- Status: ACTIVE.
- Priority: NOW (positive-EV verdict; deploy-blocking) OR LATER (frontier-holds; multi-week scope) OR Operator-decision-only (redesign).
- Owner: Codex (implementation) OR Operator-decision-only (redesign verdict).
- Depends On: PROFIT-EDGE-008 (delivered).
- Blocks: All Wave-2/Wave-3 deploys per IC §16 (only the positive-EV verdict can unblock).

Acceptance criteria match the matching Cycle-16<X> skeleton acceptance section.

## Cross-links

- `docs/_archive/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` — Cycle-15B charter (ARCHIVED Stream G R15)
- `docs/_archive/governance/2026-05-06-cycle-15b-task-split.md` — Codex C1-C10 + Claude L1-L10 (ARCHIVED Stream G R15)
- `docs/governance/cycle-16-conditional-charter-skeletons.md` — Cycle-16 skeletons (verdict-to-skeleton map)
- `docs/_archive/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — L8 cohort definitions (ARCHIVED Stream G R18)
- `docs/governance/edge-replay-cycle15b-report.md` — Cycle-15B C10 report (FUTURE)
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-16 deploys)
