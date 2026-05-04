# Adversarial review — Claude commits `681ceb9` / `9ce8315` / `cca3cea` / `83a9477` / `2bf3da1`

**Reviewer:** Codex
**Date:** 2026-05-04
**Scope:** independent review of the five latest Claude commits named in the 2026-05-04 task split

## Findings

No correctness findings.

## What was re-checked independently

1. **Per-source concentration claim (`cca3cea`).**
   Domain-normalized replay over `mac_archive/macbook_2026-05-01_import/logs/trades` reproduces the same load-bearing result as the token-based audit:
   - specialist_analyst totals: `251 MATCH_DIAGNOSTIC`, `21 OPPORTUNITY`, `3 PAPER_TRADE`
   - `vitallaw.com`: `21 MATCH_DIAGNOSTIC`, `3 OPPORTUNITY`, `3 PAPER_TRADE`
   - share of specialist-class PAPER_TRADE: `100%`
   - share of specialist-class OPPORTUNITY: `14.3%`

2. **B' orthogonality claim (`83a9477`).**
   Re-derivation against the archived `MATCH_SUPPRESSED` stream confirms the headline claim:
   - archived `MATCH_SUPPRESSED`: `489`
   - records that the spec-corrected B' predicate would also suppress: `0`
   - records that would flip to kept under B': `489`

   This is not drift. It is the expected interaction shape: the current pre-B' runtime gate and the planned B' gate target different surfaces.

3. **Unified forecast framing (`2bf3da1`).**
   The forecast already carries the right caveat: outcome 1 depends on a `vital_law`-niche path, not the geopolitics-sub-niche A.1+ candidate list. Independent re-derivation does not falsify that framing.

4. **Day-4 placeholder (`9ce8315`).**
   Accurate at commit time. It is now superseded by real 2026-05-04 records, but that is not a defect in the commit itself.

5. **Prior review doc (`681ceb9`).**
   No additional contradictions surfaced beyond the already-noted open sizing/spec-maintenance items.

## Residual risks

- The legal-niche A.1+1.5 path is still operationally constrained by live-source accessibility. On 2026-05-04, `VitalLaw` remained opaque, `Politico`'s assumed RSS endpoint returned `404`, and `Reuters Legal` returned `401`.
- The B' audit is safe only if deploy code follows the corrected spec form in `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md` section 5.1. A naive transplant of the existing pre-B' runtime predicate would be the wrong comparison.

## Cross-links

- `docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md`
- `docs/governance/2026-05-04-match001-bprime-spec-parity-verification.md`
- `docs/governance/2026-05-04-day-4-mid-soak-sanity-check.md`
- `docs/governance/2026-05-04-lever-a1-plus-1-5-legal-analyst-feed-sizing.md`
