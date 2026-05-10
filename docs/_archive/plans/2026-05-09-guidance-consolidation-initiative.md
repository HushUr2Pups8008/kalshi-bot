# Guidance Consolidation Initiative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all guidance markdown across the kalshi-bot repo + `~/.claude` + auto-memory into a coherent, conflict-free, discoverable structure where every piece of guidance lives in exactly one canonical location.

**Architecture:** Two-track sequenced approach.
- **Track 1 (this plan, Phases 1-3):** Foundational docs audit — narrow scope, low risk, validates workflow. Audits CLAUDE.md (project + global), `~/.claude/rules/`, `~/.claude/AGENTS.md`, `~/.claude/RTK.md`, `<PROJECT>/README.md`, plus `docs/superpowers/plans/` and `docs/superpowers/specs/` as secondary scope.
- **Track 2 (this plan, Phases 4-7):** Full repo-wide guidance consolidation — discovery, conflict detection, consolidation plan, gated execution. Includes auto-memory inspection.

Track 1 runs first because (a) it's already drafted, (b) results inform Track 2's consolidation plan, (c) smaller commit derisks the workflow before broader scope.

**Tech Stack:** ECC subagents (`comment-analyzer`, `code-explorer`, `refactor-cleaner`, `code-architect`); ECC skills (`claude-md-management:claude-md-improver`, `everything-claude-code:rules-distill`, `everything-claude-code:context-budget`, `everything-claude-code:workspace-surface-audit`); main-thread synthesis; `ctx_search` for auto-memory discovery. Output to `docs/housekeeping/<TODAY-UTC>/` subdirs. Branch isolation per track.

**Branches:**
- Track 1: `housekeeping/foundational-docs` (already created, off `main @ e05b9e4`)
- Track 2: `housekeeping/guidance-consolidation` (created in Phase 4)

**Trust hierarchy declared up-front (used by Phases 5-6 to resolve conflicts):**
1. `~/.claude/rules/*.md` (mechanical guardrails) — highest authority for "trigger X → action Y" rules
2. `<PROJECT>/CLAUDE.md` Critical Gotchas (narrative) — highest authority for project-specific hidden constraints
3. `<PROJECT>/CLAUDE.md` Working Style — project-level preferences
4. `~/.claude/CLAUDE.md` — global preferences
5. Auto-memory entries — provisional, can be invalidated by 1-4
6. `docs/superpowers/specs/` — design intent at point in time; not authoritative for current code state
7. Scratch / loose `.md` — lowest authority

When 1-4 conflict with each other, project-level beats global. When code drifts from 1-4, code wins for current state but doc gets fixed (not silently ignored).

---

## File Structure

**Created during this plan (planning artifacts):**
- `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/` (Track 1 reports + SUMMARY)
- `docs/housekeeping/<TODAY-UTC>/guidance-discovery/` (Track 2 Phase A artifacts)
- `docs/housekeeping/<TODAY-UTC>/guidance-conflicts/` (Track 2 Phase B artifacts)
- `docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md` (Track 2 Phase C artifact)

**Modified during this plan (consolidation execution, Phase 6):**
- `<PROJECT>/CLAUDE.md` (additions, deletions, gotcha relocation)
- `~/.claude/CLAUDE.md` (additions, deletions)
- `~/.claude/rules/*.md` (new rules added; obsolete rules retired)
- `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/*` (stale entries deleted, conflicting entries updated)
- `docs/superpowers/specs/*.md` (DEPRECATED markers added to superseded specs; no deletions without explicit user approval)
- `docs/profit_path_debt_log.md` (high-priority consolidation findings appended)

**Created post-execution (Phase 7):**
- `~/.claude/commands/foundational-docs-audit.md` (slash command extracted from Track 1, if Track 1 audit prompt proves generalizable)

---

## Phase 1: Track 1 Pre-Flight + Confirmation

