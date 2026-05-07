# Cycle-16E scorer forensics

**Verdict:** `scorer_overadmission_confirmed_no_ic16_slice_after_production_proxy`.

The Cycle-16D operational read is corrected. The raw `237 trades / 2 wins` number is a scorer-overadmission artifact, not a production-like trade count. The corrected production-proxy replay has 12 trades, 0 wins, and 0 IC §16-eligible slices.

## Load-bearing findings

1. Price units are cents-consistent. Captured Kalshi snippets expose `yes_price_dollars`; D3 stored cents via dollars x 100. No 100x unit inversion found.
2. The 50% random-win baseline is invalid for this corpus. Baseline market-implied expected wins are 9.463, not 118.5, because most raw trades are cheap YES longshots.
3. Raw scorer over-admitted trades. Paper price sanity, readiness, same-ticker cooldown, and duplicate-position gates reduce 237 raw trades to 12 production-proxy trades.
4. No IC §16 slice survives corrected replay: 0 slices with `ev_ci_95_lo > 0` and `trades >= 10`.

## Variant comparison

| variant | trades | wins | win_rate | yes/no | market_expected_wins | pnl |
|---|---:|---:|---:|---:|---:|---:|
| `baseline_abs_edge` | 237 | 2 | 0.0084 | 231/6 | 9.4630 | -7.4630 |
| `readiness_only` | 182 | 0 | 0.0000 | 182/0 | 4.1650 | -4.1650 |
| `paper_price_sanity` | 110 | 1 | 0.0091 | 108/2 | 7.7240 | -6.7240 |
| `readiness_plus_price_sanity` | 63 | 0 | 0.0000 | 63/0 | 3.4960 | -3.4960 |
| `readiness_price_signed_edge` | 63 | 0 | 0.0000 | 63/0 | 3.4960 | -3.4960 |
| `production_proxy` | 12 | 0 | 0.0000 | 12/0 | 1.0050 | -1.0050 |

## Production-proxy skip reasons

| reason | rows |
|---|---:|
| `baseline_not_trade` | 35 |
| `paper_duplicate_position` | 43 |
| `paper_price_sanity` | 119 |
| `paper_ticker_cooldown` | 8 |
| `readiness_not_admitted` | 55 |

## Price-unit audit

Unit verdict: `cents_consistent`.

| source | rows | min | max | fractional | <1c |
|---|---:|---:|---:|---:|---:|
| `live_trades` | 3561 | 0.1000 | 99.0000 | 382 | 109 |

Sub-2c prices are real replay inputs, not proof of a unit bug. They are outside the paper executor's `2c..98c` price sanity band and must be excluded from production-like trade counts.

## Production-proxy breakdowns

### By side

| side | trades | wins | win_rate |
|---|---:|---:|---:|
| `yes` | 12 | 0 | 0.0000 |

### By series

| series | trades | wins | win_rate |
|---|---:|---:|---:|
| `KXMOCTRUMP25` | 5 | 0 | 0.0000 |
| `KXPARDONSTRUMP` | 3 | 0 | 0.0000 |
| `KXVANCEPAKISTAN` | 2 | 0 | 0.0000 |
| `KXNEWDEAL` | 1 | 0 | 0.0000 |
| `KXVOTESAVEAMERICA` | 1 | 0 | 0.0000 |

## Re-run command

```bash
.venv/bin/python scripts/edge_replay/scorer_forensics_audit.py \
  --dataset logs/edge_replay/cycle16d/replay_dataset.jsonl \
  --historical-prices logs/edge_replay/cycle16d/historical_prices_cycle16d.json \
  --endpoint-diagnosis logs/edge_replay/cycle16d/endpoint_diagnosis.json \
  --output logs/edge_replay/cycle16e/scorer_forensics.json \
  --corrected-scores logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json \
  --report docs/governance/edge-replay-cycle16e-scorer-forensics.md
```

## Operational consequence

Cycle-16D's `information_frontier_holds` label remains not deploy-positive, but the anti-correlation interpretation is withdrawn. The corrected read is narrower: the previous scorer overstated trade count by admitting longshot YES rows that production paper-mode gates would reject or suppress. After correction, no positive-EV deploy slice exists; capital remains PAPER-ONLY.
