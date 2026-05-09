# Phase 4 Summary — docs/ Consolidation Design

**Phase:** 4 — Design (code-architect agent output)
**Date:** 2026-05-09
**Inputs synthesized:** Phase 2 output (`classification.md`), Phase 3 output (`tracking-sources-analysis.md`), Phase 4 Step 1 output (`one-doc-design.md`), Phase 4 Step 2 output (`consolidation-plan.md`)

---

## Bottom Line

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Total docs/ files | 275 | 268 | -7 (5 archived to `_archive/soak-status-history/`, 2 archived to `_archive/2026-05-09-docs-consolidation/`, 1 deleted via git rm) |
| Parallel tracking surfaces | 3 (`profit_path_debt_log.md` + `EDGE_STATUS.md` + 2 TLDR variants) | 1 (`profit_path_debt_log.md` only) | -3 |
| Active tracking files removed | 8 (1 deleted + 7 moved to archive) | 0 | -8 |
| `profit_path_debt_log.md` projected size | 437,313 bytes / 4,761 lines | ~450,763 bytes / ~4,905 lines | +13,450 bytes (§Current Status + preamble + R-10) |
| Bytes removed from active docs/ tracking surfaces | — | ~28,000 bytes net removed | EDGE_STATUS + soak placeholders + TLDRs |
| 600K ceiling headroom | — | ~149K remaining | Comfortable; no split required |

Files merged: 1 (EDGE_STATUS.md → §Current Status)
Files with key content folded then archived: 1 (edge-004-closure-path-tldr-v3.md → §Current Status §2.3)
Files archived without content absorption: 6 (5 soak placeholders + edge-004-closure-path-tldr.md)
Files KEEP (no mutation): 258 + 4 deferred = 262

---

## Phase 2/3/4 Synthesis

### What Phase 2 found
275 files. Dominant classifications: AUDIT_REPORT (107), DESIGN_SPEC (27), AUDIT_REVIEW (26), PLAN (24), TRACKING_STATUS (21). Only 1 TRACKING_DEBT file exists (`profit_path_debt_log.md`). 12 MIXED files flagged for review. 3 TRACKING_STATUS files classified as parallel tracking surfaces.

### What Phase 3 found
6 tracking clusters: A (open-debt), B (edge/signal status), C (wave deploy timeline), D (soak status), E (roadmap horizon), F (active-cycle decision-state). Two canonical tracking surfaces identified: `profit_path_debt_log.md` (clusters A/C/D/E/F) and `EDGE_STATUS.md` (cluster B). Duplicate-tracking pairs: EDGE_STATUS vs debt log §wave deploy, EDGE_STATUS vs debt log §replay verdict, both TLDR variants vs debt log §PROFIT-EDGE-011. Phase 3 verdict: consolidate cluster B into the debt log.

### What Phase 4 designed
- `one-doc-design.md`: 7-section post-consolidation structure for `profit_path_debt_log.md`. New `## Current Status` section absorbs all EDGE_STATUS content. Preamble restructured with canonical-surface declaration. R-10 (No New Tracking Files) appended to Operating Rules.
- `consolidation-plan.md`: Per-file action table (275 files). 3 active batches (B1/B2/B3) + 1 no-op batch (B4/KEEP) + 1 deferred batch (B5/wave-* + IC). 10 total files mutated.

---

## User Decisions Applied

| Decision | Applied | Effect |
|----------|---------|--------|
| Q1: EDGE_STATUS MERGE+DELETE | Yes | Entire file merged into §Current Status; `git rm` in B1 |
| Q2: wave-* prestaged KEEP until Wave-3 | Yes | 3 files moved to B5 (deferred); no Phase 5 action |
| Q3: IMPLEMENTATION_CONTRACT out of scope | Yes | File untouched; moved to B5 |
| Q4: 5 soak placeholder files ARCHIVE | Yes | All 5 move to `_archive/.../soak-status-history/` in B2 |
| Q5: fold v3 TLDR content, ARCHIVE both TLDRs | Yes | v3 key content folded in B1; both files archived in B3 |
| Q6: 12 MIXED files all KEEP | Yes | All 12 assigned B4; no mutation |

---

## Open Questions

None from Phases 2-4. All Q1-Q6 operator decisions are resolved. Phase 5 has no blocking ambiguities.

The following items are deferred by decision (not open questions):
- **Q2 wave-* files**: Will need a follow-up consolidation pass after Wave-3 deploys. Three files (`wave-1-changelog-entry-prestaged.md`, `wave-1-commit-messages-prestaged.md`, `wave-2-wave-3-changelog-entries-prestaged.md`) remain in `docs/governance/` until then.
- **Q3 IC §10**: Not a tracking issue; no consolidation needed.

---

## Recommended Phase 5 Batch Order

Execute in this order:

1. **B1** — Tracking merge + debt log restructure (longest, most complex; do first while context is fresh)
   - Risk: editing a 4,761-line file; use Edit tool with precise line ranges; validate section counts before/after
   - Dependencies: none; B3 must wait for B1 to finish the v3 content fold
2. **B2** — Soak placeholder archive (5 `git mv` operations; simple)
   - Risk: none; moves only; reversible
   - Can run in parallel with B1 if execution environment supports it
3. **B3** — TLDR archive (2 `git mv` operations)
   - Risk: none; moves only; reversible
   - Must wait for B1 (content must be in §2.3 before archiving the source files)
4. **B4** — KEEP (no-op; no execution needed)
5. **B5** — Deferred; do not execute in Phase 5

---

## Risk Callouts

1. **B1 is high-touch**: `profit_path_debt_log.md` is 4,761 lines. The restructure touches lines 1-7 (preamble), inserts ~130 lines at line 44 (§Current Status), eliminates lines 45-48 (separator), and appends at end (R-10). Four separate edit operations. Use line-number verification before each Edit call to confirm offsets haven't shifted from prior edits.

2. **Cross-link integrity after EDGE_STATUS deletion**: `docs/governance/README.md`, `ROADMAP.md`, and several governance files may cross-reference `docs/EDGE_STATUS.md`. Before committing B1, grep for remaining `EDGE_STATUS` references and update them to point to `docs/profit_path_debt_log.md#current-status`. The Phase 3 doc-xref audit (`2026-05-06-doc-xref-audit-triage.md`) provides a baseline; confirm no new references were added in commits since 2026-05-06.

3. **Archive directory creation**: `docs/_archive/2026-05-09-docs-consolidation/` and `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/` do not yet exist. Create with `mkdir -p` before B2/B3 `git mv` operations.

4. **B1 merge quality**: The EDGE_STATUS content spans 104 lines with 7 sections. The merge creates a new §Current Status with 6 subsections (§2.1-§2.6). The mapping in `one-doc-design.md §5 Step 2` must be followed exactly. Do not rewrite content during merge — transfer verbatim and restructure headings only.

5. **R-10 vs existing R-9 text**: R-9 is "Single Tracker Rule" — confirm its scope at time of Phase 5 execution. R-10 extends it specifically to prohibit new tracking files (R-9 may cover adjacent ground; make R-10 complementary, not contradictory). Inspect current R-9 text before appending.

---

Phase 4 design + plan complete. Review SUMMARY.md before approving Phase 5 execution.
