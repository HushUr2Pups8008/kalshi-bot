# Lessons Learned
*Hard-won lessons from debugging and development. Read this before making changes.*

---

## Project Context

**What it is:** News-driven prediction market bot. Monitors RSS/Reddit for geopolitical
events, matches them to open Kalshi markets, estimates probability shifts via LLM, and
places Kelly-sized bets.

**Machines:**
| Machine | Path | How it runs |
|---------|------|-------------|
| MacBook (primary dev) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` |
| Windows Gaming Desktop | `e:/VS_Code/kalshi-bot/` | NSSM service (`kalshi-bot`) |
| Mac Studio (incoming) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` (planned) |

**Rule:** Always `git pull` before starting a session on any machine.

**Bot is currently in paper trading mode.** Do not go live without Jake's explicit
instruction and the `--go-live` confirmation gate. Mac and Windows share the same Kalshi
API key — only ONE instance in live mode at a time.

---

## Architecture

### 6 Concurrent Async Tasks
1. RSS monitor — polls 19 feeds every 60s; calls `_enqueue_news()` (non-blocking)
2. Reddit monitor — polls 29 subreddits, 10s stagger, 300s cycle; calls `_enqueue_news()`
3. News consumer — single worker draining `asyncio.PriorityQueue`; serializes LLM calls
4. WebSocket client — real-time Kalshi price feed
5. Market cache refresh — every 30 min (refresh takes ~3 min in thread pool)
6. Daily reporter — writes report file every 24h

**+ conditional:** `fade_tweets` task — polls FADE_TWEET_FEED_URLS (RSSHub RSS) for @Kalshi,
@Polymarket, @PolymarketMoney tweets. Routes directly to `_on_fade_tweet()` (not the queue —
pattern matching is fast, no LLM needed).

### asyncio.Queue Decoupling
Feed pollers call `_enqueue_news()` which does `queue.put_nowait()` — returns immediately.
The single `_news_consumer_task` drains the queue and calls `on_news_item()` including the
60s Ollama call. This prevents feed pollers from blocking on LLM latency.
Queue is bounded at 500 items; overflow logs a warning and drops. `HeadlineDedup` runs
inside `_enqueue_news()` before the item enters the queue — duplicates never reach Ollama.

### LLM Stack
`Ollama qwen2.5:7b` (primary, local) → `Claude Haiku` (fallback, `ANTHROPIC_API_KEY`)
→ keyword scoring (final fallback, always available)

### Market Discovery Flow
1. Fetch all ~9k series from `/series` endpoint
2. Keyword-match titles via `_GEO_SERIES_KEYWORDS` → ~1,400 geo candidates
3. Apply sports prefix blocklist (`MARKET_SERIES_BLOCKLIST_PREFIXES`) → ~443 markets
4. Cache for 30 min (`MARKET_CACHE_TTL_SECONDS = 1800`)

### Match Gate in `find_candidates()`
Tiered gate — not just similarity score:
- A single **named geo entity** (country, person in `_GEO_NAMED_ENTITIES`) passes alone
- **Generic words** ("bank", "attack", "war") require 2+ overlaps
- Market must contain at least one token from `_GEOPOLITICAL_BOOST`

---

## Key Files
| File | Purpose |
|------|---------|
| `config.py` | All tuneable params and env var bindings — import `cfg` everywhere |
| `analysis/market_matcher.py` | Market discovery, series keyword matching, tiered headline gate |
| `analysis/signal_analyzer.py` | LLM + keyword probability estimation |
| `kalshi/websocket_client.py` | WS connection, auth headers, version-safe kwarg detection |
| `kalshi/rest_client.py` | RSA-PSS signed REST calls |
| `trading/executor.py` | Live/paper trade execution |
| `trading/paper_trader.py` | Paper trade tracking, source credibility, bankroll |
| `data/paper_trades.db` | SQLite — paper trades + bot state (gitignored, not synced) |
| `docs/future_plans.md` | Roadmap: Mac Studio, equity bot, OpenClaw |
| `docs/websocket_fix.md` | Detailed websocket version history and fix |

---

