# Project Memory

## Active Project: kalshi_bot

| Machine | Path | How it runs |
|---------|------|-------------|
| MacBook (primary) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` in terminal |
| Windows Gaming Desktop | `e:/VS_Code/kalshi-bot/` | NSSM Windows Service (`kalshi-bot`) |

Git repo: `https://gitlab.com/HushUr2Pups8008/kalshi-bot` — remote `origin/main`.
**Always pull before starting a session on either machine.**

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
- `.env` uses single-line PEM key with literal `\n` escapes; `_normalize_pem()` handles it
- Market blocklist checks BOTH `series_ticker` AND `ticker` prefix (API may return empty series_ticker)
- Paper trading bankroll stored in `data/paper_trades.db` → `bot_state` table, key `notional_bankroll`
- Config env vars: `BANKROLL`, `MAX_BET_HARD_CAP`, `KELLY_FRACTION`, `MIN_EDGE` (NOT `MAX_BET_DOLLARS`)
- Kalshi REST base URL: `https://api.elections.kalshi.com/trade-api/v2` (migrated from `trading-api.kalshi.com`)
- Kalshi WS URL: `wss://api.elections.kalshi.com/trade-api/ws/v2`
- Market status: Kalshi now returns `"active"` (not `"open"`) — executor checks for both
- `KALSHI_GEOPOLITICAL_SERIES` allowlist in `config.py` filters ~2000 total markets down to geopolitical only; applied in `analysis/market_matcher.py:_refresh()` — without this, sports/entertainment markets flood the pipeline
- `data/paper_trades.db` is local to each machine — DBs are NOT synced between Mac and Windows
- Reddit monitor uses public JSON endpoints (no API credentials); 29 subreddits, 10s stagger, 300s cycle
- 5 async tasks: RSS monitor, Reddit monitor, WebSocket client, daily reporter, market cache refresh

## Windows-Specific Setup
- **Python version:** 3.14 (Mac uses 3.11/3.12) — `aiohttp>=3.10.0` required (3.9.5 has no cp314 wheel)
- **Service manager:** NSSM wraps `python main.py` as Windows Service with auto-restart and boot start
- **Service name:** `kalshi-bot`
- **Log rotation:** NSSM configured at 10 MB
- **Logs:** `e:/VS_Code/kalshi-bot/logs/bot.log`
- **Service control** (requires elevated PowerShell): `Start/Stop/Restart-Service kalshi-bot`
- **Tail logs** (Git Bash): `tail -f logs/bot.log`
- **Power plan:** Sleep/hibernate disabled on AC; display off after 15 min (configured via `setup_service.ps1`)
- Windows-only files (gitignored): `WINDOWS_COMMANDS.md`, `setup_service.ps1`, `.env`
- GitLab PAT stored in Windows Credential Manager (no PAT file on disk)

## Go-Live Pre-Requisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports junk)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and has funding on live account
- **Note:** Mac and Windows share the same Kalshi API key — only ONE instance should go live at a time
