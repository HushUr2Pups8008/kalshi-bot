# Project Memory

## Active Project: kalshi_bot
Path: `/Users/Jake/vscode/kalshi_bot`
Git repo with remote `origin/main`. Run from that directory.

## Kalshi Bot — Low-Priority Backlog
These are known issues to fix in a future session:

1. **`set.pop()` LRU approximation** — `feeds/rss_monitor.py:78` and `feeds/reddit_monitor.py:135`
   - `set.pop()` removes a random element, not the oldest. Documented but imperfect.
   - Fix: use `collections.OrderedDict` or timestamp-based eviction for true LRU.

2. **Silent exception swallow on shutdown** — `main.py:293`
   - `except Exception: pass` silently drops report generation errors at shutdown.
   - Fix: `log.warning("Report generation failed: %s", exc)`.

3. **`cfg.is_paper_trading` mutability** — `config.py:207`, `trading/paper_trader.py:115/118/158`
   - Global singleton mutated directly during runtime; no locking for async access.
   - Low risk in practice (only set at startup), but architecturally unsound.

4. **Reddit monitor array indexing** — `feeds/reddit_monitor.py:86`
   - `data[0]["data"]["children"][0]` without explicit length check (try-except exists but fragile).
   - Fix: explicit `if data and data[0].get("data", {}).get("children"):` guard.

## Kalshi Bot — Key Architecture Notes
- RSA-PSS signing (not PKCS1v15) for both REST and WebSocket auth
- websockets header kwarg history: <10→`extra_headers`, 10-11→`additional_headers`, 12-13→`extra_headers`, 14+→`additional_headers`
- Code detects version at import (`_WS_HEADER_KWARG`) and uses correct kwarg dynamically — supports websockets 13–16+
- requirements.txt uses `websockets>=13.0` (not pinned); local venv may lag behind
- .env uses single-line PEM key with literal `\n` escapes; `_normalize_pem()` handles it
- Market blocklist checks BOTH `series_ticker` AND `ticker` prefix (API may return empty series_ticker)
- Paper trading bankroll stored in `data/paper_trades.db` → `bot_state` table, key `notional_bankroll`
- Config env vars: `BANKROLL`, `MAX_BET_HARD_CAP`, `KELLY_FRACTION`, `MIN_EDGE` (NOT `MAX_BET_DOLLARS`)