## Config / Env Vars
| Var | Notes |
|-----|-------|
| `BANKROLL` | Starting notional bankroll |
| `MAX_BET_HARD_CAP` | Hard ceiling per bet (NOT `MAX_BET_DOLLARS` — that name is gone) |
| `BET_PCT_BANKROLL` | % of bankroll per bet (default 5%) |
| `MIN_BET_DOLLARS` | Floor per bet |
| `KELLY_FRACTION` | Half-Kelly = 0.5 |
| `MIN_EDGE` | Min edge before live bet (default 0.04) |
| `OLLAMA_MODEL` | Model name (default `qwen2.5:7b`) |
| `OLLAMA_BASE_URL` | Default `http://localhost:11434/v1` |

### Paper Trading Constants
- `PAPER_MAX_CANDIDATES = 1` — top match only, one trade per article (clean signal data)
- `PAPER_MIN_EDGE = 0.02` (vs live `0.04`) — wider net for data collection
- `PAPER_FLAT_CONTRACTS = 5` — flat contract count during paper phase (no bankroll gating)

---

## What NOT To Do
- Do not blend LLM probability with keyword scores — removed intentionally (see Signal Analysis below)
- Do not use `KALSHI_GEOPOLITICAL_SERIES` allowlist — obsolete, zero open markets under it
- Do not use PKCS1v15 or HMAC for signing — Kalshi requires RSA-PSS
- Do not hardcode `extra_headers` or `additional_headers` — use `_WS_HEADER_KWARG`
- Do not commit `.env`, `data/`, or `logs/` — gitignored
- Do not run bot on Mac and Windows simultaneously in live mode — same API key
- Do not pin `aiohttp` to 3.9.x — no cp314 wheel, Windows machine runs Python 3.14

---

## Authentication

