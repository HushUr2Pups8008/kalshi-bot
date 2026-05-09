# Architecture Overview — kalshi-bot
**Date:** 2026-05-08  
**Scope:** feeds → analysis → tasks pipeline (trading/ excluded per domain constraint)

---

## 1. Entry Points

### Production Services (launchd — `ops/launchd/`)

| Template | Invocation | Purpose |
|---|---|---|
| `com.jake.kalshi-bot.plist.template` | `caffeinate … python main.py` | Primary bot process |
| `com.kalshi.governance.fast.plist.template` | `python -m governance --cadence fast` | Fast governance cycle |
| `com.kalshi.governance.deep.plist.template` | `python -m governance --cadence deep` | Deep governance cycle |
| `com.jake.kalshi-bothealth.plist.template` | `bash scripts/bothealth.sh` | Health watchdog |
| `com.kalshi.db-backup.plist.template` | (backup script) | DB snapshot |

### Manual / Operator Entry Points

- `main.py` — `TradingBot` class, `asyncio.run(main())` at bottom; `--go-live` flag activates live trading
- `governance/__main__.py` — delegates to `governance.agent.main()`
- `scripts/` — ~40 standalone diagnostic/review scripts; all read-only against logs and DB

---

## 2. The feeds → analysis → tasks Pipeline

### 2a. Feeds Layer (ingestion only)

Four concurrent async feed pollers started in `TradingBot.run()` as `asyncio.create_task`:

| Monitor | Source | Key function |
|---|---|---|
| `feeds/rss_monitor.py` | Publisher RSS (Reuters, BBC, etc.) + fade-tweet RSS | `run_rss_monitor(callback)` |
| `feeds/reddit_monitor.py` | OAuth2 Reddit, multi-subreddit polling | `run_reddit_monitor(callback, subreddits)` |
| `feeds/search_news_monitor.py` | Google News + Bing News RSS (market-keyed queries) | `run_search_news_monitor(callback, market_getter)` |
| `feeds/gdelt_monitor.py` | GDELT Document 2.0 API | `run_gdelt_monitor(callback, market_getter)` |

Each poller calls `TradingBot._enqueue_news(NewsItem)` as its callback. This non-blocking method:
1. Checks `DISABLED_SOURCE_FAMILIES` and `is_source_disabled()` (runtime overrides)
2. Applies an early staleness gate (`EARLY_MAX_NEWS_AGE_SECONDS = 300`)
3. Calls `HeadlineDedup.is_duplicate()` (15-min cross-source dedup)
4. Priority-encodes (RSS=1, Reddit=2, others=1) and puts into `_news_queue` (maxsize=2000)

Support modules:
- `feeds/dedup.py` — `HeadlineDedup`: sliding-window hash-based dedup
- `feeds/subreddit_selector.py` — adaptive subreddit selection keyed to active markets
- `feeds/subreddit_discovery.py` — background Reddit search for new topic-relevant subs

### 2b. News Consumer → Analysis Layer (fast lane)

`_news_consumer_task` drains `_news_queue` one item at a time and calls `on_news_item(news)`.

**`on_news_item` flow:**

```
NewsItem
  └── MarketMatcher.find_candidates(news)         [analysis/market_matcher.py]
        ├── Tokenize headline + market title
        ├── Jaccard similarity scoring
        ├── Pre-LLM keyword gate (all_required / any_hit / disabled)
        ├── Regime weight attachment
        └── Returns: list[(KalshiMarket, match_score, match_meta)]

  For each candidate → _process_candidate(news, market, ...)
        ├── Staleness check (MAX_NEWS_AGE_SECONDS, default 300s)
        ├── WS price override (from KalshiWebSocketClient cache)
        ├── estimate_probability(news, market, ...)  [analysis/signal_analyzer.py]
        │     ├── _keyword_score()  — deterministic, always runs
        │     └── LLM estimation (Ollama, semaphore=1) — optional, gated
        ├── Reject if no keywords AND no LLM signal
        ├── kelly_bet()                              [analysis/kelly.py]
        └── Build SignalAnalysis dataclass           [analysis/__init__.py]
```

**Key analysis modules (pure function layer — INV-4):**

