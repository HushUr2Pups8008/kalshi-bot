# profit_path_debt_log.md TOC + navigation audit

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `docs/profit_path_debt_log.md` (3629 lines; HEAD `3a400c1`).
**Audience:** operator considering lightweight nav aids for the unified debt-log.
**No edits applied.** Findings only.

## TL;DR

Debt-log structure is **organic but navigable**. 28 PROFIT-* entries; load-bearing entries (EDGE-004, OBS-003, PHASE2-001, LLM-001) are 200+ lines each. **Recommend 2 lightweight nav aids:** a TOC at the top + a per-entry status table. **No structural rewrite needed.**

## Current structure

Section markers (lines):
- L9: `## Header / Metadata`
- L45: `## Full Technical Debt Log`
- L49: `## Current Open Profit-Path Items`
- L55-2015: 28 individual `### PROFIT-{ID}` entries
- L2127+: cycle integration entries appended within PROFIT-PHASE2-001 entry

## Findings

### F1 (MEDIUM) — No TOC; reader must scroll

**Symptom:** Operator looking up PROFIT-EDGE-004's current status must scroll 1258 lines or grep. No table-of-contents at the top.

**Recommended fix:** add a TOC immediately after `## Header / Metadata`:

```markdown
## Table of Contents

- [Header / Metadata](#header--metadata)
- [Full Technical Debt Log](#full-technical-debt-log)
- [Current Open Profit-Path Items](#current-open-profit-path-items)
- [Per-Entry Status Table](#per-entry-status-table)
- Items:
  - [PROFIT-RUNTIME-001](#profit-runtime-001) (line 55)
  - [PROFIT-TRACE-001](#profit-trace-001) (line 178)
  - ...
  - [PROFIT-PHASE2-001](#profit-phase2-001) (line 2015)
```

GFM auto-generates anchors from `### PROFIT-X-001` headers; no manual anchor injection needed. **Cost: ~30 line addition. ~10 min wall-clock.**

### F2 (MEDIUM) — No per-entry status table

**Symptom:** to know which entries are OPEN vs CLOSED, operator must scan each entry's "Status" line. With 28 entries, this is a meaningful operator cost.

**Recommended fix:** add a status table immediately after the TOC:

```markdown
## Per-Entry Status Table

| ID | Title | Status | Severity | Owner |
|---|---|---|---|---|
| PROFIT-RUNTIME-001 | ... | COMPLETE | HIGH | Claude |
| PROFIT-EDGE-004 | matcher signal-quality / market-mix root cause | IN_PROGRESS | HIGH | Claude+Codex |
| PROFIT-PHASE2-001 | governance shadow-soak Phase 2 | IN_PROGRESS | HIGH | Claude+Codex |
| PROFIT-LLM-001 | signal-analyzer LLM unification | OPEN_PRE_SIZED | HIGH | Claude+Codex |
| ... | ... | ... | ... | ... |
```

Auto-generation candidate: a Codex script `scripts/debt_log_status_table_gen.py` could parse `### PROFIT-X-001` blocks and emit the table; rerun on each cycle's debt-log update. **Recommended:** delegate to Codex next cycle.

### F3 (LOW) — PROFIT-PHASE2-001 entry is 1614+ lines (lines 2015 → 2155 + cycle integration appendices)

**Symptom:** PHASE2-001 is the largest single entry by far; cycle integration sub-entries from 2026-05-04 / 2026-05-05 cycle 1 / cycle 2 / cycle 3 (this commit) accumulate inside the same parent entry.

**Reality:** this is the project's chosen pattern — cycles append to the parent entry rather than open new entries. Avoids fragmenting related work.

**Verdict:** ✅ pattern is correct; no fix needed. F3 is just an observation that PHASE2-001 reads long — that's structural, not a bug.

### F4 (LOW) — Inline cross-references to file paths use full repo-relative paths

**Symptom:** entries reference `docs/governance/...md` with the full path on each occurrence. ~50+ such refs.

**Reality:** GFM renders `docs/path.md` as a link to that file in GitHub UI. Operator-readable. **No fix needed.**

### F5 (LOW) — Cycle integration entries lack date anchors within parent entry

**Symptom:** PROFIT-PHASE2-001's cycle integration sub-headers are `#### 2026-05-04 cycle integration ...`, `#### 2026-05-05 cycle integration — ...`, etc. GFM auto-generates anchors (`#2026-05-04-cycle-integration-...`), but the anchor strings are long and fragile.

**Recommended fix (optional):** for each cycle entry, add an explicit anchor:

```markdown
<a id="phase2-001-cycle-2"></a>
#### 2026-05-05 cycle 2 integration — ...
```

This stabilizes the anchor for cross-reference. **Severity LOW** because cross-referencing is currently done by-grep, not by-anchor.

## Confirmed clean

- 28 PROFIT-X-001 entries are uniquely numbered; no collisions.
- Every entry has a Status / Severity / Priority / Owner row.
- Cross-references between entries use the canonical `PROFIT-X-001` ID.
- Cycle integration appends preserve historical state (no edits-in-place; immutable record-keeping).
- Top-level structure (Header → Full Debt Log → Open Items) is intelligible.

## Recommended single-commit fix

T1 + T2 (TOC + per-entry status table) in one commit. ~40-50 line addition. Auto-generated table can be Codex-scripted next cycle.

**Estimated wall-clock: ~15 min for manual table; ~30 min if Codex automates the table.**

## Out of scope

- Splitting the debt-log into per-entry files. Counter to project pattern (single canonical doc per CLAUDE.md).
- Migrating to a richer tracker (Jira / Linear). Out of project scope.
- Renaming entries. Existing IDs are load-bearing in cross-references; renames break.

## Cross-links

- `docs/profit_path_debt_log.md` — under audit
- `CLAUDE.md` (project root) — references the unified debt-log
- `~/.claude/CLAUDE.md` (global) — `docs/profit_path_debt_log.md` is the project's unified tracking system