### RSA-PSS, not PKCS1v15 or HMAC
**Lesson:** Kalshi v2 API requires RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH` for
ALL signing — both REST headers and the WebSocket HTTP upgrade handshake. Earlier code used
HMAC-SHA256 (wrong) and then PKCS1v15 (also wrong). Both return 401 silently or with a
generic auth error that doesn't name the signing algorithm as the cause.
**Rule:** Never change the signing algorithm. Never use `hmac`, never use PKCS1v15 padding.

**API URLs:**
- REST: `https://api.elections.kalshi.com/trade-api/v2`
- WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2`

### Market Status Field is `"active"` not `"open"`
**Lesson:** The Kalshi API returns market status as `"active"` for tradeable markets, not
`"open"`. The executor checks for both strings. Do not filter on `"open"` alone or active
markets will be silently skipped.

### PEM Key in .env — Single Line with Literal `\n`
**Lesson:** Windows `.env` files can't store multi-line values reliably. The key must be
stored as a single line with literal backslash-n sequences:
```
KALSHI_API_KEY_SECRET=-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----
```
`_normalize_pem()` in both `kalshi/rest_client.py` and `kalshi/websocket_client.py` converts
`\n` → real newlines before loading. Do not remove this function or change how the key is loaded.

---

## WebSocket

### `extra_headers` vs `additional_headers` — Version-Dependent
**Lesson:** The `websockets` library has renamed the custom header kwarg three times:
| Version | Kwarg |
|---------|-------|
| < 10.0 | `extra_headers` |
| 10–11 | `additional_headers` |
| 12–13 | `extra_headers` |
| 14+ | `additional_headers` |

Hardcoding either name will silently pass headers into `**kwargs` on the wrong version,
which routes them to `asyncio.create_connection` where they are silently ignored. Auth
headers never reach the HTTP upgrade, causing a 401 or immediate disconnect.

**Fix (in place):** Version is detected at import time:
```python
_ws_ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
_WS_HEADER_KWARG = "additional_headers" if _ws_ver >= (14, 0) else "extra_headers"
```
Then used as: `**{_WS_HEADER_KWARG: auth_headers}`. Never revert to a hardcoded name.

See `docs/websocket_fix.md` for the full investigation.

---

## Market Discovery

### KALSHI_GEOPOLITICAL_SERIES is Obsolete
**Lesson:** The original allowlist approach (`KXUKR`, `KXINTL`, etc.) stopped working when
Kalshi retired those organised geo series. Zero open markets are returned under them.
**Current approach:** Fetch all ~9k series, keyword-match titles via `_GEO_SERIES_KEYWORDS`,
then apply the sports prefix blocklist. Yields ~443 active geo markets.
**Rule:** Do not resurrect the allowlist approach. Do not use `KALSHI_GEOPOLITICAL_SERIES`
for market filtering.

### Sports Blocklist Must Check Both Fields
**Lesson:** `series_ticker` can be empty from the API even when the market has a sports
ticker. The blocklist must check both `series_ticker` AND the market's own `ticker` prefix.
This is already implemented in `_fetch_geo_markets()`.

### Tiered Headline Gate Prevents False Positives
**Lesson:** Pure similarity scoring matched sports markets to geo news ("Over 224.5 points"
matched "war is over"). Two fixes applied:
1. Sports noise words added to `_STOP_WORDS` ("over", "under", "scored", "points", etc.)
2. Tiered gate: a specific named geo entity (country, person) passes alone; generic words
   like "bank" or "attack" require 2+ overlaps from the headline.

### KXTRUMPSAY Series Must Be Blocked
**Lesson:** "Will Trump say [word] this week?" markets match virtually any Trump-related
headline. `KXTRUMPSAY` and `KXTRUMPSAYMONTH` are in the blocklist. Do not remove them.

### Central Bank and Approval Rating Markets Are Not Geo Events
**Lesson:** `KXCBDECISION` (central bank rate decisions) and `KXAPRPOTUS`/`KXPOLLPOTUS`
(approval rating polls) match geo keywords (Russia, China) but are not geopolitical
event markets. They are in the blocklist.

---

## Signal Analysis

### Same-Signal Guard Checks Only Most-Recent Trade — NOT All Open Trades
**Lesson (confirmed 2026-03-16):** `get_last_open_trade()` returns 1 row (most recent).
If trades alternate YES/NO, the "most recent" is the opposite side. A new signal with
est=0.440 will have large delta vs the YES at 0.556, pass the guard, and place a redundant
NO — even though an identical NO at 0.444 already exists.
**Rule:** The multi-position guard must query ALL open trades for the ticker and check:
- Any opposite side → block (no hedges)
- Any same-side within ±2% prob / ±2¢ price → block (no duplicates)
Using only the most recent trade is always wrong when both YES and NO positions exist.

### LLM Behavioral Observations (updated 2026-03-16 after 72h run)
Observed patterns from 72-hour production run (5 trades, 116 LLM evaluations, 5136 market matches):

**LLM direction distribution (101 calls):**
- `neutral` — 76 (75%) — no change from market price
- `no` — 24 (24%) — probability should decrease
- `yes` — 1 (1%) — probability should increase

**What the model gets right:**
- High neutral rate (75%) — correctly identifies most news as non-moving. Prediction markets
  are already priced; incremental war coverage doesn't move "Trump visits Iran" probability.
- Conservative magnitude — defaults to `small` or `none`, rarely `moderate`. Prevents
  over-betting on weak signals.
- Hostile rhetoric correctly read as bearish: "Trump calls Iran leaders deranged scumbags"
  → correctly predicted as negative for US-Iran visit probability.
- Active military escalation → correctly bearish on nuclear deal probability.

**Known failure mode — actor disambiguation:**
- "Iran's exiled crown prince says he's been in contact with Trump administration" → LLM
  called `dir=yes` on the Trump-visits-Iran market. The exiled crown prince (Reza Pahlavi)
  has had zero official standing in the Iranian government since 1979. Contact between
  opposition-in-exile and the Trump admin does not indicate a diplomatic visit.
- This trade directly contradicts the earlier NO trade on the same ticker — creating a
  $5/$5 costless hedge that locks capital with zero expected P&L.
- **Rule:** When the LLM calls `dir=yes/no`, the actor must have actual decision-making
  power over the market event. Opposition figures, exiles, and unofficial contacts are noise.
  TODO: add actor-standing guidance to the LLM prompt.

**Known failure mode — direction/reasoning inconsistency:**
- Zelenskyy trade: LLM reasoning said "does not directly impact Zelenskyy leaving office"
  but output `dir=no, mag=small` anyway. The headline was about Zelenskyy cooperating with
  Saudi Arabia on drones — completely unrelated to his tenure.
- **Rule:** If the LLM reasoning text contains "does not directly impact" or "not directly
  related", the magnitude MUST be `none`. A mismatch is a model output error, not a signal.
  The bet placed here was based on faulty output logic.

**The 99% funnel:**
- 4,909 market matches → 101 LLM evaluations (~2% pass rate through cooldown + same-signal guard)
- This is correct behavior — most matches are repetitive Iran war stories routing to the
  same ticker where a position already exists.
- Watch for: if new high-impact events hit non-KXTRUMPIRAN markets (e.g. Zelenskyy removal,
  Venezuela election), the funnel should open up and generate new trades.

### LLM JSON Extraction — Use JSONDecoder.raw_decode(), Not Greedy Regex
**Lesson:** `re.search(r"\{.*\}", text, re.DOTALL)` grabs from the first `{` to the last `}`.
If the LLM outputs preamble with braces — e.g. `"Consider {Russia}: {"relevant": true...}"` —
the regex captures from the opening `{Russia}` brace through the end, producing invalid JSON.
**Fix:** `_extract_json()` in `signal_analyzer.py` uses `json.JSONDecoder.raw_decode(text, pos)`
at each `{` position, keeping the **last** successfully parsed dict. This correctly handles
all preamble cases and raises `ValueError` (caught by the existing except chain) if no valid
JSON is found. Never use a greedy `{.*}` regex on LLM output.

### Cross-Source Headline Dedup — Conservative Threshold
**Lesson:** Reuters, AP, and BBC publish near-identical stories within minutes. Each copy
triggered a separate 60s Ollama call and could generate a duplicate trade.
**Fix:** `feeds/dedup.py` — `HeadlineDedup` normalizes headlines (lowercase, sorted tokens),
compares with `rapidfuzz.fuzz.token_sort_ratio`, threshold 85/100, TTL 15 min, max 500 entries.
Runs in `_enqueue_news()` before items enter the queue.
**Threshold note:** 85 is intentionally conservative — only catches near-verbatim wire copies,
not paraphrases. Tested: genuinely-same stories from different outlets score ~70-79; exact
copies score 95+. False-positive suppression (blocking a real new story) is worse than a
duplicate LLM call, so the threshold stays high.

### DB Transaction Atomicity in resolve_market()
**Lesson:** The original `resolve_market()` updated each trade in a separate `execute()` call.
A crash mid-loop (power outage, SIGKILL) would leave some trades resolved and others not —
permanently corrupting the bankroll figure, which affects every subsequent Kelly calculation.
**Fix:** Pre-calculate all outcomes (no DB writes), then wrap all UPDATEs in `with self._conn:`
(SQLite context manager = automatic SAVEPOINT/ROLLBACK). Credit bankroll once for the total
payout after the atomic block completes.
**Rule:** Any operation that reads-then-writes multiple rows touching the bankroll must be
wrapped in a DB transaction. Money math must be atomic.

### Do Not Blend LLM and Keyword Probabilities
**Lesson:** An earlier version blended LLM probability with keyword-derived probability.
This caused bets to be placed when the LLM explicitly returned "magnitude: none" — the
keyword score pushed the blend above the edge threshold anyway.
**Rule:** When LLM is available, use its probability directly. Keywords are used only as
a gate (does this news relate to this market at all?) not as a probability input.

---

## Configuration

### MAX_BET_DOLLARS is NOT a Valid Env Var
**Lesson:** The correct env var is `MAX_BET_HARD_CAP`. `MAX_BET_DOLLARS` was an old name
that no longer exists. Using the old name silently falls back to the default.

### Dynamic Bet Sizing
**Lesson:** Bet size is not static. `cfg.dynamic_max_bet(notional)` calculates the current
max based on bankroll: `min(MAX_BET_HARD_CAP, BET_PCT_BANKROLL * notional_bankroll)`.
Call this with the current notional bankroll — do not use a hardcoded dollar amount.

---

## Infrastructure

### New Dependencies Must Be Installed in the Service venv
**Lesson:** The NSSM service runs `E:\VS_Code\kalshi-bot\.venv\Scripts\python.exe`, not the
system Python. Installing a package with bare `pip install` goes into the system Python and
is invisible to the service — bot crashes at import with `ModuleNotFoundError` on next start.
**Rule:** After adding any new package to `requirements.txt`, always install it explicitly
into the service venv:
```
E:\VS_Code\kalshi-bot\.venv\Scripts\python.exe -m pip install <package>
```
Or install all requirements at once:
```
E:\VS_Code\kalshi-bot\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
Verify the import works from the venv before restarting the service.

### Python 3.14 on Windows Requires aiohttp>=3.10.0
**Lesson:** `aiohttp==3.9.5` has no cp314 wheel. Windows machine runs Python 3.14.
`requirements.txt` uses `aiohttp>=3.10.0` to handle this. Do not pin aiohttp to 3.9.x.

### Paper Trades DB is Local — Not Synced
**Lesson:** `data/paper_trades.db` is gitignored and local to each machine. Mac and Windows
have separate paper trade histories. The bankroll state does not sync between machines.

### trades.jsonl is the Only Pre-Wipe Record
**Lesson:** DB was wiped multiple times during 2026-03-11/12 test cycles. The 343 pre-production
paper trades exist only in `logs/trades.jsonl` — not in the DB. The current DB production run
starts 2026-03-13T08:07. If analyzing historical signal quality, read trades.jsonl, not just
the DB.

### Reddit Backoff Must Use Absolute Monotonic Timestamp
**Lesson:** The original backoff used a countdown (`backoff -= poll_interval`). After a 429
set backoff to 120s, the next poll cycle subtracted 300s (the poll interval) → went negative
→ clamped to 0 → subreddit immediately retried. Exponential backoff provided zero protection.
**Fix:** Store `_backoff[subreddit] = time.monotonic() + delay` and check
`if time.monotonic() < _backoff.get(subreddit, 0.0): skip`. Never use a countdown for
time-based gating — always store the absolute resume timestamp.

### Reddit Rate Limits Mass-Trigger on Startup
**Lesson:** Every bot restart polls all ~29 subreddits in a short burst, triggering simultaneous
429s across all of them. Reddit data is unavailable for ~2-5 minutes post-startup. This is
handled by backoff logic but means early-startup signals are RSS-only. A staggered startup
delay per subreddit would help.

---

## Fade Signal (Implemented — v0.4.0+)

**The signal:** When @Kalshi or @Polymarket tweet "BREAKING", "ATH", or "all-time high",
the market is likely overpriced from retail attention. Fade it: bullish tweet → buy NO.
This is a sentiment/contrarian signal, not a news signal — no LLM needed, pattern matching only.

**Architecture:** Separate code path from the main news pipeline.
- `FADE_TWEET_FEED_URLS` (config.py) — list of RSSHub RSS URLs, one per monitored account
- `_on_fade_tweet()` (main.py) — dedicated callback, bypasses the news queue
- `_process_fade_tweet(tweet, account)` — pattern matching, market lookup, executor
- Trade tags: `[FADE/GEO/@Kalshi]`, `[FADE/SPORTS/@Polymarket]` — enables per-account SQL win-rate queries
- RSSHub is used to get X/Twitter feeds without paying for the X API ($200-5000/mo)

**Accounts monitored:** @Kalshi (geo+sports), @Polymarket (geo+sports), @PolymarketMoney (geo+sports)
Configure via `.env`: `FADE_TWEET_FEED_URLS=https://rsshub.app/twitter/user/Kalshi,...`
Backward-compatible: old `KALSHI_TWEET_FEED_URL` env var still works as a single-URL fallback.

**Critical validation question (open):** The fade pattern is documented on sports/entertainment
markets where retail attention is highest. Geopolitical markets have different liquidity profiles.
Validate empirically after 2-4 weeks of paper trades — see Go-Live Prerequisites in todo.md.
If sports win rate < 50%, disable sports category from the fade pipeline.

**Self-host RSSHub before go-live.** Public rsshub.app instances get blocked by X periodically.
A self-hosted instance on Mac Studio is more reliable for production use.
