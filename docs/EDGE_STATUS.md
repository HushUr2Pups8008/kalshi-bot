# EDGE_STATUS — operator-facing edge dashboard

**Refresh by commit.** Single page; replaces 100+ doc index for "are we making money?" questions.
**Last refresh:** 2026-05-07 cycle-16D verdict landing.

## TL;DR (3 numbers)

| metric | value |
|---|---|
| Lifetime P&L | **-$7.50** (live, n=3 paper trades) |
| Lifetime trade count | **3** (all resolved, all lost, all 1 source / 1 series / 1 direction; 3/3 wrong-direction) |
| Replay verdict | **Cycle-16D verdict: `extraction_fixed_but_information_frontier_holds`. Cycle-17 operator decision active.** Coverage 99.6324%; 237 counterfactual trades / 2 wins (0.84% win rate) / -7.46 P&L; ev_ci_95_lo = -0.0382; 0 IC §16-eligible slices. Anti-correlated signal OR keyword-overfit hypothesis flagged. |

## Cycle-16D verdict landed — `extraction_fixed_but_information_frontier_holds`

Cycle-16D answered: **with restored prices, does any slice produce IC §16-eligible positive EV?** Answer: no. Price reconstruction succeeded (271/272 rows priced). 237 counterfactual trades materialized. **Only 2 wins (0.84% win rate)**, ev_ci_95_lo = -0.0382 overall. No (source × market_family × signal_type) slice meets `ev_ci_95_lo > 0` AND `trades ≥ 10`.

The 0.84% win rate is anomalously low for a "no signal" reading: pure no-signal random extraction would produce ~50% win rate. 99.16% wrong-direction trade rate suggests either anti-correlated signal at this trader's information set OR Cycle-15B keyword-extension overfit synthetic Lane B fixtures (matching production phrases in wrong context). See Claude appendix in `edge-replay-cycle16d-report.md`.

**Cycle-17 = operator decision** per `cycle-17-conditional-charter-skeletons.md`:
- §B source onboarding (2-4 weeks; new feeds with backfill replay validation)
- §C strategic redesign / pause / paper-only-as-research
- Claude recommends operator weigh §C heavily given 4-cycle cumulative evidence + 99.16% wrong-direction.

## Wave deploy status (per IC §16 + Cycle-16D verdict)

| wave | status | gate |
|---|---|---|
| Wave-1 (OBS-005, MATCH-001, OBS-003, EXEC-002, GOV-003, Lever A.1) | **ACTIVE — ships 2026-05-08 as cleanup/observability hygiene only; does NOT claim edge** | exempt under IC §16 Rule 2 (mechanical / observability / governance) |
| Wave-2 (Lever A.1+ feed onboarding, Branch C legal-analyst) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT** | requires (a) operator picks §B source onboarding AND (b) post-onboarding replay produces ≥1 slice with `ev_ci_95_lo>0` AND `trades≥10` |
| Wave-3 (Lever B G1=0.04, Lever C cross-series) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT — Lever B counterindicated** | loosening admission on a model with 0.84% win rate at 237-trade sample widens losses, not narrows them |
| Branch D escalation (PROFIT-LLM-001 / P4-GATE Appendix A) | **HALTED PENDING CYCLE-17 OPERATOR DECISION + POST-DECISION CYCLE VERDICT** | each candidate fix needs replay evidence; current evidence shows zero positive-EV slices at restored prices |
| **Capital posture** | **PAPER-ONLY. Hard guardrail** (Cycle-14 charter §5) | live trading remains blocked until ≥1 positive-EV slice surfaces under IC §16; Cycle-16D evidence does not support flip |

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
| 2026-05-07 (Cycle-16D) | 24 resolved markets / 272 replay rows + restored prices via documented Kalshi endpoints | **Verdict: `extraction_fixed_but_information_frontier_holds`.** D5 coverage 99.6324% (271/272 priced) ✓; D6 237 counterfactual trades / 2 wins / -7.46 P&L; overall ev_ci_95_lo = -0.0382; 1 raw positive-EV slice with trades=1 (below IC §16 trades≥10 floor); 0 IC §16-eligible slices; D9 sentinel POST_FIX_REBUILT cohort verified (commit 2222227). 0.84% win rate on 237 trades flagged as anomalously low (random ≈ 50%); anti-correlated signal OR keyword-overfit hypothesis. → Cycle-17 operator decision active (§B source onboarding OR §C strategic redesign). | `edge-replay-cycle16d-report.md` |

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
