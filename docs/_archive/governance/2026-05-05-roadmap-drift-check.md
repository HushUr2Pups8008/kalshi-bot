# ROADMAP.md drift check vs cycle-3 closure-path consensus

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `docs/ROADMAP.md` Stage 5 + Appendix A vs `edge-004-closure-path-tldr-v3.md` + cycle-3 sizing-scope specs.
**Audience:** operator considering ROADMAP refresh post-Wave-1.
**No edits applied.** Findings only.

## TL;DR

ROADMAP.md is **mostly accurate** — the Phase 0/1/2/3/4 structure stands. **3 MEDIUM drift findings** from cycle-3 specifically: P3-GATE definition needs cross-link to closure-path-TLDR; P4-GATE Appendix A needs cross-link to the new sizing-scope spec; "What changed" section is stale. **No HIGH findings; no MEDIUM-or-higher fixes blocking Wave-1 deploy.**

## Findings

### F1 (MEDIUM) — P3-GATE outcome lacks closure-path-TLDR cross-link

**Source:** `docs/ROADMAP.md:354` — P3-GATE outcome cell.

```
**P3-GATE outcome:**
- PASS (≥ 1 non-zero edge in paper mode, trailing 14 days) → Phase 4 authorized
- FAIL (zero non-zero edge) → escalation required; do not proceed to Phase 4
```

**Reality:** the closure-path defined in `edge-004-closure-path-tldr-v3.md` provides a much more granular escalation path: A.1+ Branch A → C → option-A → Lever B → Lever C → Branch D (PROFIT-LLM-001 → P4-GATE Appendix A → DEFERRED-CEILING). The ROADMAP P3-GATE FAIL cell says "escalation required" but doesn't point at this concrete path.

**Recommended fix:** append a sentence:

> FAIL escalation path: per `docs/governance/edge-004-closure-path-tldr-v3.md` (Wave-2 A.1+ → Wave-3 Lever B/C → Branch D handoff to PROFIT-LLM-001 / P4-GATE Appendix A).

**Severity MEDIUM** because operator hitting P3-GATE FAIL would benefit from the explicit path; cycle-3 specs assume the operator knows this surface exists.

### F2 (MEDIUM) — P4-GATE Appendix A lacks sizing-scope cross-link

**Source:** `docs/ROADMAP.md` Appendix A (line 402+) — defines the post-OT&E News Source Options + market-mix integration scope.

**Reality:** the cycle-3 spec `2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` defines the bounded sizing surface (3 axes: market-scope filter / intake-path expansion / market-resolution cadence) for when Branch D fires. The ROADMAP Appendix A intro should cross-link to this sizing-scope spec.

**Recommended fix:** add a sentence to the Appendix A intro:

> Sizing-scope when Branch D fires is bounded per `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md` (3-axis surface).

**Severity MEDIUM** because operator following ROADMAP into P4-GATE Appendix A territory needs the bounded sizing surface to avoid open-ended re-architecture.

### F3 (MEDIUM) — "What Changed From Prior Assumptions" section is 2026-04-23-frozen

**Source:** `docs/ROADMAP.md:381-401` — 5-bullet "What Changed" section captures pre-soak strategic shifts.

**Reality:** the section omits cycle 1-3 strategic shifts:
- §8.5.1 early-close path (calendar floor 14 → 7 d empirically justified)
- §8.5.2 policy-equivalence carve-out (mid-soak hot-fixes admissible under bounded conditions)
- Wave-1/2/3 deploy sequencing (post-soak landing-order + observation plan)
- Branch D escalation handoff structure (not "more operational changes" — bounded sizing-scope specs)
- Lever E closed; Lever D nomenclature now distinguishes lever-menu §3 from closure-path Branch D

**Recommended fix:** append a bullet 6:

> 6. **PHASE2-001 soak-early-close path + Wave-1/2/3 deploy infrastructure landed 2026-05-05.** §8.5.1 early-close gates documented in `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`; Wave-1/2/3 deploy sequence in `docs/governance/edge-004-closure-path-tldr-v3.md`; Branch D escalation per `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` (ARCHIVED Stream G R35) with sizing-scope specs for PROFIT-LLM-001 and P4-GATE Appendix A.

**Severity MEDIUM** because the "What Changed" section is the operator-facing context-update; missing cycle-3 work makes it look like the project has been static for 12 days.

## Confirmed clean

- Phase 0 / 1 / 2 / 3 / 4 structure: unchanged + correct.
- P0-GATE / P1-GATE / P2-GATE definitions: unchanged + correct.
- Stage 5 Phase Dependencies graph: unchanged + correct.
- Appendix A Tier 1 / Tier 2 status (integrated 2026-04-26): unchanged + correct.
- Appendix B (Polymarket dual-venue, blocked on retail waitlist): unchanged + correct.
- Appendix C (post-Mac-Studio LLM/feedback backlog): unchanged + correct.
- INV-6 / INV-7 references throughout: correct usage; unchanged.
- P4.1 / P4.2 / P4.3 task descriptions: correct; not yet started (correct, blocked on PROFIT-PHASE2-001 close + Wave-1 stabilisation).

## Recommended single-commit fix

3 sentences added at F1 / F2 / F3 locations. **Estimated wall-clock: ~5 min.**

Lower priority than the Wave-1 deploy infrastructure; can defer to post-Wave-1-close commit window.

## Out of scope

- Stage 6+ structure (post-live-trading; not in current ROADMAP scope)
- Refactor of Stage 5 Phase shape (intentional pre-existing structure)
- Appendix A Tier 3 deferral status (correct unchanged)

## Cross-links

- `docs/ROADMAP.md` — under review
- `docs/governance/edge-004-closure-path-tldr-v3.md` — closure-path-TLDR
- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` (ARCHIVED Stream G R35)
- `docs/superpowers/specs/2026-05-05-profit-llm-001-pre-sizing-scope-design.md`
- `docs/superpowers/specs/2026-05-05-p4-gate-appendix-a-pre-sizing-scope-design.md`
