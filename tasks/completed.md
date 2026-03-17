# Completed Work
*Archive of finished tasks. Moved here from todo.md when fully done.*

---

## 2026-03-17 (Windows session — v0.5.7)

### Signal Quality

- **Portfolio state object** — `trading/portfolio.py` (new)  ✓ DONE (commit c2e28db)
  `Position` dataclass + `Portfolio` class. Loaded from DB on startup, updated in-memory
  on every trade/resolution. Eliminates all DB queries from executor._validate().
  Added concentration risk check: blocks trades pushing a ticker above 25% of bankroll.
  `executor.status()` now exposes full portfolio snapshot.

- **Market snapshot at decision time** — `trading/paper_trader.py`  ✓ DONE (commit 84e8ee1)
  Added `market_snapshot TEXT` column to `paper_trades`. Serializes full `KalshiMarket`
  (yes_bid, yes_ask, yes_price, volume, open_interest, close_time, status) via `dataclasses.asdict()`.
  `_migrate_db()` runs on startup — existing DBs get the column on next launch, no data loss.

- **Priority queue** — `main.py`  ✓ DONE (commit f45d723)
  Swapped `asyncio.Queue` → `asyncio.PriorityQueue`. RSS/wire services priority 1, Reddit priority 2.
  Tuple layout `(priority, seq, news)` — monotonic `seq` counter prevents `NewsItem` comparison.
  Prevents a 10-post Reddit burst from blocking a Reuters headline by up to 10 min.

---

## 2026-03-16

### Signal Quality Bugs

- **Multi-position guard** — `trading/executor.py`  ✓ FIXED (commit 9f1d783)
  Two failures observed:
  1. Bot took YES and NO on `KXTRUMPIRAN` (opposing signals 24h apart) → costless hedge.
  2. Bot added a THIRD position (2nd NO) on `KXTRUMPIRAN` on Mar 16 — same-signal guard only
     checked the MOST RECENT open trade (the YES), saw delta > 2%, and passed it. The
     existing NO at est=0.444 was ignored. Now holding 3 positions on one ticker.
  Root cause: `get_last_open_trade()` returns only 1 row. Guard needs to check ALL open trades.
  Fix: added `get_all_open_trades(ticker)` to `paper_trader.py`, returning all rows where
  `resolved=0`. In `_validate()`, loop all open trades — if ANY has:
    (a) opposite side → skip ("opposing position exists")
    (b) prob_delta < 0.02 AND price_delta < 2.0 → skip ("same-signal, existing position")
  This replaces the current single-row check entirely.

- **LLM actor disambiguation** — `analysis/signal_analyzer.py` prompt  ✓ FIXED (commit 89e4dc2)
  Crown prince trade (2026-03-14): model saw "Iran's exiled crown prince + Trump contact"
  and called `dir=yes` without recognizing the actor has no official standing.
  Fix: added to LLM prompt — "Consider whether the named actors have actual decision-making
  power over the market event. Opposition figures, exiles, and unofficial contacts should
  not move the probability."

- **`asyncio not defined` error in RSS callbacks** — `analysis/signal_analyzer.py`  ✓ FIXED (commit 0473154)
  Root cause: `asyncio` was not imported in signal_analyzer.py, but `_ollama_estimate()`
  catches `asyncio.TimeoutError`. On restart, Ollama cold-starts (model loads in 10-30s),
  the 60s timeout fires, and the NameError propagated up to poll_feed's catch block.
  Fix: added `import asyncio` to signal_analyzer.py.

### Known Bugs (Low Priority)

- **Silent exception swallow on shutdown** — `main.py`  ✓ FIXED
  `except Exception: pass` → `except Exception as exc: log.warning("Report generation failed: %s", exc)`

- **`cfg.is_paper_trading` mutability** — `config.py`, `trading/paper_trader.py`  ✓ FIXED
  Added `BotConfig.set_paper_mode(paper: bool)` as the single mutation point.
  `paper_trader.py` now calls `cfg.set_paper_mode()` instead of `cfg.is_paper_trading = ...`.

- **Duplicate LLM parse logic** — `analysis/signal_analyzer.py`  ✓ FIXED
  Extracted `_parse_llm_response(parsed, market)` shared helper.
  Both `_ollama_estimate()` and `_anthropic_estimate()` now call it.

---

## 2026-03-17

### Weekly Review — Tier 1 Fixes (commit 9b9796f)

- **Reddit backoff decay broken** — `feeds/reddit_monitor.py`  ✓ FIXED
  Countdown-based backoff subtracted poll interval each cycle → went negative → no protection.
  Fix: store `_backoff[subreddit] = time.monotonic() + delay`; check absolute resume time.

- **`asyncio.get_event_loop()` deprecated** — `feeds/rss_monitor.py`, `trading/executor.py`  ✓ FIXED
  Replaced with `asyncio.get_running_loop()` (correct inside async functions, Python 3.10+).

