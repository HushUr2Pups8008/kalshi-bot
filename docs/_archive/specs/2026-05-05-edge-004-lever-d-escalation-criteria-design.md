# PROFIT-EDGE-004 Lever D — escalation criteria spec

**Status:** design (defines escalation criteria when A.1+ Branch A → C → option-A all stall).
**Authority:** Implementation Contract §11 — out-of-scope-of-EDGE-004 escalation requires explicit redesign discussion.
**Drafted:** 2026-05-05.
**Companion:** `docs/governance/edge-004-closure-path-tldr.md` v2.2; `2026-05-03-edge-004-lever-menu-design.md` §3 (where "Lever D" was originally pre-LLM gate re-enablement, since closed); `2026-05-05-wave-2-a1plus-branch-decision-table.md`.

## ⚠️ Nomenclature clarification

The string "Lever D" has two distinct historical uses:

| use | meaning | status |
|---|---|---|
| **Lever-menu §3 Lever D** | pre-LLM gate re-enablement (2026-05-03) | CLOSED — demoted to tertiary; volume-destructive; outside closure path |
| **Closure-path Branch D** (this spec) | escalation to PROFIT-LLM-001 / P4-GATE Appendix A | ACTIVE — what this spec defines |

This spec uses "Branch D" / "Lever-D-escalation" exclusively to mean the second interpretation. The lever-menu §3 Lever D is unaffected and stays closed.

## 1. Why this spec

The Wave-2 A.1+ branch decision table (`2026-05-05-wave-2-a1plus-branch-decision-table.md`) defines a 3-branch tree (A passive observe → C open-RSS legal-analyst → option-A specialist-geopolitics). All three branches deploy or run within EDGE-004's intake-side scope. The escalation Branch D — handing off to PROFIT-LLM-001 / P4-GATE Appendix A — is referenced by the closure-path TLDR but never specified.

This spec answers: **what triggers Branch D? What does Branch D actually mean operationally? What happens to the EDGE-004 entry on escalation?**

## 2. Triggers (any one fires Branch D)

Branch D fires when at least one of:

### 2.1 Two-branch stall

- Branch A passive-observe completed 14 d window with **0 legal-niche PAPER_TRADE**, AND
- Branch C open-RSS legal-analyst deploy completed 14 d window with **0 PAPER_TRADE from the new feed**.

This is the canonical "intake-side levers exhausted" signal. The geopolitics specialist (option-A) has historical 0/18 conversion and parallel deploy is operator-discretion; if option-A fires in parallel and produces 0 PAPER_TRADE, that strengthens the Branch-D case but is not required for trigger.

### 2.2 Negative realized P&L on Branch C admitted candidates

- Branch C deploy produced ≥ 1 PAPER_TRADE in 14 d, AND
- Aggregate realized P&L over those trades is **negative**.

This is the "new feed admits trades but they're losses" signal. Distinct from §2.1 — the feed produces signal but the signal is wrong-direction. EDGE-004 cannot close via a feed that loses money even if it admits trades.

### 2.3 Operator judgement override

The operator may declare Branch D at any time after Branch A has run ≥ 7 d without producing PAPER_TRADE, citing this spec §2.3. Reserved for cases where intake-side experimentation cost (deploy effort, attribution complexity) exceeds the marginal probability of closing EDGE-004 via remaining branches.

## 3. What Branch D actually means

Branch D is **handoff**, not a new lever. The EDGE-004 entry transitions from IN_PROGRESS → CLOSED-DEFERRED, with closure-deferred reason: "Intake-side levers (A.1+ Branch A / C / option-A) exhausted. Closure dependency now sits in PROFIT-LLM-001 (signal-analyzer LLM unification) or P4-GATE Appendix A (market-mix specificity), both ROADMAP-tracked and outside EDGE-004 scope."

### 3.1 PROFIT-LLM-001 path

PROFIT-LLM-001 is the signal-analyzer LLM unification debt entry (currently registered, not yet sized). It addresses the orthogonal question: **does the LLM produce more directional views with a different prompt template / model / context window?**

If Branch D fires under §2.1 or §2.2: the operator should size PROFIT-LLM-001 next. Codex audits the signal-analyzer LLM call shape (prompt template, model, context budget) against the post-Wave-1 OPPORTUNITY mix; if a different LLM configuration produces materially more directional views on the same headlines, PROFIT-LLM-001 lands as a separate Wave (Wave 4+).

