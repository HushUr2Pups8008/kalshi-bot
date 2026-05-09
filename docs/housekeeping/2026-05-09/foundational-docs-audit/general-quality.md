# General-Quality Audit — Foundational Docs (Phase 2)

> Read-only audit using `claude-md-improver` rubric. Built on Phase 1 (`docs/housekeeping/2026-05-08/claude-md-audit.md`). Focus: NEW drift since 2026-05-08 visible in `git log --oneline -50`.
>
> Audit date: 2026-05-09 UTC. Branch: `housekeeping/foundational-docs`. Scoring per `references/quality-criteria.md` (commands 20, architecture 20, gotchas 15, conciseness 15, currency 15, actionability 15).
>
> **No edits made.** Format-by-purpose principle honored: rule files = Trigger/Action; CLAUDE.md gotchas = bold-inline narrative. Format divergence between the two is intentional and not flagged.

---

## 1. Summary

- **Files audited:** 13 (1 project CLAUDE.md, 1 global CLAUDE.md, 1 AGENTS.md, 1 RTK.md, 9 rule files)
- **Average score:** 84/100 (B)
- **Files needing update:** 4 (project CLAUDE.md P3, global AGENTS.md P3, RTK.md P3, domain_constraints.md P2)
- **Phase 1 status:** 3/4 P1 recommendations resolved (D-1, M-1, M-2); 1 P3 (SR-1) still open
- **NEW drift since 2026-05-08:** 1 P2 (signal_analyzer refactor exposes line-citation fragility risk for any future addition); 1 P2 (analysis/→tasks/stats/ move blurs domain_constraints `/analysis` purity boundary); 1 P3 (recent fail-fast / observability fixes not yet captured as gotchas)

---

## 2. File-by-File Assessment

### 2.1 `kalshi-bot/CLAUDE.md` (78 lines) — **Score: 90/100 (A−)**

| Criterion | Score | Notes |
|-----------|-------|-------|
| Commands/workflows | 18/20 | Release Versioning section is excellent, copy-pasteable; bypass note included. Missing: lint/test/typecheck commands. |
| Architecture clarity | 18/20 | Critical Gotchas section is structured by domain (Kalshi API / WebSocket / Signal analysis / Governance / Infrastructure / Config). Missing: high-level dir map (defers to README). |
| Non-obvious patterns | 15/15 | Strong. 17 well-scoped gotchas, each with a load-bearing why. M-1 and M-2 from Phase 1 now resolved. |
| Conciseness | 13/15 | Working Style + Bug-Fixing + Continuous Improvement near-verbatim copies of global (Phase 1 R-1 to R-4, ~14 lines duplicated). |
| Currency | 14/15 | Three Phase 1 fixes applied since 2026-05-08. Two carry-over P3 imprecisions (D-2 `resolve_market` naming, D-3 same-signal mechanism) persist. New refactor (P1-02) didn't break any cited gotcha. |
| Actionability | 12/15 | Gotchas describe "what" and "why" well; for some, the "do this instead" is implicit (e.g. anti-blend gotcha says don't blend, doesn't say which path to use — though signal_analyzer.py:1123-1126 makes it obvious in code). |

**New issues since Phase 1:**
- **G-1 (P2, S):** `analysis/` stats modules moved to `tasks/stats/` (commit `8acfc47`, OQ1/P2-07). The CLAUDE.md domain boundaries are unaffected — but `domain_constraints.md` says "/analysis: keep it pure; do not add API calls or trade execution" and "/tasks: keep orchestration there; do not add trading logic." Stats are neither, but moving them blurs the purity-vs-orchestration line. **No CLAUDE.md fix needed; consider a domain_constraints update — see G-7 below.**
- **G-2 (P3, S):** Two recent fix commits (`ce70924` PROFIT-SEC-001 fail-fast RSA-PSS, `1d0714c` PROFIT-OBS-006 SQLite write silence) introduce lessons not captured as gotchas. Neither is mandatory — fail-fast is now the obvious default; the SQLite silence pattern is local to subreddit_selector. Optional capture if either pattern recurs.
- **G-3 (P3, S):** `log_signal_analysis_detail` switched from 46 kwargs to a typed struct (`1736373` P1-03). Not a CLAUDE.md gotcha-class concern — typed struct is self-documenting at the call site. No action.

**Carry-over from Phase 1 (still open, all P3):**
- D-2 `resolve_market()` naming imprecision
- D-3 same-signal guard mechanism wording
- M-4 `_normalize_pem` duplication note missing
- R-1 to R-4 Working Style / Bug-Fixing / CI dedup

