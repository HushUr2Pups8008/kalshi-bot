# Kalshi API Drift, Pricing Correctness, and Official-Source Roadmap

**Date:** 2026-05-11
**Status:** ACTIVE ROADMAP — review complete, accepted decisions folded in (2026-05-11); live fixture capture complete (2026-05-11T13:37Z); P0 implementation planning unblocked
**Authority:** operator directive after live-pricing and Kalshi API-surface audits; Claude review accepted 2026-05-11; field-drift hypothesis empirically confirmed via 5 read-only fixture payloads (2026-05-11)
**Capital posture:** PAPER-ONLY hard guardrail unchanged
**Runtime posture:** no runtime, trading, threshold, feed, analysis, config, `.env`, launchd, environment, plugin, or order-placement changes authorized by this document

**Tracking-doc invariant:** this file is the single source of truth for official-source discovery, the pricing bug, broader Kalshi API surface gaps, D1–D5 accepted decisions, P0/P1/P2/P3 sequencing, review questions and resolutions, evidence appendix, and P0 acceptance tests/gates. Do not create parallel tracking docs (per `CLAUDE.md` One Document rule, debt-log `R-10 — No New Tracking Files`).

## TL;DR

Codex found two connected architecture findings; Claude review (2026-05-11) validated evidence, surfaced additional missed paths, and resolved D1–D5 decisions:

1. Kalshi official rules, settlement sources, and contract URLs appear programmatically available for bot-relevant markets such as the KXTRUMPIRAN / VISITAREA case. Supports a future ticker-driven official-source lane.
2. That lane is blocked behind a higher-priority correctness issue: current market pricing appears stuck at synthetic `0.50` / `50c` because repo code reads legacy Kalshi fields and silently falls back to `50`.

Roadmap order:

| phase | status | purpose | completion gate |
|---|---|---|---|
| P0 | ACTIVE NEXT (prerequisite + 3 decisions accepted) | Kalshi API contract stabilization, pricing correctness, fail-closed including exchange-status gating, two-sided EV, spread validity invariant | no silent 50c fallback; fixed-point fields parsed; executable YES/NO prices available or fail-closed; spread invariant enforced; paused/inactive markets fail-closed; paper fills use executable side price; fixture-backed compatibility tests pass; 22-test acceptance matrix + 8 additional gates green |
| P1 | BLOCKED BY P0 | Trade-quality and market-state policy controls (spread *filter*, depth, liquidity, full lifecycle policy, fee-aware EV) | spread/depth/liquidity/lifecycle policy gates defined and tested |
| P2 | OFFLINE PROTOTYPE PARALLEL-ALLOWED; runtime promotion blocked by P0/P1 | Official-source and market-memory lane | rules/source/contract snapshots integrated offline first; hard import boundary against runtime modules; no runtime feed/trading changes without review |
| P3 | DEFERRED | WebSocket/tape/microstructure enhancements | REST snapshot correctness and replay parity already proven |

Current paper results remain useful for non-price diagnostics only. They are contaminated for EV, side selection, fill price, P&L, and non-zero-edge quality conclusions.

## 1. Official-Source Discovery

### Finding

Kalshi appears to expose official market rules, settlement-source metadata, and contract URLs programmatically enough to justify a later offline prototype.

### Endpoints in scope

- `GET /markets/{ticker}`
- `GET /events/{event_ticker}`
- `GET /events/{event_ticker}?with_nested_markets=true`
- `GET /events/{event_ticker}/metadata`
- `GET /series/{series_ticker}`

### Fields in scope

- `rules_primary`
- `rules_secondary`
- `early_close_condition`
- `settlement_sources`
- `contract_url`
- `contract_terms_url`

### Example case

- Market page: `https://kalshi.com/markets/kxtrumpiran/will-trump-visit-iran/kxtrumpiran`
- Contract terms PDF: `https://kalshi-public-docs.s3.amazonaws.com/contract_terms/VISITAREA.pdf`
- Mapping pattern: `market_ticker` -> `event_ticker` -> `series_ticker` -> `contract_url` / `contract_terms_url` / `settlement_sources`

### Strategic interpretation

- Official-source monitoring should become a ticker-keyed evidence lane if the metadata generalizes across bot-relevant markets.
- Generic news remains an early-warning and discovery lane.
- PDF parsing remains fallback/archive behavior, not the primary operational extraction path.
- This work is P2. Offline prototype is permitted in parallel (see §4.D4); runtime promotion is blocked by P0/P1.

## 2. Pricing Bug

### Confirmed code evidence

The REST client reads legacy fields and silently falls back to `50`:

- [kalshi/rest_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/rest_client.py:213): list-market `yes_bid` fallback.
- [kalshi/rest_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/rest_client.py:214): list-market `yes_ask` fallback.
- [kalshi/rest_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/rest_client.py:215): midpoint `yes_price`.
- [kalshi/rest_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/rest_client.py:241): single-market `yes_bid` fallback.
- [kalshi/rest_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/rest_client.py:242): single-market `yes_ask` fallback.

The market model drops current API market-state fields:

- [kalshi/__init__.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/__init__.py:6): `KalshiMarket`.
- [kalshi/__init__.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/__init__.py:9): `yes_bid`.
- [kalshi/__init__.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/__init__.py:10): `yes_ask`.
- [kalshi/__init__.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/__init__.py:11): midpoint `yes_price`.

Missing from the model today:

- `no_bid`
- `no_ask`
- bid/ask sizes
- last/previous price
- price timestamp
- price source
- `price_available`
- spread
- orderbook depth
- fixed-point volume/open-interest fields
- `exchange_active`, `trading_active`

### Downstream propagation

- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:645): captures `market_yes_price = market.yes_price`.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:664): reads WebSocket midpoint helper.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:667): overwrites `market.yes_price` with WS midpoint.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:668): reconstructs `yes_bid` as `ws_price - 1`.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:669): reconstructs `yes_ask` as `ws_price + 1`.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:713): computes YES-side edge from midpoint probability.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:714): selects side from sign of YES-side edge.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:729): passes `market.yes_price` to Kelly sizing.
- [main.py](/Users/jacobparenti/vscode/kalshi-bot/main.py:905): **second identical ws-price mutation block** (fade path) — Claude review §3 addition.
- [tasks/blend_task.py](/Users/jacobparenti/vscode/kalshi-bot/tasks/blend_task.py:382): blend edge uses carried market price.
- [tasks/blend_task.py](/Users/jacobparenti/vscode/kalshi-bot/tasks/blend_task.py:514): candidate carries `market_yes_price`.
- [trading/paper_trader.py](/Users/jacobparenti/vscode/kalshi-bot/trading/paper_trader.py:490): paper YES fill uses midpoint; NO fill uses `100 - midpoint`.
- [trading/paper_trader.py](/Users/jacobparenti/vscode/kalshi-bot/trading/paper_trader.py:508): paper snapshot stores already-reduced dataclass.
- [trading/executor.py](/Users/jacobparenti/vscode/kalshi-bot/trading/executor.py:315): `analysis.market_yes_price` overwritten from stale blend candidate — Claude review §3 addition.
- [trading/executor.py](/Users/jacobparenti/vscode/kalshi-bot/trading/executor.py:402): live YES order uses `yes_ask`.
- [trading/executor.py](/Users/jacobparenti/vscode/kalshi-bot/trading/executor.py:403): live NO order uses `100 - yes_bid`.
- [trading/portfolio.py](/Users/jacobparenti/vscode/kalshi-bot/trading/portfolio.py:27): `OpenPosition` persists `price_cents` and `market_yes_price`; same-signal guard reads back contaminated values — Claude review §3 addition.
- [analysis/__init__.py](/Users/jacobparenti/vscode/kalshi-bot/analysis/__init__.py:14): `SignalAnalysis.market_yes_price` duplicates `market.yes_price`; drift path between the two stores — Claude review §3 addition.
- [analysis/market_matcher.py](/Users/jacobparenti/vscode/kalshi-bot/analysis/market_matcher.py:487): API call hardcodes `status="open"`; `"active"` markets never returned by the call — Claude review §3 addition.
- [kalshi/websocket_client.py](/Users/jacobparenti/vscode/kalshi-bot/kalshi/websocket_client.py:154): `get_yes_price()` returns midpoint only.

### Replay / edge-replay consumers of contaminated values

Claude review §3.2 — nine scripts under `scripts/edge_replay/` consume `market_yes_price` / `price_cents` from logs. P0 acceptance includes a replay-parity gate:

