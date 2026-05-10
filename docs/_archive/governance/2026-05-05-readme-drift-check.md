# README content drift check

**Type:** read-only review (Claude task per Implementation Contract §9 — review).
**Source:** `README.md` (HEAD `3a400c1`) vs current state across the codebase.
**Audience:** operator considering README update before/after Wave-1 deploy.
**No edits applied.** Findings only.

## TL;DR

README is **5 days stale** vs the cycle 1-3 work landed 2026-05-05. **3 MEDIUM drift findings, 4 LOW.** Recommend a single follow-up commit refreshing the status block (1 paragraph) + version refs (3 cells). README content is otherwise accurate.

## Findings

### F1 (MEDIUM) — Status block frozen at 2026-05-01

**Source:** README.md line 5 ("Status (2026-05-01)") — current text reflects MacBook→Mac Studio cutover state from 2026-05-01.

**Reality at HEAD (2026-05-05):**

- PHASE2-001 soak nearly complete (3 days from close at 2026-05-08T19:01Z+ via §8.5.1 early-close path)
- Wave-1 deploy infrastructure complete (`wave-1-deploy-commit-order-decision.md`, `wave-1-changelog-entry-prestaged.md`, observation plan, dry-run report)
- Wave-2/3/Branch-D scopes spec'd (cycle 3 specs)
- 4 cycles of Claude+Codex split work landed (commits `b44dda2 → 3a400c1`)

**Recommended fix:** add a "Status (2026-05-05)" block ABOVE the existing 2026-05-01 block (preserving history). Paragraph should mention:
- §8.5.1 early-close path planned for 2026-05-08T19:01Z+
- Wave-1 → Wave-3 deploy infrastructure spec'd
- Branch D escalation criteria + sizing-scope specs landed

**Severity MEDIUM** because README is the first doc a fresh reader sees; stale status implies the project state is from 5 days ago.

### F2 (MEDIUM) — Version reference cell shows 0.29.59

**Source:** README.md line 3 — `[![Version](https://img.shields.io/badge/version-0.29.59-blue)](CHANGELOG.md)`.

**Reality:** still 0.29.59 (per `VERSION` file). Pre-commit hook syncs this on VERSION change. **No fix needed pre-Wave-1.** When Wave-1 commit 6 lands with VERSION 0.30.0, the hook auto-updates the badge.

**Verdict:** ✅ correct usage; flagged as MEDIUM only because operator should expect this to change on Wave-1 commit 6 deploy and not be confused.

### F3 (MEDIUM) — Tests count badge shows 1424; verify

**Source:** README.md line 3 — `[![Tests](https://img.shields.io/badge/tests-1424%20passing-brightgreen)](tests/)`.

**Reality:** cycle 1-3 added several new test files. Likely tests-passing count drifted vs README's 1424 by ~50-100 tests. **Recommend re-running** `.venv/bin/python -m pytest -q` and updating badge count if it's drifted > 5 %.

**Severity MEDIUM** because the badge claims a specific test count; if it's drifted the README is inaccurate even though the underlying suite is healthy.

### F4 (LOW) — "Where things live" table 2026-05-15 ETA

**Source:** README.md "Governance agent (Phase 2 shadow soak in progress on Mac Studio)" row mentions "§8.5 acceptance target ETA 2026-05-15."

**Reality:** §8.5.1 early-close path puts the target at 2026-05-08+. Update needed; LOW because the row's purpose is "where to look" not "specific date forecast."

### F5 (LOW) — `docs/_archive/` reference

**Source:** README.md "Archive" row references `docs/_archive/`.

**Reality:** primary archive directory in current cycle's plan is `docs/governance/archive/` (per cycle 2 doc-index audit + cycle 3 cleanup execution plan). Both directories may co-exist; ensure no overlap when cleanup commits run.

**Severity LOW** because operator may already plan to consolidate; no immediate user impact.

### F6 (LOW) — CHANGELOG "Current through" cell

**Source:** "Release history" row says "Current through v0.29.59."

**Reality:** matches `VERSION` file currently. Same as F2 — auto-updated on Wave-1 commit 6 deploy.

**Verdict:** ✅ no fix needed pre-Wave-1.

### F7 (LOW) — Wave-1/2/3 not mentioned

**Source:** README has no top-level reference to the post-soak Wave deploy structure.

**Reality:** post-soak Wave-1 → Wave-2 → Wave-3 → Branch D is a load-bearing operator concept now. README "Where things live" table could add a row.

**Severity LOW** because operator who reads `docs/governance/` README + closure-path-TLDR will get the same info; README is just the front door.

## Confirmed clean

- README.md describes the multi-lane architecture correctly (fast / accumulation / structural lanes blended under regime classifier).
- Claims about Kalshi API signing (RSA-PSS), websockets-version-dependent header, market `status="active"` are unchanged + correct (per CLAUDE.md gotchas section).
- 13-day MacBook paper soak summary is correct historical record.
- VitalLaw.com 3/3 PAPER_TRADE on the 13-day archive is correctly cited.

## Recommended single-commit fix

```markdown
Insert ABOVE the existing "Status (2026-05-01)" line:

> **Status (2026-05-05):** PROFIT-PHASE2-001 governance shadow-soak nearing
> early-close at 2026-05-08T19:01Z+ via §8.5.1 (volume gate over-cleared
> 8.9× by Day-4; safety counters all 0). Wave-1 / Wave-2 / Wave-3 /
> Branch-D-escalation deploy infrastructure spec'd across 4 cycles
> 2026-05-05; cycle artifacts under `docs/governance/`. Combined gate-6
> evidence (Codex's 67/67 verdicted at 100% reasonable + 241/242
> mechanically-uniform `disable_source` decisions) yields ≥99.6% reasonable
> on full corpus. Wave-1 base-stack deploy ETA: 2026-05-08+ per
> `2026-05-05-wave-1-deploy-day-timing.md`.
```

Plus refresh the test count if drifted (single sed-style edit).

**Estimated fix wall-clock: 5-10 min.**

## Out of scope

- Major README structural rewrite. The current shape is fine.
- Adding GitHub Actions CI badge. Project doesn't currently use GA workflows visibly; out of scope.

## Cross-links

- `README.md` — under review
- `docs/governance/edge-004-closure-path-tldr-v3.md` — current state authoritative
- `docs/profit_path_debt_log.md` — cycles 2 + 3 entries
