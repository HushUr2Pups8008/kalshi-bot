# Completed Work
*Archive of finished tasks. Moved here from todo.md when fully done.*

---

## 2026-03-23 (Windows session -- v0.6.4 through v0.6.7)

### Fade Signal -- Replaced Dead Tweet Feed with WebSocket Price Detector (v0.6.4)
- **rsshub.app all 404 for Twitter routes** -- X blocked public RSSHub instances.
- **Fix:** replaced tweet-based fade with WebSocket price-crossing detector.
  - `analysis/fade_signal.py`: added `detect_price_fade()` -- detects crossings above 85c or below 15c with 1c buffer to suppress boundary noise.
  - `config.py`: added `FADE_PRICE_HIGH_THRESHOLD` (85) and `FADE_PRICE_LOW_THRESHOLD` (15) env-configurable constants.
  - `main.py`: added `_ws_prev_prices` tracker, `_on_price_update()` calls `detect_price_fade()`, `_process_price_fade()` builds synthetic NewsItem + routes through executor, `_warm_ws_subscriptions` subscribes WS to all geo market tickers at startup.
- No external dependency -- uses existing authenticated Kalshi WS connection.

### Ollama Circuit Breaker Permanently Open (v0.6.5)
- **Root cause:** circuit probes were full 60s inference calls. When Ollama was slow, probes timed out, incremented the failure counter, and reset the 5-min lockout -- circuit never closed.
- **Fix:** `analysis/signal_analyzer.py` -- added `_ollama_ping()` (GET /api/version, 5s timeout). Failed ping extends timer without touching failure count. Successful ping resets counter and unlocks inference.
- Commit: 282d22b

### Logging System Overhaul (v0.6.6 -- v0.6.7)
- **NSSM service_stderr.log rotation** -- configured via registry: `AppRotateFiles=1`, `AppRotateOnline=1`, `AppRotateBytes=5242880`. One-time setup, persists across reboots.
- **Daily log rotation** -- replaced `RotatingFileHandler` (size-based, WinError 32 on rename) with `_DailyRotatingFileHandler` (midnight, copy+truncate strategy, 90-day retention). Cross-platform: copy+truncate works on both Windows and Mac.
- **errors.log** -- added WARNING+ only log file alongside `bot.log` for fast triage.
- **Singleton file handlers** -- fixed architecture bug where each `get_logger()` call created new handler instances. ~10 loggers each had their own copy -- at midnight all 10 tried to rotate simultaneously. Fixed: `_app_fh` and `_err_fh` as module-level singletons shared across all loggers.
- **Startup banner** -- `emit_startup_banner(version, model, env)` writes `# ===== v0.6.7 | env=demo | model=qwen2.5:7b | py=3.14.x =====` to both log files on startup and after every midnight rotation.
- **3 em-dash violations in main.py** -- pre-existing `--` (em dash) chars in `log.warning()`, `log.info()`, and `print()` calls fixed to `--`. Would crash NSSM cp1252 log handler.

---

## 2026-03-17 (Windows session -- v0.5.7)

### Signal Quality Improvements

- **Portfolio state object** -- `trading/portfolio.py` (new), commit c2e28db
  `Position` dataclass + `Portfolio` class. Loaded from DB on startup, updated in-memory
  on every trade/resolution. Eliminates all DB queries from executor._validate().
  Concentration risk check: blocks trades pushing a ticker above 25% of bankroll.

- **Market snapshot at decision time** -- `trading/paper_trader.py`, commit 84e8ee1
  Added `market_snapshot TEXT` column to `paper_trades`. Serializes full `KalshiMarket`
  (yes_bid, yes_ask, yes_price, volume, open_interest, close_time, status).
  `_migrate_db()` runs on startup -- existing DBs get the column with no data loss.

- **Priority queue** -- `main.py`, commit f45d723
  Swapped `asyncio.Queue` for `asyncio.PriorityQueue`. RSS/wire priority 1, Reddit priority 2.
  Tuple layout `(priority, seq, news)` -- monotonic `seq` prevents `NewsItem` comparison.
  Prevents a 10-post Reddit burst from blocking a Reuters headline by up to 10 min.

### Tier 1 Bug Fixes (commit 9b9796f)

- **Reddit backoff decay broken** -- `feeds/reddit_monitor.py`
  Countdown subtracted poll interval each cycle -- went negative -- no protection.
  Fix: store `_backoff[subreddit] = time.monotonic() + delay`; check absolute resume time.

- **`asyncio.get_event_loop()` deprecated** -- `feeds/rss_monitor.py`, `trading/executor.py`
  Replaced with `asyncio.get_running_loop()`.

- **`resolve_market()` not atomic** -- `trading/paper_trader.py`
  Crash mid-loop left some trades resolved and others not -- corrupted bankroll.
  Fix: pre-calculate outcomes, wrap all UPDATEs in `with self._conn:`, credit bankroll once.

