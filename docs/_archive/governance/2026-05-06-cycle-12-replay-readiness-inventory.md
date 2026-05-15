# Cycle-12 replay-readiness inventory

**Type:** data + API readiness audit. Direct support for Codex's Cycle-12 replay harness.
**Drafted:** 2026-05-06 (cycle 11.5/12 prep).
**Audience:** Codex implementing `scripts/edge_replay/{fetch_resolved_markets,build_replay_dataset,score_counterfactual_pnl}.py`.
**Authority:** strategic redirect doc + IC §16.

## TL;DR

**Replay corpus exists and is non-trivial.** 24 of 36 evidence-store markets are resolved (`status='finalized'` per Kalshi API). Date range 2026-04-20 → 2026-05-05 (~16 days). 266 evidence rows / 7,580 dossier-update-evidence joins. Kalshi REST client has `get_markets(status=...)` already; needs to query BOTH `'settled'` AND `'finalized'` to cover the full resolved universe.

Three readiness gaps Codex will hit:
1. Kalshi has TWO terminal statuses (`settled` + `finalized`); `fetch_resolved_markets.py` must query both.
2. Kalshi `get_market()` returns CURRENT snapshot only; **price history during market lifetime is NOT directly available via existing REST methods.** Replay must reconstruct from bot-side artifacts (evidence ingestion timestamps + any cached `market_snapshot` JSON in `paper_trades`).
3. 89 % of evidence is mainstream news (Guardian / NYT / Al Jazeera / France 24); the only TRADED source (VitalLaw) accounts for 3 evidence rows out of 266 (1.1 %). Replay's per-source slicing has heavy class imbalance.

## Evidence-store inventory (`data/evidence_store.db`)

### Schema (5 tables)

```
dossiers:                  36 rows  (per-market belief state)
evidence:                  266 rows (raw ingestion; key columns: market_ticker, source, source_class, headline, ingested_ts)
dossier_updates:           266 rows (1:1 with evidence — every evidence triggers an update)
dossier_update_evidence:   7,580 rows (join: which evidence contributed to which dossier version)
structural_priors:         36 rows  (per-market structural prior snapshot)
```

### Date range

```
first ingestion: 2026-04-20T03:43:52Z
last ingestion:  2026-05-05T22:47:36Z
window:          ~16 days
```

### Source distribution (top sources, per evidence_store)

| source | events | % |
|---|---:|---:|
| Middle East and north Africa \| The Guardian | 98 | 36.8 % |
| World news \| The Guardian | 42 | 15.8 % |
| NYT > World News | 30 | 11.3 % |
| Al Jazeera | 25 | 9.4 % |
| France 24 | 11 | 4.1 % |
| Ukraine \| The Guardian | 7 | 2.6 % |
| The Times of Israel | 7 | 2.6 % |
| The Kyiv Independent | 5 | 1.9 % |
| Kyiv Post | 4 | 1.5 % |
| **VitalLaw.com** (only source bot traded) | **3** | **1.1 %** |

```
source_class:
  news:     217 (81.6 %)
  other:    48  (18.0 %)
  official: 1   (0.4 %)
```

**Implication for replay:** the per-source × market_family × signal_type slicing in `score_counterfactual_pnl.py` has severe class imbalance. Slices with n < 10 should be flagged as inconclusive. The "official" class has n=1; the only TRADED source (VitalLaw) has n=3. Honest replay must report these as low-power samples, not hide them.

### Market coverage

36 distinct `market_ticker` values in evidence. Per-market evidence concentration (top 5):

| market | evidence rows | resolved? |
|---|---:|---|
| KXTRUMPIRAN-26MAY01 | 107 | ✅ no |
| KXMOCTRUMP25-26-MAY01 | 51 | ✅ no |
| KXMOCTRUMP25-26-APR24 | 19 | ✅ no |
| KXVANCEPAKISTAN-26APR21-APR30 | 15 | ✅ no |
| KXTRUMPIRAN-26JUN01 | 10 | active |

### Settlement audit (24 of 36 resolved)

Cross-reference of `evidence.market_ticker` against live Kalshi `get_market(ticker).status`:

```
finalized: 24
active:    12
unknown:    0
```

**Replay-eligible markets (24):** KXELECTIONEMERGENCY-26MAY01, KXFISAEXTEND-26APR-APR29/APR30/MAY01/MAY02/MAY03, KXMOCTRUMP25-26-APR24/MAY01, KXNEWDEAL-MAY01, KXPARDONSTRUMP-26APR-0/1/12/22/24/6, KXTRUMPCHINA-26-APR24/MAY01, KXTRUMPIRAN-26MAY01, KXVANCEPAKISTAN-26APR21-APR24/APR25/APR27/APR30/MAY04, KXVOTESAVEAMERICA-26MAR-MAY01.

Resolution distribution: **20 NO, 4 YES** (FISA-MAY01/02/03 + KXPARDONSTRUMP-26APR-0). Heavy NO-resolution skew. Bot's only 3 trades (all NO-side bets on FISA) lost because the 3 traded markets resolved YES.

## Kalshi REST API gaps (relevant to `fetch_resolved_markets.py`)

### Existing methods (`kalshi/rest_client.py`)

- `get_markets(status, cursor, limit, series_ticker, min_close_ts, max_close_ts)`: paged list. Codex can query with `status='settled'` AND `status='finalized'` to enumerate resolved markets in a date range.
- `get_market(ticker)`: per-market current snapshot. Returns `KalshiMarket(status, result, ...)`.

### Discovered gotcha — TWO terminal statuses