**Files:**
- Verify: `/tmp/kalshi-bot-foundational-docs-audit-prompt.md` (already drafted)
- Verify: branch `housekeeping/foundational-docs` exists at `main @ e05b9e4`

- [ ] **Step 1: Confirm audit prompt artifact exists**

Run: `ls -la /tmp/kalshi-bot-foundational-docs-audit-prompt.md`
Expected: file present, ~10-12 KB

- [ ] **Step 2: Confirm branch state**

Run: `git status -sb && git log --oneline -3`
Expected: `## housekeeping/foundational-docs`, HEAD is `e05b9e4 docs: phase-2 closure — debt-log + SUMMARY`

- [ ] **Step 3: Confirm baseline test count**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: `1626 passed, 2 skipped, 116 xfailed`

- [ ] **Step 4: User gate**

Stop. Surface state to user. User confirms readiness to proceed to Phase 2 or modifies prompt.

---

## Phase 2: Track 1 Execution — Foundational Docs Audit

**Files:**
- Read: `/tmp/kalshi-bot-foundational-docs-audit-prompt.md` (the executable prompt)
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/drift-detection.md`
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/reference-integrity.md`
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/general-quality.md`
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/gap-analysis.md`
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/context-cost.md`
- Create: `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/SUMMARY.md`

- [ ] **Step 1: Open fresh Claude conversation in kalshi-bot project**

User action. Cleaner context for the multi-agent dispatch.

- [ ] **Step 2: Verify branch in fresh session**

Run: `git status -sb`
Expected: `## housekeeping/foundational-docs`

- [ ] **Step 3: Paste audit prompt and execute**

Paste contents of `/tmp/kalshi-bot-foundational-docs-audit-prompt.md`. Receiving Claude:
- Dispatches 2 subagents in parallel (`comment-analyzer` for drift detection, `code-explorer` for reference integrity)
- Sequentially runs 3 main-thread skills (`claude-md-improver`, `rules-distill`, `context-budget`)
- Synthesizes into `SUMMARY.md`
- Stops at literal line: "Foundational docs audit complete. Review SUMMARY.md before approving any doc edits or new rule additions."

- [ ] **Step 4: Verify all 6 output files exist**

Run: `ls docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/`
Expected: 6 `.md` files (5 lens reports + SUMMARY)

- [ ] **Step 5: Verify test count unchanged (read-only invariant)**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: `1626 passed, 2 skipped, 116 xfailed` (audit is read-only, no test impact)

- [ ] **Step 6: Commit Track 1 audit reports**

