# Lever D nomenclature cleanup audit

**Type:** read-only audit (Claude task per Implementation Contract §9 — review for ambiguity).
**Source:** `grep -rn "Lever D\|Branch D" docs/` against HEAD `753ec36`.
**Audience:** operator considering nomenclature-cleanup commit before Wave-2 deploy.
**No edits applied.** Findings only.

## TL;DR

**18 occurrences across 12 files.** All but one map cleanly to the correct interpretation. **0 HIGH drift, 1 MEDIUM (the one ambiguous reference is in `wave-2-a1plus-branch-decision-table.md` where "Branch D" labels the geopolitics-specialist branch — that's a *third* meaning, conflicting with both lever-menu §3 Lever D and closure-path-Branch-D escalation). 4 LOW (cosmetic clarification opportunities).**

Recommend a single follow-up Edit to `wave-2-a1plus-branch-decision-table.md` to disambiguate the third meaning.

## Three distinct uses of "Lever D" / "Branch D" in the codebase

Per `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` ⚠️ Nomenclature clarification, two interpretations were spec'd:

1. **Lever-menu §3 Lever D** = pre-LLM gate re-enablement (closed; volume-destructive)
2. **Closure-path-TLDR-v3 Branch D** = escalation handoff to PROFIT-LLM-001 / P4-GATE Appendix A (active)

This audit surfaces a **third**:

3. **Wave-2 A.1+ branch decision-table Branch D** = geopolitics specialist-analyst feed onboard (the "option-A" path renamed inadvertently)

## Per-file findings

| file | line | text | interpretation | verdict |
|---|---|---|---|---|
| `profit_path_debt_log.md:1386` | "2026-05-03 Lever D pre-LLM gate audit" | meaning #1 (lever-menu §3) | ✅ correct |
| `profit_path_debt_log.md:1443` | "Branch D (escalation): if Branch C also stalls..." | meaning #2 (closure-path-TLDR Branch D) | ✅ correct |
| `profit_path_debt_log.md:2159` | "Branch D triggers" | meaning #2 | ✅ correct |
| `profit_path_debt_log.md:2171` | "lever-menu-Lever-D (closed) from closure-path-Branch-D (active)" | nomenclature definition | ✅ correct (definitional) |
| `2026-05-03-edge004-lever-d-pre-llm-gate-audit.md:1` | filename + "Lever D Pre-LLM Gate Re-Enable Audit" | meaning #1 | ✅ correct |
| `2026-05-03-edge004-lever-d-pre-llm-gate-audit.md:26-32` | content discussing pre-LLM gate floors | meaning #1 | ✅ correct |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md:29` | column header "Branch D (geopolitics specialist)" | **meaning #3 (CONFLICTING)** | **MEDIUM finding F1** |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md:47` | "Branch D: recommended LAST, only if A + C both produce 0 PAPER_TRADE" | meaning #3 | F1 (same) |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md:69` | "Branch D: geopolitics specialist-analyst onboard" | meaning #3 | F1 (same) |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md:71` | "EDGE-004 closes via Branch D" | meaning #3 | F1 (same) |
| `2026-05-05-wave-2-a1plus-branch-decision-table.md:85-86` | "Branch D fall-back ordering / serial behind C" | meaning #3 | F1 (same) |
| `edge-004-closure-path-tldr.md:49` | "Branch D — escalation to PROFIT-LLM-001 / P4-GATE Appendix A" | meaning #2 | ✅ correct |
| `edge-004-closure-path-tldr-v3.md:5,11,12,35,46,60,73,83,90,92,105` | meaning #2 throughout | ✅ correct |
| `2026-05-05-doc-index-audit.md:23,96,124` | meaning #2 throughout | ✅ correct |
| `docs/_archive/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md:44` | "Lever D verdict: 'Only consider D if Levers A + B + E all succeed...'" | meaning #1 | ✅ correct (adversarial review of pre-2026-05-03 framing) |
| `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md:105,136` | "escalate to Branch D" | meaning #2 | ✅ correct |
| `docs/_archive/governance/2026-05-03-claude-latest-commits-adversarial-review.md:38` | "Lever D's pre-LLM gate retention curve" | meaning #1 | ✅ correct |
| `docs/_archive/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md:35` | "Lever D demotion" | meaning #1 | ✅ correct |
| `2026-05-03-post-soak-landing-order-design.md:17` | "Lever D demoted" | meaning #1 | ✅ correct |
| `2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md:27` | "Branch D (escalation) | PROFIT-LLM-001 / P4-GATE Appendix A" | meaning #2 | ✅ correct |
| `2026-05-03-edge-004-lever-menu-design.md:65,71,125` | meaning #1 (Lever D pre-LLM gate) | ✅ correct |

## Findings

### F1 (MEDIUM) — Wave-2 A.1+ branch decision-table uses "Branch D" for the geopolitics specialist option

**Source:** `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md`.
**Lines affected:** 29, 47, 69, 71, 85, 86 (~6 occurrences).
**Conflict shape:** the doc uses "Branch D" to label the third A.1+ feed-onboarding option (geopolitics specialist), in a tree whose other branches are "Branch A passive observe" and "Branch C open-RSS legal-analyst." This conflicts with closure-path-TLDR-v3 + Lever-D escalation spec, both of which use "Branch D" for the escalation-to-PROFIT-LLM-001-handoff path.

**Why this matters:** the Lever-D escalation spec (`2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2) defines Branch D as fired by triggers including "Branch A + Branch C 0 PAPER_TRADE" — which the wave-2 doc then internally references as a different "Branch D." If an operator on Day-14 reads both docs, "Branch D" means two different things on the same page.

**Recommended fix (single Edit):** rename the wave-2 doc's Branch D → "option-A" (matching the parent A.1+ spec's terminology). Changes 6 occurrences in one file. After the fix, "option-A" = geopolitics specialist; "Branch D" = escalation handoff (consistent across all docs).

**Sample edits:**

```
| dimension | Branch A (passive observe) | Branch C (open-RSS legal-analyst) | Branch D (geopolitics specialist) |
                                                    ↓
| dimension | Branch A (passive observe) | Branch C (open-RSS legal-analyst) | option-A (geopolitics specialist) |
```

```
**Branch D: recommended LAST...**
                ↓
**option-A: recommended LAST...**
```

Same pattern for the remaining 4 occurrences. Estimated edit time: 5 min.

### F2 (LOW) — Adversarial-review docs reference closed Lever D framing

**Sources:**
- `docs/_archive/governance/2026-05-03-claude-commits-cdbf6ef-f786246-adversarial-review.md:44` — references "Lever D verdict: 'Only consider D if Levers A + B + E all succeed...'"
- `docs/_archive/governance/2026-05-03-claude-latest-commits-adversarial-review.md:38` — references "Lever D's pre-LLM gate retention curve"
- `docs/_archive/governance/2026-05-03-claude-commits-c5cbc6f-90c26cf-adversarial-review.md:35` — references "Lever D demotion"

These reference meaning #1 correctly, but the adversarial-review doc context predates the closure-path-TLDR-v3 / Lever-D escalation spec. **No fix needed** — these are historical record-of-thinking docs; the meaning-#1 interpretation was correct at draft time. The doc-index-audit (`2026-05-05-doc-index-audit.md` §D) recommends archiving these post-close anyway.

### F3 (LOW) — `lever-menu-design.md:65,71,125` reference Lever D meaning #1

**Source:** `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` §3-D + §5 sequencing notes.

These are correct usage (meaning #1) AND the §5.2 nomenclature clarification (added in cycle 2) explicitly distinguishes the two interpretations. **No fix needed.**

### F4 (LOW) — `post-soak-landing-order-design.md:17` references "Lever D demoted"

Meaning #1 (correct). Header table item from 2026-05-03 (pre-cycle-2). No fix needed.

### F5 (LOW) — `profit_path_debt_log.md:1443` "Branch D (escalation)"

Meaning #2 (correct). Legacy entry from 2026-05-04 cycle, but the parenthetical clarifies it's escalation. No fix needed.

## Summary

**Recommended action: single Edit to `2026-05-05-wave-2-a1plus-branch-decision-table.md`** changing 6 occurrences of "Branch D" → "option-A" per F1. Closes the only real ambiguity. ~5 min wall-clock.

Other 17 occurrences are correct usage of meaning #1 or #2; no fixes needed.

## Out of scope

- Renaming the audit file `2026-05-03-edge004-lever-d-pre-llm-gate-audit.md` itself. Filename references meaning #1 correctly; rename would create dead cross-links.
- Editing the lever-menu spec parent §3-D — meaning #1 is the canonical interpretation there; §5.2 clarification stands.
- Adversarial-review docs (F2). Archiving handles them.

## Cross-links

- `docs/superpowers/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` ⚠️ Nomenclature clarification
- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` §5.2 — same clarification source
- `docs/governance/edge-004-closure-path-tldr-v3.md` — meaning #2 canonical doc
- `docs/governance/2026-05-05-wave-2-a1plus-branch-decision-table.md` — F1 fix target
