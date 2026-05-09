# `docs/` Directory Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the multiple parallel tracking surfaces in `docs/` into ONE canonical "what is going on in the project" document, with every other doc serving a clear non-tracking role (specs / plans / audit records / archives).

**Architecture:** Six-phase initiative. Discovery (Phases 2-3) runs as multi-agent parallel dispatch, read-only. Synthesis (Phase 4) designs the One Document and produces a per-file consolidation plan. Execution (Phase 5) applies migrations per-batch with user approval gates. Closure (Phase 6) updates `CLAUDE.md` to make the One Document canonical.

**Tech Stack:** ECC subagents (`code-explorer`, `comment-analyzer`, `doc-updater`, `code-architect`); main-thread synthesis; `Read`/`Edit`/`Write` for in-place mutations; `git mv` semantics for archives. No code changes — pure docs work. Test suite is canary only (must stay at 1652 passing).

**Branch:** `housekeeping/docs-consolidation` (created in Phase 1, off current `main`).

**Predecessor context (already on main):**
- `e0f94bb` — foundational docs audit + remediation (2026-05-09 Track 1)
- Track 2 guidance-consolidation (`~/.claude/` rules + auto-memory) — completed in another session, landed on main this morning
- Project CLAUDE.md gotcha already declares: "This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs."

