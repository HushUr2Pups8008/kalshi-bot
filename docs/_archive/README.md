# docs/_archive/

Closed plans and one-off investigations preserved for historical context.
Content here is **not load-bearing**: nothing in `main`, the test suite,
or the active roadmap reads from these files at runtime. The active
tracking documents are:

* [`docs/ROADMAP.md`](../ROADMAP.md) — current work plan + post-Mac-Studio
  backlog + Polymarket appendix.
* [`docs/profit_path_debt_log.md`](../profit_path_debt_log.md) — unified
  technical-debt tracking system (per CLAUDE.md).
* [`docs/governance/`](../governance/) — active governance project.
* [`docs/superpowers/plans/`](../superpowers/plans/) — recent dated
  superpower plans (governance Phase 1/2, simulation buildout).

Files moved here on 2026-04-26 as part of the documentation-scrub branch
[`feat/docs-scrub-2026-04-26`](https://gitlab.com/HushUr2Pups8008/kalshi-bot).

## Layout

```
_archive/
  plans/      — closed version-locked plans (v0.20 → v0.29.4); each
                describes a specific feature delivery whose effects are
                now captured in CHANGELOG.md.
  studies/    — closed one-off investigations:
                websocket_fix.md, news_sources_evaluation.md,
                profit_cal_001_calibration_wiring.md,
                polymarket_venue_integration_investigation.md,
                future_plans.md
```

Logical separation, not granular folders — the goal is "remove from
mental load while preserving searchability."

## What lives where now (relocations)

| Old location | New location | Notes |
|---|---|---|
| `docs/plans/v0.20.0_*` through `v0.29.4_*` (12 files) | `_archive/plans/` | Closed version deliveries; CHANGELOG.md is the durable record. |
| `docs/websocket_fix.md` | `_archive/studies/` | One-off fix; the durable lessons live in CLAUDE.md "Kalshi API" + "WebSocket" gotchas sections. |
| `docs/plans/news_sources_evaluation.md` | `_archive/studies/` | Tier 1 + Tier 2 integrated per operator confirmation 2026-04-26; Tier 3 status now tracked in ROADMAP Appendix A. |
| `docs/plans/profit_cal_001_calibration_wiring.md` | `_archive/studies/` | PROFIT-CAL-001 closed 2026-04-24 (v0.29.47); the durable record is in `profit_path_debt_log.md`. |
| `docs/plans/polymarket_venue_integration_investigation.md` | `_archive/studies/` | Phase 0 research closed 2026-04-22; Phase 1 BLOCKED on retail waitlist. Condensed status + 6-phase summary now in ROADMAP Appendix B; this doc retained for the full research transcript. |
| `docs/future_plans.md` | `_archive/studies/` | Cross-project planning (kalshi-specific Phase 5/6 LLM upgrades merged into ROADMAP "Stage 6 backlog"; Alpaca-equity-bot and OpenClaw-personal-assistant content was unrelated to kalshi-bot and is preserved here as cross-project context only). |

## When to look here

* Investigating "what was the rationale for X feature" — check the
  matching `v0.X*` plan in `plans/`.
* Tracing the design of a closed initiative (PROFIT-CAL-001, Polymarket
  research, news-sources evaluation) — full research transcripts in
  `studies/`.
* Recovering a deleted decision: `git log --all` is authoritative.

## When NOT to look here

* Current work — see `ROADMAP.md` and `profit_path_debt_log.md`.
* Operator runbooks — see `docs/governance/`, `scripts/README.md`,
  `scripts/simulations/README.md`.
* Recent dated plans — see `docs/superpowers/plans/`.

## Re-activating an archived doc

If an archived plan needs to be revived:

1. `git mv` it back to its original location.
2. Refresh dates / version numbers / inbound references.
3. Update `ROADMAP.md` to link the active doc.
4. Note the resurrection in `profit_path_debt_log.md` "Audit Source"
   entry so future agents can trace the decision.