### 2.2 `~/.claude/CLAUDE.md` (31 lines) — **Score: 82/100 (B)**

| Criterion | Score | Notes |
|-----------|-------|-------|
| Commands/workflows | 16/20 | RTK section is concise and points to RTK.md. MCP tool refs are useful. |
| Architecture clarity | 17/20 | Working Style / Bug-Fixing / Tooling Preferences are clean. |
| Non-obvious patterns | 12/15 | MCP tool preference hierarchy (graph > grep) is load-bearing project guidance. |
| Conciseness | 14/15 | 31 lines is tight. |
| Currency | 13/15 | Cannot verify 7 MCP tool names this session (plugins not loaded — see reference-integrity.md F-5). |
| Actionability | 10/15 | "Use delegation only when the environment supports it" is hedge-prose; could name concrete dispatch criteria. |

**New issues since Phase 1:** None. File unchanged since 2026-05-01 per Phase 1 audit metadata.

### 2.3 `~/.claude/AGENTS.md` (21 lines) — **Score: 78/100 (B−)**

| Criterion | Score | Notes |
|-----------|-------|-------|
| Commands/workflows | 14/20 | No commands; describes precedence chain only. |
| Architecture clarity | 14/20 | 4-tier precedence model (global → user-rules → AGENTS → project) is correct and useful. |
| Non-obvious patterns | 13/15 | Good — captures the global/local rule layering. |
| Conciseness | 14/15 | Tight. |
| Currency | 12/15 | F-3 (line 13 `project/AGENTS.md` notation) is ambiguous — no `project/` directory exists. |
| Actionability | 11/15 | Tells agent what to follow but not how to resolve conflicts when two layers contradict. |

**New issues since Phase 1:** Phase 1 did not audit AGENTS.md. F-3 from reference-integrity.md is the main finding (P3, S, fix the path notation).

### 2.4 `~/.claude/RTK.md` (29 lines) — **Score: 75/100 (B−)**

| Criterion | Score | Notes |
|-----------|-------|-------|
| Commands/workflows | 18/20 | All `rtk` subcommands listed and verified to resolve. |
| Architecture clarity | 14/20 | Hook-based usage section is clear. |
| Non-obvious patterns | 13/15 | Name-collision warning (reachingforthejack/rtk) is a great gotcha. |
| Conciseness | 12/15 | OK at 29 lines but the circular reference at line 29 is wasted real estate. |
| Currency | 11/15 | Phase 1 SR-1 (line 29 dead pointer to global CLAUDE.md) still unresolved. |
| Actionability | 7/15 | Line 29 actively misleads: agents follow it to a dead end. |

**New issues since Phase 1:** None new. SR-1 carry-over.

### 2.5 Rule files — composite scoring

All 9 rule files use Trigger/Action format consistently, per `documentation_format.md`. Format-by-purpose principle correctly enforced.

| Rule file | Lines | Score | Issue |
|-----------|-------|-------|-------|
| `domain_constraints.md` | 34 | 87/100 (B+) | G-7 below: `/analysis` purity rule may need update post-stats-move |
| `documentation_format.md` | 22 | 95/100 (A) | NEW since Phase 1. Excellent — defines its own enforcement and back-reference rule. |
| `editing_safety.md` | 16 | 92/100 (A−) | Tight, well-formed. |
| `git_workflow.md` | 19 | 90/100 (A−) | Includes VERSION-file gate. Very good. |
| `planning.md` | 13 | 85/100 (B+) | "Lightweight" is hedge-prose; no concrete trigger size. |
| `portability.md` | 13 | 88/100 (B+) | UTC rule is load-bearing. |
| `release_versioning.md` | 12 | 90/100 (A−) | Clean opt-in via `VERSION` file presence. |
| `risk_review.md` | 13 | 88/100 (B+) | Well-formed. |
| `validation.md` | 16 | 92/100 (A−) | Strong — explicit "do not claim validation that was not performed." |
| `windows_local.md` | 18 | 87/100 (B+) | Specific, NSSM-aware. |

**G-7 (P2, M) — `/analysis` purity rule may not match current reality:** Commit `8acfc47` (OQ1/P2-07) moved stats modules from `/analysis` to `/tasks/stats/`. The current `domain_constraints.md` rule says `/analysis: keep it pure; do not add API calls or trade execution.` Stats are neither, but the move suggests the team is treating `/analysis` more strictly than the rule's literal wording (only "API calls or trade execution" are forbidden — stats *are* allowed by the current rule). The rule and the practice have drifted apart. Either:
- (a) Tighten the `/analysis` rule to "pure analysis logic only — no stats/aggregation, no API calls, no execution" (matches practice, stricter).
- (b) Loosen and document: leave the rule as-is and note that stats happen to live in `/tasks/stats/` for orchestration reasons.
- (c) Leave it: the rule is technically still satisfied; the move was an organizational preference, not a rule-driven boundary.
Recommend the user pick (a), (b), or (c). This is a judgment call.