| File | Purpose |
|---|---|
| `analysis/__init__.py` | `SignalAnalysis` dataclass — pipeline handoff type |
| `analysis/signal_analyzer.py` | Keyword + LLM probability estimation |
| `analysis/market_matcher.py` | Market candidate search; `MarketCache` / `MarketMatcher` |
| `analysis/kelly.py` | Kelly criterion bet sizing |
| `analysis/regime_classifier.py` | Probabilistic regime weight vector per market |
| `analysis/market_specificity.py` | Specificity score for match quality (ROADMAP P3.2) |
| `analysis/evidence_types.py` | Shared domain types: `Evidence`, `Dossier`, `EvidenceScore`, `PriorEstimate` |
| `analysis/evidence_scorer.py` | BSR-5/BSR-7 quality scoring (pure, no I/O) |
| `analysis/dossier_builder.py` | Belief dossier update engine (pure, no I/O) |
| `analysis/decision_blender.py` | Multi-lane blend formula (pure, no I/O) |
| `analysis/structural_prior.py` | Structural prior synthesis (pure, no I/O) |
| `analysis/calibration_monitor.py` | Brier score / drift detection (pure) |
| `analysis/fade_signal.py` | Fade pattern detection for prediction-market hype tweets |
| `analysis/source_credibility.py` | Per-source win-rate tracker — **stateful, DB-backed** (see §5) |
| `analysis/source_stats.py` | Per-source signal rate tracker — **stateful, DB-backed** (see §5) |
| `analysis/keyword_stats.py` | Per-(keyword, ticker) accuracy multiplier — **DB-backed** (see §5) |

### 2c. Route Through Blend (`_route_analysis_through_blend`)

After `SignalAnalysis` is built, main routes it through the task layer:

```
SignalAnalysis
  └── _route_analysis_through_blend(analysis)
        ├── _signal_to_evidence(analysis) → Evidence
        ├── _evidence_queue.put_nowait(evidence)   [maxsize=2000, drops on full]
        └── BlendTask.process_fast_lane_result(analysis)  [tasks/blend_task.py]
```

### 2d. Tasks Layer (orchestration)

Four task objects created in `TradingBot.__init__`, each running as a named `asyncio.create_task`:

#### AccumulationTask (`tasks/accumulation_task.py` — task: `accumulation`)
- Drains `_evidence_queue`
- Calls `score_evidence()` [analysis/evidence_scorer.py] — BSR-5/BSR-7
- Calls `update_dossier()` [analysis/dossier_builder.py] — belief update
- Persists `EvidenceRecord` + `DossierUpdateRecord` to SQLite via `EvidenceStore`
- Per-market async locks prevent race conditions

#### BlendTask (`tasks/blend_task.py` — task: `blend_consumer` + inline)
- Called inline from fast lane AND drains `_trading_queue`
- Reads `DossierState` + `StructuralPriorRecord` from `EvidenceStore`
- Calls `analysis.decision_blender.blend()` with fast/accumulation/structural lane inputs
- Calls `evaluate_readiness()` [tasks/trade_readiness_gate.py] — gate G1–G6
- Enqueues approved `TradeCandidate` objects into `_trading_queue` (maxsize=500)
- `_trading_queue_consumer_task` drains queue → `executor.execute(candidate)`

#### StructuralTask (`tasks/structural_task.py` — task: `structural`)
- Periodic (default 3600s), triggered by `_structural_recompute_task`
- Calls `compute_structural_prior()` [analysis/structural_prior.py]
- Persists `StructuralPriorRecord` to `EvidenceStore`
- Provides the "structural lane" input to BlendTask

#### CalibrationTask (`tasks/calibration_task.py`)
- Consumes `CALIBRATION_CHECK` events emitted at market resolution
- Tracks per-lane Brier scores using `analysis/calibration_monitor.py`
- Provides `get_scaling_factor(lane)` to BlendTask for G1 regime-scaled confidence

Support tasks (also `create_task` in `run()`):
- `runtime_overrides_poll` — polls `data/runtime_overrides.yaml` every 600s
- `market_refresh` — periodically refreshes `MarketCache`
- `auto_resolve` — resolves expired paper positions
- `subreddit_discovery` — background subreddit discovery
- `fade_tweets` (conditional) — separate RSS monitor for hype-tweet fade signals

#### Persistence (`tasks/evidence_store.py`)
- SQLite at `data/evidence_store.db`
- Tables: `evidence_records`, `dossier_updates`, `structural_priors`, `dossier_state`
- Schema documented in `docs/evidence_store_schema.md`
- Per-market async write locks; reads are lock-free

