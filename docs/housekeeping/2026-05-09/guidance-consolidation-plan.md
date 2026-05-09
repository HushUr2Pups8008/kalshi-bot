# Phase 7: Guidance Consolidation Plan

**Generated:** 2026-05-09  
**Phase:** 7 of 8 (action planning)  
**Input:** Phase 6 SUMMARY.md — 33 findings (30 intra-cluster + 3 code-vs-doc drift)  
**Branch:** `housekeeping/guidance-consolidation`  
**Author:** Phase 7 Architect Agent

---

## Trust Hierarchy (reference)

| Layer | Source |
|-------|--------|
| 1 | `~/.claude/rules/*.md` |
| 2 | Project `CLAUDE.md` Critical Gotchas |
| 3 | Project `CLAUDE.md` Working Style |
| 4 | `~/.claude/CLAUDE.md` |
| 5 | Auto-memory entries (`~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/`) |
| 6 | `docs/superpowers/specs/` |
| 7 | Scratch/loose `.md`, inline code comments, audit docs |

---

## Verb Glossary

| Verb | Meaning |
|------|---------|
| KEEP | No action — current state is correct |
| MOVE | Relocate content from source to destination; update cross-references |
| MERGE | Combine two redundant items into one canonical copy |
| DELETE | Remove entirely; verify no live callers or cross-references first |
| DEPRECATE | Retain but add deprecation banner; do not enforce going forward |
| SPLIT | Separate mixed content into two files |
| REWRITE | Edit in place — content stays, wording changes |
| ANNOTATE | Add a comment or label without changing content |

---

## Plan Summary Table

