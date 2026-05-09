# Context Cost Audit — Foundational Docs (Phase 2)

> Read-only audit under the `context-budget` rubric. Measures the per-session token cost of foundational docs auto-loaded into every Claude Code session for this project.
>
> Audit date: 2026-05-09 UTC. Scope: `~/.claude/CLAUDE.md`, `kalshi-bot/CLAUDE.md`, `~/.claude/rules/*.md` (10 files), `~/.claude/AGENTS.md`, `~/.claude/RTK.md`.
>
> Token estimation: `bytes / 4` (standard rough heuristic for English prose; ±15%). Total cross-checked with `wc -c` over all 14 files.

---

## 1. Total Context Cost

| Metric | Value |
|---|---|
| Files measured | 14 |
| Total lines | 335 |
| Total bytes | 19,659 |
| Estimated tokens | ~4,915 |
| Share of 200K context window | ~2.5% |
| Share of 1M context window (Opus 4.7 1M) | ~0.5% |

**Verdict: well within budget.** Foundational docs are small relative to typical session overhead. MCP server schemas alone often consume 30–50K tokens; skills can add another 5–10K. Foundational docs at ~5K are not the lever to optimize.

---

## 2. Per-File Breakdown

Sorted by token cost (descending). Signal density rated by load-bearing-ness vs verbosity (subjective, but informed by the drift-detection / reference-integrity / general-quality reports in this same audit).

| Rank | File | Lines | Bytes | ~Tokens | Signal density | Notes |
|------|------|-------|-------|---------|----------------|-------|
| 1 | `kalshi-bot/CLAUDE.md` | 78 | 7,330 | ~1,833 | **HIGH** | 17 verified gotchas + Release Versioning section. Single-file context backbone. |
| 2 | `~/.claude/CLAUDE.md` | 31 | 1,799 | ~450 | HIGH | Working Style + tooling preferences + RTK pointer. |
| 3 | `~/.claude/rules/domain_constraints.md` | 34 | 1,650 | ~413 | HIGH | Project-specific domain boundaries (analysis/trading/tasks/feeds/governance). Recently expanded (M-3). |
| 4 | `~/.claude/rules/documentation_format.md` | 22 | 1,470 | ~368 | HIGH | Format-by-purpose principle. NEW since Phase 1. Self-enforcing. |
| 5 | `~/.claude/RTK.md` | 29 | 964 | ~241 | MEDIUM | Reference info; rarely consulted mid-task. Has 1 P3 dead link (RTK.md:29). |
| 6 | `~/.claude/AGENTS.md` | 21 | 954 | ~239 | MEDIUM | 4-tier precedence chain. Useful at session start; rarely re-read. Has 1 P3 ambiguity (line 13). |
| 7 | `~/.claude/rules/git_workflow.md` | 19 | 902 | ~226 | HIGH | VERSION-file gate; load-bearing for release safety. |
| 8 | `~/.claude/rules/windows_local.md` | 18 | 870 | ~218 | MEDIUM-HIGH | Windows-specific; only relevant for cross-platform changes. |
| 9 | `~/.claude/rules/portability.md` | 13 | 778 | ~195 | HIGH | UTC rule, cross-platform defaults. |
| 10 | `~/.claude/rules/editing_safety.md` | 16 | 683 | ~171 | HIGH | "Inspect first" / scope discipline. |
| 11 | `~/.claude/rules/risk_review.md` | 13 | 648 | ~162 | HIGH | Safety-gate preservation. Load-bearing for govenance/security paths. |
| 12 | `~/.claude/rules/validation.md` | 16 | 556 | ~139 | HIGH | "Don't claim validation that wasn't performed." |
| 13 | `~/.claude/rules/planning.md` | 13 | 553 | ~138 | HIGH | Trigger for non-trivial work. |
| 14 | `~/.claude/rules/release_versioning.md` | 12 | 502 | ~126 | HIGH | Opt-in via VERSION file. Tight. |

---

## 3. Signal-per-Token Analysis

### Highest signal-per-token (keep)

| File | Reason |
|------|--------|
| `validation.md` (139 tok) | Smallest "don't claim what you didn't do" rule possible — every line is load-bearing |
| `release_versioning.md` (126 tok) | 12 lines, opt-in, exemplary tight Trigger/Action |
| `editing_safety.md` (171 tok) | Five Trigger/Actions cover the entire write-time discipline |
| `risk_review.md` (162 tok) | Smallest possible safety-gate rule; each trigger fires only when relevant |
| `planning.md` (138 tok) | Plan-first discipline in 13 lines |

These five files together = ~736 tokens. Removing any one would require a longer replacement elsewhere (e.g. CLAUDE.md prose). They are the floor.

### Lowest signal-per-token (consider streamlining, not deleting)

