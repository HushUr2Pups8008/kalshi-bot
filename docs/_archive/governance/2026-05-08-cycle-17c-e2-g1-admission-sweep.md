# Cycle-17C E2 G1 Admission Sweep

**Type:** Outcome-blind admission-count projection.
**Dataset:** `logs/edge_replay/cycle16d/replay_dataset.jsonl`
**Rows:** 272

**Guardrail:** This is not an IC §16 replay. Admission counts are projection-only and cannot justify keep/deploy.

No wins, P&L, market-implied expected wins, EV, accepted slices, settlement, or resolution fields are computed.

## Counts

| G1 threshold | baseline_abs_edge | readiness_only | paper_price_sanity | readiness_plus_price_sanity | readiness_price_signed_edge | production_proxy |
|---:|---:|---:|---:|---:|---:|---:|
| 0.05 | 237 | 182 | 110 | 63 | 63 | 12 |
| 0.04 | 237 | 182 | 110 | 63 | 63 | 12 |
| 0.03 | 237 | 182 | 110 | 63 | 63 | 12 |
| 0.02 | 237 | 182 | 110 | 63 | 63 | 12 |
| 0.01 | 237 | 182 | 110 | 63 | 63 | 12 |
| 0.00 | 237 | 182 | 110 | 63 | 63 | 12 |

## Projection Read

All tested G1 thresholds produce identical `readiness_only` and `production_proxy` counts.

Lowering G1 does not admit additional candidates on the frozen corpus. Current G1 already projects `production_proxy >= 10`; loosening it is a no-op for admission count. Do not criteria-lock an E2 G1 implementation unless a fresh operator decision overrides this projection.

## Skip Breakdown

### G1 = 0.05

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |

### G1 = 0.04

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |

### G1 = 0.03

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |

### G1 = 0.02

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |

### G1 = 0.01

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |

### G1 = 0.00

| variant | skipped |
|---|---|
| `baseline_abs_edge` | baseline_not_trade: 35 |
| `readiness_only` | baseline_not_trade: 35, readiness_not_admitted: 55 |
| `paper_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 127 |
| `readiness_plus_price_sanity` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `readiness_price_signed_edge` | baseline_not_trade: 35, paper_price_sanity: 119, readiness_not_admitted: 55 |
| `production_proxy` | baseline_not_trade: 35, paper_duplicate_position: 43, paper_price_sanity: 119, paper_ticker_cooldown: 8, readiness_not_admitted: 55 |