Kalshi's status taxonomy includes both `"settled"` AND `"finalized"`. Both are terminal (resolved); the difference is age / trading-window. `get_markets(status='settled')` returns recent settles; `get_markets(status='finalized')` returns older ones. Codex's `fetch_resolved_markets.py` MUST query both to enumerate the full resolved universe. Filtering `result` field directly (any non-empty string = resolved) is a robust alternative.

Also confirmed: live `KalshiMarket.result` field IS populated for finalized markets (`'yes'` or `'no'` per market_type=binary).

### CRITICAL gap — no price-history endpoint exposed

`get_market(ticker)` returns the CURRENT snapshot (`yes_bid`, `yes_ask`, `yes_price`, `previous_yes_bid_dollars`, `previous_yes_ask_dollars`, `previous_price_dollars`). This is the latest tick + one-tick-back. **It does NOT provide historical price at arbitrary timestamps T during the market's lifetime.**

Replay scoring requires "what was Kalshi's YES price at the time the bot's evidence was ingested?" — i.e., per-decision-point historical price. Options Codex should evaluate:

1. **Kalshi `/trades` or `/markets/{ticker}/trades` endpoint** (if exists). Per-trade history would let replay reconstruct mid-price at any T. Live test: `c._request('GET', f'/markets/{ticker}/trades')` — try this in Codex's first task to confirm the endpoint exists.
2. **Bot-side cached snapshots** in `paper_trades.market_snapshot` JSON field (column `market_snapshot TEXT`). For markets bot already traded, snapshots may exist. For untraded markets, this won't help.
3. **Reconstruct from `evidence.raw_payload_json`** if the bot logged market price alongside each evidence ingestion. Worth checking.
4. **Settlement-only proxy**: replay scores ONLY on resolution outcome (would-have-traded × resolution = win/loss × cost basis). Skips intra-market-lifetime EV. Less informative but achievable with current data.

If Option 1 (Kalshi trades endpoint) doesn't exist, Codex should fall back to Option 4 with explicit caveat in the report. Option 4 still answers the load-bearing question ("does any feature slice have positive replayed EV?") but understates EV-vs-realtime nuance.

## Output schema scaffolding (for `score_counterfactual_pnl.py`)

The replay-output table contract Codex should produce. Recommended Pandas-DataFrame / CSV / JSON schema:

```
columns:
  source              str   # signal_source (e.g. "VitalLaw.com", "Middle East ... Guardian")
  market_family       str   # series_ticker prefix (e.g. "KXFISAEXTEND", "KXVANCEPAKISTAN")
  signal_type         str   # "news" / "official" / "other" / "fast_lane" / "structural" — bot's lane
  source_class        str   # "news" / "official" / "other" (matches evidence.source_class)
  trades              int   # would-have-traded count under current (or Wave-1-projected) gates
  wins                int   # trades where resolution side matched bet side
  win_rate            float # wins / trades; NaN if trades = 0
  pnl_dollars         float # sum across all would-have-traded; cost basis = $2.50/contract default
  ev_per_trade        float # pnl / trades
  ev_ci_95_lo         float # 95% CI lower bound on ev_per_trade (bootstrap or t-test)
  ev_ci_95_hi         float # 95% CI upper bound
  sharpe              float # ev_per_trade / std(per-trade-pnl); NaN if trades < 5
  notes               str   # "n<10 inconclusive" / "single-cluster correlated" / etc.
```

Aggregate also at coarser slices:
- per-source-only (collapse market_family + signal_type)
- per-market_family-only
- per-source × market_family
- "all" (overall replayed EV)

**Success criterion (per IC §16 Rule 5 + strategic-redirect §"Cycle-12 Codex assignment"):**
- Identify ≥1 row in the table with `ev_ci_95_lo > 0` (positive EV at 95 % CI), AND `trades ≥ 10` (not single-cluster).
- OR explicitly report "no slice has positive replayed EV at our sample size."

The table itself is the deliverable. The interpretation report (`docs/_archive/governance/edge-replay-cycle12-report.md`) summarizes which row(s) — if any — pass the threshold.

## Pre-replay sanity checks Codex should hit early

Before running the full harness, Codex should validate:

1. **All 24 finalized markets have ≥ 1 evidence row.** True by construction (set is intersection of evidence_store and resolved); confirm anyway via an assert.
2. **Bot's 3 paper trades replay correctly under the current gates.** I.e., feed the 3 traded markets through `score_counterfactual_pnl.py` and verify it reproduces the actual `paper_trades` history (3 trades, 0 wins, -$7.50). This is the harness self-test — if replay can't reproduce known history, it's broken.
3. **Per-decision-point timestamps align.** `evidence.ingested_ts` is the bot's decision input timestamp. The harness needs this to query "Kalshi price at T" — confirm the timestamp is monotonic + populated for all evidence rows.

If any of these 3 fail, the harness has bugs that invalidate downstream conclusions.

## What this inventory unblocks

Codex's `fetch_resolved_markets.py`: confirmed approach (query both `status` values; cross-reference with `evidence.market_ticker`). Sample size: 24 markets in current 16-day window.

Codex's `build_replay_dataset.py`: data shape known (`evidence` + `dossier_updates` + `paper_trades` joined per market). Per-decision-point reconstruction is straightforward; price-history reconstruction is the gap (see API gap §3 above).

Codex's `score_counterfactual_pnl.py`: schema above. Replay-eligible window: ~13 days (drop first 3 days of bot startup noise). Per-source distribution shows VitalLaw n=3, mainstream news ~80 % — class imbalance reporting is required.

## Cross-links

- `docs/_archive/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — redirect authority
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate
- `data/evidence_store.db` — replay input
- `data/paper_trades.db` — replay self-test target (3 trades, 0 wins, -$7.50)
- `kalshi/rest_client.py` — REST methods Codex extends
