# EDGE_STATUS — operator-facing edge dashboard

**Refresh by commit.** Single page; replaces 100+ doc index for "are we making money?" questions.
**Last refresh:** 2026-05-07 cycle-15B verdict landing.

## TL;DR (3 numbers)

| metric | value |
|---|---|
| Lifetime P&L | **-$7.50** |
| Lifetime trade count | **3** (all resolved, all lost, all 1 source / 1 series / 1 direction; 3/3 wrong-direction) |
| Replay verdict | **Cycle-15B verdict: `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`. Cycle-16D active per matching skeleton.** Lane B post-fix 8/8 directional + 2/2 NEUTRAL ✓; IC §16 acceptance fails (0 slices with `ev_ci_95_lo>0` AND `trades≥10`) BUT 0 of 272 replay rows have decision-time executable price → scorer-blocked, NOT "negative EV proven." |

## Cycle-15B verdict landed — `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`

Cycle-15B answered: **does extraction now emit signal post-fix?** Answer: yes (C8 8/8 directional Lane B fixtures + 2/2 NEUTRAL pass; 7/272 replay rows have nonzero post-fix model delta). But the IC §16 acceptance gate cannot be evaluated — the replay scorer requires per-decision-time `market_yes_price` to compute counterfactual P&L, and 0/272 rows have one. This is the same `/markets/{ticker}/trades` 404 issue first surfaced in cycle-13 / noted in cycle-14 charter §"Historical price endpoint gap."

Cycle-16D (PROFIT-EDGE-009) instantiates `cycle-16-conditional-charter-skeletons.md` §D — price-reconstruction prerequisite scope. Pure replay-harness work; bot extraction code untouched.

## Wave deploy status (per IC §16 + Cycle-15B verdict)

| wave | status | gate |
|---|---|---|
| Wave-1 (OBS-005, MATCH-001, OBS-003, EXEC-002, GOV-003, Lever A.1) | **ACTIVE — ships 2026-05-08 as cleanup/observability hygiene only; does NOT claim edge** | exempt under IC §16 Rule 2 (mechanical / observability / governance) |
| Wave-2 (Lever A.1+ feed onboarding, Branch C legal-analyst) | **HALTED PENDING CYCLE-16D PRICE-RECONSTRUCTION + POST-RECONSTRUCTION REPLAY VERDICT** | requires (a) Cycle-16D price reconstruction AND (b) post-reconstruction replay producing ≥1 slice with `ev_ci_95_lo>0` AND `trades≥10` |
| Wave-3 (Lever B G1=0.04, Lever C cross-series) | **HALTED PENDING CYCLE-16D PRICE-RECONSTRUCTION + POST-RECONSTRUCTION REPLAY VERDICT — Lever B counterindicated until IC §16 evidence gate evaluable** | loosening admission on a model whose IC §16 status is unknown converts unknown-EV non-bets into unknown-EV bets |
| Branch D escalation (PROFIT-LLM-001 / P4-GATE Appendix A) | **HALTED PENDING CYCLE-16D PRICE-RECONSTRUCTION + POST-RECONSTRUCTION REPLAY VERDICT** | each candidate fix needs replay evidence; replay needs prices |
| **Capital posture** | **PAPER-ONLY. Hard guardrail post-Wave-1 deploy** (Cycle-14 charter §5) | live trading remains blocked until Cycle-16D + post-reconstruction replay produces ≥1 IC §16 slice |

## Are we near a Wave-2-eligible slice?