This initiative HONORS that existing gotcha by **enforcing it across docs/** — currently parallel surfaces exist despite the rule.

---

## Current State Inventory (verified 2026-05-09)

```
docs/
├── _archive/                         19 files   (historical, archived)
├── governance/                      195 files   (charters, ledgers, audits — BIGGEST subdir)
├── housekeeping/                     26 files   (audit reports including 2026-05-08, 2026-05-09)
├── superpowers/                      27 files   (plans + specs)
├── EDGE_STATUS.md                  10.0K
├── IMPLEMENTATION_CONTRACT.md      53.7K
├── ROADMAP.md                      95.6K
├── evidence_store_schema.md         9.5K
├── evidence_store_schema.sql        6.3K
└── profit_path_debt_log.md        427.1K       (designated canonical tracker)

Total: 272 .md files
```

**Suspected parallel-tracking surfaces (to be confirmed in Phase 3):**
- `profit_path_debt_log.md` (canonical per CLAUDE.md)
- `EDGE_STATUS.md` (likely status snapshot — overlaps debt log?)
- `ROADMAP.md` (likely strategic horizon — overlaps debt log priorities?)
- `IMPLEMENTATION_CONTRACT.md` (semi-tracking; commits + status mixed with architecture)
- `docs/governance/<cycle>-ledger.md` (cycle-specific tracking — overlaps debt log?)
- `docs/housekeeping/*/SUMMARY.md` (audit-event tracking — short-lived)
- `docs/superpowers/plans/*.md` (planned-work tracking)

**Trust hierarchy for resolving "which surface wins" (declared up-front):**
1. `profit_path_debt_log.md` — already declared canonical by CLAUDE.md
2. Active cycle ledgers in `docs/governance/` — for in-flight cycle work; merges back to debt log at cycle close
3. `docs/superpowers/plans/` — planning artifacts; not state-tracking
4. `docs/superpowers/specs/` — design records at point in time; not tracking
5. `docs/housekeeping/` — audit-event records; archival
6. `docs/_archive/` — historical only

When two surfaces conflict on current state: 1 wins. When 1 is silent on a topic 2 covers: 2 fills the gap until cycle close.

---

## File Structure

**Created during execution (artifact files):**
- `docs/housekeeping/2026-05-09/docs-consolidation/inventory.md` (Phase 2 Agent 1)
- `docs/housekeeping/2026-05-09/docs-consolidation/classification.md` (Phase 2 Agent 2)
- `docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md` (Phase 2 Agent 3)
- `docs/housekeeping/2026-05-09/docs-consolidation/tracking-sources-analysis.md` (Phase 3)
- `docs/housekeeping/2026-05-09/docs-consolidation/one-doc-design.md` (Phase 4)
- `docs/housekeeping/2026-05-09/docs-consolidation/consolidation-plan.md` (Phase 4)
- `docs/housekeeping/2026-05-09/docs-consolidation/SUMMARY.md` (Phase 4)

**Modified during execution (per Phase 5 batches — exact list determined by Phase 4 plan):**
- `docs/profit_path_debt_log.md` (likely receives merged content from parallel sources)
- `docs/EDGE_STATUS.md` (likely DELETE or DEPRECATE-WITH-NOTE)
- `docs/ROADMAP.md` (likely SPLIT — strategic content stays, operational tracking moves)
- `docs/IMPLEMENTATION_CONTRACT.md` (likely SPLIT — architecture stays, status moves)
- `docs/governance/<various>-ledger.md` (cycle ledgers — KEEP active; ARCHIVE closed)
- `kalshi-bot/CLAUDE.md` Continuous Improvement section (sharpen the canonical-tracker rule)

**NOT modified (preservation list):**
- `docs/_archive/*` — already archived; do not touch
- `docs/superpowers/plans/*` — planning artifacts; orthogonal to tracking
- `docs/superpowers/specs/*` — design records at point in time
- `docs/housekeeping/2026-05-08/*` and `2026-05-09/foundational-docs-audit/*` — completed audit records

---

## Phase 1: Pre-Flight + Branch

**Files:**
- Verify: working tree on `main`, clean
- Verify: latest test baseline (1652 passed)
- Create: branch `housekeeping/docs-consolidation`

- [ ] **Step 1: Confirm tree state**

Run: `git status -sb && git log --oneline -3`
Expected: `## main`, HEAD includes Track 2 closure commit, working tree clean.

- [ ] **Step 2: Confirm baseline tests**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: `1652 passed, 2 skipped, 116 xfailed` (or higher if subsequent commits added tests; never lower).

- [ ] **Step 3: Create branch**

Run: `git checkout -b housekeeping/docs-consolidation && git status -sb`
Expected: `## housekeeping/docs-consolidation`

- [ ] **Step 4: User gate**

Stop. Surface state. User confirms readiness for Phase 2.

---

## Phase 2: Discovery (3 parallel subagents, read-only)

**Files:**
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/inventory.md`
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/classification.md`
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md`

Dispatch 3 subagents in a single message (parallel). All write to the discovery dir.

- [ ] **Step 1: Dispatch Agent 1 — code-explorer (full inventory)**

Agent prompt:
```
You are running Phase 2 — Inventory for the docs/ consolidation initiative.

Scope: <PROJECT_ROOT>/docs/ recursive (~272 .md files).

Task: build a complete inventory. For each .md file, capture:
- Absolute path (relative to project root)
- Size (bytes)
- Line count
- First-line content (often the title)
- Last modified date (git log -1 --format=%ai for the file)
- First commit date (git log --reverse --format=%ai | head -1 for the file)

Output a markdown table sorted by path. Group by top-level subdir (_archive, governance, housekeeping, superpowers, top-level).

DO NOT classify or interpret. Inventory only.

Cap at 500 entries. If exceeded, surface a count and ask the controller before continuing.

Output: docs/housekeeping/2026-05-09/docs-consolidation/inventory.md
```

- [ ] **Step 2: Dispatch Agent 2 — comment-analyzer (classification)**

Agent prompt:
```
You are running Phase 2 — Classification for the docs/ consolidation initiative.

Read the inventory produced by Agent 1 at docs/housekeeping/2026-05-09/docs-consolidation/inventory.md.

For each .md file, classify by primary content type:
- TRACKING_DEBT — ongoing debt log entries, P0/P1/P2 items with status
- TRACKING_STATUS — current-state snapshot (e.g. EDGE_STATUS, source credibility, soak status)
- TRACKING_ROADMAP — forward-looking strategic horizon (multi-week / multi-month)
- TRACKING_LEDGER — cycle-specific decision log (governance cycle ledgers)
- CONTRACT — architectural / behavioral contract document (IMPLEMENTATION_CONTRACT)
- CHARTER — formal definition of a cycle's scope and exit criteria
- AUDIT_REPORT — past audit findings (housekeeping reports)
- AUDIT_REVIEW — adversarial code reviews and verdicts
- DESIGN_SPEC — design intent at point in time (superpowers/specs/)
- PLAN — implementation plan (superpowers/plans/)
- RUNBOOK — operational procedure (e.g., PHASE2_RUNBOOK)
- README — orientation doc for a subdir
- SCHEMA — data schema documentation (evidence_store_schema)
- ARCHIVE — already archived, historical only
- MIXED — file contains 2+ classifications (flag for review)

For each file, output: path | classification | confidence (high/medium/low) | secondary_classification (if MIXED) | rationale (one line).

Read enough of each file to classify confidently. For files >50K, you may classify based on first 100 lines + last 50 lines.

Output: docs/housekeeping/2026-05-09/docs-consolidation/classification.md
```

- [ ] **Step 3: Dispatch Agent 3 — doc-updater (tracking-purpose extraction)**

Agent prompt:
```
You are running Phase 2 — Tracking Purpose Extraction for the docs/ consolidation initiative.

Read inventory at docs/housekeeping/2026-05-09/docs-consolidation/inventory.md.

Filter the file list to ONLY files in these classes (per Agent 2's classification.md once it lands; you may proceed in parallel by inferring from path):
- Top-level docs/*.md (5 files)
- docs/governance/*.md  (filter to TRACKING_*-shaped files: ledgers, audits, status reports — NOT charters/specs)
- docs/_archive/*.md (NOTE only — do not analyze deeply)

For each file in scope, extract:
- WHAT does this file assert about current project state? (max 3 lines)
- HOW OFTEN does it get updated? (one-line: per-cycle / per-day / one-shot / abandoned)
- WHO updates it? (operator manually / agent automation / both)
- WHAT OTHER FILES does it duplicate or overlap with? (cite paths)

For files that are NOT tracking (specs, plans, charters, schemas), output a single line: "[NOT TRACKING] — <one-word category>".

Output: docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md
```

- [ ] **Step 4: Wait for all 3 agents**

All dispatched in parallel. Wait for all to return before proceeding.

- [ ] **Step 5: Verify all 3 output files exist**

Run: `ls docs/housekeeping/2026-05-09/docs-consolidation/`
Expected: 3 .md files (inventory, classification, tracking-purpose).

- [ ] **Step 6: Commit Phase 2 artifacts**

```bash
git add docs/housekeeping/2026-05-09/docs-consolidation/inventory.md docs/housekeeping/2026-05-09/docs-consolidation/classification.md docs/housekeeping/2026-05-09/docs-consolidation/tracking-purpose.md
git commit -m "$(cat <<'EOF'
docs: docs/ consolidation Phase 2 — discovery

Three parallel subagents:
- code-explorer: inventory of all 272 .md files in docs/ tree
- comment-analyzer: classification by content type
- doc-updater: tracking-purpose extraction for tracking-shaped files

Read-only. No source files modified.

Part of docs-directory-consolidation initiative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate**

Stop. User reviews the 3 discovery files. Confirms inventory/classification feel complete. If anything looks wrong (missing files, mis-classification), re-dispatch the affected agent before proceeding.

---

## Phase 3: Tracking-Source Analysis (read-only)

**Files:**
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/tracking-sources-analysis.md`

This phase is one main-thread analysis pass (no subagent needed — synthesis benefits from full main-thread context with the 3 Phase 2 outputs in hand).

- [ ] **Step 1: Read all 3 Phase 2 outputs**

Read: inventory.md, classification.md, tracking-purpose.md.

- [ ] **Step 2: Cluster files by what they track**

Build a clustering table. Each cluster = a topic that tracking surfaces try to capture. Examples (will be derived from actual data, not pre-listed):
- "Open debt items" cluster
- "Active cycle status" cluster
- "Edge / signal status" cluster
- "Roadmap horizon" cluster
- "Source credibility" cluster
- etc.

For each cluster, list every file that tracks any part of it. Highlight which files DUPLICATE coverage.

- [ ] **Step 3: Identify the canonical surface per cluster**

Per the trust hierarchy declared in this plan's header. For most clusters, `profit_path_debt_log.md` will be canonical.

For clusters where `profit_path_debt_log.md` is silent: identify whether (a) the canonical surface needs a new section, (b) a different file should be canonical (e.g., active cycle ledger for cycle work), or (c) the cluster shouldn't be tracked at all (transient state).

- [ ] **Step 4: Quantify the bloat**

For each duplicate-tracking pair, estimate:
- Token cost of the duplication (line counts × ~4 tokens/line)
- Drift risk (how often the two surfaces have diverged historically — check git log)
- Operator confusion risk (subjective: high/med/low)

- [ ] **Step 5: Write tracking-sources-analysis.md**

Sections:
1. Cluster inventory (every tracking topic with the files contributing)
2. Canonical-surface assignment per cluster
3. Duplicate-tracking pairs with bloat / drift / confusion estimates
4. Open Questions for user judgment (where canonical assignment isn't clear-cut)

End with: "Tracking-sources analysis complete. Phase 4 designs the One Document around these clusters."

- [ ] **Step 6: Commit**

```bash
git add docs/housekeeping/2026-05-09/docs-consolidation/tracking-sources-analysis.md
git commit -m "$(cat <<'EOF'
docs: docs/ consolidation Phase 3 — tracking-sources analysis

Main-thread synthesis of Phase 2 outputs. Files clustered by tracking
topic; canonical surface assigned per cluster; duplicate-tracking
pairs quantified with bloat / drift / confusion estimates.

Read-only. Open Questions surfaced for Phase 4 user gate.

Part of docs-directory-consolidation initiative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate**

Stop. User reads tracking-sources-analysis.md. Resolves any Open Questions before proceeding to Phase 4.

---

## Phase 4: Synthesis — Design the One Document + Per-File Consolidation Plan

**Files:**
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/one-doc-design.md`
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/consolidation-plan.md`
- Create: `docs/housekeeping/2026-05-09/docs-consolidation/SUMMARY.md`

This phase has two artifacts: the design of the One Tracking Document (its structure / sections / update cadence) AND the per-file action plan that gets executed in Phase 5.

- [ ] **Step 1: Dispatch code-architect agent for One Document design**

Agent prompt:
```
You are designing the canonical "One Document" tracking surface for the kalshi-bot docs/ directory.

Context:
- Project's existing canonical tracker is docs/profit_path_debt_log.md (declared in CLAUDE.md)
- Phase 3 analysis is at docs/housekeeping/2026-05-09/docs-consolidation/tracking-sources-analysis.md
- Trust hierarchy is declared in docs/superpowers/plans/2026-05-09-docs-directory-consolidation.md header

Task: Design the structure of the One Document. Output a section-level outline:

For each section:
- Section title
- What state it tracks
- Update cadence (per-event / per-cycle / per-day / weekly / one-shot-then-frozen)
- Who updates it (operator / agent / both)
- Source of truth — does this section hold the canonical state, or reference another file?

The design should:
- Build on profit_path_debt_log.md as foundation (it's already 427K — clearly load-bearing)
- Define structure that absorbs content from EDGE_STATUS, ROADMAP-tracking-portion, IMPLEMENTATION_CONTRACT-status-portion
- Keep CHARTER and CYCLE_LEDGER as separate files (they have lifecycle different from ongoing debt)
- Avoid bloating profit_path_debt_log.md beyond ~600K (start considering split if approaching 1M)

Also output: target token cost per section (so total budget stays bounded).

Output: docs/housekeeping/2026-05-09/docs-consolidation/one-doc-design.md
```

- [ ] **Step 2: Build the per-file consolidation plan (main thread)**

Read one-doc-design.md.

For every file in inventory.md, decide one action:
- **KEEP** — file stays as-is, role unchanged
- **MERGE-INTO-ONE-DOC** — content folds into a section of profit_path_debt_log.md
- **MERGE-INTO-OTHER** — content folds into a different specified file
- **SPLIT** — file contains 2+ purposes; split into target files
- **ARCHIVE** — move to docs/_archive/<cycle-or-date>/
- **DELETE** — superseded with no historical value
- **DEPRECATE-WITH-NOTE** — keep file, add deprecation banner pointing at new location (for files referenced externally)

Write consolidation-plan.md. Structure:
- Per-file table: path | classification (from Phase 2) | action | target | rationale | batch
- "Batches" section: group items by safe-to-execute-together (e.g., "Batch 1 — top-level tracking merges", "Batch 2 — governance ledger archive sweep", "Batch 3 — superpowers preservation pass", "Batch 4 — CLAUDE.md update")

Cap batches at 5. Each batch should produce one commit in Phase 5.

- [ ] **Step 3: Write SUMMARY.md**

Synthesize Phase 2/3/4 outputs:
- Bottom-line: how many files, how many merged, how many archived, how many deleted
- Estimated docs/ size before/after
- Open Questions (ANY user-judgment items not resolved in Phase 3)
- Recommended Phase 5 batch order
- Risk callouts (e.g., "ROADMAP.md split is the highest-risk batch — preserves strategic content while removing operational tracking")

End with literal line: "Phase 4 design + plan complete. Review SUMMARY.md before approving Phase 5 execution."

- [ ] **Step 4: Verify all 3 Phase 4 outputs exist**

Run: `ls docs/housekeeping/2026-05-09/docs-consolidation/ | grep -E '(one-doc|consolidation-plan|SUMMARY)'`
Expected: 3 files.

- [ ] **Step 5: Commit Phase 4 artifacts**

```bash
git add docs/housekeeping/2026-05-09/docs-consolidation/one-doc-design.md docs/housekeeping/2026-05-09/docs-consolidation/consolidation-plan.md docs/housekeeping/2026-05-09/docs-consolidation/SUMMARY.md
git commit -m "$(cat <<'EOF'
docs: docs/ consolidation Phase 4 — One Document design + per-file plan

Outputs:
- one-doc-design.md (code-architect agent): section structure for the
  canonical tracking document, building on profit_path_debt_log.md
- consolidation-plan.md (main-thread synthesis): per-file action
  (KEEP / MERGE / SPLIT / ARCHIVE / DELETE / DEPRECATE) with batches
- SUMMARY.md: bottom-line + open questions + recommended batch order

Read-only. No execution yet.

Part of docs-directory-consolidation initiative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: User gate — approve plan or revise**

Stop. User reads SUMMARY.md, then consolidation-plan.md and one-doc-design.md.

User decisions per batch:
- Approve as-is → proceed to Phase 5 batch
- Modify specific items → annotate plan inline
- Reject batch → skip in Phase 5
- Hold whole plan → defer Phase 5 indefinitely

Record decisions in `/tmp/docs-consolidation-decisions.md` for Phase 5 reference.

---

## Phase 5: Execution (per-batch user-gated, mutates files)

For each approved batch in `consolidation-plan.md` § Batches:

- [ ] **Step 1: Read the batch from the plan**

Reference batch by name. List items, source paths, target paths, actions.

- [ ] **Step 2: Present batch diff preview to user**

Show: what gets created, what gets modified, what gets deleted, what gets merged. List target file post-edit size estimate.

Wait for explicit user approval ("execute batch X").

- [ ] **Step 3: Apply batch edits**

For each item in batch:

**KEEP** — no-op.

**MERGE-INTO-ONE-DOC** —
1. Read source file
2. Read target file (profit_path_debt_log.md)
3. Identify target section per one-doc-design.md
4. Use Edit tool to insert source content under target section
5. Add a "consolidated from <source-path> on 2026-05-09" comment at the merge boundary
6. Stage source file deletion (`git rm <source>`)

**MERGE-INTO-OTHER** — same as above but with a different target.

**SPLIT** —
1. Read source file
2. Identify split points per consolidation-plan.md
3. Write target file(s) with extracted content
4. Either delete source OR replace source with reduced content (per plan)

**ARCHIVE** —
1. Use `git mv <source> docs/_archive/2026-05-09-docs-consolidation/<source-basename>` (preserves history as rename)
2. If `docs/_archive/2026-05-09-docs-consolidation/` doesn't exist yet, create it via Bash mkdir before the first git mv

**DELETE** —
1. Use `git rm <source>` directly. Only after explicit per-file user confirmation in batch preview.

**DEPRECATE-WITH-NOTE** —
1. Read source
2. Edit source: prepend a deprecation banner:
```markdown
> **DEPRECATED 2026-05-09:** This file's tracking content has been consolidated into [`docs/profit_path_debt_log.md`](profit_path_debt_log.md) § <section>. Retained for cross-reference; not updated.
```
3. Do not delete the file.

- [ ] **Step 4: Update cross-references**

Anything that cited a moved/merged/deleted file may have a broken link. Run grep across the project:

```bash
grep -rn "<old-path>" docs/ kalshi-bot/CLAUDE.md README.md scripts/ 2>/dev/null
```

For each citing reference, update to new path or remove (if the old reference was to deleted content).

- [ ] **Step 5: Run test suite (canary check)**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: pass count >= 1652 (no regressions from doc changes).

If a test reads a doc directly and broke: fix the test reference, re-run.

- [ ] **Step 6: Commit batch**

```bash
git add <list of explicit files modified/created/deleted in this batch>
git commit -m "$(cat <<'EOF'
docs(consolidation): execute batch <batch-name>

[Per-item summary derived from consolidation-plan.md]

Cross-references updated: [N files]
Test count: unchanged (1652 passed)

Part of docs-directory-consolidation initiative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate — proceed to next batch?**

Stop. User reviews diff via `git show HEAD`. Decides:
- Approve → next batch
- Revert + redo → reset HEAD~1, fix issue, re-execute batch
- Pause execution → resume later

Repeat Steps 1-7 for each remaining batch.

---

## Phase 6: Closure

**Files:**
- Modify: `kalshi-bot/CLAUDE.md` (sharpen the canonical-tracker rule)
- Modify: `docs/profit_path_debt_log.md` (add a closure note in its own header)

- [ ] **Step 1: Update CLAUDE.md Continuous Improvement section**

Read `kalshi-bot/CLAUDE.md` Continuous Improvement section (currently at line 19-20).

Sharpen the existing rule. Replace:
```
- This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs.
```

With (exact replacement):
```
- This project's unified tracking system is `docs/profit_path_debt_log.md`. Do not create parallel tracking surfaces (status / roadmap / debt / decision-log / etc.). The 2026-05-09 docs consolidation removed N parallel surfaces (see `docs/housekeeping/2026-05-09/docs-consolidation/SUMMARY.md`); preserving consolidation is now a maintenance invariant. New tracking content lands as a section in the One Document, not a new file.
```

(Receiving Claude fills in `N` with the actual count from Phase 5 commits.)

- [ ] **Step 2: Verify edit**

Run: `grep -A1 "unified tracking system" kalshi-bot/CLAUDE.md`
Expected: matches new wording.

- [ ] **Step 3: Add One Document header note**

Read `docs/profit_path_debt_log.md` header (first ~10 lines).

Add (or replace existing intro) with:
```markdown
> **Canonical project tracking surface.** Per `kalshi-bot/CLAUDE.md`, this is the single source of truth for "what is going on in the project." Sections below cover open debt, current status, roadmap horizon, decision log, and cycle outcomes. Any tracking content that doesn't fit a section means a new section is needed — not a new file. Last consolidated: 2026-05-09 (`docs/housekeeping/2026-05-09/docs-consolidation/`).
```

- [ ] **Step 4: Final commit**

```bash
git add kalshi-bot/CLAUDE.md docs/profit_path_debt_log.md
git commit -m "$(cat <<'EOF'
docs: docs/ consolidation closure — CLAUDE.md + One Document header

Phase 6 closure. CLAUDE.md Continuous Improvement section sharpened
to enforce single-tracking-surface rule. profit_path_debt_log.md
header declares its canonical role and references the consolidation
record.

N parallel tracking surfaces removed in Phases 5 (see SUMMARY.md
in housekeeping/2026-05-09/docs-consolidation/).

Closes docs-directory-consolidation initiative.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push branch**

```bash
git push -u origin housekeeping/docs-consolidation
```

- [ ] **Step 6: User gate — merge decision**

Stop. User decides: direct merge to main / open MR / hold for review.

If direct merge approved:
```bash
git checkout main && git pull --ff-only origin main && git merge --ff-only housekeeping/docs-consolidation && git push origin main && git branch -d housekeeping/docs-consolidation && git push origin --delete housekeeping/docs-consolidation
```

---

## Estimated Cost / Time

| Phase | Mechanism | Cost | Wall time |
|---|---|---|---|
| 1 | Pre-flight (main thread) | <$0.50 | 2 min |
| 2 | 3 parallel subagents (inventory + classification + tracking-purpose) | $5-8 | 15-25 min |
| 3 | Main-thread synthesis (no subagent) | $1-2 | 10-15 min |
| 4 | code-architect agent + main-thread plan + SUMMARY | $4-7 | 20-30 min |
| 5 | Per-batch execution (4-5 batches likely) | $8-15 | 60-120 min |
| 6 | Closure | <$1 | 5 min |
| **Total** | | **~$20-35** | **~2-3 hours active** |

User-attended time is much shorter — gates only.

---

## Risk Mitigations

- **Read-only-first.** Phases 1-4 are entirely read-only. Only Phase 5 mutates. Phase 5 itself is per-batch user-gated with diff preview.
- **Test invariant.** Test count (1652) is the canary. Doc-only changes shouldn't move it. Any drop = something broke; fix before proceeding.
- **Cross-reference grep.** Phase 5 Step 4 prevents broken-pointer regressions from file moves/deletes.
- **History preservation.** `git mv` preserves rename history for archived files; `git rm` only used on items confirmed in batch preview.
- **One-Doc bloat ceiling.** Phase 4 design pass declares a soft ceiling (~600K) for `profit_path_debt_log.md`. If consolidation would exceed it, the plan SPLITS into multiple sections vs one giant file — but stays one document conceptually.
- **Trust hierarchy declared up-front.** Conflict resolution (which surface wins for any given cluster) isn't ad hoc — it's deterministic per the hierarchy in this plan's header.
- **Caps on findings.** Each agent's output capped (500 inventory entries, top-30 cluster, 5 batches) to keep human review tractable.
- **Branch isolation.** All work on `housekeeping/docs-consolidation`. Can be abandoned without polluting main.

---

## Self-Review Notes

(Per superpowers:writing-plans skill)

- **Spec coverage:** User asked for "highly in-depth review focused on consolidation" with goal of "one document to track what is going on." Plan addresses both: in-depth via 3-agent parallel discovery + analysis pass + design pass; consolidation via explicit per-file action plan with execution gating; one-document outcome via Phase 6 CLAUDE.md sharpening.
- **Placeholder scan:** Concrete agent prompts in every dispatch step. Trust hierarchy explicit. File paths verbatim where known; placeholder `<TODAY-UTC>` resolved to `2026-05-09`. Action verbs (MERGE / SPLIT / ARCHIVE / DELETE / DEPRECATE) explicitly defined. No "TBD" or "implement later".
- **Type consistency:** Phase numbering consistent. Action verbs (KEEP/MERGE/SPLIT/ARCHIVE/DELETE/DEPRECATE) defined once and used consistently. Branch name (`housekeeping/docs-consolidation`) consistent throughout. File-path conventions consistent (`docs/housekeeping/2026-05-09/docs-consolidation/...`).
- **Identified gap:** Phase 5 doesn't specify what happens if a batch's grep finds 50+ broken cross-references. Implicit answer: walk each one. If user wants different treatment, declare before Phase 5.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-docs-directory-consolidation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per phase, two-stage review between phases (spec compliance → quality), fast iteration. Best fit for the multi-phase shape and dispatch pattern.

**2. Inline Execution** — Execute phases in this session using executing-plans, batch execution with checkpoints. Slower for this scope but keeps everything in one transcript.

Which approach?