### Tier 2 Improvements (commit ac6f822)

- **LLM JSON extraction fragility** -- `analysis/signal_analyzer.py`
  Greedy regex captured from first `{` to last `}` -- broke on preamble with braces.
  Fix: `_extract_json()` using `JSONDecoder.raw_decode()` scan, keeps last valid JSON object.

- **asyncio.Queue decoupling** -- `main.py`
  Feed pollers blocked for 60s during Ollama inference. Fix: bounded `asyncio.Queue(maxsize=500)`,
  `_enqueue_news()` non-blocking callback, single `_news_consumer_task` drains queue.

- **Cross-source headline dedup** -- `feeds/dedup.py`
  Reuters/AP/BBC publish same story within minutes -- duplicate LLM calls + trades.
  Fix: `HeadlineDedup` using `rapidfuzz.fuzz.token_sort_ratio`, threshold 85, TTL 15 min.

### Tweet-Based Fade Signal -- Multi-Account (superseded by v0.6.4)
- Replaced single `KALSHI_TWEET_FEED_URL` with `FADE_TWEET_FEED_URLS: list[str]`.
  Added @Polymarket and @PolymarketMoney. Trade tags: `[FADE/GEO/@Kalshi]`, `[FADE/SPORTS/@Polymarket]`.
- **Superseded:** rsshub.app X/Twitter routes blocked in March 2026. Entire tweet feed approach
  replaced by WebSocket price-crossing detector in v0.6.4. `FADE_TWEET_FEED_URLS` is now unused.

---

## 2026-03-16

### Signal Quality Bugs

- **Multi-position guard** -- `trading/executor.py`, commit 9f1d783
  `get_last_open_trade()` returned only 1 row -- missed existing positions when YES + NO both open.
  Bot took YES + NO on `KXTRUMPIRAN`, then added a 3rd position on the same ticker.
  Fix: `get_all_open_trades(ticker)` returns all open rows. Guard loops all -- blocks on any
  opposite side or same-signal within +/-2% prob / +/-2c price.

- **LLM actor disambiguation** -- `analysis/signal_analyzer.py`, commit 89e4dc2
  Exiled crown prince trade: model called `dir=yes` without recognizing the actor has no
  official standing. Fix: added actor-standing guidance to LLM prompt.

- **`asyncio not defined` in RSS callbacks** -- `analysis/signal_analyzer.py`, commit 0473154
  `asyncio` was not imported but `_ollama_estimate()` caught `asyncio.TimeoutError`.
  On restart, Ollama cold-start timeout triggered NameError, propagated up to poll_feed.
  Fix: added `import asyncio`.

### Minor Fixes

- **Silent exception swallow on shutdown** -- `main.py`
  `except Exception: pass` changed to log the exception.

- **`cfg.is_paper_trading` mutability** -- `config.py`, `trading/paper_trader.py`
  Added `BotConfig.set_paper_mode(paper: bool)` as the single mutation point.

- **Duplicate LLM parse logic** -- `analysis/signal_analyzer.py`
  Extracted `_parse_llm_response(parsed, market)` shared helper used by both backends.

---

## 2026-03-17 (Mac Onboarding -- v0.5.2 through v0.5.6)

### Python 3.14 / Mac Compatibility Fixes

- **`asyncio.get_event_loop()` in `market_matcher.py`** -- v0.5.2
  Replaced with `get_running_loop()` in `_refresh()` and `_refresh_all()`.

- **Duplicate `PaperTrader` init on startup** -- v0.5.3
  `async_main()` created a standalone instance unconditionally; `TradingBot.__init__()` created
  a second one. Fix: defer to CLI-flag paths only (`--report`, `--go-live`, etc.).

- **`CancelledError` on shutdown (Python 3.14)** -- v0.5.4
  Python 3.14 propagates `CancelledError` from `shutdown_default_executor()` when thread pool
  threads are still running at cleanup. Fix: added `asyncio.CancelledError` to the except at
  the `asyncio.run()` entry point.

- **`UnknownTimezoneWarning` for EST/PST** -- `market_matcher.py` v0.5.5, `rss_monitor.py` v0.5.6
  Kalshi `close_time` strings use abbreviated US timezone names dateutil doesn't recognize.
  Fix: `_TZ` / `_RSS_TZINFOS` dicts mapping EST/EDT/CST/CDT/MST/MDT/PST/PDT to UTC offsets.

### Diagnosed (Not Bugs)

- **Market cache lower than Windows** -- short-term geo markets expired overnight; count recovers as
  Kalshi opens new markets. Not a code issue.

- **`qwen2.5:3b` running instead of `7b`** -- config.py default is `3b`; fixed by setting
  `OLLAMA_MODEL=qwen2.5:7b` in `.env`.