- **`resolve_market()` not atomic** — `trading/paper_trader.py`  ✓ FIXED
  Each trade updated in a separate execute() call — crash mid-loop → corrupted bankroll.
  Fix: pre-calculate all outcomes, wrap all UPDATEs in `with self._conn:` (SAVEPOINT/ROLLBACK),
  credit bankroll once for total payout after the atomic block.

### Weekly Review — Tier 2 Improvements (commit ac6f822)

- **LLM JSON extraction fragility** — `analysis/signal_analyzer.py`  ✓ FIXED
  Greedy `re.search(r"\{.*\}", text, re.DOTALL)` captured from first `{` to last `}`.
  Fix: `_extract_json()` using `JSONDecoder.raw_decode()` scan, keeps last valid JSON object.

- **asyncio.Queue decoupling** — `main.py`  ✓ DONE
  Feed pollers blocked for 60s during Ollama inference. Fix: bounded `asyncio.Queue(maxsize=500)`,
  `_enqueue_news()` callback (non-blocking), single `_news_consumer_task` drains queue.

- **Cross-source headline dedup** — `feeds/dedup.py`  ✓ DONE
  Reuters/AP/BBC publish same story within minutes → duplicate LLM calls + trades.
  Fix: `HeadlineDedup` using `rapidfuzz.fuzz.token_sort_ratio`, threshold 85, TTL 15 min.
  Runs in `_enqueue_news()` before items enter the queue.

### Multi-Account Fade Signal (commit from 2026-03-17)

- **`FADE_TWEET_FEED_URLS`** — `config.py`, `main.py`  ✓ DONE
  Replaced single `KALSHI_TWEET_FEED_URL` with `FADE_TWEET_FEED_URLS: list[str]`.
  Backward-compatible via env var fallback. Added @Polymarket and @PolymarketMoney.
  Dedicated `_on_fade_tweet()` callback with `_account_from_rsshub_url()` helper.
  Trade tags: `[FADE/GEO/@Kalshi]`, `[FADE/SPORTS/@Polymarket]` for per-account SQL queries.

---

## 2026-03-17 (Mac Onboarding Session — v0.5.2 → v0.5.6)

### Python 3.14 / Mac Compatibility Fixes

- **`asyncio.get_event_loop()` in `market_matcher.py`** — `analysis/market_matcher.py`  ✓ FIXED (v0.5.2)
  Both `_refresh()` and `_refresh_all()` used deprecated `get_event_loop()` inside async functions.
  Fix: replaced with `get_running_loop()` (consistent with prior fixes in rss_monitor + executor).

- **Duplicate `PaperTrader` init on startup** — `main.py`  ✓ FIXED (v0.5.3)
  `async_main()` created a standalone `PaperTrader()` unconditionally, then `TradingBot.__init__()`
  created a second one. Both logged "Paper trading resumed" on every normal startup.
  Fix: defer standalone `PaperTrader()` to only when a CLI flag (`--report`, `--go-live`, etc.) needs it.
  Also replaced `asyncio.get_event_loop()` → `get_running_loop()` in the same function.

- **`CancelledError` on shutdown (Python 3.14)** — `main.py`  ✓ FIXED (v0.5.4)
  Python 3.14 changed `asyncio.run()` to propagate `CancelledError` from `shutdown_default_executor()`
  when thread pool threads (the 1585-series market fetch) are still running at cleanup time.
  Fix: added `asyncio.CancelledError` to the `except` at the `asyncio.run()` entry point.

- **`UnknownTimezoneWarning` for EST/PST in `market_matcher.py`** — `analysis/market_matcher.py`  ✓ FIXED (v0.5.5)
  Kalshi `close_time` strings use abbreviated US timezone names that dateutil doesn't recognise
  without explicit `tzinfos`. Would become a hard exception in a future dateutil version.
  Fix: added `_TZ` dict mapping EST/EDT/CST/CDT/MST/MDT/PST/PDT to UTC offsets; passed to `dp.parse()`.

- **`UnknownTimezoneWarning` for EST/PST in `rss_monitor.py`** — `feeds/rss_monitor.py`  ✓ FIXED (v0.5.6)
  Same issue as above but in `_parse_date()` — the actual source of the startup warning (fires on
  first RSS item before market cache is built). Fix: `_RSS_TZINFOS` dict, passed to `dateutil_parser.parse()`.

### Diagnosed (Not Bugs)

- **Market cache lower than Windows** — 79–208 markets vs ~443 on Windows.
  Cause: short-term geo markets expired overnight; remaining markets are longer-dated.
  Count recovers as Kalshi opens new markets for upcoming events. Not a code issue.

- **`qwen2.5:3b` running instead of `qwen2.5:7b`** — config.py default is `3b`; user set
  `OLLAMA_MODEL=qwen2.5:7b` in `.env` after downloading the 7b model.