| Script | Key lines |
|---|---|
| `build_replay_dataset.py` | 99, 160-168, 264-288 |
| `fetch_resolved_markets.py` | 90, 117, 119, 139, 163, 175-177, 208-223 |
| `fetch_historical_prices.py` | 29, 107-111 |
| `score_counterfactual_pnl.py` | 47-49, 53-54, 86 |
| `side_flip_counterfactual.py` | 91-92, 139, 157, 172 |
| `g1_admission_sweep.py` | 90-92, 98, 106, 183, 197 |
| `scorer_forensics_audit.py` | 72, 109, 234, 251, 277, 330 |
| `cycle17d_schema_audit.py` | 36, 52, 137 |
| `price_coverage_audit.py` | 88-89, 117 |

### Latent `50` / `0.5` constants outside `rest_client.py`

Claude review §3.3 — production-replay silent fallbacks must be removed or annotated to fail-closed in P0; synthetic test/simulation defaults remain acceptable if explicitly tagged.

| File | Lines | Class |
|---|---|---|
| `scripts/performance_analysis.py` | 568 | **Production replay** — must fix in P0 |
| `scripts/edge_replay/reingest_dossier_updates_post_fix.py` | 96 | **Production replay** — must fix in P0 |
| `scripts/edge_replay/cycle15b_common.py` | 51-52 | Synthetic test fixture |
| `scripts/edge_replay/synthetic_injection_lanes.py` | 117-118 | Synthetic test fixture |
| `scripts/edge_replay/per_step_extraction_trace.py` | 54-55 | Synthetic test fixture |
| `scripts/simulations/_common.py` | 154 | Simulation default |
| `scripts/simulations/executor_validate.py` | 84 | Simulation default |
| `scripts/simulations/obs003_skipped_stream_synthesis.py` | 78, 97 | Simulation default |
| `scripts/simulations/g1_admittance_counterfactual.py` | 87-88 | Simulation default |

### DB schema persistence

- `trading/paper_trader.py:61,64,78` — `paper_trades` schema: `price_cents INTEGER NOT NULL`, `market_yes_price REAL NOT NULL`, `market_snapshot TEXT` (full serialized `KalshiMarket` dict).
- `trading/portfolio.py:53-54` — read query consumes `price_cents`, `market_yes_price` for same-signal guard.

Decision required at P0 implementation time: historical rows in `data/paper_trades.db` are persistently contaminated. Mark with a contamination flag rather than backfill. Recommendation locked.

### Runtime evidence

| runtime surface | rows inspected | numeric price values observed | non-50 observed | status |
|---|---:|---|---:|---|
| `SIGNAL_ANALYSIS_DETAIL` | 25 | `market_price: 0.5` | 0 | confirmed by logs |
| `OPPORTUNITY` | 11 | `market_yes_price: 50` | 0 | confirmed by logs |
| `SKIPPED` | 9 | `market_price: 50` | 0 | confirmed by logs |
| `PAPER_TRADE` | 2 | `market_yes_price: 50`, `price_cents: 50` | 0 | confirmed by logs |
| `data/paper_trades.db` | 9 | `market_yes_price=50`, `price_cents=50` for all rows | 0 | confirmed by DB |
| combined `logs/trades/live/trades.jsonl` scan | 26,165 | distinct numeric market-price fields only `0.5` / `50` | 0 | confirmed by logs |

### Confidence split

| claim | status |
|---|---|
| Repo code silently falls back to `50` | confirmed by code |
| Runtime/paper prices inspected are all `0.5` / `50` | confirmed by logs/DB |
| Root cause is Kalshi API field drift | confirmed by docs plus code mismatch; validate live payload before implementation (see §6 prerequisite) |
| Existing paper EV/P&L is contaminated | inferred from confirmed code/log flow; high confidence |

## 3. Broader API-Surface Findings

Current Kalshi docs show the bot should treat market state as a wider contract than just `yes_price`.

### P0 API contract surfaces

Confirmed empirically by §6 fixture capture (2026-05-11):

