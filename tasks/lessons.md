# Lessons Learned
*Hard-won lessons from debugging and development. Read this before making changes.*

---

## Authentication

### RSA-PSS, not PKCS1v15 or HMAC
**Lesson:** Kalshi v2 API requires RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH` for
ALL signing — both REST headers and the WebSocket HTTP upgrade handshake. Earlier code used
HMAC-SHA256 (wrong) and then PKCS1v15 (also wrong). Both return 401 silently or with a
generic auth error that doesn't name the signing algorithm as the cause.
**Rule:** Never change the signing algorithm. Never use `hmac`, never use PKCS1v15 padding.

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
