# Changelog

All notable changes to kalshi-bot are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

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
