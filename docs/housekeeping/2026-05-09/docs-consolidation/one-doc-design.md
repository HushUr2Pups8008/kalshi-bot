# One Document Design — profit_path_debt_log.md Post-Consolidation

**Phase:** 4 — Design (code-architect agent output)
**Date:** 2026-05-09
**Inputs:** tracking-sources-analysis.md (Phase 3), profit_path_debt_log.md (current state), EDGE_STATUS.md, edge-004-closure-path-tldr-v3.md, ROADMAP.md, CLAUDE.md, 2026-05-09-docs-directory-consolidation.md (plan)
**User decisions applied:** Q1 (MERGE+DELETE), Q2 (wave-* excluded), Q3 (IC out of scope), Q4 (ARCHIVE 5 files), Q5 (fold v3 content + ARCHIVE both TLDRs), Q6 (12 MIXED files KEEP)
**Phase 6 Step 3 input:** Section 4 (Header Design) is the verbatim replacement for profit_path_debt_log.md lines 1-7.

---

## 1. Existing-Section Fate Table

| Section | Current lines | Current title | Fate | Notes |
|---------|--------------|--------------|------|-------|
| Preamble | 1-7 (7 lines) | `# Profit Path Technical Debt Log` + 2-paragraph prose | RESTRUCTURE | Replace 7 lines with ~19-line canonical declaration. See Section 4 for verbatim replacement. |
| inter-section blank | 8 (1 line) | — | KEEP | Natural separator before Header/Metadata. No change. |
| 1 — Header / Metadata | 9-43 (35 lines) | `## Header / Metadata` | RESTRUCTURE (minor) | Add `Consolidated From` field row after line 22 (`Items COMPLETE` row). No other changes. |
| separator | 45-48 (4 lines) | `## Full Technical Debt Log` | ELIMINATE | Zero-content separator: heading + blank + `---` + blank. Contains no tracked state. |
| 2 — Debt Items | 49-4579 (4531 lines) | `## Current Open Profit-Path Items` | KEEP | Optional rename to `## Profit-Path Debt Items`. No content changes. |
| 3 — Execution Views | 4580-4687 (108 lines) | `## Execution Views` | KEEP | No changes. |
| 4 — Dependency Map | 4688-4730 (43 lines) | `## Dependency Map` | KEEP | No changes. |
| 5 — Operating Rules | 4731-4761 (31 lines) | `## Operating Rules` | KEEP + ADD R-10 | Append R-10 (No New Tracking Files) after R-9. See Section 7 for verbatim R-10 text. |

---

## 2. New-Section Spec

### Section: `## Current Status`

**Insert position:** After `## Header / Metadata` (after line 43), replacing the eliminated `## Full Technical Debt Log` separator (lines 45-48). Becomes section 2 in the post-consolidation section index.