#### Trade Readiness Gate (`tasks/trade_readiness_gate.py`)
- Pure function `evaluate_readiness(blend_result, regime_confidence) → ReadinessDecision`
- Gates: G1 (regime-scaled confidence), G3 (disagreement score), G4 (fail-safe tightening), G6 (recency)
- Formally specified in `docs/IMPLEMENTATION_CONTRACT.md §5`

#### BudgetManager (`tasks/budget_manager.py`)
- LLM call admission control (S2.7 hourly budget)
- Circuit breaker pattern
- Does not execute LLM calls — gate only

---

## 3. Data Flow Diagram

```
External Sources                  kalshi-bot process
─────────────────                 ─────────────────────────────────────────────────────────────
RSS / Wire feeds ──────────────▶ run_rss_monitor
Reddit ────────────────────────▶ run_reddit_monitor          ┌──────────────────────────────┐
Google/Bing News ──────────────▶ run_search_news_monitor     │       feeds layer            │
GDELT ─────────────────────────▶ run_gdelt_monitor           │ (ingestion + dedup only)     │
                                         │                   └──────────────────────────────┘
                                         ▼
                                 _enqueue_news(NewsItem)
                                   [src check / dedup / age gate]
                                         │
                                         ▼
                                 _news_queue (PriorityQueue)
                                         │
                                         ▼
                                 _news_consumer_task
                                         │
                                         ▼
                          ┌──────────────────────────────────┐
                          │       analysis layer             │
                          │                                  │
                          │  MarketMatcher.find_candidates() │
                          │  estimate_probability()          │  ◀── Ollama LLM (optional)
                          │  kelly_bet()                     │
                          │  → SignalAnalysis                │
                          └──────────────────────────────────┘
                                         │
                                         ▼
                          _route_analysis_through_blend()
                               │                    │
                               ▼                    ▼
                     _evidence_queue         BlendTask.process_fast_lane_result()
                          │                         │
                          ▼                         │  reads: EvidenceStore (dossier + structural prior)
                   AccumulationTask                 │  calls: decision_blender.blend()
                   [score → dossier update]         │  calls: evaluate_readiness()
                          │                         │
                          ▼                         ▼
                    EvidenceStore ──────────▶  _trading_queue (if gate passes)
                    (SQLite)                         │
                       ▲                             ▼
                       │                  _trading_queue_consumer_task
                  StructuralTask                     │
                  [periodic, 3600s]                  ▼
                                             trading/ (TradeExecutor)  ← EXCLUDED FROM AUDIT
```

```
Governance process (separate launchd service)
─────────────────────────────────────────────
governance/agent.py (run_cycle)
  └── KalshiGovernanceAdapter  ─▶  reads DB + logs (read-only tap into main bot state)
  └── LocalQwenLLM / FakeLLM   ─▶  Ollama qwen3
  └── Decision → runtime_overrides.yaml  ─▶  runtime_overrides_poll picks up changes
```

---

## 4. Key Files Reference