- Fixed-point prices (market-level, 100% present): `yes_bid_dollars`, `yes_ask_dollars`, `no_bid_dollars`, `no_ask_dollars`, `last_price_dollars`, `previous_price_dollars`, `previous_yes_bid_dollars`, `previous_yes_ask_dollars`.
- Fixed-point quantities (market-level, 100% present): `volume_fp`, `volume_24h_fp`, `open_interest_fp`, `yes_bid_size_fp`, `yes_ask_size_fp`. (NO-side sizes require orderbook endpoint, not in `/markets` payload.)
- Precision metadata (market-level, 100% present): `price_level_structure`, `price_ranges`, `fractional_trading_enabled`, `response_price_units`, `notional_value_dollars`.
- Operationally relevant additions (market-level, 100% present): `liquidity_dollars`, `created_time`, `updated_time`, `market_type`.
- **Exchange / market-state gating (promoted into P0 — D1, corrected per §6.2 finding #1):**
  - **Global**: `exchange_active`, `trading_active` — live on `GET /exchange/status`, NOT per-market. P0 parser must consume `/exchange/status` separately and gate ALL markets uniformly when global posture is paused.
  - **Per-market**: `status == "paused"` (also "closed", "settled", "finalized" — only `"active"` is tradeable; see §6.2 finding #3 — `"active"` is the live state name, not `"open"`).
  - Fail-closed predicate: `(global.exchange_active AND global.trading_active AND market.status == "active") OR fail-closed`.
- Status value caveat (corrected per §6.2 finding #3): the bot's request param `status=open` returned `status="active"` markets. CLAUDE.md already records this; P0 parser must recognize `"active"` and the `analysis/market_matcher.py:487` filter must be corrected.
- Full lifecycle policy (`open_time`, `close_time`, `expected_expiration_time`, `latest_expiration_time`, `settlement_timer_seconds`, `settlement_ts`, `can_close_early`, `early_close_condition`, `is_provisional`) remains P1. Note: `is_provisional` is conditionally present (sports yes, politics no).
- **Order-direction fields (`outcome_side`, `book_side`) RECLASSIFIED to P2/P3** per §6.2 finding #2 — these are trade-tape / order-event fields, not market-state.

### P1 API contract surfaces

- Market lifecycle policy: `open_time`, `close_time`, `expected_expiration_time`, `latest_expiration_time`, `settlement_timer_seconds`, `settlement_ts`, `can_close_early`, `early_close_condition`, `is_provisional`.
- Status caveat: current docs include `paused` in the `GET /markets` status enum even where prose summaries use older wording.

### P2 API contract surfaces

- REST orderbook: `orderbook_fp`; yes-bid and no-bid ladders only, with asks derived by binary complement.
- WebSocket orderbook caveat: default no-side scale can differ unless `use_yes_price: true` is used; normalize before storage.
- Trade tape: `GET /markets/trades` with `count_fp`, yes/no price fields, taker direction fields (`outcome_side`, `book_side`, `taker_outcome_side`, `taker_book_side` — reclassified from P0 per §6.2 finding #2), `created_time`.
- Rules/source metadata (corrected per §6.2 finding #4 — confirmed empirically by fixture capture):
  - **Market-level** (`GET /markets/{ticker}`, observed on KXTRUMPIRAN-27JAN01): `rules_primary`, `rules_secondary`, `early_close_condition`.
  - **Series-level** (`GET /series/{ticker}`, observed on KXTRUMPIRAN: 17 settlement sources, both URLs present): `settlement_sources`, `contract_url`, `contract_terms_url`, `fee_type`, `fee_multiplier`, `tags`, `additional_prohibitions`, `frequency`.
  - **Event-level** (`GET /events/{ticker}`): `category`, `sub_title`, `title`, `mutually_exclusive`, `available_on_brokers`, `series_ticker`; event response includes nested `markets[]` by default (§6.3 finding #2).
- Auth caveat: docs disagree on REST orderbook auth; live verification required.

### P3 API contract surfaces

- Event nested markets: prefer `event.markets` with `with_nested_markets=true` over deprecated top-level `markets`.
- Event metadata: source/display enrichment.
- Series metadata: template-level settlement sources, contract URLs, fee fields, product metadata.
- Metadata polling hooks: `min_updated_ts` for non-trading metadata refresh, not price refresh.

## 4. Accepted Decisions (D1–D5, 2026-05-11)

Operator accepted Claude review recommendations per the matrix below. Decisions are now binding scope for P0.

| ID | Decision | Status | P0 scope impact |
|---|---|---|---|
| D1 | Promote exchange-status fail-closed gating from P1 into P0 | **ACCEPTED** | `exchange_active`, `trading_active`, `status==paused` fail-closed in same predicate as `price_available=False`. Full lifecycle policy stays P1. |
| D2 | P0 must compute both YES and NO EV using side-specific executable prices | **ACCEPTED** | Side selection consumes side-specific executable price; never reconstructs NO from `100 - yes_midpoint`. Paper fill uses executable side ask. |
| D3 | Spread *validity invariant* (`0 <= bid <= ask <= 100`) in P0; spread *policy filter / threshold* stays P1 | **ACCEPTED** | Invariant violation → `price_available=False`. No tunable spread threshold in P0. |
| D4 | Allow parallel offline-only P2 prototype | **ACCEPTED, with strict boundary** | Prototype writes to `data/market_memory/` or `logs/p2_prototype/`. **Hard one-directional import boundary**: must not import from `trading/`, `tasks/blend_task.py`, `main.py`, `analysis/`, `kalshi/websocket_client.py`, or any runtime decision module. Must not modify runtime behavior. Boundary enforced at PR-review time. |
| D5 | Three-layer kill-switch (env var + drift detector + fixture-pinned CI) | **PARTIALLY ACCEPTED** | (a) Fixture-pinned CI gate → **ACCEPTED in P0**. (b) Field-drift detector → scope TBD between P0 and P1 based on implementation cost; specify at P0 implementation-design step. (c) `KALSHI_API_CONTRACT` env var → **NOT APPROVED YET** (touches runtime/config behavior outside current authorization). Re-propose with explicit operator sign-off if needed. |

## 5. Roadmap

### P0 — Kalshi API Contract Stabilization + Fail-Closed Detection

Goal: make current market state trustworthy enough for EV, side selection, paper fills, and logs; gate paused/inactive markets at the same boundary; remove silent fallbacks across runtime and production-replay paths.

Scope (mandatory deliverables):

1. One canonical normalized market-state contract — covers REST list, REST detail, WS-derived state.
2. Parse fixed-point `_dollars` (and `_fp` quantity) fields. Remove the `or 50` chains at `rest_client.py:213-215, 241-242`.
3. `KalshiMarket` gains: `no_bid`, `no_ask`, `yes_bid_size`, `yes_ask_size`, `no_bid_size`, `no_ask_size`, `price_available` (bool), `price_source` ({`rest_list`, `rest_detail`, `ws`, `unavailable`}), `price_method` ({`bid`, `ask`, `midpoint`, `last`, `none`}), `price_retrieved_at` (UTC ISO), `exchange_active` (bool), `trading_active` (bool).
4. **D2** — compute executable YES *and* NO entry prices.
5. Side selection consumes side-specific executable price; never reconstructs NO from `100 - yes_midpoint`.
6. Paper fill uses executable side ask (eliminates paper/live asymmetry at `paper_trader.py:490` vs `executor.py:402`).
7. **D3** — spread validity invariant `0 <= bid <= ask <= 100` enforced at parse boundary. Violation → `price_available=False`.
8. **D1** — exchange-status / pause / `trading_active` fail-closed in same predicate as `price_available=False`. **Two-source predicate** (per §6.2 finding #1): global `GET /exchange/status` (`exchange_active`, `trading_active`) AND per-market `status == "active"`. Skip reasons logged distinctly (`exchange_paused`, `trading_inactive`, `status_not_active`, `price_unavailable`).
9. Provenance fields propagated through `SignalAnalysis`, `OPPORTUNITY` log, `PAPER_TRADE` log, and `paper_trades` DB.
10. Second WS-mutation site at `main.py:905-910` updated alongside `:664-669`.
11. `SignalAnalysis.market_yes_price` collapsed into `SignalAnalysis.market` to remove the duplicate-store drift path (`analysis/__init__.py:14`).
12. Production-replay silent-50 fallbacks at `scripts/performance_analysis.py:568` and `scripts/edge_replay/reingest_dossier_updates_post_fix.py:96` removed or annotated to fail-closed.
13. **D5(a)** — fixture-pinned CI gate: live payload captured at P0 spike, committed under `tests/fixtures/kalshi_payloads/`, parser-against-fixture equivalence test in CI.
14. **D5(b)** — field-drift detection: implementation scope decided at P0 implementation-design step (counter only vs counter + botcheck heartbeat surface vs counter + halt threshold). If full halt-threshold logic is too large for P0, ship counter+log in P0 and defer halt logic to P1.

Non-goals (held outside P0):

- Spread *policy filter / threshold* — P1.
- Depth filter — P1.
- Liquidity thresholds — P1.
- Fee-aware EV — P1.
- Full lifecycle policy (`open_time`, `close_time`, settlement timer, provisional) — P1.
- Official-source runtime promotion — P2 (offline prototype allowed per D4).
- WS rewrite beyond compatibility / fail-closed — P3.
- Trade-tape feature work — P3.
- `KALSHI_API_CONTRACT` env-var hot-rollback (D5(c)) — not approved; reopen if needed with explicit operator sign-off.

Acceptance criteria (folded with Claude review §5.3 additions):

- Legacy missing fields cannot silently become `50`.
- Current fixed-point fixtures parse into cents-consistent internal state.
- `bid <= ask` invariants hold for YES and NO sides where both exist.
- Midpoint is logged/reference-only unless explicitly selected by policy.
- Missing executable price marks market unusable for EV/trade evaluation.
- `exchange_active=False`, `trading_active=False`, or `status==paused` mark market unusable for EV/trade evaluation.
- Paper trade `price_cents` uses executable side price.
- Logs include price availability, source, method, and retrieval timestamp.
- Unit tests cover current-field, legacy-field, and malformed/missing-field payloads.
- A post-fix runtime/log audit observes non-50 prices or explicit fail-closed skips for markets without usable prices.
- **Negative-path observability** — every fail-closed market emits structured log with `reason ∈ {missing_yes_bid, missing_yes_ask, missing_no_bid, missing_no_ask, invariant_violation, exchange_paused, status_not_active}`.
- **Field-drift counter surfaced** — per-cycle count of markets with any missing expected fixed-point field; surface location per D5(b) scope decision.
- **Replay parity gate** — re-run most recent completed soak cycle replay against new parser; replay must produce non-50 prices or explicit fail-closed skips.
- **Provenance round-trip test** — `price_source`, `price_retrieval_ts`, `price_method` survive both JSONL log and DB serialization.
- **Two-sided EV symmetry test** — YES-perspective and NO-perspective edge calculations agree on side-selection sign.
- **WS / REST reconciliation invariant** — when both surfaces available, agree on `price_available` verdict within a small time window. No silent WS overwrite of REST state.
- **Audit-trail invariant on fail-closed** — skipped markets still appear in decision-trace log with raw payload (or payload hash + retention path).
- **Migration-window dual-write** — during P0 deploy, log legacy-parsed and new-contract prices side-by-side for one soak cycle; remove dual-write after one full cycle.

### P1 — Trade-Quality and Market-State Policy Controls

Goal: prevent false edge from thin, stale, paused, or lifecycle-unsafe markets via policy-tunable controls (after invariants and fail-closed primitives ship in P0).

Scope:

- Spread *policy filter* (tunable threshold; the *validity invariant* is already in P0 per D3).
- Bid/ask size capture.
- Top-of-book depth capture for selected candidates.
- Volume, 24h volume, and open-interest fields.
- Price staleness classification.
- Full market lifecycle fields in eligibility (`open_time`, `close_time`, settlement timer, `is_provisional`).
- Fee-aware EV design and tests.
- Pre-execution market-state revalidation.
- Field-drift halt-threshold logic if not shipped in P0 per D5(b).

Acceptance criteria:

- Spread, size, depth, and lifecycle fields are available in market snapshots.
- Lifecycle fields can block analysis/execution where appropriate.
- Fee-aware EV has an explicit design before any real-money promotion.
- Paper logs can explain skip reason for wide spread, stale price, lifecycle state, or missing liquidity.

### P2 — Official-Source and Market-Memory Lane

Goal: add ticker-keyed official evidence context without increasing trade frequency or bypassing safety gates.

**D4 boundary (binding):** offline prototype permitted in parallel with P0/P1. Writes to `data/market_memory/` or `logs/p2_prototype/`. **No imports from runtime modules** (`trading/`, `tasks/blend_task.py`, `main.py`, `analysis/`, `kalshi/websocket_client.py`, or any runtime decision module). **No modification of runtime behavior.** Import boundary enforced at PR-review time. Runtime promotion remains blocked by P0/P1.

Scope:

- Offline sample of bot-relevant markets only: politics/elections, geopolitics, macro/rates/inflation, government/legal/regulatory, limited source-driven finance/business.
- API-first capture of rules/source fields.
- Market-memory snapshot schema:
  - market/event/series identity
  - title/subtitle/category
  - `rules_primary`
  - `rules_secondary`
  - `early_close_condition`
  - raw/normalized `settlement_sources`
  - `contract_url`
  - `contract_terms_url`
  - retrieval timestamp
  - source origin
  - payload hash
  - PDF hash if fetched
  - source quality classification
  - monitoring feasibility classification
  - price-state snapshot provenance from P0/P1
- Compare ticker-keyed official-source evidence against existing generic-news-first evidence.

Non-goals:

- No sports/weather/entertainment platform crawler.
- No source promotion without offline validation.
- No website scraping as primary dependency.
- No PDF parsing as primary dependency.
- No direct path from official-source hit to executor.
- No imports from runtime modules (D4 boundary).

Acceptance criteria:

- Relevant-market sample meets coverage target defined by operator before runtime promotion.
- Usable rules and settlement-source coverage is quantified.
- Monitorable official/listed sources are normalized with confidence classes.
- Replay/report shows whether official-source lane improves evidence quality, replayability, explainability, and market-match precision.
- Hybrid beats generic-news-only on evidence quality, not trade count.

### P3 — WebSocket, Trade Tape, and Microstructure Enhancements

Goal: improve timeliness and movement awareness after REST snapshot correctness and replay parity are proven.

Scope:

- WebSocket ticker channel normalization.
- WebSocket orderbook snapshot/delta maintenance with explicit price-scale normalization.
- Trade tape ingestion.
- Market movement / already-priced-in features.
- Historical microstructure replay.

Acceptance criteria:

- REST-normalized market contract remains canonical.
- WS state reconciles to REST snapshots within documented tolerances.
- Trade tape features are evidence/quality signals, not direct execution triggers.

## 6. P0 Prerequisite — Live Payload Capture (COMPLETE 2026-05-11)

**Status: COMPLETE.** Read-only live payload capture executed 2026-05-11T13:37Z against `https://api.elections.kalshi.com/trade-api/v2`. No `.env`, no auth, no bot runtime, no orders.

Fixtures + SHA-256 hashes (see `tests/fixtures/kalshi_payloads/CAPTURE_METADATA_2026-05-11.json` for full sidecar):

| fixture | endpoint | sha256 | size |
|---|---|---|---|
| `exchange_status_2026-05-11.json` | `GET /exchange/status` | `457ba5176d7f54a556573f117659bab7f8990f8af12548a8b3cd27a12c06f383` | 46 B |
| `list_markets_status_open_limit50_2026-05-11.json` | `GET /markets?status=open&limit=50` | `6173c20ddc53380d2baccd79775cf5a5f3b80cbf264a1571935a1c49810b62f0` | 169.4 KB |
| `single_market_KXTRUMPIRAN-27JAN01_2026-05-11.json` | `GET /markets/KXTRUMPIRAN-27JAN01` | `42375c772066b5e24ada7cd02156073bb6fa27de84ffb53d6042af93d8c7a8fc` | 2.6 KB |
| `event_KXTRUMPIRAN_2026-05-11.json` | `GET /events/KXTRUMPIRAN` | `d5325604cfc58e7616fa7f1e6dfa14bc26981e4f78d6030d3cbe1701667db66d` | 11.0 KB |
| `series_KXTRUMPIRAN_2026-05-11.json` | `GET /series/KXTRUMPIRAN` | `8dbebaff09308504a64e1e402309db829cd336b21410b7064cbbc895062b25a1` | 1.8 KB |

Bot-relevant ticker used for detail/event/series capture: **`KXTRUMPIRAN-27JAN01`** — politics/geopolitics, status `active`, observed prices `yes_bid_dollars=0.0880, yes_ask_dollars=0.0980, no_bid_dollars=0.9020, no_ask_dollars=0.9120`. Real, non-50 prices that the legacy `or 50` parser would silently overwrite.

### 6.1 — Empirical confirmation of field-drift hypothesis

| field class | observed in 100% of payloads | observed in 0% of payloads | implication |
|---|---|---|---|
| Fixed-point prices `*_dollars` | yes_bid, yes_ask, no_bid, no_ask, last_price, previous_price, previous_yes_bid, previous_yes_ask | — | current API baseline; P0 parser must consume these |
| Fixed-point quantities `*_fp` | volume, volume_24h, open_interest, yes_bid_size, yes_ask_size | — | current API baseline |
| Precision metadata | price_level_structure, price_ranges, fractional_trading_enabled, response_price_units, notional_value_dollars | — | P0 parser provenance |
| Legacy cents-int | — | yes_bid, yes_ask, no_bid, no_ask, last_price, volume, volume_24h, open_interest | absent → triggers `or 50` chain at `rest_client.py:213-215, 241-242` |

**Verdict:** field-drift hypothesis EMPIRICALLY CONFIRMED. P0 implementation planning is unblocked.

### 6.2 — Roadmap contradictions surfaced by capture

These are folded into §3 and §5 below; recorded here for traceability:

1. **`exchange_active` and `trading_active` are GLOBAL fields on `GET /exchange/status`, NOT per-market.** Roadmap §3 previously listed them as per-market P1 API contract surfaces. **D1 fail-closed gating must call `/exchange/status` for the global posture and gate per-market on `status==paused`.** Two-source predicate, not one.
2. **`outcome_side` / `book_side` are NOT on `/markets` endpoints.** Likely trade-tape / order-event fields. Roadmap §3 P0 "canonical order direction" section is reclassified as **P2/P3 (trade tape)**, not P0 market-state.
3. **`status` value returned is `"active"`, NOT `"open"`.** Request param `status=open` returned 50/50 markets with `status="active"`. Confirms `analysis/market_matcher.py:487` bug and CLAUDE.md gotcha. P0 must accept `"active"` as the live state name.
4. **`settlement_sources`, `contract_url`, `contract_terms_url` are SERIES-LEVEL, not market-level.** `rules_primary`, `rules_secondary`, `early_close_condition` are MARKET-level. P2 schema in §5 reflects this split.

### 6.3 — Roadmap clarifications surfaced by capture

1. **`is_provisional` is conditionally present** — sports markets carry it; KXTRUMPIRAN-27JAN01 (politics) does NOT. P0 parser must treat as optional, not required.
2. **`event.markets[]` is returned by default on `GET /events/{ticker}`** — `with_nested_markets=true` param noted in §3 is redundant for nested-market access.
3. **Additional operationally relevant fields present** (not previously enumerated in roadmap §3):
   - Market-level: `liquidity_dollars` (P1 liquidity gate input), `notional_value_dollars` (binary invariant), `market_type` (sanity check), `response_price_units` (cents-vs-dollars provenance), `created_time`, `updated_time` (staleness inputs).
   - Series-level: `fee_type` (`"quadratic"`), `fee_multiplier` (P1 fee-aware EV inputs), `tags` (P2 category enrichment), `additional_prohibitions` (P2 rules supplement).
4. **`yes_bid_size_fp` / `yes_ask_size_fp` present per market**; NO equivalent `no_bid_size_fp` / `no_ask_size_fp` per `/markets` or `/markets/{ticker}`. Roadmap §3 P0 size list (`yes_bid_size_fp`, `yes_ask_size_fp`) is correct; NO-side sizes likely require the orderbook endpoint.
5. **First-page `/markets?status=open&limit=50` is dominated by KXMVECROSSCATEGORY / KXMVESPORTSMULTIGAMEEXTENDED markets.** P0 implementation must validate that the bot's existing market-list pagination + sports-prefix blocklist actually filters these to bot-relevant candidates before any decision logic consumes them. Not a parser bug — but the audit window for "first page returned" is misleading without the blocklist applied.

## 7. P0 Acceptance Test Matrix

22-test matrix; files land in `tests/test_kalshi_pricing_p0.py` and `tests/test_kalshi_pricing_p0_replay.py`; fixtures under `tests/fixtures/kalshi_payloads/`.

| test_id | layer | given | when | then | criterion |
|---|---|---|---|---|---|
| P0-REST-001 | unit | Fixture: real `GET /markets` payload with `*_dollars` fields | parser called | cents-consistent state | fixed-point fields parse correctly |
| P0-REST-002 | unit | Fixture: legacy cents-int payload | parser called | same state; no 50 fallback | legacy fields parse correctly |
| P0-REST-003 | unit | Payload with all price fields absent | parser called | `price_available=False`; no field equals `50` unless explicit | missing fields → unusable |
| P0-REST-004 | unit | Malformed prices (null, string, "") | parser called per variant | raises or marks unusable; no silent 50 | malformed → raise or unusable |
| P0-REST-005 | unit | Single-market detail fixture | `get_market()` called | cents-consistent contract; no 50 path | detail parser correct |
| P0-REST-006 | unit | Inverted spread (`yes_bid_dollars > yes_ask_dollars`) | parser called | rejected or marked unusable | spread validity invariant (D3) |
| P0-MARKET-007 | unit | `KalshiMarket` with valid YES + NO bid/ask | dataclass + property access | `yes_bid <= yes_ask`, `no_bid <= no_ask`, `yes_bid + no_ask <= 100` | invariant on both sides |
| P0-MARKET-008 | unit | `KalshiMarket` with `price_available=False` | access executable price | returns None or raises; never `50.0` | missing price blocks downstream |
| P0-SIDE-009 | unit | `yes_ask=38, no_ask=65` | side selection with YES-favoring prob | side=`yes`, entry price `38` | side-specific executable price (D2) |
| P0-SIDE-010 | unit | `yes_ask=72, no_ask=30` | side selection with positive NO edge | side=`no`, entry price `30` | NO executable ask, not `100 - yes_mid` (D2) |
| P0-PAPER-011 | integration | In-mem SQLite + market `yes_ask=42` | `record_trade(side="yes")` | DB `price_cents=42` | paper fill uses YES ask |
| P0-PAPER-012 | integration | In-mem SQLite + market `no_ask=61` | `record_trade(side="no")` | DB `price_cents=61` | paper fill uses NO ask |
| P0-KELLY-013 | unit | `kelly_bet(market_price_cents=42)` for YES | function called | Kelly computed from `q=0.58`, differs from midpoint result | Kelly receives executable side price |
| P0-KELLY-014 | unit | midpoint `50` vs `yes_ask=42` same prob | both calls | bet sizes differ | regression guard vs midpoint |
| P0-WS-015 | unit | WS `(36, 38)` and REST `yes_ask=37` | `get_yes_price` after REST absorbed | WS midpoint does not overwrite REST executable | WS no silent overwrite |
| P0-WS-016 | unit | WS no data for ticker | `get_yes_price("TICK")` | returns `None`; not `50` | WS missing → not 50 |
| P0-BLOCK-017 | unit | `price_available=False` market | gate function called | returns early / raises; no edge computed | gate blocks EV |
| P0-BLOCK-018 | unit | `price_available=False` market | paper recorder called | rejected with logged reason; no DB row | gate blocks paper trade |
| P0-PROV-019 | integration | valid paper-trade flow | `record_trade()` produces log + DB row | both carry `price_source`, `price_method`, `price_retrieved_at` | provenance round-trip |
| P0-PROV-020 | integration | OPPORTUNITY log emitted | record inspected | carries provenance fields; not `50` unless fixture explicit | provenance in opportunity records |
| P0-REG-021 | regression | all JSON fixtures | loader iterates | no parsed `yes_*`/`no_*` exactly `50.0` unless `_fixture_encodes_real_50c: true` | no latent 50 constants |
| P0-REG-022 | regression | post-fix `trades.jsonl` + `paper_trades.db` | scan OPPORTUNITY + PAPER_TRADE | at least one non-50, OR every `price_available=false` carries explicit `skip_reason` | post-fix produces real prices or explicit skips |

### Property-based opportunities (Hypothesis)

- Parser round-trip: `@given(st.decimals("0.00", "1.00", places=2))` — parsed cents = `round(val * 100)`; never `50` unless input exactly `"0.50"`.
- `bid <= ask` invariant: `@given(st.integers(1, 99), st.integers(1, 99))` — constructor rejects or normalizes `bid > ask`.
- Kelly monotonicity: `@given(st.floats(0.01, 0.99))` for executable side price — Kelly dollars monotonically grow with edge magnitude.

### Fixture-backed vs synthetic

| Tests | Backing |
|---|---|
| P0-REST-001/002/005, P0-REG-021 | Fixture-backed (live capture at P0 spike, plus walked) |
| P0-REST-003/004/006, P0-MARKET-007/008, P0-SIDE-009/010, P0-KELLY-013/014, P0-WS-015/016, P0-BLOCK-017/018 | Synthetic |
| P0-PAPER-011/012, P0-PROV-019/020 | Synthetic + in-memory SQLite via `_shared_memory_connect` pattern from `test_paper_trader.py` |
| P0-REG-022 | Replay-corpus (skip-marked in CI when paths absent) |

## 8. Impact Assessment

Contaminated current conclusions:

- EV calculations.
- Side selection.
- Paper fill price.
- Paper P&L.
- Non-zero-edge quality.
- Any comparison that assumes market-implied price was real.

Still useful current diagnostics:

- Logging paths.
- LLM output shape.
- Source matching traces.
- Evidence-store behavior.
- Orchestration behavior.
- Soak counters unrelated to price correctness.

## 9. Review Questions — Resolutions

| # | Question | Resolution |
|---|---|---|
| 1 | P0 = API contract stabilization, not local pricing patch? | Yes (§5 P0 scope). |
| 2 | Paper results contaminated for EV/P&L/side? | Yes (§2 evidence, §8). |
| 3 | P0/P1/P2/P3 sequencing? | Yes, modified: D1 promotes exchange-status fail-closed into P0; D4 allows parallel offline P2 prototype (§4). |
| 4 | Exchange status / pause gating into P0? | Yes — D1 accepted, fail-closed only; full lifecycle policy stays P1 (§4 D1, §5 P0 scope item 8). |
| 5 | P0 computes both YES and NO EV? | Yes — D2 accepted, non-negotiable (§4 D2, §5 P0 scope items 4–6). |
| 6 | What would falsify field-drift hypothesis? | Live demo payload capture shows no `_dollars`/`_fp` fields (§6 prerequisite). |
| 7 | What remains unverified after P0? | Spread policy threshold, depth/liquidity, fee-aware EV, full lifecycle policy, official-source runtime — all P1+ (§5). |
| 8 | Rollback / kill-switch? | D5 partial: fixture-pinned CI ACCEPTED in P0; drift detector scope deferred to implementation-design step; env-var hot-rollback NOT APPROVED (§4 D5). |
| 9 | Repo paths Codex missed? | Yes — folded into §2 downstream-propagation, replay consumers, latent constants, DB schema; see also Evidence Appendix §10. |
| 10 | Mandatory tests before P0 acceptance? | 22-test matrix + 3 property-based + 8 additional acceptance gates (§5 P0 acceptance criteria, §7). |

## 10. Evidence Appendix

| finding | evidence type | path / endpoint | line / field | confidence | notes |
|---|---|---|---|---:|---|
| Legacy REST list parser reads `yes_bid` / `yes_ask` with `50` fallback | code | `kalshi/rest_client.py` | 213-215 | high | confirmed production parser path |
| Single-market parser repeats fallback | code | `kalshi/rest_client.py` | 241-243 | high | confirmed detail parser path |
| Market model lacks NO side, sizes, price provenance | code | `kalshi/__init__.py` | 6-19 | high | confirmed model surface |
| Main path computes edge from midpoint probability | code | `main.py` | 713-714 | high | confirmed decision path |
| Kelly uses `market.yes_price` | code | `main.py` | 729-732 | high | confirmed sizing path |
| Blend uses carried market price | code | `tasks/blend_task.py` | 382-384 | high | confirmed blend path |
| Paper fills at midpoint | code | `trading/paper_trader.py` | 490-492 | high | confirmed paper path |
| Live order pricing depends on parsed/synthetic bid/ask | code | `trading/executor.py` | 402-404 | high | confirmed live path, PAPER-ONLY remains active |
| WS helper reduces state to midpoint | code | `kalshi/websocket_client.py` | 154-158 | high | confirmed helper path |
| WS path reconstructs bid/ask from midpoint | code | `main.py` | 664-669 | high | confirmed runtime mutation |
| **Second ws-price mutation block (fade path)** | code | `main.py` | 905-910 | high | Claude review §3.1 |
| **Executor mutates `analysis.market_yes_price` from blend candidate** | code | `trading/executor.py` | 315-318 | high | Claude review §3.1 |
| **Duplicate `SignalAnalysis.market_yes_price` drift store** | code | `analysis/__init__.py` | 14 | high | Claude review §3.1 |
| **`OpenPosition` persists contaminated prices for same-signal guard** | code | `trading/portfolio.py` | 27, 29, 53-67 | high | Claude review §3.1 |
| **API call hardcodes `status="open"`, never returns `"active"`** | code | `analysis/market_matcher.py` | 487-488 | high | Claude review §3.1 |
| **9 replay scripts consume `market_yes_price` from contaminated logs** | code | `scripts/edge_replay/*.py` | per §2.replay table | high | Claude review §3.2 |
| **Production-replay silent 50 fallback** | code | `scripts/performance_analysis.py` | 568 | high | Claude review §3.3 |
| **Production-replay silent 50 fallback** | code | `scripts/edge_replay/reingest_dossier_updates_post_fix.py` | 96 | high | Claude review §3.3 |
| Signal detail prices stuck at `0.5` | log | `logs/trades/live/trades.jsonl` | `SIGNAL_ANALYSIS_DETAIL` | high | 25/25 numeric price fields equal `0.5` |
| Opportunities stuck at `50` | log | `logs/trades/live/trades.jsonl` | `OPPORTUNITY` | high | 11/11 numeric price fields equal `50` |
| Skips stuck at `50` | log | `logs/trades/live/trades.jsonl` | `SKIPPED` | high | 9/9 numeric price fields equal `50` |
| Paper trades stuck at `50` | log/DB | `logs/trades/live/trades.jsonl`, `data/paper_trades.db` | `PAPER_TRADE`, DB rows | high | JSONL 2/2, DB 9/9 |
| Fixed-point fields are current API baseline | docs + live fixture | `GET /markets`, `GET /markets/{ticker}` | `*_dollars`, `*_fp` 100% present in 50 list + 1 detail | **confirmed** | live capture 2026-05-11T13:37Z (§6) |
| **Legacy cents-int fields ABSENT from live API** | live fixture | `GET /markets`, `GET /markets/{ticker}` | `yes_bid`/`yes_ask`/`no_bid`/`no_ask`/`last_price`/`volume`/`open_interest` all 0% present | **confirmed** | live capture 2026-05-11T13:37Z — directly triggers `or 50` chain |
| **`exchange_active`/`trading_active` are GLOBAL, not per-market** | live fixture | `GET /exchange/status` | both fields present at top level | **confirmed** | live capture 2026-05-11T13:37Z (§6.2 finding #1) |
| **`status` value is `"active"`, not `"open"`** | live fixture | `GET /markets?status=open` | 50/50 returned with `status="active"` | **confirmed** | live capture 2026-05-11T13:37Z (§6.2 finding #3) |
| **`outcome_side`/`book_side` not on `/markets`** | live fixture | `GET /markets`, `GET /markets/{ticker}` | both 0% present on market-level | **confirmed** | reclassified P0→P2/P3 (§6.2 finding #2) |
| **Official-source metadata: rules at market, sources/URLs at series** | live fixture | `GET /markets/{KXTRUMPIRAN-27JAN01}` + `GET /series/{KXTRUMPIRAN}` | `rules_primary`, `rules_secondary`, `early_close_condition` at market; `settlement_sources` (len=17), `contract_url`, `contract_terms_url` at series | **confirmed** | live capture 2026-05-11T13:37Z (§6.2 finding #4) |
| **Real non-50 prices observed on bot-relevant politics ticker** | live fixture | `GET /markets/KXTRUMPIRAN-27JAN01` | `yes_bid_dollars=0.0880`, `yes_ask_dollars=0.0980`, `no_bid_dollars=0.9020`, `no_ask_dollars=0.9120` | **confirmed** | live capture 2026-05-11T13:37Z; legacy parser would overwrite to 50 |

Evidence commands used:

- Numbered repo scans over `kalshi/rest_client.py`, `kalshi/__init__.py`, `kalshi/websocket_client.py`, `main.py`, `tasks/blend_task.py`, `trading/executor.py`, `trading/paper_trader.py`, `analysis/kelly.py`, `analysis/__init__.py`, `analysis/market_matcher.py`, `trading/portfolio.py`.
- JSONL scan of `logs/trades/live/trades.jsonl` by event names `SIGNAL_ANALYSIS_DETAIL`, `OPPORTUNITY`, `SKIPPED`, `PAPER_TRADE`.
- SQLite summary of `data/paper_trades.db` for `market_yes_price` and `price_cents`.
- Claude review (2026-05-11) dispatched three ECC subagents (code-explorer, architect, tdd-guide) for gap-finding, architectural judgment, and test design.
- Official Kalshi docs reviewed:
  - `https://docs.kalshi.com/getting_started/fixed_point_migration`
  - `https://docs.kalshi.com/getting_started/order_direction`
  - `https://docs.kalshi.com/api-reference/market/get-market`
  - `https://docs.kalshi.com/api-reference/market/get-markets`
  - `https://docs.kalshi.com/api-reference/market/get-market-orderbook`
  - `https://docs.kalshi.com/api-reference/market/get-trades`
  - `https://docs.kalshi.com/api-reference/exchange/get-exchange-status`
  - `https://docs.kalshi.com/api-reference/events/get-event`
  - `https://docs.kalshi.com/api-reference/events/get-event-metadata`
  - `https://docs.kalshi.com/api-reference/market/get-series`

## 11. P0 Implementation Design — Locked Decisions + Open Questions

**Design dispatched 2026-05-11** via three parallel ECC subagents (architect, code-architect, code-explorer). Full design returned in operator chat; canonical record below.

### 11.1 — Locked design decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| LD-1 | Internal canonical price unit | **`int` cents (0–100)** | All existing consumers (`paper_trader.py`, `executor.py`, `portfolio.py`, `paper_trades.price_cents` DB column) already cents-int. `Decimal` would force translation at every call site. |
| LD-2 | `KalshiMarket` shape | **Extend dataclass in place at `kalshi/__init__.py:6-19`** | New fields default backwards-compat; no sibling-type translation layer in every consumer. |
| LD-3 | Normalizer module | **New `kalshi/normalizer.py`** with three entry points: `normalize_market_list_entry`, `normalize_market_detail`, `normalize_exchange_status` | Single canonical parser; `rest_client.py` list + detail paths both delegate. |
| LD-4 | Unknown-contract handling | **`UnsupportedPayloadContractError` raised AT PARSE BOUNDARY**; caller catches per-entry, emits stub `KalshiMarket(unsupported_payload_contract=True, price_available=False)`, increments drift counter, logs structured `SKIPPED` with `reason="unsupported_payload_contract"` and `payload_hash` | One bad payload does not crash the list; halt comes from the counter, not the exception. |
| LD-5 | Drift halt scope | **Single-market skip at parse boundary; orchestrator-wide cycle halt at threshold trip** | Halt is fail-closed (refuse to act on contaminated data), not fail-fast (bot keeps fetching/observing). |
| LD-6 | Halt threshold defaults | **Strict P0 — absolute count ≥ 1** (halt on any unsupported payload contract). Relax to `≥5` absolute OR `≥10%` ratio in P1 if noise dominates signal. | Operator-locked 2026-05-11 (OQ-A): during API-drift repair, a single unknown contract is a hard signal, not noise. |
| LD-6b | Halt clearance policy | **Manual operator clearance required** — bot will not auto-retry on next cycle. `data/runtime/kalshi_drift_halt.json` sentinel persists until operator deletes/clears. | Operator-locked 2026-05-11 (OQ-B): auto-retry can mask recurring schema drift. |
| LD-7 | POST_FIX_NEW reset mechanism | **Cohort cut authoritatively uses `bot_state.p0_price_fix_deployed_ts` ts sentinel as the filter predicate** (NOT JSONL field presence). The JSONL `p0_contract_version: 1` field remains a forward-tagged audit signal but is NOT the cohort predicate, because pre-P0 SQLite `paper_trades` rows never carry the field and field-absence-equals-pre-P0 would leak pre-P0 SQLite rows into the post-P0 cohort. The `build_replay_dataset.py:301-317` filter compares `row.decision_ts >= sentinel.value`. **NOT a `paper_trades` schema ALTER TABLE.** | Code-explorer found 8+ test fixtures replicate `paper_trades` schema; ALTER would force 8 test-file rewrites. The 4-layer reset (JSONL field forward as audit tag, bot_state sentinel as auditable boundary AND cohort predicate, ts filter for historical reads, normalizer at root) covers every replay-script consumer with zero schema migration. Code-explorer CR-F finding 2026-05-11: pre-P0 SQLite rows never carry the JSONL field, so absence-equals-pre-P0 leaks rows. ts-sentinel cut is correct single cut point. |
| LD-8 | Backfill of historical contaminated `paper_trades` rows | **NO backfill** | Operator-locked per §2 evidence appendix; values cannot be verified against historical bid/ask snapshots. |
| LD-9 | `analysis/market_matcher.py:487` `status="open"` filter | **Fix in P0** (one-line: `status="open"` → `status="active"`) | After parser fix, current filter literally returns zero markets — holding to P1 leaves bot non-functional. |
| LD-10 | `SignalAnalysis.market_yes_price` duplicate field | **Deprecate-not-delete in P0.** Add `executed_price_cents: Optional[int]` as new source of truth for chosen side's ask. Keep `SignalAnalysis.market_yes_price` field at `analysis/__init__.py:14` populated from `executed_price_cents` (set at SignalAnalysis construction time; effectively `market_yes_price = executed_price_cents` on the dataclass instance) until P1 (deprecated-compat alias). Add `# DEPRECATED — semantically executed_price_cents post-P0; remove in P1` comment at field declaration. Hard removal deferred to P1 cleanup packet. | Code-explorer CR-B finding 2026-05-11: 40+ caller sites across `main.py`, `executor.py`, `paper_trader.py`, `portfolio.py`, `tasks/blend_task.py`, `utils/logger.py`, 9 replay scripts, and 20+ test fixtures. Hard removal in P0 multiplies blast radius onto same patch as parser/normalizer/side-EV/exchange-gate work. Operator-locked DT-2b: deprecate-not-delete reduces P0 risk; P1 removes alias after all reads migrated. |
| LD-11 | WS midpoint mutation at `main.py:664-669` (news flow), `:905-910` (fade-on-tweet flow), and `:1077-1081` (`_process_price_fade` price-fade-loop flow) | **Remove all three sites** | WS becomes reference / staleness signal only; never overwrites REST executable bid/ask. Third site enumerated 2026-05-12 after spec-reviewer flagged unenumerated leak into trade pipeline (`SignalAnalysis.market_yes_price` populated from WS-mutated `market.yes_price` at `main.py:1101-1102`). All three sites removed in P-4. |
| LD-12 | `trading/executor.py:315-318` blend-staleness mutation | **Reshape, not delete.** P-5 must add an explicit `rest.get_market(ticker)` re-fetch callback at `trading/executor.py:315-318`, then construct a fresh `KalshiMarket` via `kalshi/normalizer.py.normalize_market_detail()`, and recompute `executed_price_cents` from the side selector against that re-fetched market. The original `candidate.market` from `tasks/blend_task.py:514-516` is a fast-lane-time snapshot and is NOT REST-fresh at blend time (architect CR-D finding 2026-05-11: `BlendTask` passes `market=fast_lane_result.market` through unchanged with zero REST fetches). | Fast-lane shape preservation is correct; price re-derivation from stale snapshot was the bug. Architect CR-D verdict 2026-05-11: NEEDS-REFETCH-HOOK-IN-P5 — wire explicit `rest.get_market(ticker)` callback (not implicit), because `BlendTask` does not re-fetch by contract. |
| LD-13 | Replay-script touch policy | **Annotation-only** for 9 `scripts/edge_replay/*` consumers — single-line guard filtering on `p0_contract_version` or `ts` boundary. **Fix** for production-replay silent-50 paths at `scripts/performance_analysis.py:568` and `scripts/edge_replay/reingest_dossier_updates_post_fix.py:96`. **Unchanged** synthetic test/sim defaults (already explicit). | Minimum-viable touch preserves Cycle-17D halt rationale. |
| LD-14 | Botcheck heartbeat surface for drift counter | **Single additive line** in heartbeat: `kalshi_drift: cycle_count=<n> halt=<bool> last_halt_at=<iso|null> threshold_abs=5 threshold_ratio=0.10` | Per P0 deliverable §5 #14, operator pre-authorized. |
| LD-15 | Audit-trail payload-hash retention | **Hash only in P0** (sha256 sorted-keys utf-8 of raw market dict); raw payload retrieval = re-fetch endpoint at cycle timestamp. Full raw archive deferred to P1. | Roadmap acceptance criterion satisfied without persistence cost. |
| LD-16 | `_dollars` → cents rounding | **`ROUND_HALF_EVEN`** at parse boundary; raw `_dollars` string preserved in optional `raw_dollars` provenance for forensics | Live executor already clamps to `max(1, min(99, price_cents))` per `executor.py:404`. |
| LD-17 | `paper_trades.market_yes_price` column + `OpenPosition.market_yes_price` field compat | **Keep both names for P0.** Column at `paper_trades.market_yes_price` and dataclass field at `trading/portfolio.py:29` retain their current names. P0 INSERT path writes `analysis.executed_price_cents` into the `market_yes_price` column (semantic-shift: column now means executed-side entry price). Add `# DEPRECATED — semantically executed_price_cents post-P0` comment at `OpenPosition.market_yes_price` field declaration. No DB ALTER TABLE in P0. Test fixtures (8+ files per code-explorer CR-B) keep current column name; only the populating value changes. | Code-explorer CR-B finding 2026-05-11 + operator-locked DT-1b: schema migration during correctness repair is unacceptable risk. No-schema-change posture matches LD-7. Rename to `entry_price_cents` deferred to P1 cleanup. |

### 11.2 — Implementation sequence (10 packets, TDD-first, stop points)

| # | Title | Files | Stop point |
|---|---|---|---|
| P-1 | Tests-only, fixture-backed parser tests (RED) | `tests/test_kalshi_normalizer_p0.py` (new), `tests/test_kalshi_pricing_p0.py` (new stubs) | Operator review of test names + fixture assertions |
| P-2 | `kalshi/normalizer.py` + extended `KalshiMarket` (GREEN) | `kalshi/normalizer.py` (new), `kalshi/__init__.py` (extend) | Codex/Claude review of normalizer surface + dataclass shape |
| P-3 | Drift counter + `UnsupportedPayloadContractError` halt | `kalshi/normalizer.py` (DriftCounter, abs≥1 threshold, no auto-retry), `kalshi/rest_client.py` (catch + `get_exchange_status`), `tests/test_drift_counter_halt_p0.py` (new — asserts strict-1 trip + manual-clearance-only behavior) | Operator review of test assertions (thresholds locked: OQ-A/OQ-B resolved) |
| P-4 | Replace `or 50` chains + remove WS midpoint mutations | `kalshi/rest_client.py:213-215, 241-242`, `main.py:664-669, 905-910`, `kalshi/websocket_client.py:154-158` | Codex/Claude review of WS mutation removal at both sites |
| P-5 | `SignalAnalysis` collapse + side selector + Kelly wiring | `analysis/__init__.py:14` (add `executed_price_cents`, keep `market_yes_price` deprecated alias per LD-10), `main.py:713-714, 729-732` (side selector + Kelly wiring against `executed_price_cents`), `tasks/blend_task.py:382, 514` (read-through; no re-fetch added here), `trading/executor.py:315-318` (reshape — wire `rest.get_market(ticker)` re-fetch hook per LD-12; NOT in blend_task.py — see LD-12), `trading/portfolio.py:29, 67` (keep `market_yes_price` field; add deprecated comment per LD-17), ~30 caller sites for `.yes_price` / `.yes_bid` / `.yes_ask` / `.yes_prob` property migration to `is_tradeable()` guards + 6 test fixtures (per CR-C verdict) | Operator review of blend-staleness reshape (LD-12 re-fetch hook) + property-migration coverage (CR-C ~30 sites) |
| P-6 | Paper-trade fill on executable side + provenance | `trading/paper_trader.py:490-491, 508`, `trading/portfolio.py` (read-through), `trading/executor.py:402-403` (paper-only-gated) | Operator review of paper-fill semantics |
| P-7 | Exchange-status fail-closed gate + market-matcher `status` fix | `analysis/market_matcher.py:487`, `main.py` orchestrator (one-per-cycle `/exchange/status` call), propagate `ExchangeState` | Operator review of one-fetch-per-cycle policy |
| P-8 | Replay-script annotations + production-replay fail-closed | `scripts/edge_replay/*.py` (9 scripts, single-line guard), `scripts/edge_replay/reingest_dossier_updates_post_fix.py:96`, `scripts/performance_analysis.py:568` | Operator review of corpus reset boundary |
| P-9 | Botcheck heartbeat surface + audit-trail payload hash + `bot_state` sentinel | `botcheck` script, `utils/logger.py` emitter wrapper, `trading/paper_trader.py:81-83` bot_state insert | Operator review of heartbeat additive line (OQ-C) |
| P-10 | CI fixture-pinned gate + VERSION/CHANGELOG bump | CI workflow, `VERSION`, `CHANGELOG.md`, debt-log P0 entry | Operator final approval before merge |

### 11.3 — Open questions

**Operator-resolved 2026-05-11 (folded into LD table above; no longer blocking):**

- **OQ-A → LD-6 (RESOLVED)** — Drift halt threshold for P0 = **absolute ≥ 1** (strict: halt on any unsupported payload contract). Relax to `≥5` absolute OR `≥10%` ratio in P1 only if noise dominates signal. Rationale: during API-drift repair, a single unknown contract is a hard signal.
- **OQ-B → LD-6b (RESOLVED)** — Halt clearance = **manual operator only**. No auto-retry on next cycle. `data/runtime/kalshi_drift_halt.json` sentinel persists until operator clears. Rationale: auto-retry can mask recurring schema drift.
- **OQ-C → LD-14 (RESOLVED)** — Botcheck heartbeat = **single additive line** (per design); lowest blast radius. No fold into existing field group.

**Claude/Codex review complete 2026-05-11 (folded into LD table above):**

- **CR-A → LD-16 (SAFE)** — `_dollars_to_cents` `ROUND_HALF_EVEN` is safe for P0. Two independent integer-cent enforcement points already exist: `trading/executor.py:402-404` clamps `max(1, min(99, price_cents))`, and `kalshi/rest_client.py:place_limit_order` (line 324) types `limit_price: int  # cents (1–99)` and ships integer cents to `/portfolio/orders`. Sub-cent precision cannot reach the order POST regardless of normalizer rounding. Captured fixture confirms `price_level_structure="tapered_deci_cent"` markets are present but bot's order path is already integer-clamped. Future code that bypasses the integer clamp is out of P0 scope.
- **CR-B → LD-10 + LD-17 (DEPRECATE-NOT-DELETE LOCKED)** — Code-explorer enumerated 40+ caller sites including DB-hydrated `OpenPosition.market_yes_price` and `paper_trades.market_yes_price` column. Operator-locked 2026-05-11 (DT-1b + DT-2b): deprecate-not-delete `SignalAnalysis.market_yes_price` (LD-10) and keep DB column + dataclass field name (LD-17). 40-site hard removal deferred to P1 cleanup packet.
- **CR-C → LD-2 (CALLER MIGRATION REQUIRED IN P-5)** — All ~30 production callers of `KalshiMarket.yes_price` / `yes_bid` / `yes_ask` / `yes_prob` are MUST-MIGRATE because no caller currently guards on `price_available` (field doesn't exist pre-P0). P-5 implementation packet must (a) extend dataclass to add `price_available` defaulting to `False`, (b) update each call site to guard on `is_tradeable()` or read `executed_price_cents`, (c) update 6+ test fixtures to set `price_available=True`. `signal_analyzer.py` alone has 15+ `.yes_prob` reads — single largest cluster. `dataclasses.asdict(analysis.market)` at `paper_trader.py:508` also exercises properties; handled by CR-E custom encoder.
- **CR-D → LD-12 (REFETCH-HOOK-IN-P5 LOCKED)** — `tasks/blend_task.py:514-516` passes `market=fast_lane_result.market` through unchanged; zero REST fetches in BlendTask. P-5 must wire explicit `rest.get_market(ticker)` re-fetch at `trading/executor.py:315-318` and renormalize via `kalshi/normalizer.py.normalize_market_detail()`. LD-12 wording updated to reflect this.
- **CR-E → MANDATORY CUSTOM ENCODER (P-6)** — `paper_trades.market_snapshot` column write at `trading/paper_trader.py:508` currently calls `json.dumps(dataclasses.asdict(analysis.market))` with vanilla encoder. Extended `KalshiMarket` carries `Decimal` and `datetime` fields that vanilla `json.dumps` rejects (raises `TypeError`). P-6 must define `_market_to_jsonable(market)` helper that maps `Decimal → str` (preserve precision) and `datetime → ISO8601 str` before `json.dumps`. No Python `json.loads(market_snapshot)` reader exists today (sole reader is SQLite `json_extract(market_snapshot, '$.series_ticker')`); risk is forward-only WRITE-path break.
- **CR-F → LD-7 (TS-SENTINEL CUT POINT)** — `scripts/edge_replay/build_replay_dataset.py:301-317` is the sole corpus-assembly site. Cohort filter must cut on `bot_state.p0_price_fix_deployed_ts` ts sentinel, not on JSONL `p0_contract_version` field presence, because pre-P0 SQLite `paper_trades` rows never carry the field and field-absence-equals-pre-P0 would leak those rows into the post-P0 cohort. LD-7 wording updated to reflect this.

### 11.4 — Additional consumers found by code-explorer (folded into impact map)

Beyond the prior audit, these surfaces read price fields and need P-8 annotation:

| File | Lines | Class |
|---|---|---|
| `scripts/signal_edge_diagnostics.py` | 193-195, 404, 415, 642, 644, 664, 750-759, 806, 901, 948 | Diagnostics reader |
| `scripts/flag_outcome_correlation.py` | 117, 158, 237, 277, 346, 426, 472 | Anchor-rate correlation |
| `scripts/source_market_alignment_audit.py` | 195 | Alignment audit |
| `scripts/daily_review.py` | 688, 779, 790 | Reporting |
| `scripts/regime_weight_validation.py` | 89-93, 138 | Regime audit (builds synthetic KalshiMarket) |
| `scripts/simulations/paper_trade_roundtrip.py` | 194 | Simulation |
| `scripts/simulations/trading_queue_handoff.py` | 114 | Simulation |
| `scripts/edge_replay/render_cycle16d_report.py` | 117 | Replay reporter |
| `scripts/edge_replay/build_cycle17d_broader_corpus.py` | 53 | Broader corpus builder |
| `scripts/llm_eval.py` | 143-145 | Synthetic eval fixture (`yes_bid=49, yes_ask=51`) |
| `scripts/simulations/match_score_audit.py` | 114-116 | Synthetic match audit fixture |

Production-replay sites (must-fix in P-8): `scripts/performance_analysis.py:568`, `scripts/edge_replay/reingest_dossier_updates_post_fix.py:96`, `scripts/simulations/g1_admittance_counterfactual.py:87` (silent `0.5` default).

### 11.5 — Must-not-touch (governance lockdown)

- `governance/prompts.py:27-31` (anchor_rate polarity block) — PROFIT-GOV-002 lockdown
- `governance/agent.py` real-mode flip authority — high-blast-radius, explicit user confirmation only
- `governance/llm.py` `LocalQwenLLM.complete` `think: False` — PROFIT-GOV-001 fix preservation
- `analysis/signal_analyzer.py` Chat Completions endpoint — `think: False` does NOT apply there
- `feeds/reddit/*` backoff — `_backoff[x] = time.monotonic() + delay` invariant
- `.env`, all launchd plists, `data/paper_trades.db` historical rows (no backfill)
- `tests/fixtures/kalshi_payloads/*.json` — captured fixtures are P0-gate evidence; modification invalidates D5(a) CI gate

## 12. Cross-links

- `docs/ROADMAP.md` — authoritative project roadmap surface.
- `docs/governance/2026-05-10-cycle-17d-halt-on-historical-corpus-degeneracy.md` — historical corpus degeneracy halt.
- `docs/governance/2026-05-10-cycle-17d-broader-api-fetch-sub-amendment.md` — broader API fetch sub-amendment.
- `docs/governance/2026-05-06-cycle-13-live-api-coordination.md` — live API coordination precedent.
- `docs/profit_path_debt_log.md` — debt/roadmap issue ledger; P0 lands as a debt-log entry on merge.
