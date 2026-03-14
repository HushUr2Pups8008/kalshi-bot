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

### 5 Concurrent Async Tasks
1. RSS monitor — polls 19 feeds every 60s
2. Reddit monitor — polls 29 subreddits, 10s stagger, 300s cycle
3. WebSocket client — real-time Kalshi price feed
4. Market cache refresh — every 30 min (refresh takes ~3 min in thread pool)
5. Daily reporter — writes report file every 24h

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

### Python 3.14 on Windows Requires aiohttp>=3.10.0
**Lesson:** `aiohttp==3.9.5` has no cp314 wheel. Windows machine runs Python 3.14.
`requirements.txt` uses `aiohttp>=3.10.0` to handle this. Do not pin aiohttp to 3.9.x.

### Paper Trades DB is Local — Not Synced
**Lesson:** `data/paper_trades.db` is gitignored and local to each machine. Mac and Windows
have separate paper trade histories. The bankroll state does not sync between machines.

---

## Future Signal: "Fade the Kalshi Tweet"

**The signal:** When @Kalshi tweets "BREAKING" or "[market] odds at ATH/all-time high,"
sharp money has historically faded it — the market is already overpriced from retail
attention and the edge is on the underpriced side. This is a sentiment/contrarian signal,
not a news signal.

**Why it's different from the existing pipeline:** The current pipeline is:
`news → find matching market → estimate probability shift → bet`
The fade signal is inverted: the tweet *is* the market identifier, and the action is always
to fade (no directional estimation needed). Requires a separate code path.

**How to get the feed without paying for X API:**
X's free API tier has no search and ~1 req/15min — useless for this. Options:
- **RSSHub** (recommended): open source, generates RSS from public X accounts.
  Add `https://rsshub.app/twitter/user/Kalshi` to `RSS_FEEDS` — zero new code needed.
  Self-hosting RSSHub is more reliable than public instances (public instances get blocked by X).
- X official API: Basic = $200/mo, Pro = $5,000/mo — not worth it for one account.
- Third-party scrapers (e.g. Xpoz): ~$20/mo for 1M results — overkill for one account.

**Implementation notes for when this gets built:**
- Detect tweet patterns: "BREAKING", "all-time high", "ATH", "surging" in @Kalshi tweets
- Parse the market ticker or title from the tweet text
- Look up current YES price via REST API
- Fade: if tweet is bullish on YES → buy NO (and vice versa)
- Position size: treat as low-confidence signal, use minimum bet size initially
- Paper trade this signal separately to measure its actual edge before going live with it

**Critical validation question:** The fade signal is documented primarily on sports and
entertainment markets (Super Bowl, alien odds, etc.) where retail attention is highest.
Geopolitical markets have different liquidity profiles and different trader behavior.
Before building signal logic, validate empirically: does the pattern actually hold on
geo/political markets specifically, or is it a sports phenomenon that doesn't transfer?

**Status:** Not yet implemented. Investigate before going live — could be meaningful edge
with zero marginal infrastructure cost if RSSHub approach works.
