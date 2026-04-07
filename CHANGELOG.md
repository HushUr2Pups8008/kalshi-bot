# Changelog

All notable changes to kalshi-bot are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.23.0] - 2026-04-07

### Fixed
- **Bankroll desync bug (CRITICAL):** `_credit_bankroll(total_payout)` was called outside
  the `with self._conn:` atomic block in `resolve_market()`. A process crash between the
  trade resolution commit and the bankroll credit would permanently understate the notional
  bankroll. Moved inside the block so both writes are committed atomically (`paper_trader.py`).

### Added
- **Defensive market object copy:** Three sites in `main.py` that mutated `KalshiMarket`
  objects pulled from the shared cache now call `dataclasses.replace(market)` before any
  field write. Eliminates a latent race condition where a concurrent WebSocket price update
  could see a partially-mutated object.
- **Kelly shadow sizing:** `paper_trades` gains a `kelly_contracts` column (INTEGER) via
  migration. Every paper trade now also computes and stores what Kelly sizing would have
  recommended (without applying it -- flat 5 still used for paper). Enables retrospective
  comparison of flat-5 vs Kelly P&L trajectories before going live (`paper_trader.py`).
- **Section 7e -- Kelly Shadow Sizing** in `scripts/performance_analysis.py`: shows flat-5
  net P&L, capital deployed, and ROI vs what Kelly shadow would have returned on the same
  resolved trades, with a Delta column.

### Changed
- Paper trade log line now includes `kelly_shadow=N` annotation when in paper mode, showing
  what Kelly would have sized for that trade.

---

## [0.22.0] - 2026-04-07

### Added
- `analysis/keyword_stats.py`: new `KeywordStats` class reads per-(keyword, series_ticker)
  accuracy from the `keyword_outcomes` table (written since v0.19.0 but never consumed).
  Returns a multiplier in [0.5, 1.5] applied to each keyword's base strength in
  `_keyword_score()`. Loaded at startup, refreshed every 6h, thread-safe. Closes the
  keyword feedback loop that was the largest "Phase 3" gap found in the v0.22.0 audit.
- Seven new columns in `paper_trades` schema (added via ALTER TABLE migration, zero data
  loss): `series_ticker`, `resolved_ts`, `signal_type`, `match_score`, `llm_direction`,
  `llm_magnitude`, `llm_confidence`. Historical `series_ticker` backfilled from
  `json_extract(market_snapshot, '$.series_ticker')`.
- `SignalAnalysis` dataclass extended with `match_score`, `signal_type`, `llm_direction`,
  `llm_magnitude`, `llm_confidence` fields (`analysis/__init__.py`).
- `signal_type` tagging: news pipeline signals tagged `"news"`, price-fade signals tagged
  `"price_fade"`, fade-tweet signals tagged `"fade_tweet"` (`main.py`).
- `match_score` now flows from `find_candidates()` through `_process_candidate()` into
  `SignalAnalysis` and is persisted to `paper_trades` (`main.py`, `paper_trader.py`).
- Raw LLM fields (`direction`, `magnitude`, `confidence`) now returned from
  `_parse_llm_response()` / `estimate_probability()` and stored in each trade record,
  enabling future LLM calibration analysis (`signal_analyzer.py`, `paper_trader.py`).
- Three new sections in `scripts/performance_analysis.py`:
  - **7b. Per-series win rate**: win rate and P&L grouped by `series_ticker`.
  - **7c. Keyword accuracy**: per-(keyword, series_ticker) accuracy table with applied
    multiplier; aggregate per-keyword table for overview.
  - **7d. Match score calibration**: win rate by match_score band with advisory if
    empirical data suggests a better `PAPER_MIN_MATCH_SCORE` threshold.

### Changed
- `_keyword_score()` now accepts optional `keyword_stats` and `series_ticker` args;
  applies per-(keyword, series_ticker) accuracy multiplier when sufficient data exists
  (>= 10 samples). Falls back to 1.0 (neutral) below threshold (`signal_analyzer.py`).
- `estimate_probability()` signature extended to accept `keyword_stats` and returns a
  7-tuple including raw LLM fields. All callers updated (`main.py`).
- `resolve_market()` now populates `resolved_ts` on every resolution (`paper_trader.py`).
- Two pre-existing em dashes in `paper_trader.py` log strings replaced with `--` to
  comply with Windows cp1252 logging safety rules.

## [0.21.0] - 2026-04-07

### Added
- **Subreddit discovery loop** (`feeds/subreddit_discovery.py` NEW,
  `feeds/subreddit_selector.py`, `main.py`, `config.py`,
  `trading/paper_trader.py`, `scripts/performance_analysis.py`) -- closes the
  second half of the source quality feedback loop. v0.20.0 pruned zero-signal
  subreddits; v0.21.0 finds new ones. Reddit's public post search API is queried
  every 6h for active market topics; subreddits generating that discussion are
  inserted as candidates and probed automatically. source_stats evaluates quality
  the same way as any other source -- good ones stay, bad ones are suppressed.