| File | Layer | Role |
|---|---|---|
| `main.py` | Orchestration | `TradingBot` class; all async task wiring; fast-lane pipeline |
| `config.py` | Config | All env-var bindings and tunable constants; `cfg` singleton |
| `feeds/__init__.py` | Feeds | `NewsItem` dataclass |
| `feeds/rss_monitor.py` | Feeds | RSS + fade-tweet polling |
| `feeds/reddit_monitor.py` | Feeds | OAuth2 Reddit polling |
| `feeds/search_news_monitor.py` | Feeds | Google News + Bing News |
| `feeds/gdelt_monitor.py` | Feeds | GDELT monitor |
| `feeds/dedup.py` | Feeds | Cross-source headline dedup |
| `feeds/subreddit_selector.py` | Feeds | Adaptive subreddit selection |
| `analysis/__init__.py` | Analysis | `SignalAnalysis` handoff type |
| `analysis/signal_analyzer.py` | Analysis | Keyword + LLM probability estimation |
| `analysis/market_matcher.py` | Analysis | Market candidate search; `MarketCache` |
| `analysis/evidence_types.py` | Analysis | Core domain types |
| `analysis/evidence_scorer.py` | Analysis | BSR-5/BSR-7 quality scoring (pure) |
| `analysis/dossier_builder.py` | Analysis | Belief update engine (pure) |
| `analysis/decision_blender.py` | Analysis | Multi-lane blend formula (pure) |
| `analysis/structural_prior.py` | Analysis | Structural prior synthesis (pure) |
| `analysis/kelly.py` | Analysis | Kelly bet sizing |
| `analysis/regime_classifier.py` | Analysis | Regime weight vector |
| `analysis/source_credibility.py` | Analysis | Per-source win-rate (DB-backed) |
| `analysis/source_stats.py` | Analysis | Per-source signal rate (DB-backed) |
| `analysis/keyword_stats.py` | Analysis | Per-keyword accuracy (DB-backed) |
| `tasks/evidence_store.py` | Tasks | SQLite persistence for evidence + dossiers |
| `tasks/accumulation_task.py` | Tasks | Evidence accumulation orchestration (S2.5) |
| `tasks/blend_task.py` | Tasks | Blend-lane orchestration (S3.4) |
| `tasks/structural_task.py` | Tasks | Structural prior recompute (S3.2) |
| `tasks/calibration_task.py` | Tasks | Calibration drift tracking |
| `tasks/trade_readiness_gate.py` | Tasks | Gate G1–G6 readiness evaluation |
| `tasks/budget_manager.py` | Tasks | LLM call admission control (S2.7) |
| `tasks/runtime_overrides_task.py` | Tasks | Polls `runtime_overrides.yaml` every 600s |
| `kalshi/rest_client.py` | Kalshi API | RSA-PSS signed REST calls; market fetch + order placement |
| `kalshi/websocket_client.py` | Kalshi API | Orderbook WS; live price feed |
| `governance/agent.py` | Governance | Run-cycle: evidence → LLM → Decision |
| `governance/adapter.py` | Governance | Read-only tap into DB + logs for audit data |
| `governance/llm.py` | Governance | `LocalQwenLLM` (Ollama), `FakeLLM`; `think=False` fix |
| `utils/logger.py` | Utils | Structured trade log; daily-rotating file handler |
| `utils/runtime_overrides.py` | Utils | Runtime override reader / global singleton |
| `ops/launchd/` | Ops | launchd plist templates; `install.sh` |

---

## 5. Boundary Observations

These are observed deviations from the stated layer contract; noted, not fixed.

### 5a. Stateful DB-backed modules in `analysis/`

`analysis/source_credibility.py`, `analysis/source_stats.py`, and `analysis/keyword_stats.py` perform direct SQLite reads/writes against `data/paper_trades.db`. The `IMPLEMENTATION_CONTRACT.md §2` (`/analysis`) states the layer must be pure (no I/O, no DB access). These three modules are operational trackers that happened to be placed in `analysis/` rather than `tasks/`.

- `source_credibility:line 37` — `CREATE TABLE IF NOT EXISTS source_credibility …`
- `source_stats:line 48` — `CREATE TABLE IF NOT EXISTS source_stats …`
- `keyword_stats:line 23` — reads `keyword_outcomes` table

Impact: low (no trading logic involved), but complicates INV-4 purity claims and makes unit-testing these modules require a live DB fixture.

### 5b. `analysis/__init__.py` imports `feeds.NewsItem` and `kalshi.KalshiMarket`

`analysis/__init__.py:4-5` — the analysis layer imports directly from `feeds` and `kalshi`. This is an intentional design choice (shared domain types) but makes the dependency graph `analysis → feeds` rather than `feeds → analysis`. The direction is benign but means feeds cannot import from analysis without a cycle.

### 5c. `feeds/gdelt_monitor.py` imports from `feeds/search_news_monitor.py`

`feeds/gdelt_monitor.py:32` — `from feeds.search_news_monitor import _markets_to_queries`. GDELT reuses the query-building function from search_news. Not a cross-layer leak, but tight coupling between two sibling feed modules on a private (`_`) function.

### 5d. `feeds/google_news_monitor.py` imports from `feeds/rss_monitor.py`

`feeds/google_news_monitor.py:25` — `from feeds.rss_monitor import poll_feed`. Same pattern as 5c. `google_news_monitor.py` is largely superseded by `search_news_monitor.py` (which also imports `poll_feed`) and does not appear in `TradingBot.run()` task list — it may be dead code.