| File | Reason |
|------|--------|
| `RTK.md` (241 tok) | Reference info that's also in `rtk --help`. Useful at first encounter; rarely consulted later. Has 1 P3 dead link. |
| `AGENTS.md` (239 tok) | 4-tier precedence — likely only useful at session start; doesn't need to stay loaded mid-task. Has 1 P3 ambiguity. |
| Project `CLAUDE.md` Working Style + Bug-Fixing + Continuous Improvement (~14 lines, ~120 tok) | ~90% verbatim copies of global CLAUDE.md (Phase 1 R-1 to R-4). Pure dedup opportunity. |

### Project CLAUDE.md (1,833 tok — largest single file)

Despite being the biggest, this file scores high signal density: 17 verified gotchas + Release Versioning section + Critical Gotchas headers. Phase 1 R-1 to R-4 dedup would shave ~100 tokens (~5%). Not a meaningful saving relative to the ~50K+ that MCP servers typically cost.

---

## 4. Comparison to "Don't Bloat the System Prompt" Guidance

Affaan Mustafa's guidance distinguishes two failure modes:

1. **Bloated system prompt** — every line costs tokens on every turn, multiplied across the session.
2. **Empty system prompt** — agent must rediscover constraints each turn, costs more in clarification round-trips than the original guidance would have.

**Where this project sits:** ~5K tokens of foundational docs is comfortably below typical bloat thresholds (a 200-line CLAUDE.md is ~5K tokens; ours is 78 lines). The 10 rule files are deliberately small and trigger-scoped, so they pay their cost only when relevant.

**Token economics for this audit:**

- Phase 1 R-1 to R-4 dedup → save ~100 tokens × every session = small absolute win (~0.05% of context). Worth doing for hygiene, not for tokens.
- Removing AGENTS.md or RTK.md entirely → ~480 tokens saved, but at the cost of removing precedence/RTK reference info that has no obvious replacement surface.
- Removing any rule file → lose Trigger/Action discipline; not worth ~150–225 tokens per file.

The foundational doc set is **not** a top-3 lever for context optimization in this project. MCP servers, skill loadout, and agent description bloat would each be a higher-impact target.

---

## 5. Bucketed Recommendations

### KEEP (unchanged) — 11 files, ~4,070 tokens

Every rule file (`documentation_format`, `domain_constraints`, `editing_safety`, `git_workflow`, `planning`, `portability`, `release_versioning`, `risk_review`, `validation`, `windows_local`) plus `~/.claude/CLAUDE.md`.

Rationale: each rule file has high signal density and trigger-scoped content. Global CLAUDE.md is the entry point for every other doc.

### STREAMLINE (apply Phase 1 R-1 to R-4 dedup) — 1 file, ~1,833 tokens → ~1,733 tokens (~100 token savings)

Project `kalshi-bot/CLAUDE.md`: replace the verbatim Working Style / Bug-Fixing / Continuous Improvement sections with a single deferral line ("See `~/.claude/CLAUDE.md` for working style. Project-unique additions: ..."). Saves ~14 lines, ~100 tokens. Aligns with Phase 1 R-1 to R-4.

Effort: S (10 minutes). Severity: P3.

### MINOR CLEANUP — 2 files, ~1 line each

- `RTK.md:29` — delete or rewrite the circular reference (Phase 1 SR-1 / Phase 2 F-2). Saves ~20 tokens.
- `AGENTS.md:13` — clarify `project/AGENTS.md` notation (Phase 2 F-3). No token change; clarity-only.

### LAZY-LOAD CANDIDATES (defer for now)

`AGENTS.md` (~239 tok) and `RTK.md` (~241 tok) are good candidates for "load on first relevant question" rather than every-turn loading. Implementing this would require harness-level changes (not a doc edit), so defer until a context-pressure session demonstrates need.

### NOT RECOMMENDED

- Do not delete any rule file. Each is < 250 tokens and trigger-scoped.
- Do not collapse rule files (e.g. merging `editing_safety` + `risk_review` + `validation`). The trigger-scoped separation is what makes them cheap to ignore when irrelevant; merging would force every trigger to be evaluated together.

---

## 6. Top 3 Optimizations

1. **Apply Phase 1 R-1 to R-4 dedup on project CLAUDE.md → save ~100 tokens, improve clarity.** (P3, S)
2. **Fix RTK.md:29 circular reference → save ~20 tokens, improve clarity.** (P3, S — Phase 1 SR-1 / Phase 2 F-2)
3. **Defer further pruning.** Foundational docs are not the high-impact context lever here. Audit MCP server tool counts and active skill loadout instead if context pressure becomes a real concern.

**Total potential saving from pruning:** ~120 tokens (~2.4% of foundational total, ~0.06% of 200K context).

---

## 7. Three-Bullet Summary

- **Foundational docs cost ~4,915 tokens per session — about 2.5% of a 200K context window.** Not a context-pressure problem. Median rule file is ~150 tokens; max is 1,833 (project CLAUDE.md).
- **Eleven of fourteen files are KEEP-as-is.** Five rule files (validation, release_versioning, editing_safety, risk_review, planning) hit the floor for signal density — each line is load-bearing.
- **Realistic optimization ceiling is ~120 tokens (~0.06% of 200K).** The project should optimize MCP server schemas and active skill loadout instead — those are the levers that move the needle.