| Field | Value |
|-------|-------|
| **Section title** | `## Current Status` |
| **What state tracked** | (2.1) Current edge verdict and PROFIT-EDGE-011 posture (Cycle-16E / Cycle-17 routing). (2.2) Wave deploy posture: Wave-1 ACTIVE / Wave-2 HALTED / Wave-3 HALTED / Branch-D HALTED — with IC §16 gate criteria for AUTHORIZED transition. (2.3) EDGE-004 closure path: condensed lever map (A.1 / A.1+ branches / B / C / Branch-D / Lever D closed / Lever E closed / Lever F out-of-scope), closure criteria (>=5% conversion lift + non-negative P&L + per-lane attribution), probability ranking (Branch A ~30%, Branch C ~40% conditional, intake-side closure ~58%). (2.4) Replay harness state: scripts, test count, capabilities, current cycle scope. (2.5) Live operations: launchd job list, db-backup last-fire. (2.6) Cross-links to IC §16, strategic-redirect doc, replay pivot playbook. |
| **Update cadence** | Per-cycle. Heavy on cycle verdict events; lighter on soak/deploy state changes. Wave posture updates when operator issues a decision or IC §16 gate is cleared. |
| **Who updates** | Operator + agent. Verdict labels from replay tooling (Codex/Claude); deploy posture from operator decisions; live ops from launchd state. |
| **Source of truth** | CANONICAL for current gate posture (HALTED / ACTIVE / AUTHORIZED). `docs/ROADMAP.md` remains canonical for wave deploy timeline (dates, sequencing). On conflict: this section wins for posture; ROADMAP wins for timeline dates. |
| **Estimated size** | ~130 lines, ~12,000 bytes. Breakdown: (2.1) 30 lines, (2.2) 30 lines, (2.3) 35 lines, (2.4) 15 lines, (2.5) 10 lines, (2.6) 10 lines. |
| **Absorbed from** | `docs/EDGE_STATUS.md` (full file, 104 lines, 10,277 bytes — MERGED + DELETED per Q1). `docs/governance/edge-004-closure-path-tldr-v3.md` (lever map + closure criteria + honest-read, ~40 lines — key content absorbed into subsection 2.3; file ARCHIVED per Q5). `docs/governance/edge-004-closure-path-tldr.md` (v2.2, superseded — ARCHIVED per Q5, no content absorption). |

---

## 3. Size Budget

| Item | Lines delta | Bytes delta | Notes |
|------|------------|------------|-------|
| Current state | 4,761 lines | 437,313 bytes | Measured 2026-05-09 |
| Preamble restructure | +12 | +1,200 | Lines 1-7 (7 lines) replaced with ~19-line canonical declaration; net +12 |
| Header/Metadata `Consolidated From` row | +1 | +150 | Single table row inserted after line 22 |
| `## Current Status` (NEW) | +130 | +12,000 | Full section with 6 subsections |
| `## Full Technical Debt Log` separator (ELIMINATE) | -4 | -400 | Lines 45-48: heading + blank + `---` + blank |
| `## Operating Rules` add R-10 | +5 | +500 | H3 heading + body (~4 lines) |
| **Net delta** | **+144** | **+13,450** | |
| **Post-consolidation projected** | **~4,905 lines** | **~450,763 bytes (~451K)** | |
| **600K ceiling headroom** | — | **~149K** | Comfortable; no split required |

---

## 4. Header Design (verbatim — Phase 6 Step 3 input)

Verbatim replacement for `docs/profit_path_debt_log.md` lines 1-7. Phase 5 Step 5 applies this via Edit tool. Phase 6 Step 3 uses this as the "One Document header note."

```markdown
# Profit Path Technical Debt Log

> **Canonical project tracking surface.** Per `kalshi-bot/CLAUDE.md`, this is the single source of truth for "what is going on in the project." Sections below cover open debt, current status, roadmap horizon, decision log, and cycle outcomes. Any tracking content that doesn't fit a section means a new section is needed — not a new file. Last consolidated: 2026-05-09 (`docs/housekeeping/2026-05-09/docs-consolidation/`).

**Single system of record for technical debt that could materially reduce the bot's ability to make money through disciplined, well-educated trades.** Scope: platform, signal-quality, belief-system, execution-boundary, observability, validation, and documentation risks. Supersedes `docs/macos_migration_debt.md` (2026-04-20); absorbed `docs/EDGE_STATUS.md` (2026-05-09 consolidation).

**Trust hierarchy:** this file > active cycle ledgers in `docs/governance/` > `docs/superpowers/plans/` > `docs/superpowers/specs/` > `docs/housekeeping/` (audit records) > `docs/_archive/` (historical). On conflict: this file wins. Active cycle ledgers merge back here at cycle close.

**Forward horizon:** `docs/ROADMAP.md` is canonical for multi-week strategic priorities and wave deploy timeline. The Recommended Execution Order below cross-references ROADMAP; it does not duplicate it.

| # | Section | What it tracks | Update cadence |
|---|---------|---------------|----------------|
| 1 | Header / Metadata | Document metadata, item counts, high-risk areas, execution order | Per-event |
| 2 | Current Status | Edge verdict, wave deploy posture, replay harness, live ops | Per-cycle |
| 3 | Current Open Profit-Path Items | All PROFIT-* and MAC-* debt entries (open + complete) | Per-event |
| 4 | Execution Views | Fix queue, pre-go-live gate, work streams | Per-cycle |
| 5 | Dependency Map | Item dependency graph | Per-event |
| 6 | Operating Rules | R-1 through R-10 governing update conventions | Amendment only |

---
```