```bash
git add docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/
git commit -m "$(cat <<'EOF'
docs: foundational docs audit (Track 1, read-only)

Multi-lens audit of CLAUDE.md, rules, AGENTS.md, RTK.md, README, plus
docs/superpowers/plans+specs as secondary scope.

5 lenses: drift detection, reference integrity, general quality,
gap analysis, context cost. Synthesis in SUMMARY.md.

Findings only — no doc edits applied. Phase 3 reviews + decides
remediation.

Part of guidance-consolidation-initiative (Track 1 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate — review SUMMARY.md**

Stop. User reads `docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/SUMMARY.md`. User decides:
- Approve findings → proceed to Phase 3
- Reject specific findings → annotate in SUMMARY before Phase 3
- Need clarification → ask Claude to re-dispatch a specific lens

---

## Phase 3: Track 1 Remediation Decisions

This is a planning-and-edit phase. No subagent dispatch. Main-thread reviews findings + applies approved edits. One commit per logical group of findings.

**Files (depend on Phase 2 SUMMARY findings):**
- Modify: `<PROJECT>/CLAUDE.md` (if drift findings approved)
- Modify: `~/.claude/CLAUDE.md` (if global drift)
- Modify: `~/.claude/rules/*.md` (if new rules approved from gap-analysis)
- Modify: `docs/superpowers/specs/*.md` (DEPRECATED markers if SUPERSEDED status confirmed)
- Modify: `docs/profit_path_debt_log.md` (high-priority findings appended)
- Create: `~/.claude/rules/<new-rule>.md` (if rules-distill candidates approved)

- [ ] **Step 1: Walk SUMMARY.md Open Questions one by one**

For each Open Question, present to user, receive decision, log in a session-local notes file at `/tmp/track-1-decisions.md`.

- [ ] **Step 2: Walk "New Rules to Consider" section**

For each candidate from rules-distill, present Trigger/Action draft, receive decision (accept / modify / reject), log in `/tmp/track-1-decisions.md`.

- [ ] **Step 3: Apply approved edits — Drift fixes**

For each approved drift finding:
- Read the affected file
- Apply Edit per finding
- Run `.venv/bin/pytest -q --tb=line` after each batch (if any code-adjacent file touched)
- Verify test count: 1626 passed minimum

- [ ] **Step 4: Apply approved edits — Reference fixes**

For each approved broken-reference finding:
- Read the affected doc
- Apply Edit to fix or remove reference
- No test impact (doc-only)

- [ ] **Step 5: Apply approved edits — New rules**

For each approved rules-distill candidate:
- Create new file at `~/.claude/rules/<rule-name>.md` with frontmatter-free Trigger/Action body (per `documentation_format.md`)
- Verify file is auto-loaded by reading global CLAUDE.md `See ~/.claude/rules/...` references and confirming the new rule is implicitly included by the loader

- [ ] **Step 6: Apply approved edits — Spec deprecations**

For each spec marked SUPERSEDED in drift detection:
- Add `> **DEPRECATED:** Superseded by <reference>. Retained for historical context.` as the second line of the spec file
- Do NOT delete spec files without explicit user approval

- [ ] **Step 7: Update debt log**

Append a section to `docs/profit_path_debt_log.md` listing high-priority Track 1 findings as new entries (severity P0/P1 only). Use the existing format. Increment the header's "Total Items" counter.

- [ ] **Step 8: Run final test pass**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: `1626 passed, 2 skipped, 116 xfailed` (or higher if new tests landed; never lower)

- [ ] **Step 9: Commit Track 1 remediation**

```bash
git add <list of explicit files modified>
git commit -m "$(cat <<'EOF'
docs: foundational docs audit Track 1 remediation

Applied approved findings from docs/housekeeping/<TODAY-UTC>/foundational-docs-audit/SUMMARY.md.

[List specific changes — agent fills in based on actual edits applied]

Test delta: [unchanged | +N if new rules added tests]

Part of guidance-consolidation-initiative (Track 1 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: Push branch**

```bash
git push -u origin housekeeping/foundational-docs
```

- [ ] **Step 11: User gate — merge decision**

Stop. User decides:
- Direct merge to main (solo project, fast-forward)
- Open MR for review
- Cherry-pick specific commits
- Hold for batch with Track 2

---

## Phase 4: Track 2 Branch + Pre-Flight

**Files:**
- Verify: working tree clean, on main
- Create: branch `housekeeping/guidance-consolidation` off `main` (post-Track-1-merge)

- [ ] **Step 1: Confirm Track 1 merged or held**

Run: `git checkout main && git status -sb && git log --oneline -5`
Expected: clean tree on main; latest commit reflects Track 1 outcome (merged or pending depending on Phase 3 Step 11 decision).

- [ ] **Step 2: Create Track 2 branch**

Run: `git checkout -b housekeeping/guidance-consolidation`

- [ ] **Step 3: Capture baseline test count**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Record output to `/tmp/track-2-baseline.txt`. Track 2 must preserve this count.

- [ ] **Step 4: User gate**

Stop. Confirm readiness to proceed to Phase 5 (full repo-wide discovery).

---

## Phase 5: Track 2 — Phase A Discovery (Read-Only)

**Files:**
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-discovery/inventory.md`
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-discovery/classification.md`
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-discovery/auto-memory-extract.md`

This phase dispatches 3 subagents in parallel via the Agent tool. Each writes to a known location. No mutations.

- [ ] **Step 1: Dispatch Agent 1 — `code-explorer` for inventory**

Agent prompt (verbatim):
```
You are running Phase A — Discovery for the guidance-consolidation initiative.

Task: Build a complete inventory of every `.md` file across these surfaces:
- <PROJECT_ROOT>/ (recursive, all subdirs)
- ~/.claude/ (recursive, but EXCLUDE ~/.claude/plugins/cache/ which is plugin-managed)
- ~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/ (auto-memory)

For each file, capture:
- Absolute path
- Size (bytes)
- Line count
- Last modified date
- First-line content (often the title)

Output a markdown table sorted by path. Group by top-level directory.

Cap inventory at 500 entries. If more found, surface a count and ask user before continuing.

Output: docs/housekeeping/<TODAY-UTC>/guidance-discovery/inventory.md
```

- [ ] **Step 2: Dispatch Agent 2 — `comment-analyzer` for classification**

Agent prompt (verbatim):
```
You are running Phase A — Classification for the guidance-consolidation initiative.

Read the inventory produced by Agent 1 at docs/housekeeping/<TODAY-UTC>/guidance-discovery/inventory.md.

For each file in the inventory, classify by primary content type:
- RULE — Trigger/Action style mechanical guardrail (rules/*.md)
- GOTCHA — narrative hidden-constraint documentation (CLAUDE.md gotcha sections)
- WORKING_STYLE — preferences and conventions (CLAUDE.md working style sections)
- DESIGN_SPEC — design intent at point in time (docs/superpowers/specs/)
- PLAN — implementation plan (docs/superpowers/plans/)
- ADR — architecture decision record
- RUNBOOK — operational procedure
- README — repo or subdir README
- LEDGER — debt log, cycle ledger, charter
- AUDIT_REPORT — housekeeping/audit findings
- SCRATCH — informal notes, dated, non-authoritative
- MIXED — file contains 2+ types (flag for splitting)

Read enough of each file to classify confidently. For MIXED files, list which types coexist.

Output a markdown table: path | classification | confidence (high/medium/low) | summary (3-line max).

Output: docs/housekeeping/<TODAY-UTC>/guidance-discovery/classification.md
```

- [ ] **Step 3: Dispatch Agent 3 — auto-memory extract**

Agent prompt (verbatim):
```
You are running Phase A — Auto-Memory Extract for the guidance-consolidation initiative.

Auto-memory lives at ~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/.

The MEMORY.md index at that path lists individual memory files. For each entry:
- Read the memory file
- Extract: name, type (user/feedback/project/reference), description, body
- For feedback/project entries: extract the **Why** and **How to apply** structured fields if present

For each memory entry, classify provisional status vs current:
- ACTIVE — still applies, consistent with current CLAUDE.md/rules
- STALE — references a file/state that no longer exists
- CONFLICTS — directly contradicts a CLAUDE.md gotcha or rule
- DUPLICATE — same content lives elsewhere (cite the elsewhere location)

Output a markdown table: name | type | status | conflict_with (if any) | content_summary

Output: docs/housekeeping/<TODAY-UTC>/guidance-discovery/auto-memory-extract.md
```

- [ ] **Step 4: Wait for all 3 agents to return**

All 3 dispatched in parallel via single message with multiple Agent tool calls. Wait for all to return before proceeding.

- [ ] **Step 5: Verify all 3 output files exist**

Run: `ls docs/housekeeping/<TODAY-UTC>/guidance-discovery/`
Expected: 3 `.md` files (inventory, classification, auto-memory-extract)

- [ ] **Step 6: Commit Phase A artifacts**

```bash
git add docs/housekeeping/<TODAY-UTC>/guidance-discovery/
git commit -m "$(cat <<'EOF'
docs: guidance discovery (Track 2, Phase A)

Repo + ~/.claude + auto-memory inventory and classification.
Three parallel subagents:
- code-explorer: file inventory
- comment-analyzer: classification by content type
- general-purpose: auto-memory extraction

Read-only. No edits.

Part of guidance-consolidation-initiative (Track 2 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate — review discovery output**

Stop. User reviews 3 files. Confirms:
- Inventory feels complete (no surprising omissions)
- Classification is sane (high-confidence majority)
- Auto-memory extract surfaces no shocking conflicts requiring immediate handling

If discovery surfaces 200+ files: discuss whether to narrow Phase B scope before proceeding.

---

## Phase 6: Track 2 — Phase B Conflict Detection (Read-Only)

**Files:**
- Read: `docs/housekeeping/<TODAY-UTC>/guidance-discovery/*.md` (Phase A outputs)
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-conflicts/intra-cluster-conflicts.md`
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-conflicts/code-vs-doc-drift.md`
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-conflicts/SUMMARY.md`

- [ ] **Step 1: Dispatch Agent 4 — intra-cluster conflicts**

Agent prompt (verbatim):
```
You are running Phase B — Intra-Cluster Conflict Detection.

Read Phase A outputs:
- docs/housekeeping/<TODAY-UTC>/guidance-discovery/classification.md
- docs/housekeeping/<TODAY-UTC>/guidance-discovery/inventory.md
- docs/housekeeping/<TODAY-UTC>/guidance-discovery/auto-memory-extract.md

For each pair of guidance items in the same logical topic cluster, check for contradictions. Topic clusters to focus on (extract more from the data):
- Kalshi API patterns (signing, market status, rate limits)
- Governance LLM (qwen3 thinking, anchor_rate, prompts.py)
- Signal analysis (LLM/keyword separation, JSON extraction, blending)
- Infrastructure (Python version, dependencies, platform constraints)
- Config / env (variable names, dynamic sizing, allowlists)
- Tracking system (debt log path, parallel logs prohibition)
- Domain constraints (/analysis, /trading, /tasks, /feeds, /governance)

For each conflict found, output:
- Cluster name
- Conflicting source A (file:line)
- Conflicting source B (file:line)
- Statement A
- Statement B
- Severity (P0=load-bearing-discrepancy / P1=meaningful / P2=minor / P3=stylistic)
- Resolution proposal (per trust hierarchy in plan header)

Cap at top-30 conflicts (drown the signal beyond that). If more found, surface a count.

Output: docs/housekeeping/<TODAY-UTC>/guidance-conflicts/intra-cluster-conflicts.md
```

- [ ] **Step 2: Dispatch Agent 5 — code-vs-doc drift**

Agent prompt (verbatim):
```
You are running Phase B — Code-vs-Doc Drift Detection.

Read Phase A classification.md. Filter to RULE, GOTCHA, WORKING_STYLE, DESIGN_SPEC entries.

For each guidance item that makes a verifiable claim about code (e.g., "function X at file Y line Z does behavior W"):
- Verify the claim against current code (use Grep/Read on the project)
- Tag MATCH / DRIFT / BROKEN / UNVERIFIABLE

For DRIFT and BROKEN findings, output:
- Source doc + claim location
- Current code state with file:line
- Severity (P0/P1/P2/P3 per the same convention as Agent 4)
- Resolution proposal

Cross-reference Track 1's drift-detection.md if it exists — DO NOT duplicate findings; explicitly cite when a finding overlaps and explain what's new.

Cap at top-30. Surface if more.

Output: docs/housekeeping/<TODAY-UTC>/guidance-conflicts/code-vs-doc-drift.md
```

- [ ] **Step 3: Dispatch in parallel**

Single message with 2 Agent tool calls. Wait for both.

- [ ] **Step 4: Synthesize Phase B SUMMARY (main thread)**

Read both Phase B reports. Produce `docs/housekeeping/<TODAY-UTC>/guidance-conflicts/SUMMARY.md`:
- P0 conflicts (must resolve before proceeding to Phase 7 consolidation)
- P1 conflicts (resolve in Phase 7)
- P2/P3 (defer or batch)
- "Open Questions" — conflicts where trust hierarchy doesn't resolve cleanly

End SUMMARY.md with literal line: "Phase B conflict detection complete. Review SUMMARY.md before approving Phase C consolidation plan."

- [ ] **Step 5: Verify all 3 output files exist**

Run: `ls docs/housekeeping/<TODAY-UTC>/guidance-conflicts/`
Expected: 3 `.md` files

- [ ] **Step 6: Commit Phase B artifacts**

```bash
git add docs/housekeeping/<TODAY-UTC>/guidance-conflicts/
git commit -m "$(cat <<'EOF'
docs: guidance conflicts detection (Track 2, Phase B)

Two parallel subagents: intra-cluster contradictions (Agent 4)
and code-vs-doc drift (Agent 5). Synthesis in SUMMARY.md.

Read-only. No edits.

Part of guidance-consolidation-initiative (Track 2 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate — review conflicts**

Stop. User reads `SUMMARY.md`. Decides which P0/P1 conflicts to resolve in Phase 7. Open Questions get user judgment now (logged in `/tmp/track-2-decisions.md`).

---

## Phase 7: Track 2 — Phase C Consolidation Plan (Read-Only)

**Files:**
- Read: all Phase A and Phase B artifacts
- Create: `docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md`

- [ ] **Step 1: Dispatch `code-architect` agent for plan synthesis**

Agent prompt (verbatim):
```
You are running Phase C — Consolidation Plan for the guidance-consolidation initiative.

Inputs:
- docs/housekeeping/<TODAY-UTC>/guidance-discovery/classification.md
- docs/housekeeping/<TODAY-UTC>/guidance-conflicts/SUMMARY.md
- /tmp/track-2-decisions.md (user judgments on Open Questions)
- ~/.claude/rules/documentation_format.md (canonical format-by-purpose rule)

Task: For every guidance item in the inventory, propose exactly one canonical destination per the trust hierarchy declared in the plan header.

Destination locations (canonical):
- ~/.claude/rules/<topic>.md — for mechanical Trigger/Action rules (project-portable)
- <PROJECT>/CLAUDE.md Critical Gotchas — for project-specific narrative gotchas
- <PROJECT>/CLAUDE.md Working Style — for project-level preferences
- ~/.claude/CLAUDE.md — for global preferences
- docs/superpowers/specs/<existing> — design specs stay where they are unless SUPERSEDED
- ~/.claude/projects/.../memory/ — auto-memory entries (FEEDBACK type only)

For every guidance item, output an entry:

| Item | Source | Action | Target | Rationale |

Where Action is one of:
- KEEP — already in canonical location
- MOVE — needs relocation (specify exact target)
- MERGE — duplicate; merge into existing canonical, then delete source
- DELETE — stale, superseded, or scratch
- DEPRECATE — add deprecation marker, keep file for history (specs only)
- SPLIT — MIXED-type file; split into N targets

Output: docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md

Final section: "Execution Batches" — group items by safe-to-execute-together cluster (e.g., "all rules additions", "all CLAUDE.md gotcha relocations", "all auto-memory cleanups"). Phase 8 executes one batch at a time with user approval per batch.

Length cap: ~200 entries max. If more found, escalate to user.
```

- [ ] **Step 2: Verify output**

Run: `ls docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md && wc -l docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md`
Expected: file present, reasonable line count

- [ ] **Step 3: Commit Phase C artifact**

```bash
git add docs/housekeeping/<TODAY-UTC>/guidance-consolidation-plan.md
git commit -m "$(cat <<'EOF'
docs: guidance consolidation plan (Track 2, Phase C)

Per-item action plan derived from Phases A+B. Each guidance item
has exactly one proposed canonical destination. Items grouped
into execution batches for Phase 8 review.

Read-only. No edits applied.

Part of guidance-consolidation-initiative (Track 2 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: User gate — approve plan**

Stop. User reads the consolidation plan. Decides:
- Approve all batches → proceed to Phase 8
- Approve subset of batches → annotate plan; Phase 8 only executes approved
- Reject plan → restart Phase C with different constraints
- Hold → defer Phase 8 indefinitely (plan stays as a record)

---

## Phase 8: Track 2 — Phase D Execution (Writes, Per-Batch User-Gated)

This phase mutates files. One commit per execution batch. User approves each batch BEFORE execution, not just before commit.

**Files (per batch — exact list lives in consolidation-plan.md):**
- Modify: target files per batch
- Delete: source files marked DELETE (only after user explicit confirmation per file)
- Create: new files per MOVE/SPLIT actions

For each batch in `guidance-consolidation-plan.md` § Execution Batches:

- [ ] **Step 1: Read the batch from the plan**

Reference the specific batch by name. List items, source paths, target paths, actions.

- [ ] **Step 2: Present batch diff preview to user**

Show: what gets created, what gets modified, what gets deleted, what gets merged. Wait for explicit user approval ("execute batch X").

- [ ] **Step 3: Apply batch edits**

For each item in batch:
- KEEP: no-op
- MOVE: read source, Write to target, delete source (use `git mv` semantics: stage delete + new file in same commit so git tracks rename)
- MERGE: read source + target, Write merged content to target, delete source
- DELETE: delete source file
- DEPRECATE: Edit source to add deprecation marker
- SPLIT: read source, Write multiple target files, delete source

- [ ] **Step 4: Update cross-references**

Any moved/merged item may be cited by other docs. Run grep across `docs/`, `~/.claude/`, and project root for the old path. Update each citing reference to the new path.

Run: `grep -rn "<old-path>" docs/ ~/.claude/ <PROJECT_ROOT>/CLAUDE.md`
Expected: zero matches after updates

- [ ] **Step 5: Run test suite**

Run: `.venv/bin/pytest -q --tb=line 2>&1 | tail -3`
Expected: pass count >= baseline (1626) — no regressions from doc moves
If a doc-rename broke a test (unlikely but possible if test reads docs): fix the test reference, re-run.

- [ ] **Step 6: Commit batch**

```bash
git add <list of explicit files modified/created/deleted in this batch>
git commit -m "$(cat <<'EOF'
docs(consolidation): execute batch <batch-name> (Track 2, Phase D)

[Per-item summary — agent fills in based on actual actions]

Cross-references updated: [N files]
Test count: unchanged (1626 passed)

Part of guidance-consolidation-initiative (Track 2 of 2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: User gate — proceed to next batch?**

Stop. User reviews diff via `git show HEAD`. Decides:
- Approve, proceed to next batch
- Revert this commit, fix issue, re-approach batch
- Pause execution; resume later

Repeat Steps 1-7 for each remaining batch.

---

## Phase 9: Closure + Slash Command Extraction

**Files:**
- Modify: `docs/profit_path_debt_log.md` (closure entry)
- Create: `~/.claude/commands/foundational-docs-audit.md` (only if Track 1 audit prompt proves generalizable)

- [ ] **Step 1: Update debt log**

Append closure section noting:
- Total guidance items consolidated
- Files moved / merged / deleted / deprecated counts
- Test impact (none expected)
- Reference to all Phase artifacts (single line per phase)

- [ ] **Step 2: Decide on slash command extraction**

Did the Track 1 audit prompt prove worth saving as a slash command?
- Yes (audit findings useful, prompt portable across projects): scrub project-specific tokens (similar process to `/housekeeping-audit`), install at `~/.claude/commands/foundational-docs-audit.md`
- No (audit findings minor or prompt too project-specific): skip; the prompt stays in the housekeeping dir as a one-off

- [ ] **Step 3: Track 2 prompts/plans NOT saved as slash commands**

By design. Track 2 is a deep, project-specific consolidation effort. Findings drive future audits, but the consolidation itself isn't a recurring workflow — it's a one-time reset. (Same logic that made Phase 2 + Phase 3 prompts not durable in the prior housekeeping initiative.)

- [ ] **Step 4: Final commit**

```bash
git add docs/profit_path_debt_log.md [+~/.claude/commands/... if extracted]
git commit -m "$(cat <<'EOF'
docs: guidance-consolidation initiative closure

Initiative complete. Track 1 (foundational docs audit) and Track 2
(repo-wide guidance consolidation) both landed. Debt log updated.

[If slash command extracted: note path]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push branch**

```bash
git push -u origin housekeeping/guidance-consolidation
```

- [ ] **Step 6: User gate — merge decision**

Stop. User decides: merge to main / open MR / hold for review.

---

## Estimated Cost / Time

| Phase | Mechanism | Cost | Wall time |
|---|---|---|---|
| 1 | Pre-flight (main thread) | <$0.50 | 2 min |
| 2 | Track 1 audit (5 lenses + synthesis) | $4-6 | 25-40 min |
| 3 | Track 1 remediation (main thread edits) | $1-3 | 10-30 min (depends on findings count) |
| 4 | Track 2 pre-flight | <$0.50 | 2 min |
| 5 | Track 2 Phase A (3 parallel agents) | $3-5 | 15-25 min |
| 6 | Track 2 Phase B (2 parallel agents + synthesis) | $3-5 | 15-25 min |
| 7 | Track 2 Phase C (1 agent) | $2-4 | 10-15 min |
| 8 | Track 2 Phase D (per-batch execution) | $5-15 | 30-90 min (depends on batch count) |
| 9 | Closure | <$1 | 5-10 min |
| **Total** | | **~$20-40** | **~2-4 hours active execution time, plus user review pauses** |

User-attended time is much shorter — most phases run agent dispatch and return; user only engages at gates.

---

## Risk Mitigations

- **Read-only-first.** Phases 1-7 are entirely read-only. Only Phase 8 mutates. Even Phase 8 is per-batch user-gated.
- **Branch isolation.** Track 1 and Track 2 each on dedicated branches. Either can be abandoned without polluting main.
- **Test invariant.** Test count (1626) is the canary. Any phase that drops it below 1626 must fix before proceeding.
- **Cross-reference grep.** Phase 8 Step 4 prevents broken-pointer regressions from doc moves.
- **Trust hierarchy declared up front.** Conflict resolution isn't ad hoc — a deterministic ranking governs.
- **Caps on findings.** Each agent's output capped (top-10, top-30, top-200) to prevent unreviewable dumps.
- **Auto-memory inclusion.** Phase 5 explicitly extracts auto-memory entries. Conflicts with CLAUDE.md surface in Phase 6 Agent 4.

---

## Self-Review Notes

(Per superpowers:writing-plans skill)

- **Spec coverage:** All sections of user's request addressed: foundational audit (Track 1), repo-wide discovery (Phase 5), conflict detection (Phase 6) including auto-memory, consolidation plan (Phase 7), gated execution (Phase 8), closure with slash command extraction (Phase 9).
- **Placeholder scan:** Concrete agent prompts in every dispatch step. Trust hierarchy explicit. File paths verbatim. No "TBD" or "implement later." Some `<TODAY-UTC>` placeholders are deliberate — receiving Claude resolves at run time.
- **Type consistency:** Phase numbering consistent. Branch names consistent. Commit message convention consistent (HEREDOC + Co-Authored-By).
- **Identified gap:** Phase 8 doesn't specify what happens if a batch's cross-reference grep finds 50+ matches. Implicit answer: agent walks each one. If user wants different treatment (e.g., skip batches with >20 references), should declare that constraint before Phase 8.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-guidance-consolidation-initiative.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per phase, review between phases, fast iteration. Best fit for the long-running multi-phase shape.

2. **Inline Execution** — Execute phases in this session using executing-plans, batch execution with checkpoints. Slower for this scope but keeps everything in one transcript.

Which approach?
