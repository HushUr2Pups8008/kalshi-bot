# 2026-05-10 Repo Closure Cleanup

**Goal.** Close every open dev item before any additive feature work. No new
"good ideas"; bug fixes and required tech debt are in scope; no extended soak
periods unless technically required to prevent system failure.

**Status.** ACTIVE — controller plan for the subagent-driven-development loop
on commit `1d0f65a` (CI green, Stream G complete, Wave-1 flipped).

## User-confirmed decisions (carry forward as defaults unless overridden)

1. **Claude command scope:** no repo-local slash wrapper retained. Use the Make target or ask explicitly when GitLab CI usage is needed.
2. **GitLab helper:** keep — commit `scripts/gitlab_ci_usage.py`,
   `tests/test_gitlab_ci_usage.py`, and the `Makefile` target.
3. **Docs-only `[skip ci]`:** implement via `.githooks/prepare-commit-msg`
   (mechanical, repo-portable). NOT via agent memory.
4. **Docs allowlist:** `docs/**`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`,
   `AGENTS.md`. Excludes `.gitlab-ci.yml`, `Makefile`,
   `.githooks/**`, `VERSION`, code, tests, scripts, requirements, runtime
   config.
5. **Local-only files (do NOT sweep into commits):**
   `.claude/settings.local.json`, `.claude/scheduled_tasks.lock`, any other
   `.claude/` runtime state.
6. **EDGE-004 Sub-1 plan draft:** delete. Decision context lives in the debt
   log; no parallel plan file while gated.
7. **Operator gate carry-forward:**
   - `PROFIT-EVID-001` — defer until Cycle-17C verdict.
   - `PROFIT-EDGE-004` Sub-1 — wait for Cycle-17C E3 verdict (per
     no-additive-dev priority).
8. **Wave-2/3 un-archive:** NOT performed. Lever specs live in active
   `docs/_archive/governance/cycle-17-conditional-charter-skeletons.md` + ROADMAP
   §2.3 lever map (per 2026-05-09 docs consolidation).

## Tier 0 — Working tree + reconciliation (this sprint)

| Sub | Description | Files |
|-----|-------------|-------|
| T0.1 | Remove stale `.claude/commands/gitlab-ci-usage.md` project command wrapper; helper remains available via Make target | `.claude/commands/gitlab-ci-usage.md` |
| T0.2 | Commit GitLab helper + Makefile target | `scripts/gitlab_ci_usage.py`, `tests/test_gitlab_ci_usage.py`, `Makefile` |
| T0.3 | Add docs-only `[skip ci]` hook | `.githooks/prepare-commit-msg` |
| T0.4 | Delete EDGE-004 Sub-1 plan draft | `docs/superpowers/plans/2026-05-10-profit-edge-004-sub-1-matcher-jaccard-audit.md` |
| T0.5 | Reconcile ROADMAP: EDGE-011 closed → EDGE-012 active | `docs/ROADMAP.md` (lines 25, 36) |
| T0.6 | Delete stale `backup/wave-1-dry-run-2026-05-05` (local + remote, only after verifying it has no uniques vs main) | git branch ops |

## Tier 1 — Independent closures (no soak, no operator gate)

| Sub | Description | Surface |
|-----|-------------|---------|
| T1.1 | Fix `PROFIT-OBS-004` — `paper_trades.edge` records YES-side edge regardless of trade side | code + tests + debt log |
| T1.2 | Close `PROFIT-SOURCE-001` — Reddit permanently degraded (HTTP 429) → status `PERMANENTLY_DEGRADED` | debt log + source-class metadata |
| T1.3 | Close `PROFIT-DEBT-OQ1-SHIM` — docs artifact final state | debt log |

## Tier 2 — Holds (no code; clock or operator dependency)

| Item | Closure trigger |
|------|-----------------|
| `PROFIT-PHASE2-001` | 14-day shadow-soak ends ~2026-05-15. Early-close only on KILL_SWITCH / VALIDATION_ERROR / PARSE_ERROR. None invoked. Ride out — forcing early close = system-failure risk. |
| `PROFIT-CUTOVER-001` | Closes on first resolved trade or 2026-05-15 milestone. |
| `PROFIT-EDGE-005`, `PROFIT-EDGE-006` | Inherit Cycle-17C verdict. |
| Cycle-17C E3 (`PROFIT-EDGE-012`) | Single-variable redesign in progress; closes by ~2026-05-14. |

## Tier 3+ — Post-verdict (NOT executed this sprint)

| Item | Reason |
|------|--------|
| `PROFIT-EDGE-004` Sub-1 → Sub-5 | Gated on Cycle-17C E3 verdict + §B operator pick |
| `PROFIT-EXEC-002`, `PROFIT-MATCH-001`, `PROFIT-OBS-005` | Spec-ready; deferred post-cycle verdict |
| `PROFIT-LLM-001`, `PROFIT-GOV-004` | Deep-deferred; post-P3 + 2w real-mode soak |
| `PROFIT-EVID-001` | Deferred until Cycle-17C verdict (operator carry-forward) |

## Out-of-scope this sprint

- Wave-2/3 archive restore (lever specs live in cycle-17 charter + ROADMAP §2.3).
- Any new "good idea" feature work.
- Any addition that creates parallel tracking surfaces beyond the debt log.

## Execution sequence

Subagent-driven-development loop:

1. **Task A** = Tier 0 (six sub-items, single implementer; cleanup + reconciliation).
2. **Task B1** = Tier 1 PROFIT-OBS-004 (code fix with tests).
3. **Task B2** = Tier 1 batch close (SOURCE-001 + DEBT-OQ1-SHIM, debt-log only).

Each task: implementer → spec-compliance review → code-quality review →
mark complete. Tier 2 holds run on the calendar, not on this loop.
