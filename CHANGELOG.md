# Changelog

All notable changes to kalshi-bot are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

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
