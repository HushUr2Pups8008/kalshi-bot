# Pre-Wave-1 source-class EV baseline

Date: 2026-05-04
Window: 2026-04-18 through 2026-05-03 UTC
Input: Mac archive trade logs plus current May archive logs; realized paper-trade rows from `data/paper_trades.db`.

## Method

Classes use the A.1+ feed-class mapping as a pre-Wave-1 proxy:

- `official`: government bulletin sources
- `specialist_analyst`: legal/geopolitics specialist feeds, including VitalLaw
- `analysis`: proposed A.1+ analyst-feed labels not yet live in the archive
- `news`: remaining mainstream/news sources

Expected return uses `OPPORTUNITY.edge` as dollars per contract. Realized return uses resolved paper trades within 24 h.

## Results

| class | OPPORTUNITY | median expected $/contract | paper trades | resolved <=24 h | median realized PnL | median realized ROI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| news | 241 | 0.000 | 0 | 0 | n/a | n/a |
| analysis | 0 | n/a | 0 | 0 | n/a | n/a |
| official | 1 | 0.000 | 0 | 0 | n/a | n/a |
| specialist_analyst | 22 | 0.000 | 3 | 3 | -2.50 | -100% |

## Readout

No class is EV-positive on median archive opportunity edge before Wave-1. `specialist_analyst` is the only class that produced actual paper trades, but all three were VitalLaw FISA `NO` trades that resolved against the bot.

Operator read: Wave-1 ceiling is not supported by median class-level EV. The actionable signal remains tail behavior and conversion path repair: preserve specialist/legal coverage, but do not treat class membership alone as positive-EV evidence.