---

## 5. Migration Sequence

Steps must execute in dependency order. Steps 4 and 5 are independent of each other and can run in parallel.

**Step 1 (FIRST — prerequisite for steps 2-3):** Add `## Current Status` section template to `profit_path_debt_log.md`. Insert an empty skeleton (H2 heading + 6 H3 subsection headings with placeholder comments) at line 44 position (after the `## Header / Metadata` trailing `---` at line 43). The `## Full Technical Debt Log` separator at lines 45-48 remains until Step 5.

**Step 2 (requires Step 1):** Merge `docs/EDGE_STATUS.md` full content into `## Current Status`. Mapping:
- EDGE_STATUS §TL;DR + §Cycle-16E verdict + §Replay verdict log → §2.1 Edge Verdict
- EDGE_STATUS §Wave deploy status table + §Are we near Wave-2? + §Pre-deploy state → §2.2 Wave Deploy Posture
- EDGE_STATUS §Replay harness state → §2.4 Replay Harness State
- EDGE_STATUS §Live operations → §2.5 Live Operations
- EDGE_STATUS §Cross-links → §2.6 Cross-links (prune to canonical links only)

After merge: `git rm docs/EDGE_STATUS.md`. Add boundary comment `<!-- Merged from docs/EDGE_STATUS.md on 2026-05-09 -->` at top of `## Current Status`.

**Step 3 (requires Step 2):** Fold lever map + closure criteria + honest-read from `docs/governance/edge-004-closure-path-tldr-v3.md` into `## Current Status §2.3 EDGE-004 Closure Path`. Extract:
- Table from §"Lever map at a glance (v3)" (lines 54-66) — condense, drop n/a rows
- Criteria from §"What closure looks like" (lines 79-85) — verbatim 3-point list
- Probability ranking from §"Honest read" (lines 87-96) — 4 bullets + 58% bottom line

After fold:
```bash
mkdir -p docs/_archive/2026-05-09-docs-consolidation
git mv docs/governance/edge-004-closure-path-tldr-v3.md docs/_archive/2026-05-09-docs-consolidation/
git mv docs/governance/edge-004-closure-path-tldr.md docs/_archive/2026-05-09-docs-consolidation/
```

**Step 4 (independent):** Archive 5 Q4 placeholder files:
```bash
mkdir -p docs/_archive/2026-05-09-docs-consolidation/soak-status-history
git mv docs/governance/2026-05-03-day-3-pending-mid-soak-confirmation.md docs/_archive/2026-05-09-docs-consolidation/soak-status-history/
git mv docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation.md docs/_archive/2026-05-09-docs-consolidation/soak-status-history/
git mv docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md docs/_archive/2026-05-09-docs-consolidation/soak-status-history/
git mv docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-3.md docs/_archive/2026-05-09-docs-consolidation/soak-status-history/
git mv docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-4.md docs/_archive/2026-05-09-docs-consolidation/soak-status-history/
```
Add archive pointer in `PROFIT-PHASE2-001` entry: "Per-day soak placeholder history archived 2026-05-09; see `docs/_archive/2026-05-09-docs-consolidation/soak-status-history/`."