**No.** Cycle-12 replay (paper-trade scope only, n=3) found 0 positive-EV slices. Cycle-13 will expand to 24 resolved markets in evidence_store; if still no slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`, IC §16 Rule 5 triggers strategic-pivot playbook (`docs/governance/edge-replay-pivot-playbook.md`).

**Cycle-13 leading indicator (cycle-13 dossier integrity audit):** 21 of 24 resolved-market dossiers stuck at `current_estimate = 0.5000` (the prior). Bot's belief model rarely exits the prior despite ingesting evidence. This is a calibration signal — most evidence is non-informative under current update logic. Replay verdict is bounded by this calibration regardless of feed onboarding.

## Pre-deploy state (for fire-time operator)

| metric | value (cycle-13 refresh, 2026-05-06T22:30Z) |
|---|---|
| Soak elapsed | ~125 h (Day 5/7 under §8.5.1 path) |
| GOVERNANCE_DECISION count | 552 |
| Safety counters | 0/0/0 (KILL_SWITCH / batch_aborted / VALIDATION_ERROR) |
| PARSE_ERROR (total / trailing-72h) | 7 / 0 |
| Latest decision | 2026-05-06T21:43Z |
| Bot health | GREEN (`scripts/bothealth.sh` cycle-13) |
| Gate-6 capacity | **AT RISK** — 0.663 reviewable fraction at 80/day budget; needs Path 1 (raise budget to ≥169) per `2026-05-06-gate-6-capacity-resolution-plan.md` |
| §8.5.2 carve-out commits surfaced | 5 (3 INVOKED, 2 OUT-OF-SCOPE; gate 7 clean via attestation) |

## Replay harness state

- Harness exists: `scripts/edge_replay/{fetch_resolved_markets,build_replay_dataset,score_counterfactual_pnl}.py`
- Test coverage: 9 tests including synthetic +EV self-test (validates scorer can FIND edge when it exists, not just report negative)
- Bootstrap CI implemented (cycle-12 task #5 done by Codex)
- Per-decision-time price reconstruction implemented via `--historical-prices` (cycle-13 task #6 done by Codex)
- "Left on the table" measure implemented (cycle-13 task #7 done by Codex)
- Cycle-13 charter: expand scope from 3 → 24 markets via `--live-kalshi`
- Cycle-13 status: in progress (Codex implementing)

## Replay verdict log

| date | scope | verdict | report |
|---|---|---|---|
| 2026-05-06 | 3 paper-trade markets / 1 source / 1 series / n=3 trades | 0 positive-EV slices, P&L -$7.50, win rate 0.00, harness self-test passes | `edge-replay-cycle12-report.md` |
| 2026-05-06 (Cycle-13) | 24 resolved evidence_store markets / 255 replay rows | **0 positive-EV slices, 0 left-on-table winners, P&L -$7.50, IC §16 Rule 5 fires** | `edge-replay-cycle13-report.md` |
| 2026-05-06 (Cycle-14) | 24 resolved markets / 255 replay rows + synthetic Lane A/B injection | **Verdict: `extraction_broken`.** Movement_rate 1.57%, direction-correctness 0/6 when directional, sized-bet 0/3 (-$7.50), Brier 0.2599 (n=24, supporting only), Lane A PASS / Lane B FAIL at delta=0.000 on both fixtures. → Cycle-15B extraction rebuild active. | `edge-replay-cycle14-diagnosis.md` |
| 2026-05-07 (Cycle-15B) | 24 resolved markets / 272 replay rows + post-fix re-ingestion + 10 Lane B fixtures | **Verdict: `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`.** C8 Lane B 8/8 directional + 2/2 NEUTRAL ✓; C9 idempotent re-ingestion (SHA256 stable); C10 IC §16 0 slices BUT 0/272 rows had decision-time executable price; 183/272 readiness-admitted; 7/272 nonzero post-fix model delta. Scorer-blocked, NOT negative-EV proven. → Cycle-16D price reconstruction active. | `edge-replay-cycle15b-report.md` |

## What changes Wave-2 from HALTED → AUTHORIZED

A row in the replay output with **all** of:
- `ev_ci_95_lo > 0` (positive EV at 95% CI)
- `trades ≥ 10` (not single-cluster correlated)
- Documented in `edge-replay-cycle{N}-report.md` with reproducible commands

When that row exists, the slice it identifies (e.g., "Reuters × KXTRUMPCHINA × news") becomes the Wave-2 candidate. The pre-staged Wave-2 specs (legal/geopolitics speculation) are NOT the candidate; they remain BLOCKED.

## Live operations

- `com.jake.kalshi-bot` running (paper mode); uptime ~5d 5h
- `com.kalshi.governance.fast` shadow-mode soak active
- `com.kalshi.db-backup` daily 06:00 MDT (last fire 2026-05-06T1200Z confirmed)
- 06:00 MDT db-backup monitor armed (next fire Thu 2026-05-07T1200Z)

## Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 strategic-pivot diagnostic
- `docs/governance/edge-replay-cycle12-report.md` — Cycle-12 replay output
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-005` — replay harness debt entry
- `data/paper_trades.db` — 3-trade lifetime history
- `data/evidence_store.db` — 266 evidence rows / 24 resolved markets
