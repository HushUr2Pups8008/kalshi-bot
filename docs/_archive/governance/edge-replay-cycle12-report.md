# Edge Replay Cycle 12 Report

Date: 2026-05-06

## Scope

Cycle-12 redirects post-Wave-1 work from speculative deploy prep to replayed edge evidence.

This pass builds the replay harness and runs it against currently available local resolved evidence only:

- `data/paper_trades.db.paper_trades`
- `logs/**/*.jsonl`
- no live Kalshi API call
- no production behavior change

## Artifacts

- `scripts/edge_replay/fetch_resolved_markets.py` — normalizes resolved Kalshi markets from API JSON, live API, or local resolved paper trades.
- `scripts/edge_replay/build_replay_dataset.py` — joins resolved market labels to local paper trades, matching trade-log decisions, and evidence-store dossier updates.
- `scripts/edge_replay/score_counterfactual_pnl.py` — scores candidate decisions by counterfactual P&L and groups by source, series, signal type, and news class.
- `tests/test_edge_replay_fetch_resolved_markets.py`
- `tests/test_edge_replay_build_replay_dataset.py`
- `tests/test_edge_replay_score_counterfactual_pnl.py`

Generated local artifacts:

- `docs/governance/edge-replay-cycle12-artifacts/resolved_markets.json`
- `docs/governance/edge-replay-cycle12-artifacts/replay_dataset.jsonl`
- `docs/governance/edge-replay-cycle12-artifacts/counterfactual_scores.json`

## Local Replay Result

Input coverage:

- resolved markets: 3
- replay dataset rows: 6
- executed counterfactual trades: 3
- dossier-update rows without executable market price: 3

Overall result:

| Metric | Value |
|---|---:|
| Wins | 0 |
| Trades | 3 |
| Win rate | 0.00 |
| Counterfactual P&L | -$7.50 |
| Average P&L / trade | -$2.50 |
| Positive-EV slices | 0 |

Scored slices:

| signal_source | series_ticker | signal_type | news_class | candidates | trades | wins | P&L |
|---|---|---|---|---:|---:|---:|---:|
| VitalLaw.com | KXFISAEXTEND | state | other | 3 | 0 | 0 | $0.00 |
| VitalLaw.com | KXFISAEXTEND | blend | unknown | 3 | 3 | 0 | -$7.50 |

## Decision

No positive-EV feature slice was found in current local resolved evidence.

This does not prove no edge exists anywhere. It proves the current local resolved sample does not support Wave-2 or Wave-3 behavioral deployment. Per Implementation Contract §16, speculative feed onboarding and threshold loosening remain halted until replayed-EV evidence identifies a positive candidate slice.

## Data Limitation

The local resolved-outcome sample is still too small for broad feature discovery:

- 3 resolved paper trades
- all one source
- all one series
- all one direction

The harness is now in place, but the next evidence-producing step is expanding resolved-market labels beyond local paper trades. That can be done by running `fetch_resolved_markets.py` against Kalshi resolved markets or by importing a saved Kalshi markets payload, then rebuilding and rescoring the dataset.

## Verification

```bash
.venv/bin/pytest tests/test_edge_replay_fetch_resolved_markets.py tests/test_edge_replay_build_replay_dataset.py tests/test_edge_replay_score_counterfactual_pnl.py
```

Result: 8 passed.

Local replay commands:

```bash
.venv/bin/python scripts/edge_replay/fetch_resolved_markets.py --from-paper-trades-db data/paper_trades.db --output docs/governance/edge-replay-cycle12-artifacts/resolved_markets.json
.venv/bin/python scripts/edge_replay/build_replay_dataset.py --markets docs/governance/edge-replay-cycle12-artifacts/resolved_markets.json --paper-trades-db data/paper_trades.db --evidence-store-db data/evidence_store.db --trade-log 'logs/**/*.jsonl' --output docs/governance/edge-replay-cycle12-artifacts/replay_dataset.jsonl
.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py --dataset docs/governance/edge-replay-cycle12-artifacts/replay_dataset.jsonl --output docs/governance/edge-replay-cycle12-artifacts/counterfactual_scores.json
```
