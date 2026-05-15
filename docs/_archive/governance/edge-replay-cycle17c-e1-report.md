# Cycle-17C E1 replay report — Bayesian log-odds update rule

**Type:** E1 result report.
**Criteria lock:** `4add94f` (`cycle-17c codex: lock e1 criteria`).
**Implementation commit tested:** `fa8e15a` (`cycle-17c codex: implement e1 log odds update`).
**Frozen baseline:** `c913ffd` — 12 production-proxy trades / 0 wins / -$1.005 P&L / 0 IC §16 slices.
**Capital posture:** PAPER-ONLY.

## Verdict

`revert_required_no_ic16_slice`

E1 does not meet IC §16. The Bayesian log-odds update produced only 2 production-proxy trades, 0 wins, and 0 accepted IC §16 slices. The implementation commit must be reverted by default per Cycle-17C rules.

## Replay Inputs

| input | value |
|---|---|
| E1 regenerated dossier DB | `logs/edge_replay/cycle17c/e1/dossier_updates_e1.db` |
| Reingestion rows | 295 |
| Reingestion digest | `7da1c85e7ebf838de6d389207e46b1b3ed9f742c4dea775a99c1c0527e428c84` |
| Replay dataset | `logs/edge_replay/cycle17c/e1/replay_dataset.jsonl` |
| Replay rows | 272 |
| Historical prices | `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` |
| Coverage audit | 271/272 rows covered = 99.6324% |
| Missing-price anomaly | 1 row; same one-row anomaly as Cycle-16D/Cycle-16E class |

## Metrics

| variant | trades | wins | yes/no | market-implied expected wins | P&L |
|---|---:|---:|---:|---:|---:|
| baseline_abs_edge | 237 | 2 | 231/6 | 9.4630 | -7.4630 |
| readiness_only | 90 | 0 | 90/0 | 1.0460 | -1.0460 |
| paper_price_sanity | 110 | 1 | 108/2 | 7.7240 | -6.7240 |
| readiness_plus_price_sanity | 24 | 0 | 24/0 | 0.8100 | -0.8100 |
| readiness_price_signed_edge | 24 | 0 | 24/0 | 0.8100 | -0.8100 |
| production_proxy | 2 | 0 | 2/0 | 0.1500 | -0.1500 |

Production-proxy skip reasons:

| reason | rows |
|---|---:|
| readiness_not_admitted | 147 |
| paper_price_sanity | 66 |
| baseline_not_trade | 35 |
| paper_duplicate_position | 19 |
| paper_ticker_cooldown | 3 |

Production-proxy series breakdown:

| series | trades | wins |
|---|---:|---:|
| KXMOCTRUMP25 | 1 | 0 |
| KXNEWDEAL | 1 | 0 |

## IC §16 Acceptance

| gate | result |
|---|---|
| At least one slice with `ev_ci_95_lo > 0` | FAIL — 0 accepted slices |
| Same slice has `trades >= 10` | FAIL — production-proxy total is 2 trades |
| Candidate-fix keep condition | FAIL |
| Required action | Revert E1 implementation commit |

The apparent P&L improvement versus baseline is not deploy-positive. It comes from suppressing almost all trades, leaving only 2 production-proxy trades. That is below the IC §16 minimum sample threshold and is diagnostic-only at best.

## Commands Run

```bash
.venv/bin/python scripts/edge_replay/reingest_dossier_updates_post_fix.py \
  --source-db data/evidence_store.db \
  --output-db logs/edge_replay/cycle17c/e1/dossier_updates_e1.db \
  --audit logs/edge_replay/cycle17c/e1/reingestion_audit.json

.venv/bin/python scripts/edge_replay/price_coverage_audit.py \
  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --post-fix-db logs/edge_replay/cycle17c/e1/dossier_updates_e1.db \
  --output logs/edge_replay/cycle17c/e1/coverage_audit.json

.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets logs/edge_replay/cycle13_live/resolved_markets_full.json \
  --paper-trades-db /private/tmp/kalshi_cycle16d_no_paper_trades.db \
  --trade-log '/private/tmp/kalshi_cycle16d_no_trade_logs/*.jsonl' \
  --evidence-store-db logs/edge_replay/cycle17c/e1/dossier_updates_e1.db \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --output logs/edge_replay/cycle17c/e1/replay_dataset.jsonl

.venv/bin/python scripts/edge_replay/scorer_forensics_audit.py \
  --dataset logs/edge_replay/cycle17c/e1/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json \
  --output logs/edge_replay/cycle17c/e1/scorer_forensics.json \
  --corrected-scores logs/edge_replay/cycle17c/e1/counterfactual_scores_production_proxy.json \
  --report docs/_archive/governance/edge-replay-cycle17c-e1-report.md
```

## Artifacts

- `logs/edge_replay/cycle17c/e1/reingestion_audit.json`
- `logs/edge_replay/cycle17c/e1/dossier_updates_e1.db`
- `logs/edge_replay/cycle17c/e1/coverage_audit.json`
- `logs/edge_replay/cycle17c/e1/replay_dataset.jsonl`
- `logs/edge_replay/cycle17c/e1/scorer_forensics.json`
- `logs/edge_replay/cycle17c/e1/counterfactual_scores_production_proxy.json`

## Next Action

Revert `fa8e15a`, record E1 as `revert` in the experiment ledger, then choose E2 under the no-overlap rule.