---

## 3. Cross-File Findings

### G-4 (P2, S) — Stale line-number citations risk after recent refactor
The drift-detection report I produced today cites `signal_analyzer.py:293, 301` for `raw_decode` and `:1123-1126` for the anti-blend comment. After 4 refactor commits (`bcadc7e`, `2ad8072`, `24df410`, plus `1736373`) `signal_analyzer.py` is now 1263 lines. The CLAUDE.md gotchas themselves do NOT cite line numbers — they cite behavior — so they survived. But this is a structural risk: any future gotcha that adds a line citation will rot at the next refactor.

**Recommendation:** maintain the existing convention (cite function/behavior, not line numbers) when adding new gotchas. Already implicitly followed; consider a one-line note in `documentation_format.md` to make it explicit.

### G-5 (P3, S) — Recent infra fix lessons not captured as gotchas
Two recent fix commits introduce reusable lessons:

- `ce70924` PROFIT-SEC-001 — RSA-PSS signing failures now fail fast. Lesson: silent failure was the antipattern; fail-fast is the default for security-critical paths.
- `1d0714c` PROFIT-OBS-006 — subreddit_selector SQLite write failures no longer silent. Lesson: per `silent-failure-hunter` rubric, observability writes should not swallow exceptions.

Neither is currently a CLAUDE.md gotcha. They map to the same meta-pattern: *don't swallow security or observability errors*. **Recommendation:** consider one cross-cutting gotcha or rule (see gap-analysis.md output for new-rule candidates).

### G-6 (P3, S) — `documentation_format.md` is new since Phase 1; not yet referenced from CLAUDE.md
The rule was added recently (line count 22; Phase 1 audit on 2026-05-08 did not include it in the file list). Project CLAUDE.md cross-references planning, validation, git_workflow, release_versioning — but not `documentation_format.md`. Adding a one-line cross-ref would make the format-by-purpose principle visible to agents that read CLAUDE.md before diving into rule files. **Optional — agents typically encounter the rule via the auto-loaded global instructions block in any case.**

---

## 4. Top 5 Actions (P0 → P3)

1. **(P2, M) Resolve `/analysis` purity rule drift (G-7).** Pick (a) tighten, (b) loosen with note, or (c) no change. Stats moved from `/analysis` to `/tasks/stats/` in commit `8acfc47`; current rule wording is silent on whether stats belong in `/analysis`.

2. **(P2, S) Make line-citation convention explicit (G-4).** Add a one-line note to `documentation_format.md` recommending behavior/function citations over line numbers in CLAUDE.md gotchas. Prevents rot.

3. **(P3, S) Capture cross-cutting "fail-fast on security/observability" lesson (G-5).** Either as a CLAUDE.md gotcha under a new "Error handling" header, or as a new rule in `~/.claude/rules/`. See gap-analysis.md for proposed wording.

4. **(P3, S) Phase 1 carry-overs.** Address D-2 (`resolve_market` naming), D-3 (same-signal mechanism), M-4 (`_normalize_pem` duplication), R-1 to R-4 (dedup global/project boilerplate), SR-1 (RTK.md:29 circular). See drift-detection.md and reference-integrity.md.

5. **(P3, S) Cross-reference `documentation_format.md` from project CLAUDE.md (G-6).** Optional — small visibility boost for the format-by-purpose principle.

---

## 5. Three-Bullet Summary

- **Average grade B+ (84/100), one A on `documentation_format.md`.** Project CLAUDE.md is the strongest at 90/100 with all 17 gotchas verified accurate. Global CLAUDE.md, AGENTS.md, RTK.md, and 9 rule files are tight, well-formed, and mostly current.
- **Three Phase 1 P1 recommendations resolved (D-1 docstring, M-1 anchor_rate, M-2 endpoint warning); seven P3 carry-overs persist.** Carry-overs are cosmetic — they do not actively mislead an agent — and can be batched into a single dedup commit.
- **One NEW P2 finding worth user judgment: G-7 `/analysis` purity rule vs `tasks/stats/` move.** Stats were relocated in commit `8acfc47` but the domain_constraints rule is silent on whether stats belong in `/analysis`. Pick (a) tighten / (b) loosen / (c) no-change. Other new findings (G-1 to G-6) are P2/P3 informational.