### 3.2 P4-GATE Appendix A path

P4-GATE Appendix A (per `docs/ROADMAP.md` Stage 4 P4) integrates narrower-scope markets (specific political / sector / event-based markets) into the active polling set. The post-Wave-1 broad-scope geopolitical market mix produces 0.5-anchored LLM verdicts because the headlines aren't narrow enough to admit a directional read.

If Branch D fires AND PROFIT-LLM-001 sizing returns "current LLM is already producing the signal it can": P4-GATE Appendix A is the next lever. Same Wave-4+ territory.

### 3.3 Honest read

If Branch D fires and BOTH PROFIT-LLM-001 + P4-GATE Appendix A sizing return inadequate: the bot's edge-production pipeline is at its current architectural ceiling. EDGE-004 closes as **DEFERRED-CEILING**, with operator decision: live-trade at the current 1 % conversion rate (not viable for paid bot), pivot to a different market venue (out of scope for this codebase), or reset the strategic frame.

## 4. Operator-side procedure on Branch D fire

1. **Document the trigger.** Add an entry to `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry: which §2 trigger fired, with the relevant 14-day window data.
2. **Tag the window.** `git tag -a edge-004-branch-d-fired-${UTC_DATE}` for audit-trail.
3. **Update the closure-path TLDR.** Bump `docs/governance/edge-004-closure-path-tldr.md` to v3 with Branch D fired status; remove Branch A / C from active candidate list.
4. **Size PROFIT-LLM-001.** Operator + Codex audit signal-analyzer LLM call shape per §3.1.
5. **Size P4-GATE Appendix A IF PROFIT-LLM-001 inadequate.** Per §3.2.
6. **DO NOT roll back Wave-1 / Wave-2 deploys.** Branch D is escalation, not regression. The base-stack improvements (OBS-005 / MATCH-001 / OBS-003 / EXEC-002 / GOV-003 / Lever A.1) all stand on their own merit.

## 5. Anti-triggers (when NOT to fire Branch D)

- **Single 14-d window of 0 PAPER_TRADE on Branch A.** Window may be too narrow; operator decision is to deploy Branch C, not escalate.
- **Branch C admitted ≥ 1 PAPER_TRADE with positive realized P&L.** EDGE-004 closes via Branch C; Branch D not required.
- **Operator emotional fatigue** ≠ Branch D §2.3 trigger. The §2.3 operator-override requires explicit attestation that intake-side experimentation cost exceeds marginal closure probability — not just "tired of waiting."
- **Calibration drift on Branch A / C admitted candidates.** Calibration drift is `PROFIT-CAL-001` territory, separate consumer. Branch D fires only on PAPER_TRADE realized P&L, not on calibration metrics.

## 6. Acceptance criteria (this spec)

This spec is satisfied when:

1. The Wave-2 A.1+ branch decision table (`2026-05-05-wave-2-a1plus-branch-decision-table.md`) cross-references this spec at its "Branch D" mention.
2. The closure-path TLDR (`edge-004-closure-path-tldr.md`) v3 update mentions Branch D triggers per §2.
3. The lever-menu (`2026-05-03-edge-004-lever-menu-design.md`) is updated with the Lever D nomenclature clarification (§"⚠️ Nomenclature clarification" above) so future readers don't conflate the two uses.

## 7. Out of scope

- **PROFIT-LLM-001 design.** Separate spec; this doc only defines the trigger to size it.
- **P4-GATE Appendix A market-mix work.** ROADMAP-tracked; this doc only defines the trigger to size it.
- **Live-trade pivot.** Outside EDGE-004; outside this spec.

## 8. Cross-links

- `docs/governance/edge-004-closure-path-tldr.md` v2.2 (will become v3 post-Branch-D fire)
- `docs/_archive/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — defines branches A / C / option-A
- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` §3 — original (closed) Lever D + nomenclature history
- `docs/ROADMAP.md` Stage 4 P4-GATE Appendix A — alternate escalation path
- `docs/profit_path_debt_log.md` PROFIT-EDGE-004 entry — receives the Branch-D fire log
- `docs/profit_path_debt_log.md` PROFIT-LLM-001 entry — sized post-Branch-D
