# Edge Replay Cycle 13 Report

Date: 2026-05-06

## Verdict

Cycle-13 expanded replay from the Cycle-12 local paper-trade sample to the live Kalshi-resolved evidence-store scope.

Result: no positive-EV feature slice found.

This keeps Wave-2 and Wave-3 behavioral deploys halted under Implementation Contract §16.

## Live Scope

The live fetch attempted Kalshi `get_markets` with both status filters named in the readiness inventory:

- `settled`
- `finalized`

Kalshi returned `400 invalid status filter` on the list endpoint. The harness then fell back to bounded per-ticker `get_market` lookups for tickers present in `data/evidence_store.db`.

Live replay scope:

| Metric | Value |
|---|---:|
| Resolved markets | 24 |
| Replay rows | 255 |
| Unique tickers | 24 |
| Executable trades | 3 |
| Wins | 0 |
| Win rate | 0.00 |
| P&L | -$7.50 |
| Average P&L / trade | -$2.50 |
| Bootstrap 95% EV CI | [-$2.50, -$2.50] |
| Positive-EV slices | 0 |

Replay row mix:

| decision_kind | rows |
|---|---:|
| dossier_update | 238 |
| paper_trade | 3 |
| skipped | 1 |
| paper_resolution | 2 |
| match_diagnostic | 5 |
| analysis_rejected | 5 |
| signal_analysis_detail | 1 |

## Harness Changes

- `fetch_resolved_markets.py`
  - added `--live-kalshi`
  - queries both `settled` and `finalized`
  - supports evidence-store intersection
  - falls back to per-ticker `get_market` when list status filters fail

- `build_replay_dataset.py`
  - supports evidence-store dossier replay rows
  - accepts optional historical price JSON via `--historical-prices`
  - filters trade-log `PAPER_TRADE` duplicates so `paper_trades.db` remains the source of truth for executed trades

- `score_counterfactual_pnl.py`
  - adds synthetic positive-EV coverage
  - replaces point CI with bootstrap CI
  - requires `ev_ci_95_lo > 0` for positive-EV slice eligibility
  - adds readiness-gate admission and left-on-table counters for non-executed dossier rows

- `fetch_historical_prices.py`
  - probes Kalshi `/markets/{ticker}/trades`
  - normalizes returned trade rows into ticker-price JSON for replay

- `ingestion_freshness_check.py`
  - asserts `evidence_store.evidence.max(ingested_ts)` freshness

- `run_full_replay.sh`
  - runs fetch -> build -> score -> freshness with local or live defaults
  - wired into `scripts/pre_day7_smoke.sh` as a warn-level replay sanity gate

## Price Reconstruction

The replay harness can now consume decision-time price rows from `--historical-prices`.

Live endpoint probe:

```text
GET /markets/{ticker}/trades
```

Probe result for the first three live resolved tickers: `404 page not found`.

No decision-time price history was available from that endpoint in this API surface, so Cycle-13 scoring uses actual `paper_trades.db` execution prices for executed trades and does not synthesize executable P&L for dossier-only rows without market price.

## Left On The Table

Readiness-gate scoring found:

| Metric | Value |
|---|---:|
| left_on_table | 0 |
| left_on_table_would_have_won | 0 |

Under the current readiness thresholds, dossier-only rows did not identify a missed winning trade surface.

## Ingestion Freshness

Freshness check result during Cycle-13:

| Metric | Value |
|---|---:|
| last_ingested_ts | 2026-05-06T18:51:44.474522+00:00 |
| age_hours | 3.697 |
| max_age_hours | 6.0 |
| ok | true |

Replay scope is not invalidated by a stale evidence-store ingestion window.

## Artifacts

Live run output:

- `logs/edge_replay/cycle13_live/resolved_markets_full.json`
- `logs/edge_replay/cycle13_live/replay_dataset.jsonl`
- `logs/edge_replay/cycle13_live/counterfactual_scores.json`
- `logs/edge_replay/cycle13_live/ingestion_freshness.json`
- `logs/edge_replay/cycle13_live/historical_prices.json`
- `logs/edge_replay/cycle13_live/historical_prices.errors.json`

Local sanity output:

- `logs/edge_replay/cycle13_local/`

## Commands

```bash
bash scripts/edge_replay/run_full_replay.sh --live-kalshi --max-pages-per-status 20 --sleep-seconds 0.5 --out-dir logs/edge_replay/cycle13_live
.venv/bin/python scripts/edge_replay/fetch_historical_prices.py --markets logs/edge_replay/cycle13_live/resolved_markets_full.json --output logs/edge_replay/cycle13_live/historical_prices.json --limit 3 --sleep-seconds 0.5
```

## Decision

There is still no replay-backed reason to proceed with Wave-2 feed onboarding or Wave-3 threshold/lever changes.

The next edge-producing step is not deploy. It is one of:

- import a source of decision-time historical prices, then rerun replay with `--historical-prices`
- widen resolved-market history beyond current evidence-store coverage
- change the model/calibration hypothesis and require the same replay evidence before deploy

## Production-Thresholds Rerun

Cycle-14 pre-queue item 1 reran the Cycle-13 replay scorer against the existing live replay dataset with production readiness sensitivity:

```bash
.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py --dataset logs/edge_replay/cycle13_live/replay_dataset.jsonl --readiness-confidence 0.05 --output logs/edge_replay/cycle13_live/counterfactual_scores_readiness_005.json
```

Result:

| Metric | Value |
|---|---:|
| Replay rows | 255 |
| Executable trades | 3 |
| Wins | 0 |
| P&L | -$7.50 |
| Positive-EV slices | 0 |
| Left-on-table winners | 0 |

Lowering the readiness confidence threshold to `0.05` does not create a positive-EV slice and does not surface missed winners.