### 5e. `analysis/signal_analyzer.py` reads runtime overrides directly

`signal_analyzer.py:21` — `from utils.runtime_overrides import is_keyword_disabled`. Analysis layer reaching into utils for a runtime override check. Not a severe violation (utils is explicitly a shared utility layer), but it means `signal_analyzer` has a soft dependency on the override singleton being initialized before first call.

### 5f. `main.py` imports private function `_compute_pre_llm_match_meta`

`main.py:69` — `from analysis.market_matcher import MarketMatcher, _compute_pre_llm_match_meta`. A private function exported across a module boundary.

### 5g. `main.py` late imports inside methods

`main.py:726`, `main.py:891`, `main.py:1007`, `main.py:1031-1032` — several `from analysis.*` and `from feeds.*` imports inside method bodies. Defers import until first call; fragile if the module has side effects or raises on import.

---

## 6. Gaps / Undocumented Surface

### 6a. No `docs/CODEMAPS/` directory exists

**Gap confirmed.** `docs/` contains `IMPLEMENTATION_CONTRACT.md`, `ROADMAP.md`, `EDGE_STATUS.md`, `profit_path_debt_log.md`, and `evidence_store_schema.md` — but no CODEMAPS folder. Architectural diagrams, module dependency maps, and pipeline flow documentation exist only in `IMPLEMENTATION_CONTRACT.md` prose. This file is the first machine-readable diagram.

### 6b. `analysis/__init__.py` has no module-level docstring

The file begins with bare `from` imports. Given it defines the central `SignalAnalysis` handoff type, a docstring explaining the layer's purpose and INV-4 rule would help.

### 6c. `feeds/__init__.py` has no module-level docstring

Defines `NewsItem`; no docstring.

### 6d. `analysis/regime_classifier.py` docstring is a bare stub

First three lines: `"""\nRegime classifier: computes a probabilistic regime weight vector for a market.\n\n"""` — no detail on regime names, weight semantics, or interaction with BlendTask.

### 6e. `feeds/google_news_monitor.py` appears unused

Not wired into `TradingBot.run()`. `search_news_monitor.py` is the active replacement. No deprecation notice or removal ticket visible in the file.

### 6f. `tasks/budget_manager.py` admission control is not wired to `AccumulationTask`

`BudgetManager` exists and has tests but `AccumulationTask.__init__` does not accept a `BudgetManager` argument in the grep-visible signature. Whether admission control is actually enforced in the live accumulation path is unclear from static analysis alone.

### 6g. Governance process is architecturally isolated but not documented as such

`governance/` runs as a separate launchd service. Its only coupling to the main bot is:
- **Reads**: `governance/adapter.py` queries the bot's SQLite DBs and log files
- **Writes**: produces `data/runtime_overrides.yaml`, consumed by `runtime_overrides_poll`

This clean separation is not documented anywhere outside CLAUDE.md gotchas. A CODEMAPS entry would make this explicit.

### 6h. `analysis/fade_signal.py` signal type not reflected in `EvidenceScore`

`SignalAnalysis.signal_type` includes `"fade_tweet"` but `Evidence` and `EvidenceScore` types in `analysis/evidence_types.py` have no corresponding `source_class` for fade signals. Fade signals route through the same blend path as news items but their provenance is not separately trackable in the accumulation DB.

---

## Dependencies

### External
- `aiohttp` — async HTTP (feeds, Kalshi REST)
- `websockets` — Kalshi WS (version-sniffed header kwarg)
- `feedparser` — RSS parsing (sync, run in executor)
- `praw` / manual OAuth2 — Reddit
- `cryptography` — RSA-PSS signing
- `sqlite3` (stdlib) — evidence store + paper trades DB
- Ollama (local HTTP) — LLM inference (`signal_analyzer`, `governance/llm.py`)

### Internal Module Dependency Summary
```
feeds ──────────────▶ kalshi (KalshiMarket type)
analysis ───────────▶ feeds (NewsItem), kalshi (KalshiMarket, KalshiRestClient)
tasks ───────────────▶ analysis (types + pure functions), kalshi (KalshiMarket)
governance ──────────▶ utils (logger), config; does NOT import analysis/feeds/tasks
main.py ─────────────▶ all layers
utils ───────────────▶ config only (no upward deps)
config ──────────────▶ stdlib + dotenv only
```