| # | Finding | Verb | Batch | File(s) changed |
|---|---------|------|-------|-----------------|
| 2 | Release Versioning T/A format in project CLAUDE.md | KEEP (resolved by #3 Trigger 4 rewrite) | 1 | — |
| 3 | `documentation_format.md` Trigger 4 vs Trigger 5 self-contradiction | REWRITE | 1 | `~/.claude/rules/documentation_format.md` |
| 1 | `windows_local.md` NSSM rule (deprecated Windows runtime) | DELETE (Option A — file deleted) | 2 | `~/.claude/rules/windows_local.md` |
| 14 | `windows_local.md` E: drive rule (deprecated Windows runtime) | DELETE (Option A — file deleted) | 2 | `~/.claude/rules/windows_local.md` |
| 28 | Same as #14 — E: drive duplicate angle | KEEP (covered by #14) | 2 | — |
| (post-state) | ASCII log strings + UTF-8 encoding rules retained | MOVE | 2 | `~/.claude/rules/windows_local.md` → `~/.claude/rules/portability.md` |
| (cross-ref) | `~/.claude/project/AGENTS.md:17` line referencing windows_local | DELETE (line removed) | 2 | `~/.claude/project/AGENTS.md` |
| 4 | `analysis/__init__.py:19` "$50 hard cap" comment | REWRITE | 3 | `analysis/__init__.py` |
| 5 | `analysis/evidence_scorer.py` "trigrams" docstring | REWRITE | 3 | `analysis/evidence_scorer.py` |
| 6 | `analysis/kelly.py:159` "rounds down to stay within budget" | REWRITE | 3 | `analysis/kelly.py` |
| 7 | `analysis/fade_signal.py:10-13` "WebSocket-based replacement" | REWRITE | 3 | `analysis/fade_signal.py` |
| 8 | `general-quality.md` G-7 stale "silent on stats" claim | ANNOTATE | 4 | `docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` |
| 17 | `gap-analysis.md` C-1..C-4 listed as open proposals | ANNOTATE | 4 | `docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md` |
| 19 | `reference-integrity.md` RTK.md:29 flagged BROKEN | ANNOTATE | 4 | `docs/housekeeping/2026-05-09/foundational-docs-audit/reference-integrity.md` |
| 21 | `domain_constraints.md` cycle-17C label "no path" | ANNOTATE | 4 | `docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` |
| 30 | `general-quality.md` G-6 "documentation_format.md not referenced" | ANNOTATE | 4 | `docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md` |
| 10 | Working Style verbatim duplication (4 of 5 bullets) | DELETE | 5 | Project `CLAUDE.md` |
| 11 | Bug-Fixing Preference exact duplication | DELETE | 5 | Project `CLAUDE.md` |
| 12 | Continuous Improvement first bullet duplication | DELETE | 5 | Project `CLAUDE.md` |
| 13 | Rule cross-references 3 of 4 duplicated | DELETE | 5 | Project `CLAUDE.md` |
| 18 | Project `AGENTS.md` step 4 says `project/AGENTS.md` | REWRITE | 5 | `/Users/jacobparenti/vscode/kalshi-bot/AGENTS.md` |
| 24 | `~/.claude/project/AGENTS.md` omits `/governance` | REWRITE | 5 | `~/.claude/project/AGENTS.md` |
| 25+drift-#2 | `executor.py:218` line-number pin + misdirection | REWRITE | 5 | Project `CLAUDE.md` |
| drift-#1 | `resolve_market()` called "thin wrapper" understates calibration role | REWRITE | 5 | Project `CLAUDE.md` |
| 9+23 | Auto-memory stale P&L metrics + FUTURE tag on existing doc | REWRITE | 6 | `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_edge_priority_over_deploy_safety.md` |
| 15 | `signal_analyzer.py:547-552` stale "Revert if 12h re-run" comment | REWRITE | 6 | `analysis/signal_analyzer.py` |
| 16 | `signal_analyzer.py:37` "Cycle-15B diagnostics" label | REWRITE | 6 | `analysis/signal_analyzer.py` |
| 22 | `regime_classifier.py:178` references nonexistent `lessons.md` | REWRITE | 6 | `analysis/regime_classifier.py` |
| 27 | README 9 dead links to `transfer/macbook_handoff_2026-05-01/` | DELETE | 6 | `README.md` |
| drift-#3 | MEMORY.md `p_yes_at_decision_time` symbol not in codebase | ANNOTATE | 6 | `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/MEMORY.md` |

---

## KEEP Table (no action items)

| # | Finding | Rationale |
|---|---------|-----------|
| 20 | `~/.claude/AGENTS.md` "project-root AGENTS.md" notation ambiguity | Wording is accurate; #18 fixes the project copy |
| 26 | Global CLAUDE.md Tooling Preferences hybrid format | Readable; format rule is a preference not a mandate for this content type |
| 28 | `windows_local.md` E: drive — duplicate of #14 | Action under #14; no separate edit needed |
| 29 | `feedback_market_implied_baseline.md` commit SHA citation | SHA stable in this repo; spirit-of-rule concern only; no regression risk |
| 31 | `~/.claude/project/AGENTS.md` architecture overlaps `domain_constraints.md` | Partial overlap with #24; cross-ref exists; action covered in #24 |
| 32 | Phase 1 M-4 `_normalize_pem()` audit carry-over | Already present in CLAUDE.md gotchas; no new action |
| 33 | Phase 1 D-2 `resolve_market()` naming carry-over | Cosmetic; covered by drift-#1 rewrite |
| 34 | Phase 1 D-3 "query all open trades" / in-memory Portfolio | Self-documenting comment in place; covered by #25+drift-#2 rewrite |
| 35 | Python 3.14 / aiohttp Windows gotcha — Windows deprecated | Harmless if Windows used again; accurate as written |
| 36 | Mac + Windows Reddit 403 gotcha — Windows half stale | Mac-only caution remains valid; harmless overage |

---

## Sequencing Rationale and Cross-Cluster Dependencies

```
Batch 1 (format rule) → must precede Batch 5 (CLAUDE.md dedup)
  Reason: Trigger 4 rewrite scopes what "mixed format" means before
  dedup decisions about CLAUDE.md content are made. Without Batch 1,
  Phase 8 agents lack a non-contradictory format rule to cite.

Batch 2 (Windows cleanup) → independent; can run in parallel with Batch 3
  Reason: edits are in ~/.claude/rules/windows_local.md only; no
  Python files touched; test invariant not relevant.

Batch 3 (code comment fixes) → independent; can run in parallel with Batch 2
  Reason: edits are docstrings/comments in analysis/ only; no logic
  changes; test invariant must be verified after Batch 3.

Batch 4 (stale audit annotation) → independent; can run anytime after Batch 1
  Reason: annotations in docs/housekeeping/ audit files only; no
  rule files or Python source touched.

Batch 5 (CLAUDE.md dedup) → must follow Batch 1
  Reason: dedup decisions reference the corrected format rule. Also
  requires Batch 2 to be complete before updating ~/.claude/project/AGENTS.md
  windows_local.md reference at line 17.

Batch 6 (cross-ref + label cleanup) → independent; can run after Batch 3
  Reason: signal_analyzer.py edits are comment-only; no interaction
  with Batch 3's analysis/ edits (different files).
```

**Recommended Phase 8 execution order:**
1. Batch 1 (unblocks Batch 5)
2. Batch 2 + Batch 3 in parallel (independent, no test conflict)
3. Batch 4 (independent, light)
4. Batch 5 (requires Batch 1 complete; requires Batch 2 complete for AGENTS.md update)
5. Batch 6 (signal_analyzer.py; independent of prior batches)

---

## Batch 1 — Layer-1 Format Rule Self-Contradiction

**Affects:** `~/.claude/rules/documentation_format.md`, project `CLAUDE.md`  
**Test invariant:** N/A (no Python source)  
**Rationale:** Batch 1 must execute first — every subsequent CLAUDE.md content decision depends on a non-contradictory format rule.

---

### Entry B1-1: Finding #3 — REWRITE `documentation_format.md` Trigger 4

**Finding:** Trigger 4 ("split mixed-format files") and Trigger 5 ("reject CLAUDE.md format findings") are internally contradictory for CLAUDE.md files that legitimately contain both narrative gotchas and mechanical rules.

**Action:** REWRITE Trigger 4 to scope it to non-CLAUDE.md files. The rewrite adds "non-CLAUDE.md" as a qualifier so Triggers 4 and 5 address different situations rather than contradicting.

**File:** `/Users/jacobparenti/.claude/rules/documentation_format.md`

**Before (Trigger 4):**
> Trigger: when a single file contains both mechanical rules and narrative gotchas.  
> Action: split it into two files. Do not mix formats within one file.

**After (Trigger 4):**
> Trigger: when a non-CLAUDE.md file contains both mechanical Trigger/Action rules and narrative gotchas.  
> Action: split it into two files. Do not mix formats within one file. (CLAUDE.md files intentionally use multiple formats by section; see Trigger 5.)

**Verification:** After edit, read the file and confirm Triggers 4 and 5 are non-contradictory. No grep needed (no DELETE or MOVE).

---

### Entry B1-2: Finding #2 — WITHDRAWN (superseded by B1-1)

**Revision (2026-05-08, post-Phase-7 user review):** B1-2 originally proposed MOVING the project `CLAUDE.md` "Release Versioning" section to `~/.claude/rules/release_versioning.md`. On user review, this was reclassified as KEEP. Rationale:

1. Content is project-specific (cites `scripts/sync_readme_version.py`, `.githooks/pre-commit:15-21`, `scripts/launchd_template_equivalence_audit.py`) — does not generalize to other projects.
2. Moving to global rule file pollutes every project's loaded context with kalshi-specific procedure.
3. Content is closer to runbook+gotcha than mechanical rule — belongs in CLAUDE.md.
4. B1-1's Trigger 4 rewrite already addresses Conflict #2 semantically: CLAUDE.md files are explicitly exempted from the "split mixed-format" rule. Trigger 5 (existing) covers the "reject the finding" clause for CLAUDE.md format mixes.

**Resulting disposition for Finding #2:** KEEP. The Release Versioning section stays in project `CLAUDE.md` as-is. After Trigger 4 rewrite, this is no longer a violation.

**No edit applied. No cross-references to update.**

---

## Batch 2 — Windows Runtime Deprecation Cleanup

**Affects:** `~/.claude/rules/windows_local.md` (deleted), `~/.claude/rules/portability.md` (extended), `~/.claude/project/AGENTS.md` (cross-ref removed)
**Test invariant:** N/A (no Python source)
**Rationale:** PLATFORMS.md declares Windows runtime deprecated. Three rules in `windows_local.md` are dead artifacts of the deprecated runtime. Two other rules (ASCII log strings, UTF-8 encoding) are cross-platform-applicable and were moved to `portability.md`.

**Revision (2026-05-08, post-Phase-7 user review):** OQ-2 resolved as 2a (delete log-rotation rule too — full Windows purge). Then user picked post-state Option A (move retained rules to portability.md, delete windows_local.md entirely). Result: cleaner separation, no orphan file. One live cross-ref at `~/.claude/project/AGENTS.md:17` removed in same batch.

---

### Entry B2-1: Finding #1 — DELETE NSSM rule from `windows_local.md`

**Finding:** `windows_local.md` Trigger "when installing packages for an NSSM-managed service" enforces a workflow for a deprecated Windows NSSM service. The service no longer exists on the primary runtime (macOS).

**Action:** DELETE the NSSM Trigger/Action bullet from `~/.claude/rules/windows_local.md`.

**Rule to delete:**
> Trigger: when installing packages for an NSSM-managed service.  
> Action: install them into the service virtual environment and verify imports there before restarting the service.

**Cross-ref grep (confirm no other callers):**
```bash
grep -r "NSSM\|nssm" /Users/jacobparenti/vscode/kalshi-bot --include="*.md" -l
grep -r "NSSM\|nssm" /Users/jacobparenti/.claude --include="*.md" -l
```

**Verification:** Read `windows_local.md` after deletion. Confirm NSSM rule is absent and remaining rules are intact.

---

### Entry B2-2: Finding #14 — DELETE E: drive rule from `windows_local.md`

**Finding:** `windows_local.md` Trigger "when writing files on the `E:` drive for this project" is a Windows-specific artifact. The E: drive does not exist on macOS.

**Action:** DELETE the E: drive Trigger/Action bullet from `~/.claude/rules/windows_local.md`.

**Rule to delete:**
> Trigger: when writing files on the `E:` drive for this project.  
> Action: verify the file is readable immediately after the write.

**Cross-ref grep (confirm no other callers):**
```bash
grep -r "E: drive\|E:\\\\" /Users/jacobparenti/vscode/kalshi-bot --include="*.md" -l
grep -r "E: drive\|E:\\\\" /Users/jacobparenti/.claude --include="*.md" -l
```

**Verification:** Read `windows_local.md` after deletion. Confirm the file retains: ASCII log strings rule, UTF-8 encoding rule, log rotation rule. Confirm E: drive rule and NSSM rule are both absent.

**Open question OQ-2:** `windows_local.md` currently retains 3 rules post-deletion: (1) ASCII log strings, (2) UTF-8 encoding, (3) log rotation ("avoid rename-based rollover on Windows"). Rules 1-2 have cross-platform value. Rule 3 is Windows-specific (references "other processes holding the file open" on Windows). Should rule 3 also be deleted (scope-consistent with the deprecation) or kept (defensive for any future Windows use)? Requires user decision before Phase 8 executes.

---

## Batch 3 — Code Comment Fixes

**Affects:** `analysis/__init__.py`, `analysis/evidence_scorer.py`, `analysis/kelly.py`, `analysis/fade_signal.py`  
**Test invariant:** 1652 passed, 2 skipped, 116 xfailed — must hold after this batch. These edits are comment/docstring text only; no logic changes.

---

### Entry B3-1: Finding #4 — REWRITE `analysis/__init__.py:19` `$50 hard cap` comment

**Finding:** `analysis/__init__.py` line 19 annotates `capped_dollars: float` with `# after $50 hard cap`. CLAUDE.md gotcha (Layer 2) says bet size is dynamic via `cfg.dynamic_max_bet(notional)` — hardcoded $50 is wrong.

**Action:** REWRITE the inline comment to reference the dynamic cap function.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/__init__.py`

**Before:** `capped_dollars: float  # after $50 hard cap`  
**After:** `capped_dollars: float  # after cfg.dynamic_max_bet(notional) dynamic cap`

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B3-2: Finding #5 — REWRITE `analysis/evidence_scorer.py` "trigrams" docstring

**Finding:** `analysis/evidence_scorer.py` line 48 docstring says "word trigrams" but `_NGRAM_SIZE = 2` computes bigrams.

**Action:** REWRITE the docstring to say "word bigrams."

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/evidence_scorer.py`

**Before:** `"""Jaccard similarity over word trigrams …"""`  
**After:** `"""Jaccard similarity over word bigrams …"""`

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B3-3: Finding #6 — REWRITE `analysis/kelly.py:159` budget comment

**Finding:** Comment says "Rounds down to stay within budget" but `max(1, int(...))` can return 1 even when 1 contract exceeds the budget.

**Action:** REWRITE the comment to accurately describe the edge case.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/kelly.py`

**Before:** `# Rounds down to stay within budget.`  
**After:** `# Rounds down via int(); enforces minimum of 1 contract even if 1 contract exceeds budget.`

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B3-4: Finding #7 — REWRITE `analysis/fade_signal.py:10-13` "WebSocket-based replacement" comment

**Finding:** `fade_signal.py` lines 10-13 say price fade is a "WebSocket-based replacement" implying tweet fade was retired. Both strategies run in parallel in `main.py`.

**Action:** REWRITE the comment block to remove "replacement" and accurately describe parallel operation.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/fade_signal.py`

**Guidance:** Replace "WebSocket-based replacement" with language indicating price fade is an additional strategy that runs in parallel with tweet fade. Remove "no Twitter dependency required" or clarify it means this strategy does not require Twitter (not that Twitter feed is gone).

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

## Batch 4 — Stale Audit Document Annotation

**Affects:** `docs/housekeeping/2026-05-09/foundational-docs-audit/` files  
**Test invariant:** N/A (no Python source, no rule files)  
**Rationale:** Five findings are stale audit notes whose target issues were resolved in Phase 3 or earlier. These audit documents are historical — annotate rather than delete to preserve the resolution trail.

---

### Entry B4-1: Finding #8 — ANNOTATE `general-quality.md` G-7

**Finding:** G-7 says `domain_constraints.md` is "silent on stats." Live rule already says "no stats/aggregation modules."

**Action:** ANNOTATE the G-7 entry in `general-quality.md` with a "FIXED IN PHASE 3" marker.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md`

**Annotation to add:** `<!-- RESOLVED: domain_constraints.md was updated before or concurrent with Phase 1 audit to include "no stats/aggregation modules." G-7 finding is stale. No action required. -->`

---

### Entry B4-2: Finding #17 — ANNOTATE `gap-analysis.md` C-1 through C-4

**Finding:** `gap-analysis.md` lists C-1..C-4 as "proposals requiring user approval." All four are live in rule files.

**Action:** ANNOTATE the C-1..C-4 header in `gap-analysis.md` with "ALREADY IMPLEMENTED" markers.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/gap-analysis.md`

**Annotation to add:** At top of C-1 through C-4 section: `<!-- RESOLVED: All four proposals (C-1 through C-4) are implemented in live rule files as of Phase 3. C-1: risk_review.md. C-2: documentation_format.md. C-3: portability.md. C-4: editing_safety.md. No action required. -->`

---

### Entry B4-3: Finding #19 — ANNOTATE `reference-integrity.md` RTK.md:29 finding

**Finding:** `reference-integrity.md` row 43 marks RTK.md:29 as BROKEN. Live RTK.md:29 reads "Run `rtk --help` for the full command reference." — fix was applied.

**Action:** ANNOTATE the row in `reference-integrity.md`.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/reference-integrity.md`

**Annotation:** Mark row 43 as `RESOLVED — RTK.md:29 was updated to "Run rtk --help for the full command reference." Circular self-reference removed.`

---

### Entry B4-4: Finding #21 — ANNOTATE `general-quality.md` F-4 cycle-17C path finding

**Finding:** F-4 says `domain_constraints.md` cycle-17C label has "no path." Live file includes the full path.

**Action:** ANNOTATE the F-4 entry in `general-quality.md`.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md`

**Annotation:** `<!-- RESOLVED: domain_constraints.md line 19 already contains the full path: "docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md". F-4 finding is stale. -->`

---

### Entry B4-5: Finding #30 — ANNOTATE `general-quality.md` G-6 documentation_format.md reference finding

**Finding:** G-6 says `documentation_format.md` is "not yet referenced from CLAUDE.md." Project CLAUDE.md line 25 already contains the cross-reference.

**Action:** ANNOTATE the G-6 entry in `general-quality.md`.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/docs/housekeeping/2026-05-09/foundational-docs-audit/general-quality.md`

**Annotation:** `<!-- RESOLVED: Project CLAUDE.md line 25 already references documentation_format.md. G-6 finding is stale. -->`

---

## Batch 5 — CLAUDE.md and AGENTS.md De-duplication

**Affects:** Project `CLAUDE.md`, project `AGENTS.md`, `~/.claude/project/AGENTS.md`  
**Depends on:** Batch 1 complete (format rule non-contradictory before CLAUDE.md edits)  
**Depends on:** Batch 2 complete (`windows_local.md` changes before AGENTS.md update referencing it)  
**Test invariant:** N/A (no Python source)

---

### Entry B5-1: Findings #10, #11, #12, #13 — DELETE verbatim-duplicate bullets from project CLAUDE.md

**Finding:** Project CLAUDE.md Working Style, Bug-Fixing Preference, Continuous Improvement, and rule cross-references sections contain verbatim copies of global `~/.claude/CLAUDE.md` content. Both files are loaded in context simultaneously via inheritance.

**Action:** DELETE verbatim-duplicate bullets from project CLAUDE.md. Retain only project-additive content.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md`

**Bullets to DELETE (verbatim duplicates of global CLAUDE.md):**

From Working Style (keep only the project-unique bullet):
- DELETE: "For non-trivial work, plan first and keep the user informed as scope changes."
- DELETE: "Prefer direct execution once the scope is clear."
- DELETE: "Prefer simple root-cause fixes over temporary patches."
- DELETE: "Use delegation only when the environment supports it and it clearly reduces risk or latency."
- DELETE: "Keep summaries concise and decision-oriented."
- KEEP: "Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly."

From Bug-Fixing Preference (entire section is duplicate):
- DELETE: "When given a bug report, diagnose it from concrete evidence such as logs, errors, and failing checks."
- DELETE: "Reduce user back-and-forth where the next safe step is clear."
- If section becomes empty after deletions, DELETE the section header too.

From Continuous Improvement (keep only project-unique bullet):
- DELETE: "After repeated correction on the same pattern, capture the lesson in the project's preferred tracking system if one exists."
- KEEP: "This project's unified tracking system is `docs/profit_path_debt_log.md`; do not create parallel macOS, logging, S4.5, or architecture debt logs."

From rule cross-references (keep only project-unique line):
- DELETE: "See `~/.claude/rules/planning.md` for planning rules."
- DELETE: "See `~/.claude/rules/validation.md` for validation rules."
- DELETE: "See `~/.claude/rules/git_workflow.md` for git workflow rules."
- KEEP: "See `~/.claude/rules/documentation_format.md` for documentation format rules."
- KEEP: Release versioning cross-reference added in B1-2.

**Cross-ref grep (confirm inheritance path loads global CLAUDE.md before project CLAUDE.md):**
```bash
# Verify global CLAUDE.md is loaded by checking both files appear in context
ls -la /Users/jacobparenti/.claude/CLAUDE.md
ls -la /Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md
```

**Verification:** Read project CLAUDE.md after edits. Confirm project-unique bullets retained. Confirm verbatim duplicates removed. Confirm no section headers left with zero bullets.

---

### Entry B5-2: Finding #18 — REWRITE project AGENTS.md step 4

**Finding:** Project `AGENTS.md` line 13 says "`project/AGENTS.md` and project-local rules" — the `project/` directory does not exist. Global AGENTS.md correctly says "project-root `AGENTS.md`."

**Action:** REWRITE line 13 of project AGENTS.md to match global AGENTS.md wording.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/AGENTS.md`

**Before:** `4. `project/AGENTS.md` and project-local rules: explicit project-specific additions or overrides`  
**After:** `4. project-root `AGENTS.md` (if present) and project-local rules: explicit project-specific additions or overrides`

**Verification:** Read project AGENTS.md. Confirm step 4 wording matches `~/.claude/AGENTS.md` step 4.

---

### Entry B5-3: Finding #24 — REWRITE `~/.claude/project/AGENTS.md` architecture list

**Finding:** `~/.claude/project/AGENTS.md` architecture list omits `/governance` (which has a domain constraint in `domain_constraints.md`) and retains a stale `windows_local.md` reference. After Batch 2, the windows_local.md reference may need updating.

**Action:** REWRITE `~/.claude/project/AGENTS.md` to add `/governance` to the architecture list and update the windows_local.md reference line to reflect that Windows-specific rules were removed.

**File:** `/Users/jacobparenti/.claude/project/AGENTS.md`

**Change 1:** Add `/governance` to the architecture boundaries list:
> `- /governance`: prompt editing, LLM decision authority, anchor_rate scaffolding

**Change 2:** Update the windows_local.md reference (line 17) to reflect Batch 2 deletions:
> See `rules/windows_local.md` for cross-platform file-writing and encoding safeguards. (Windows runtime is deprecated per PLATFORMS.md; NSSM and E:-drive rules removed in 2026-05-09 consolidation.)

**Verification:** Read `~/.claude/project/AGENTS.md` after edit. Confirm `/governance` is listed. Confirm windows_local.md reference is accurate.

---

### Entry B5-4: Findings #25 + drift-#2 — REWRITE project CLAUDE.md `executor.py:218` line-number pin

**Finding:** Project CLAUDE.md line 63 reads "See self-documenting comment at `executor.py:218`." Two problems: (1) line-number citations rot per documentation_format.md; (2) line 218 documents the data source (in-memory vs DB), not the "all open trades not just the most recent" rationale that CLAUDE.md says it documents.

**Action:** REWRITE the gotcha sentence to cite the function/class name and describe the behavior, not pin a line number.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md`

**Before:** "See self-documenting comment at `executor.py:218`."  
**After:** "See the multi-position guard comment block in `executor.py` above the `open_positions(ticker)` loop."

**Verification:** Read project CLAUDE.md. Confirm line-number pin is removed. Confirm behavior reference is accurate by reading `trading/executor.py` around the guard block.

---

### Entry B5-5: Drift finding #1 — REWRITE project CLAUDE.md `resolve_market()` description

**Finding:** Project CLAUDE.md says "the public `resolve_market()` is a thin wrapper." In reality, `resolve_market()` also emits `CALIBRATION_CHECK` events per lane per resolved trade, both to the structured trade log and to in-process `CalibrationTask`. The atomicity claim is still correct; "thin" understates the function's role.

**Action:** REWRITE the description to accurately characterize `resolve_market()`.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/CLAUDE.md`

**Before:** "(the public `resolve_market()` is a thin wrapper)"  
**After:** "(the public `resolve_market()` runs `_resolve_market_sync` via `asyncio.to_thread`, then emits `CALIBRATION_CHECK` events per lane — failures on the calibration path are independent of the DB transaction)"

**Verification:** Read project CLAUDE.md. Confirm updated description matches `trading/paper_trader.py` implementation at `resolve_market()`.

---

## Batch 6 — Cross-Reference and Label Cleanup

**Affects:** Auto-memory files, `analysis/signal_analyzer.py`, `analysis/regime_classifier.py`, `README.md`  
**Test invariant:** 1652 passed, 2 skipped, 116 xfailed — must hold for signal_analyzer.py and regime_classifier.py edits. Edits are comment-only.

---

### Entry B6-1: Findings #9 + #23 — REWRITE `feedback_edge_priority_over_deploy_safety.md`

**Finding:** Auto-memory file has two problems: (1) embeds 2026-05-06 P&L metrics (3 trades, -$7.50, etc.) as present-tense facts without a staleness caveat; (2) tags `docs/governance/edge-replay-cycle12-report.md` as "(FUTURE; Codex's Cycle-12 deliverable)" — the file exists on disk.

**Action:** REWRITE the file to add a staleness caveat to P&L metrics section and remove the FUTURE tag.

**File:** `/Users/jacobparenti/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_edge_priority_over_deploy_safety.md`

**Change 1:** Add staleness label to P&L metrics block:
> **[as of 2026-05-06 — stale; verify against current paper_trader state]**  
> 3 lifetime paper trades, 0 wins, -$7.50 P&L, ...

**Change 2:** Remove "(FUTURE; Codex's Cycle-12 deliverable)" from the `edge-replay-cycle12-report.md` reference. Replace with: `docs/governance/edge-replay-cycle12-report.md`

**Open question OQ-3:** Should the specific P&L numbers (3 trades, -$7.50, 89% SKIPPED) be deleted entirely since they are stale and can mislead strategy discussions? Or is the staleness-labeled historical record valuable? Stale metrics with a caveat are less harmful than stale metrics presented as current, but deletion removes the misleading data entirely. Requires user decision before Phase 8 executes.

**Verification:** Read the file after edit. Confirm staleness caveat present. Confirm FUTURE tag removed.

---

### Entry B6-2: Finding #15 — REWRITE `analysis/signal_analyzer.py:547-552` stale revert comment

**Finding:** `signal_analyzer.py` lines 547-552 contain a "Revert if 12h re-run shows no drop..." condition from P0.4 experiment. P0.4 decision is closed — the revert condition is a pending decision that has already been made.

**Action:** REWRITE the comment to remove the revert condition or note that P0.4 is closed.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/signal_analyzer.py`

**Guidance:** Replace the revert-condition comment with a note that P0.4 (v0.29.48) experiment is closed and the current behavior is the retained state.

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B6-3: Finding #16 — REWRITE `analysis/signal_analyzer.py:37` cycle label

**Finding:** `signal_analyzer.py` line 37 reads `"""Optional debug-only extraction trace hook for Cycle-15B diagnostics."""` — current cycle is 17C. The function may appear as abandoned debug scaffolding from a closed cycle.

**Action:** REWRITE the docstring to remove the cycle label or update it.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/signal_analyzer.py`

**Before:** `"""Optional debug-only extraction trace hook for Cycle-15B diagnostics."""`  
**After:** `"""Optional debug-only extraction trace hook (retained from Cycle-15B; still valid)."""`

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B6-4: Finding #22 — REWRITE `analysis/regime_classifier.py:178` dead `lessons.md` reference

**Finding:** `regime_classifier.py` near line 178 contains a comment "series_ticker can be empty … — see lessons.md" but `lessons.md` does not exist anywhere in the repository.

**Action:** REWRITE the comment to remove the dead reference or replace with a self-contained explanation.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/analysis/regime_classifier.py`

**Guidance:** Remove "see lessons.md" from the comment. If the note needs context, describe the behavior inline.

**Verification:** Run test suite. Confirm 1652 passed, 2 skipped, 116 xfailed.

---

### Entry B6-5: Finding #27 — DELETE dead links from README

**Finding:** `README.md` lines 243-258 contain 9 links to `transfer/macbook_handoff_2026-05-01/` paths. The directory was removed from git history 2026-05-02. README:219 explains the removal.

**Action:** DELETE the 9 dead links. The surrounding explanation prose at README:219 can remain.

**File:** `/Users/jacobparenti/vscode/kalshi-bot/README.md`

**Cross-ref grep (confirm no other references to the removed directory):**
```bash
grep -rn "transfer/macbook_handoff_2026-05-01" /Users/jacobparenti/vscode/kalshi-bot --include="*.md"
```

**Verification:** Read README lines 240-265 after edit. Confirm dead links removed. Confirm prose explanation at :219 intact.

---

### Entry B6-6: Drift finding #3 — ANNOTATE MEMORY.md `p_yes_at_decision_time` index entry

**Finding:** `MEMORY.md` index entry for `feedback_market_implied_baseline.md` uses symbol `p_yes_at_decision_time` — this symbol does not exist in the codebase. Live code uses `market_yes_price`. The math is accurate; only the symbol name is an alias not found in code.

**Action:** ANNOTATE the MEMORY.md index entry to note the symbol name difference.

**File:** `/Users/jacobparenti/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/MEMORY.md`

**Before:** `replay win-rate baseline is Σ p_yes_at_decision_time, not 50% coin-flip`  
**After:** `replay win-rate baseline is Σ market_yes_price/100 (called p_yes_at_decision_time in this doc), not 50% coin-flip`

**Verification:** Read MEMORY.md entry. Confirm codebase symbol `market_yes_price` is now cited.

---

## Phase 8 Execution Order

```
Step 1: Batch 1 (B1-1, B1-2)       — format rule + Release Versioning MOVE
Step 2: Batch 2 (B2-1, B2-2)       — can run in parallel with Batch 3
Step 2: Batch 3 (B3-1..B3-4)       — can run in parallel with Batch 2
Step 3: Batch 4 (B4-1..B4-5)       — independent; light; run after Step 1
Step 4: Batch 5 (B5-1..B5-5)       — requires Steps 1 and 2 complete
Step 5: Batch 6 (B6-1..B6-6)       — independent of Steps 3-4; run after Step 2
```

**After Batch 3 (Step 2) and Batch 6 (Step 5):** Run full test suite. Expected: 1652 passed, 2 skipped, 116 xfailed. Any deviation stops Phase 8.

---

## Open Questions for User (resolve before Phase 8)

**OQ-1 — Release Versioning destination (Batch 1, B1-2):**  
Append kalshi-bot project extension to global `~/.claude/rules/release_versioning.md`, or create a project-local rule file at `/Users/jacobparenti/vscode/kalshi-bot/.claude/rules/release_versioning_kalshi.md`? The global-append approach is simpler but adds project-specific content to a project-agnostic file.

**OQ-2 — Windows log rotation rule scope (Batch 2):**  
After deleting NSSM and E: drive rules, `windows_local.md` will retain the log rotation rule ("avoid rename-based rollover on Windows"). This rule is Windows-specific. Delete it (consistent with deprecation) or keep it (defensive for possible future Windows use)? Phase 6 did not flag it as a finding; it is an out-of-scope decision.

**OQ-3 — Auto-memory stale P&L metrics treatment (Batch 6, B6-1):**  
Add staleness caveat to the 2026-05-06 P&L metrics in `feedback_edge_priority_over_deploy_safety.md`, or delete the specific numbers entirely? Stale metrics with a caveat are less harmful than stale metrics as current truth, but deletion eliminates the misleading data.

---

## Entry Count

| Category | Count |
|----------|-------|
| Active entries (batches 1-6) | 27 |
| KEEP table entries | 10 |
| Total Phase 6 findings covered | 37 |
| Findings not in Phase 6 scope (OQ-2 log rotation) | 1 |
| Phase 6 cap (official) | 33 + 6 cut = 39 total inputs |
| Inputs with explicit disposition | 39 |

**Total entries in plan:** 27 active + 10 KEEP = **37 dispositions** (entries #28 and #31 covered as "KEEP — covered by #14" and "KEEP — covered by #24" respectively).