**Step 5 (after steps 1-3):** Apply structural changes to `profit_path_debt_log.md`:
- Replace preamble lines 1-7 with the verbatim header from Section 4 of this document
- Add `Consolidated From` row to `## Header / Metadata` table after line 22: `| Consolidated From | \`docs/EDGE_STATUS.md\` (merged 2026-05-09 → §Current Status); \`docs/governance/edge-004-closure-path-tldr-v3.md\` (lever map + EDGE-004 closure criteria merged 2026-05-09 → §Current Status §2.3) |`
- Eliminate `## Full Technical Debt Log` separator (lines 45-48)
- Append R-10 to `## Operating Rules` (see Section 7)

**Step 6 (LAST):** Update `CLAUDE.md` Continuous Improvement rule per Phase 6 Step 1 of the parent plan.

---

## 6. Scope Constraints

**Q2 — wave-* prestaged files excluded until Wave-3 deploys:**

Active pre-deploy artifacts. Stay in `docs/governance/` until Wave-2/3 deploy executed. Do not archive in Phase 5.
- `docs/governance/wave-1-changelog-entry-prestaged.md`
- `docs/governance/wave-1-commit-messages-prestaged.md`
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md`

**Q3 — IMPLEMENTATION_CONTRACT.md out of scope entirely:**

IC §10 update-convention rules are correctly distinct from the debt log's application of those rules. No extraction, no merge, no deprecation. Unchanged.

**Q6 — 12 MIXED files all KEEP (9 non-wave + 3 Q2-excluded wave):**

All 12 MIXED-classification files stay as-is. No SPLIT action unless user identifies specific drift conflict during Phase 5.
- `docs/governance/2026-05-05-day-7-attestation-prestage.md`
- `docs/governance/2026-05-06-cycle-15b-task-split.md`
- `docs/governance/2026-05-07-cycle-16d-task-split.md`
- `docs/governance/2026-05-07-cycle-16e-task-split.md`
- `docs/governance/2026-05-07-cycle-17c-e1-claude-verdict-appendix.md`
- `docs/governance/cycle-14-post-verdict-action-checklist.md`
- `docs/governance/cycle-15b-post-verdict-action-checklist.md`
- `docs/governance/cycle-16d-post-verdict-action-checklist.md`
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md`
- `docs/governance/wave-1-changelog-entry-prestaged.md` (also Q2-excluded)
- `docs/governance/wave-1-commit-messages-prestaged.md` (also Q2-excluded)
- `docs/governance/wave-2-wave-3-changelog-entries-prestaged.md` (also Q2-excluded)

---

## 7. R-10 Text (verbatim — for `## Operating Rules` addition)

Append the following after `### R-9 — Single Tracker Rule` in `docs/profit_path_debt_log.md`:

```markdown
### R-10 — No New Tracking Files

This file is the sole tracking surface for all project state. New tracking content lands as a section or sub-section here, not a new standalone file. If content genuinely belongs in a separate lifecycle (e.g., an active cycle ledger in `docs/governance/`), it merges back here at cycle close. Per CLAUDE.md Continuous Improvement: do not create parallel status, roadmap, decision-log, or operational-dashboard files. The 2026-05-09 docs consolidation removed parallel surfaces (EDGE_STATUS.md and TLDR variants) to enforce this rule; preserving the consolidation is now a maintenance invariant.
```

---

**Design summary:** This design produces 7 sections in `profit_path_debt_log.md` post-consolidation (preamble restructured, Header/Metadata minor restructure, new `## Current Status`, existing debt items, Execution Views, Dependency Map, Operating Rules + R-10). Projected total ~451K — 149K under the 600K ceiling. All 6 user decisions absorbed cleanly: Q1 drives the `## Current Status` creation and EDGE_STATUS.md deletion; Q2 excludes 3 wave-* files from scope; Q3 excludes IC entirely; Q4 archives 5 placeholder files; Q5 folds v3 TLDR content into the new section and archives both TLDR variants; Q6 keeps all 9 non-wave MIXED files in place.