- **New module `feeds/subreddit_discovery.py`** -- `run_discovery_pass()` queries
  Reddit post search for up to SUBREDDIT_DISCOVERY_MAX_QUERIES active market topic
  queries, extracts subreddit names, filters against known subs and a generic
  content blocklist, and inserts new candidates into `subreddit_candidates` DB table.
  Rate-limited to 1 request per 2 seconds (well within Reddit's public API limits).

- **New table `subreddit_candidates`** in `paper_trades.db` (`trading/paper_trader.py`)
  -- tracks discovered subreddits with probe_count, last_probed, discovered_via, and
  status (candidate/suppressed). Added via `CREATE TABLE IF NOT EXISTS` -- safe on
  existing DBs.

- **Tier 3 subreddit selection** (`feeds/subreddit_selector.py`) -- `select_subreddits()`
  now accepts `db_path` and fills remaining capacity slots with candidate probes.
  Candidates are selected by probe_count ASC (never-probed first), then oldest.
  Per-candidate 3h cooldown prevents re-probing already-evaluated subs. Suppressed
  candidates are marked in DB and permanently excluded.

- **Discovery scheduled task** (`main.py` `_subreddit_discovery_task()`) -- runs
  every SUBREDDIT_DISCOVERY_INTERVAL_SECS (default 6h) with a 5-minute startup
  delay to let the market cache warm. Candidate count logged at INFO level.

- **4 new env-configurable constants** (`config.py`):
  SUBREDDIT_DISCOVERY_INTERVAL_SECS (default 21600),
  SUBREDDIT_PROBE_COOLDOWN_SECS (default 10800),
  SUBREDDIT_PROBE_SLOTS (default 3),
  SUBREDDIT_DISCOVERY_MAX_QUERIES (default 10).

- **Candidate subreddits section** (`scripts/performance_analysis.py` section 6b)
  -- shows discovered subs with probe count, last probed timestamp, and
  status (candidate/suppressed).

- **Plan archiving convention** (`docs/plans/` NEW) -- non-trivial implementation
  plans archived as Architectural Decision Records. Added rule to both project
  `CLAUDE.md` and global `~/.claude/CLAUDE.md`. First two ADRs archived:
  `docs/plans/v0.20.0_source_quality_feedback_loop.md` and
  `docs/plans/v0.21.0_subreddit_discovery_loop.md`.

## [0.20.0] - 2026-04-06

### Added
- **Source quality feedback loop** (`analysis/source_stats.py` NEW, `main.py`,
  `feeds/subreddit_selector.py`, `config.py`, `trading/paper_trader.py`,
  `scripts/performance_analysis.py`) -- closes the "garbage subreddit" problem.
  The pipeline previously had no visibility into what happened before a SIGNAL event,
  so zero-signal subreddits consumed poll slots indefinitely. Now every post,
  signal, opportunity, and trade is counted per source.

- **New module `analysis/source_stats.py`** -- SourceStats class tracking the full
  quality funnel (posts_seen -> signals -> opportunities -> trades) per source.
  Uses signal_rate (signals / posts_seen) as the primary quality metric, available
  within 24-48 hours vs the existing win_rate credibility system which needs 10+
  resolved trades (weeks). Writes are batched in-memory and flushed every 30 minutes
  (piggybacked on the auto-resolve task) to avoid SQLite write contention.

- **Subreddit suppression** (`feeds/subreddit_selector.py`) -- zero-signal subreddits
  (>= SOURCE_STATS_ZERO_SIGNAL_POSTS posts, 0 signals) are skipped from topic-driven
  selection. Topic subreddits are sorted by signal rate so high-quality sources fill
  the REDDIT_MAX_SUBREDDITS cap first. Core subreddits (worldnews, geopolitics, etc.)
  are always exempt from suppression and are never quality-gated.

- **New DB table `source_stats`** in `data/paper_trades.db` -- schema added via
  `CREATE TABLE IF NOT EXISTS` in `trading/paper_trader.py` DDL (safe migration on
  existing DB). Columns: source, posts_seen, signals, opportunities, trades,
  last_signal, last_updated.

- **Source quality funnel section** in `scripts/performance_analysis.py` -- new
  section 6 ("SOURCE QUALITY FUNNEL") shows per-source signal rate, opportunity rate,
  and quality label (Good / Low / SUPPRESSED / ?) using the source_stats table.

- **3 new env-configurable constants** (`config.py`):
  SOURCE_STATS_MIN_POSTS (default 100), SOURCE_STATS_LOW_SIGNAL_RATE (default 0.005),
  SOURCE_STATS_ZERO_SIGNAL_POSTS (default 200).

### Changed
- `_process_candidate()` in `main.py` now captures the return value of
  `executor.execute()` so trade placements can be counted per source.

## [0.19.0] - 2026-04-06

### Added
- **Market feedback loop — Phase 1 and 2** (`main.py`, `trading/paper_trader.py`,
  `utils/logger.py`, `config.py`, `docs/market_feedback_loop_roadmap.md`) --
  closes the one-way news->trade pipeline into four feedback loops. See
  `docs/market_feedback_loop_roadmap.md` for architecture and Mac Studio upgrade path.

- **Loop A: Price-velocity-driven targeted news search** (`main.py`) -- when a geo
  market price moves >= PRICE_MOVE_THRESHOLD_CENTS (10c, env-configurable) within a
  5-minute rolling window, immediately fetch Google News RSS + GDELT for that
  specific market's tokens. Inverts discovery: volatile markets proactively hunt for
  news rather than waiting for RSS. Rate-limited to PRICE_SEARCH_COOLDOWN_SECS (30min)
  per ticker. Uses existing `_markets_to_queries()` and `poll_feed()` -- no new deps.

- **Loop B: Keyword outcome tracking** (`trading/paper_trader.py`) -- new
  `keyword_outcomes` table in `paper_trades.db`. On every trade resolution, one row
  per keyword that fired on that trade is written with: keyword, its declared direction,
  the side we bet, resolved_yes, and a `correct` flag (1 if keyword pointed the right
  way). Enables per-series keyword accuracy queries: e.g. "invasion" on KXTRUMP* series
  has 10% correct rate -- the foundation for Phase 3 dynamic signal weighting.
  Schema added via `CREATE TABLE IF NOT EXISTS` (safe migration on existing DB).

- **Loop C: Open position price drift logging** (`main.py`, `utils/logger.py`) --
  when a WS price update arrives for a ticker with an open paper position, emit a
  `POSITION_DRIFT` event to `trades.jsonl` if price has moved >= DRIFT_ALERT_CENTS
  (15c, env-configurable) from entry. Rate-limited to DRIFT_LOG_COOLDOWN_SECS (1h)
  per ticker. On Mac Studio this will trigger LLM re-analysis; now it builds the
  data trail for position health monitoring.

- **Loop D: New market detection** (`main.py`, `utils/logger.py`) -- at each 30-min
  market cache refresh, compare the new ticker set against the previous. Any ticker
  not seen before emits a `NEW_MARKET` event to `trades.jsonl` and immediately
  triggers a Loop A targeted news search. New Kalshi listings are discovered within
  30 minutes of appearing rather than waiting for RSS to bring a matching story.

- **5 new env-configurable constants** (`config.py`): `DRIFT_ALERT_CENTS`,
  `DRIFT_LOG_COOLDOWN_SECS`, `PRICE_MOVE_THRESHOLD_CENTS`, `PRICE_SEARCH_COOLDOWN_SECS`,
  `PRICE_VELOCITY_WINDOW_SECS`.

- **Architecture roadmap** (`docs/market_feedback_loop_roadmap.md`) -- committed to
  repo so it travels to Mac Studio on `git pull`. Documents all four loops,
  Phase 3 dynamic weighting implementation sketch, and multi-agent vision.

---

## [0.18.0] - 2026-04-06

### Changed
- **Paper ticker cooldown 4h -> 2h** (`.env` `PAPER_TICKER_COOLDOWN=7200`) -- historical
  analysis showed 34 of 178 skips were cooldown blocks. The 4h window was blocking
  legitimate follow-on signals (ceasefire news after airstrike news, ~2-6h later).
  2h still prevents intra-burst spam (bot polls every 60s, so 2h = 120 suppression cycles)
  but allows real signal diversity. No code change; `cfg.paper_ticker_cooldown` reads from env.
- **Staleness guard 5min -> 10min** (`.env` `MAX_NEWS_AGE_SECONDS=600`) -- with
  max_candidates=3 and 20-40s/inference, articles can be 3-5 min in queue before LLM
  evaluation. The old 300s window was discarding valid articles during busy inference periods.
  10 min is still within alpha window for 24h Kalshi markets.
- **Same-side duplicate guard: flat block -> prob+price delta** (`trading/executor.py:134-138`) --
  the paper phase was blocking ALL same-side re-entry on a ticker regardless of how much
  the probability estimate shifted. 11 historical skips used this path. Now allows
  re-entry when estimated_prob has shifted >=0.07 OR market price has moved >=5c since
  the open position. This lets the LLM accumulate on genuinely updated signals while
  still blocking informationally-identical ones.

### Added
- **Senate confirmation + Trump action keyword groups** (`config.py` `GEOPOLITICAL_SIGNALS`) --
  active Kalshi markets include KXSENATECONFIRM (Bondi, Gabbard, Hegseth), KXBONDITESTIFY,
  and various Trump executive action markets. The keyword gate had no terms for confirmation
  hearings ("confirmation hearing", "senate confirms", "nominee confirmed", "testifies before")
  or Trump-specific actions ("trump fires", "trump pardons", "fired by trump"). These markets
  were generating zero SIGNAL events despite active news flow. Two new keyword groups
  cover these categories at strength 0.12 and 0.10 respectively.

---

## [0.17.1] - 2026-04-06

### Changed
- **`PAPER_MAX_CANDIDATES` 1 -> 3** (`config.py`) -- the bot was evaluating only the
  single top Jaccard match per article. With 822+ cached markets, the top match is
  frequently the wrong market (e.g. "Trump fires Bondi" -> China visit at score 0.033).
  The LLM correctly returned neutral on these mismatches, suppressing real signals.
  Raising to 3 lets the LLM evaluate the top 3 conceptual candidates and find genuine
  relevance. CPU constraint: at 20-40s/call this is ~120s max per article. On Mac Studio
  M4 Max (<5s inference) raise to 8-10.
- **`PAPER_MIN_MATCH_SCORE` 0.03 -> 0.06** (`config.py`) -- empirical analysis of match
  score distribution showed scores below 0.06 are almost always wrong-market noise.
  Real relevance starts at ~0.06. The old floor of 0.03 was passing garbage matches
  to the LLM unnecessarily. This pairs with the candidates increase: fewer but better
  candidates per article.

---

## [0.17.0] - 2026-04-06

### Added
- **Performance analysis script** (`scripts/performance_analysis.py`) -- repeatable
  end-to-end analysis covering the full signal pipeline. Reads `logs/trades.jsonl`
  and `data/paper_trades.db` to produce a dated report in `logs/analysis_YYYYMMDD_HHmm.txt`.
  Sections: signal pipeline funnel (signal->opportunity->trade conversion rates), placed
  trades performance (win rate, P&L, ROI, edge calibration), skip reason breakdown,
  missed opportunities with counterfactual P&L for those with known resolutions,
  per-source performance, edge calibration vs actual outcomes, go-live readiness.
  Optional `--enrich` flag fetches Kalshi API resolutions for skipped tickers and caches
  them to `logs/market_resolution_cache.json`. Date window via `--since`/`--until`
  (default: last 30 days). Designed for daily/weekly/monthly cadence.

---

## [0.16.0] - 2026-04-03

### Added
- **Source credibility time decay** (`analysis/source_credibility.py`) -- added
  `_time_decayed_accuracy()` method. Rather than computing accuracy as a flat win/loss
  ratio, accuracy is now a time-weighted average: each resolved outcome is weighted by
  `exp(-ln2 / half_life * age_days)` where half_life defaults to 30 days. An outcome
  from 60 days ago counts 25% as much as one from today. Prevents stale performance
  data from permanently locking a source into a high or low multiplier. The half-life
  is configurable via `CREDIBILITY_HALF_LIFE_DAYS` in `config.py`.
- **Config-driven go-live gates** (`config.py`, `main.py`) -- added three new `BotConfig`
  fields controlling readiness checks before live trading confirmation:
  `go_live_min_resolved` (default 20), `go_live_min_win_rate` (default 0.52),
  `go_live_max_drawdown_pct` (default 0.20). All three are overridable via env vars.
  `_check_go_live_gates()` in `main.py` evaluates them before the CONFIRM prompt and
  prints each gate's FAIL/pass status. Gate values are logged at startup.
- **Config-driven ticker cooldowns** (`config.py`, `trading/executor.py`) -- moved
  `_LIVE_TICKER_COOLDOWN = 600` and `_PAPER_TICKER_COOLDOWN = 14400` from hardcoded
  module constants in `executor.py` to `BotConfig` fields (`live_ticker_cooldown`,
  `paper_ticker_cooldown`) overridable via `LIVE_TICKER_COOLDOWN` and
  `PAPER_TICKER_COOLDOWN` env vars. Operator can tune cooldowns without code changes.

---

## [0.14.0] - 2026-04-03

### Added
- **Startup validation gate** (`config.py`) -- `BotConfig.__post_init__()` validates all
  critical env vars at import time. Checks: `KALSHI_API_KEY_ID` non-empty,
  `KALSHI_API_KEY_SECRET` is a loadable RSA PEM key, `KALSHI_ENV` is 'demo' or 'prod',
  `BANKROLL` is positive, `KELLY_FRACTION` is in (0, 1]. On failure, prints clear error
  messages to stderr and exits immediately -- no more silent failures hours later on first
  trade attempt.
- **Feedparser timeout guard** (`feeds/rss_monitor.py`) -- wrapped `feedparser.parse()` in
  `asyncio.wait_for()` with a 30-second timeout. A hanging feed server can no longer stall
  the entire RSS poll cycle indefinitely; the feed is skipped with a warning and the cycle
  continues.

### Fixed
- **REST client error log sanitization** (`kalshi/rest_client.py`) -- HTTP error response
  bodies are now checked for sensitive patterns (auth keys, signatures) before logging.
  If detected, the body is redacted. Defense in depth for live trading mode.
- **Non-ASCII in log strings** -- replaced em dash in `rss_monitor.py` log message and
  right arrow in `rest_client.py` log message with ASCII equivalents. Prevents Windows
  cp1252 logging failures under NSSM.

---

## [0.13.0] - 2026-04-03

### Changed
- **AIMD query-limit controller for GDELT** (`feeds/gdelt_monitor.py`) -- replaced the
  static `GDELT_MAX_QUERIES=15` constant with an adaptive `_gdelt_query_limit` variable
  (starts at 5, range 1-25). Signal: HTTP response codes from GDELT itself. On any 429
  in a cycle: halve the limit (multiplicative decrease). On a clean cycle with at least
  one successful response: increment by 1 (additive increase). The limit self-calibrates
  to GDELT's actual tolerance ceiling without guessing. Timeouts are treated as network
  noise and do not adjust the limit. Removed the now-redundant `GDELT_MAX_QUERIES`
  constant.
- **AIMD articles-per-query controller for search** (`feeds/search_news_monitor.py`) --
  replaced the static `SEARCH_MAX_ARTICLES_PER_QUERY=5` constant with an adaptive
  `_search_articles_cap` variable (starts at 3, range 1-15). Signal: news queue fill
  ratio passed in via a new `queue_depth_fn` parameter. Queue >60% full: decrement cap
  (back off ingestion -- consumer is falling behind). Queue <20% full: increment cap
  (consumer has headroom -- feed it more). Renamed `SEARCH_MAX_ARTICLES_PER_QUERY` to
  `_search_articles_cap`; the old constant is gone. Cap snapshot is taken per-query to
  avoid mid-gather inconsistency.
- **`run_search_news_monitor` signature** (`feeds/search_news_monitor.py`) -- added
  optional `queue_depth_fn: Callable[[], float] | None = None` parameter. When None
  (default), the cap holds steady at its current AIMD value. Main wires it as
  `lambda: queue.qsize() / queue.maxsize`.
- **`main.py` task wiring** -- passes `queue_depth_fn` lambda to `run_search_news_monitor`
  so the search AIMD controller receives live queue fill ratio each cycle.

---

## [0.12.0] - 2026-04-03

### Fixed
- **Sports/blocklisted market filter in query generator** (`feeds/search_news_monitor.py`) --
  `_markets_to_queries()` now skips any market whose series_ticker or ticker prefix matches
  `MARKET_SERIES_BLOCKLIST_PREFIXES`. Previously, sports and crypto markets that slipped through
  the geo cache would generate irrelevant queries like "tiger woods dui" or "royal challengers
  bengaluru", flooding the queue with celebrity golf and IPL cricket articles.
- **Per-query article cap** (`feeds/search_news_monitor.py`) -- added `SEARCH_MAX_ARTICLES_PER_QUERY = 5`
  constant and switched the gather pattern from all-at-once to per-query with a capped callback.
  A single hot query can no longer dump 20+ articles in one burst; max is now 5 new articles
  across both engines per query per cycle (25 queries x 5 = 125 max/cycle, down from ~500).
  This is the primary fix for the 8,700+ queue overflow drops observed overnight.
- **News queue maxsize** (`main.py`) -- increased from 500 to 2000 to absorb legitimate
  high-volume news events (e.g. breaking geopolitical stories across many sources) without dropping.
- **GDELT 429 rate-limit backoff** (`feeds/gdelt_monitor.py`) -- added module-level
  `_gdelt_backoff_until` / `_gdelt_backoff_secs` state mirroring the Reddit backoff pattern.
  On HTTP 429: enters backoff, skips the remainder of the current cycle, doubles the delay
  (60s initial, max 900s). On next successful response: resets to 60s. Previously GDELT
  returned 429 with no recovery, retrying immediately on the next cycle.

---

## [0.11.1] - 2026-04-02

### Changed
- **Search/GDELT query prioritization** (`feeds/search_news_monitor.py`) -- `_markets_to_queries()`
  now ranks markets by `open_interest * (1 - |price - 50| / 50)` instead of open_interest alone.
  This weights query slots toward contested markets (price near 50c) with meaningful volume,
  where fresh news is most likely to create exploitable edge. Fully-decided markets (price
  near 0 or 100) score near zero regardless of volume. Applies to both the search monitor
  (Google + Bing) and GDELT, which both import `_markets_to_queries` from this module.

---

## [0.11.0] - 2026-04-02

### Added
- **Multi-engine search monitor** (`feeds/search_news_monitor.py`) -- replaces
  `google_news_monitor.py`. Now fetches each query from both Google News RSS and Bing
  News RSS in a single `asyncio.gather` call (50 fetches/cycle at 25 queries x 2 engines).
  Shared dedup cache suppresses cross-engine duplicates. No new dependencies.
- **GDELT monitor** (`feeds/gdelt_monitor.py`) -- new async task querying the GDELT
  Document 2.0 geopolitical event database. Free, no API key, updates every 15 minutes.
  Generates up to 15 queries/cycle from active market titles (reusing `_markets_to_queries`
  from search_news_monitor), fetches sequentially with a 2s stagger, emits NewsItem
  via the same callback chain. Poll interval 900s to match GDELT's indexing cadence.
- **`_make_market_getter()`** on `TradingBot` (`main.py`) -- replaces `_make_gnews_getter()`;
  shared by both search and GDELT monitors.

### Changed
- **`TradingBot.run()`** (`main.py`) -- replaced `gnews` task with two concurrent tasks:
  `search` (Google + Bing RSS, 300s) and `gdelt` (GDELT JSON API, 900s). Total async
  tasks now 10 (11 when fade_tweets is configured).
- `feeds/google_news_monitor.py` superseded by `feeds/search_news_monitor.py` (file kept
  for git history; no longer imported).

---

## [0.10.0] - 2026-04-02

### Added
- **Google News RSS monitor** (`feeds/google_news_monitor.py`) -- new async task that
  generates targeted Google News RSS queries from active Kalshi market titles and fetches
  them in parallel every 300s. No API key required. Indexes within minutes of publication.
  Markets are sorted by open_interest so the most-traded topics get query priority. Up to
  25 distinct queries per cycle (cap configurable via `GNEWS_MAX_QUERIES`). Reuses
  `poll_feed()` from `rss_monitor.py` for feedparser, dedup, and NewsItem creation.
  Feeds the same news consumer callback as RSS and Reddit -- all three sources run
  concurrently as independent async tasks.
- **`_make_gnews_getter()`** method on `TradingBot` (`main.py`) -- sync callable that
  reads `self.matcher._cache._markets` and passes it to the Google News monitor each cycle.

### Changed
- **`TradingBot.run()`** (`main.py`) -- added `gnews` as a 9th concurrent async task
  alongside rss, reddit, news_consumer, websocket, ws_warm, daily_report, market_refresh,
  and auto_resolve.

---

## [0.9.1] - 2026-04-02

### Fixed
- **Reddit stagger sleep wasted on backed-off subreddits** (`feeds/reddit_monitor.py`) --
  the poll loop now checks `_backoff` before calling `_poll_subreddit` and skips the
  entire subreddit (including the `asyncio.sleep(stagger)`) when the sub is in backoff.
  Previously the 10s stagger ran even for subs that `_fetch_subreddit()` immediately
  short-circuited. Saves 10s per backed-off subreddit per cycle in public mode.

---

## [0.9.0] - 2026-04-02

### Added
- **Adaptive subreddit selection** (`feeds/subreddit_selector.py`, `config.py`) -- Reddit
  polling is now vocabulary-driven instead of always querying all 35 hardcoded subreddits.
  Each poll cycle selects up to 20 subreddits: 5 core (worldnews, geopolitics,
  InternationalNews, CredibleDefense, ArmedConflicts) are always included; topic-specific
  subreddits (12 topics: military, elections, trade, nuclear, sanctions, US domestic,
  and 6 regional buckets) are added only when open Kalshi market titles match their
  keyword sets. This reduces steady-state request volume and targets subreddits to the
  markets actually being traded.
- **`REDDIT_CORE_SUBREDDITS`**, **`REDDIT_SUBREDDIT_TOPIC_MAP`**,
  **`REDDIT_TOPIC_KEYWORDS`**, **`REDDIT_MAX_SUBREDDITS`** constants in `config.py` --
  fully configurable; topic map and keyword sets can be expanded without touching logic.

### Changed
- **`run_reddit_monitor()`** (`feeds/reddit_monitor.py`) -- `subreddits` parameter now
  accepts a static `list[str]`, an async callable, or `None` (falls back to full
  `REDDIT_SUBREDDITS` list). The subreddit list is re-evaluated at the top of each
  poll cycle when an async callable is passed.
- **`TradingBot.run()`** (`main.py`) -- wires the Reddit monitor with
  `_make_subreddit_getter()`, an async callable that reads the live market cache
  (`self.matcher._cache._markets`) and calls `select_subreddits()` each cycle.

---

## [0.8.2] - 2026-04-02

### Fixed
- **Startup crash after v0.8.1** (`main.py:532`) -- startup log referenced
  `cfg.bet_pct_bankroll` which was renamed to `cfg.max_bet_pct_bankroll` in v0.8.1.
  Caused `AttributeError` on every startup, putting the NSSM service into a crash loop.
  Fixed by updating the one reference in the startup log message.

---

## [0.8.1] - 2026-04-02

### Changed
- **Confidence-scaled bet sizing** (`analysis/kelly.py`) -- LLM confidence is now a
  first-class multiplier on the Kelly fraction. Previously confidence only affected the
  probability estimate (via magnitude scaling); now it directly scales the bet:
  `f_adjusted = f_full * kelly_fraction * confidence * source_multiplier * time_discount`.
  A 0.95-confidence signal bets 95% of what Kelly says; a 0.50-confidence signal bets 50%.
  Keyword-only fallback confidence is already capped at 0.70, so fallback trades are
  automatically reduced relative to LLM-backed trades.
- **Time discount for long-duration bets** (`analysis/kelly.py`) -- new `_time_discount()`
  function applies exponential decay to bets that lock up capital far into the future.
  Markets closing within 3 days get full sizing (1.0x). Beyond that, discount decays with
  a 14-day half-life toward a 0.20 floor (60+ day markets get at most 20% of base sizing).
  Half-life and floor are configurable via `TIME_DISCOUNT_HALF_LIFE` and `TIME_DISCOUNT_FLOOR`
  env vars.
- **Bankroll-proportional bet ceiling** (`config.py`) -- replaced the fixed `$25` hard cap
  with `MAX_BET_PCT_BANKROLL * bankroll` (default 15% = $75 on $500, grows with bankroll).
  The old hard cap is retained as a safety backstop raised to $200 (`MAX_BET_HARD_CAP`).
  `dynamic_max_bet()` now uses `max_bet_pct_bankroll` as the operating ceiling.
- **`kelly_bet()` call site updated** (`main.py`) -- now passes `confidence` and
  `days_to_close` (extracted from `market.close_time` via `_days_to_close()`) into
  `kelly_bet()`. Defaults to 14 days when close time is unavailable.

## [0.8.0] - 2026-04-02

### Added
- **Auto-resolution task** (`main.py`) -- new 6th concurrent async task
  `_auto_resolve_task()` polls Kalshi every 30 minutes for settled markets.
  `_check_and_resolve()` queries all open paper trade tickers, calls
  `rest_client.get_market()` for each, and auto-calls `paper_trader.resolve_market()`
  when status is `finalized`/`settled` with a `yes`/`no` result. Eliminates the need
  for manual `--resolve` commands and unblocks the daily performance report.
- **`result` field on `KalshiMarket`** (`kalshi/__init__.py`, `kalshi/rest_client.py`) --
  both `get_market()` and `get_markets()` now parse the `result` field from the API
  response (`"yes"`, `"no"`, or `""` if not yet settled). Required by the auto-resolver.

### Changed
- **Expanded signal vocabulary** (`config.py`, `analysis/market_matcher.py`) -- added
  trade/economic policy, foreign policy, and domestic policy terms across all four
  vocabulary layers:
  - `GEOPOLITICAL_SIGNALS` (+6 new keyword categories for tariffs, trade deals, diplomatic
    events, and domestic US policy triggers)
  - `_GEOPOLITICAL_BOOST` (+30 terms: tariff, trade, import, export, embargo, diplomatic,
    executive order, shutdown, impeachment, cabinet, confirmation, etc.)
  - `_GEO_NAMED_ENTITIES` (+20 terms: additional country demonyms, current officials
    Vance/Rubio/Waltz/Macron/Scholz/Starmer, institutions NATO/Pentagon/Kremlin/Congress/Senate)
  - `_GEO_SERIES_KEYWORDS` (+35 terms for series discovery: tariffs, trade war, trade deal,
    liberation day, embargo, customs, debt ceiling, executive order, recession, treasury,
    deportation, border, and more country names)
  Goal: capture Trump tariff/trade policy markets and broader domestic/foreign policy
  markets that were previously invisible to the signal pipeline.

---

## [0.7.1] - 2026-03-30

### Fixed
- **errors.log rotation stall** (`utils/logger.py`) -- `errors.log` (WARNING+ only) failed to
  rotate at midnight when no warnings were emitted around that time. `TimedRotatingFileHandler`
  only checks `shouldRollover()` on `emit()`, so the errors handler was never triggered during
  quiet periods. Fixed by adding a peer-nudge mechanism: when `bot.log` (DEBUG+, always active)
  rotates at midnight, it now also triggers `shouldRollover()` on the errors handler, ensuring
  both files rotate in lockstep regardless of message volume.

---

## [0.7.0] - 2026-03-29

### Added
- **Reddit OAuth2 support** (`feeds/reddit_monitor.py`, `config.py`) -- when `REDDIT_CLIENT_ID`
  and `REDDIT_CLIENT_SECRET` are set in `.env`, the Reddit monitor authenticates via OAuth2
  `client_credentials` grant and uses `oauth.reddit.com` (600 req/10 min). Without credentials,
  falls back to the public JSON API (current behavior, aggressively rate-limited).
- **`_RedditAuth` token manager** (`feeds/reddit_monitor.py`) -- handles token acquisition,
  caching, auto-refresh (60s before expiry), HTTP 401 retry, and permanent credential failure
  detection. Zero new dependencies (uses existing `aiohttp`).
- **OAuth-aware circuit breaker** (`feeds/reddit_monitor.py`) -- under OAuth, HTTP 403 means
  private/quarantined subreddit (not IP block) and does not count toward the global circuit
  breaker. Only 429s trigger the global pause. OAuth mode uses a shorter 10-min global backoff
  (vs 30-min for public mode) and 3s stagger between subreddits (vs 10s).
- **Reddit OAuth env vars** (`.env.example`) -- documented `REDDIT_CLIENT_ID`,
  `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` with setup instructions.
- **`reddit_oauth_available` property** (`config.py`) -- convenience check for whether Reddit
  OAuth credentials are configured.

### Fixed
- **Reddit 403 storm** -- the public JSON API was producing ~2,600 HTTP 403s/week with
  escalating IP blocks lasting 6-14+ hours. OAuth2 eliminates this entirely.

---

## [0.6.7] - 2026-03-23

### Added
- **Startup banner** (`utils/logger.py`) — `emit_startup_banner(version, model, env)` writes
  a one-line context header (`# ===== v0.6.7 | env=demo | model=qwen2.5:7b | py=3.14.x =====`)
  to both `bot.log` and `errors.log` on startup. The banner is stored in each handler and
  re-emitted automatically after every midnight rotation, so every archived log file is
  self-describing without needing to search back to the beginning of the run.

### Fixed
- **Multiple file handlers per log file** (`utils/logger.py`) — each `get_logger()` call
  previously created new `_DailyRotatingFileHandler` instances. With ~10 named loggers,
  ~10 separate handlers were all writing to and trying to rotate `bot.log` simultaneously
  at midnight. Fixed by promoting `_app_fh` and `_err_fh` to module-level singletons shared
  across every logger — exactly one write and one rotation attempt per record.
- **Em dash encoding** (`main.py`) — three pre-existing `--` (em dash U+2014) characters in
  `log.warning()`, `log.info()`, and `print()` calls. Windows cp1252 log handlers silently
  drop messages containing non-ASCII and dump a fake crash traceback. Replaced with `--`.

---

## [0.6.6] - 2026-03-23

### Added
- **Daily log rotation** (`utils/logger.py`) — replaced the size-based
  `_WindowsSafeRotatingFileHandler` with `_DailyRotatingFileHandler` (midnight rotation,
  90-day retention, named `bot.log.YYYY-MM-DD`). Uses copy+truncate strategy so the base
  log path never moves — open file handles (VS Code, tail) continue uninterrupted. Works
  identically on Mac and Windows.
- **`errors.log`** (`utils/logger.py`) — WARNING+ only log file written alongside `bot.log`.
  Enables fast triage without grepping the full DEBUG output.
- **NSSM `service_stderr.log` rotation** — configured `AppRotateFiles=1`,
  `AppRotateOnline=1`, `AppRotateBytes=5242880` via registry. Rotation now fires at 5MB
  while the service is running, not just on restart.

---

## [0.6.5] - 2026-03-23

### Fixed
- **Ollama circuit breaker permanently open** (`analysis/signal_analyzer.py`) — circuit
  probes were full 60s inference calls. When Ollama was slow, each probe timed out,
  incremented the failure counter, and reset the 5-minute lockout window — the circuit
  could never close. Added `_ollama_ping()`: a cheap `GET /api/version` call with a 5s
  timeout. At probe time, ping first. A failed ping extends the lockout timer without
  touching the failure counter. A successful ping resets the counter and unlocks inference.

---

## [0.6.4] - 2026-03-23

### Changed
- **Fade signal source: tweet feed replaced with WebSocket price detector**
  (`analysis/fade_signal.py`, `main.py`, `config.py`) — rsshub.app Twitter routes all
  returned 404 after X blocked public RSSHub instances. Replaced with a price-crossing
  detector on the existing authenticated Kalshi WebSocket connection (no new dependency).
  `detect_price_fade()` fires when a geo market crosses above 85c or below 15c, with a
  1c hysteresis buffer to suppress boundary noise. `_warm_ws_subscriptions()` subscribes
  to all geo market tickers at startup. Trade reasoning is prefixed `[PRICE_FADE...]`
  for SQL win-rate queries.

### Removed
- `FADE_TWEET_FEED_URLS`, `_on_fade_tweet()`, `_process_fade_tweet()`,
  `_account_from_rsshub_url()` — tweet feed infrastructure no longer needed.

---

## [0.6.3] - 2026-03-19

### Fixed
- **Same-side duplicate positions blocked** (`trading/executor.py`, `config.py`) — the
  multi-position guard only blocked same-side trades within a +-2% probability delta,
  allowing duplicate NO positions on the same ticker when estimates differed by >2%.
  Added `PAPER_BLOCK_SAME_SIDE_DUPLICATE = True` in paper mode: any existing same-side
  open position on a ticker now unconditionally blocks a new one. Live mode retains the
  narrow delta guard. Fixes 2 open NO positions on `KXZELENSKYYOUT-26APR01`.
- **NSSM log rotation now active while running** (`setup_service.ps1`) — `AppRotateOnline`
  was 0, so NSSM only rotated logs at service restart. With 48h uptime, `service_stderr.log`
  grew to 10MB unchecked. Set `AppRotateOnline 1` so rotation fires at 10MB regardless
  of uptime.
- **Double market cache refresh debounced** (`analysis/market_matcher.py`) — two code paths
  could trigger `_refresh()` within seconds of each other (scheduled task + TTL expiry on
  an incoming signal). Added a 60-second debounce inside `_refresh()` and `_refresh_all()`:
  if a refresh completed within the last 60s, the second call returns immediately.

### Removed
- **"Just In News" (The Hill) RSS feed** (`config.py`) — feed re-served articles up to
  45 days old as new items, contributing 301 of 740 stale-skipped queue entries over 48h.
  Zero tradeable signals produced. Removed from `RSS_FEEDS`.

---

## [0.6.2] - 2026-03-17

### Fixed
- **Windows log rotation** (`utils/logger.py`) — `RotatingFileHandler.doRollover()` was
  calling `os.rename()` which fails with `PermissionError: [WinError 32]` when any process
  holds `bot.log` open (VS Code, tail -f, etc.). After failure, `self.stream` was left `None`
  and every subsequent log emit was silently dropped. Replaced with a
  `_WindowsSafeRotatingFileHandler` subclass that uses copy+truncate instead of rename —
  the file keeps the same path/handle, so open processes are unaffected.
- **Em dash encoding trap** (`signal_analyzer.py`, `reddit_monitor.py`) — new log strings
  introduced in v0.6.1 contained em dashes (`—` U+2014). Windows log handlers default to
  cp1252, causing `handleError()` to dump fake crash tracebacks to `service_stderr.log`
  while silently dropping the message. Replaced with `--`.

### Added
- Windows ASCII-only log string rule documented in `CLAUDE.md` — enforced by pre-commit grep.

---

## [0.6.1] - 2026-03-17

### Added
- **Ollama circuit breaker** (`analysis/signal_analyzer.py`) — after 3 consecutive
  connection/timeout failures, skips the 60s timeout window and returns `None` immediately.
  Probes every 5 minutes. Auto-recovers on first success. Prevents the bot burning 60s per
  signal when Ollama is down.
- **Reddit global circuit breaker** (`feeds/reddit_monitor.py`) — if >=50% of subreddits
  fail in a single poll cycle, suspends all Reddit polling for 30 minutes. Previously only
  individual subreddits were backed off; a mass 403 storm from running concurrent instances
  was not handled.
- **Reddit 403 backoff** — 403 responses now trigger a 60s per-subreddit backoff (was
  previously ignored). Reddit's API blocks non-OAuth bots on private subreddits; this
  prevents hammering blocked endpoints.

---

## [0.6.0] - 2026-03-17

### Added
- **Portfolio state object** (`trading/portfolio.py`) — single source of truth for open
  paper positions. Previously open-position checks queried SQLite directly on every signal;
  now the in-memory portfolio object handles deduplication and cooldown enforcement. Required
  before go-live to avoid double-entry on service restart.

---

## [0.5.8] - 2026-03-17

### Added
- `market_snapshot` JSON column in `paper_trades` table — stores the full market state
  (yes/no prices, volume, days to close) at decision time. Enables post-resolution analysis
  of whether the bot had accurate pricing information when it placed the trade.

---

## [0.5.7] - 2026-03-17

### Changed
- Replaced `asyncio.Queue` with `asyncio.PriorityQueue` in `main.py` signal pipeline.
  RSS feeds (Reuters, AP, BBC, Al Jazeera) are assigned higher priority than Reddit, so
  authoritative news sources are processed first during bursts.

---

## [0.5.1] - 2026-03-17

### Added
- **LLM semaphore** — caps concurrent Ollama calls to 1, preventing queue backup when
  multiple signals arrive simultaneously (each call takes 20-40s on CPU).
- **Staleness guard** — signals older than 10 minutes are discarded before LLM analysis.
  Prevents stale queue items from generating trades on outdated news.
- **Queue observability** — logs queue depth on each dequeue so backpressure is visible
  in bot.log.

### Fixed
- `UnknownTimezoneWarning` for EST/PST timezone strings in RSS feeds and `_days_to_close()`.
- `CancelledError` on shutdown in Python 3.14 — suppressed cleanly instead of propagating.
- Duplicate `PaperTrader` initialization in `main.py`.
- Deprecated `asyncio.get_event_loop()` calls in `main.py` and `market_matcher.py`.

---

## [0.5.0] - 2026-03-17

### Added
- Version control rule in `CLAUDE.md` — `VERSION` file bumped on every feature/fix commit.
- Project self-improvement system: `CLAUDE.md` (auto-loaded by Claude Code), `tasks/lessons.md`
  (hard-won runbook), `tasks/todo.md` (authoritative backlog).

### Changed
- LLM JSON parsing made robust — handles malformed responses, missing keys, extra whitespace.
- Cross-source headline deduplication — same story from Reuters + AP within 15 minutes is
  processed only once, cutting redundant LLM calls by ~30-50%.

### Fixed
- Reddit 429 backoff — was not being respected, causing the monitor to hammer the API.
- DB transaction isolation — concurrent writes were occasionally causing `SQLITE_BUSY`.

---

## [0.4.0] - 2026-03-17

### Added
- **Fade signal for multiple accounts** — `executor.py` now routes fade signals to
  configurable account list (`@Kalshi`, `@Polymarket`, `@PolymarketMoney`). Each account
  tracked separately in paper trades for win-rate analysis.
- **Fade-the-Kalshi-tweet signal** — monitors Kalshi's official social accounts for
  announcement posts and fades the implied move (documented but paper-only until validated).

---

## [0.3.0] - 2026-03-16

### Added
- Semantic versioning introduced (`VERSION` file, `config.py` version constant).
- Actor standing + reasoning consistency rules added to LLM prompt — reduces hallucinated
  high-confidence outputs on irrelevant news.
- Multi-position guard fixed: now checks ALL open trades on a ticker, not just the most
  recent, before allowing a new entry.

### Fixed
- `NameError` on missing `asyncio` import in `signal_analyzer.py`.
- Three low-priority signal quality bugs (see `tasks/completed.md`).
- Duplicate trades on service restart (`executor.py`) — now checks DB state on init.

---

## [0.2.0] - 2026-03-12 to 2026-03-13

*Pre-versioning sprint — major architecture work before semantic versioning was introduced.*

### Added
- **Ollama integration** — local LLM (qwen2.5:7b) as primary probability estimator.
  Startup health check confirms model is loaded before first signal.
- **Categorical LLM design** — replaced float probability approach (was hallucinating 0.98
  on every call) with structured JSON: `relevant`, `new_info`, `direction`, `magnitude`,
  `confidence`. Magnitude maps to price shift: none=0.0, small=0.08, moderate=0.15, large=0.25.
- **Deterministic inference** — `temperature=0`, `repetition_penalty=1.05` for stable
  JSON schema classification.
- **Inference timeout raised** to 60s (from 30s) to handle 7B CPU inference latency.
- **Tiered headline gate** — requires at least one headline token in market title to prevent
  body-only spurious matches (e.g. body mentions "Korea" matching "Bank of Korea" market).
- **Geo-coherence edge suppression** — filters edges where geographic context is inconsistent.
- **Market series-title discovery** — replaced static blocklist with keyword search across
  all ~9k Kalshi series; discovers ~250-443 open geopolitical markets. Blocklist now only
  covers sports leagues.
- **Keywords expanded** in `market_matcher.py` — +143 additional geopolitical series.
- **Paper trade noise reduction** — blocklist CB/TRUMPSAY markets, fan-out capped at 1,
  4-hour cooldown per ticker. Goal: maximize resolved trades on true geo markets.

### Fixed
- Windows-1252 em dash `SyntaxError` in `signal_analyzer.py` (commit ea196e0).
- WebSocket header kwarg compatibility across websockets library versions (v10-14+).
- Kalshi API URL migration and aiohttp Python 3.14 compatibility.
- Market status check to accept Kalshi's `'active'` status string.

---

## [0.1.0] - 2026-03-09 to 2026-03-11

*Initial build.*

### Added
- Core async bot (`main.py`) with 5 concurrent tasks: RSS monitor, Reddit monitor,
  WebSocket price feed, daily reporter, market cache refresh.
- Kalshi REST client (`kalshi/rest_client.py`) with RSA-PSS signing.
- Kalshi WebSocket client (`kalshi/websocket_client.py`) — real-time price feed.
- RSS monitor — Reuters, AP, BBC, Al Jazeera (60s poll).
- Reddit monitor — r/worldnews, r/ukraine, r/geopolitics (public JSON, no OAuth).
- Signal analyzer (`analysis/signal_analyzer.py`) — keyword scoring pipeline.
- Half-Kelly bet sizing (`analysis/kelly.py`) — 5% bankroll cap, $25 hard cap.
- Market matcher (`analysis/market_matcher.py`) — Jaccard token similarity + geo boost.
- Source credibility tracker (`analysis/source_credibility.py`) — per-source win/loss
  multiplier (0.5x to 1.5x).
- Paper trading engine (`trading/paper_trader.py`) — SQLite-backed, flat 5 contracts.
- Paper/live execution gate (`trading/executor.py`) — `--go-live` + `CONFIRM` required.
- Source credibility system — win/loss tracking per feed, applied as signal multiplier.
