# CLAUDE.md — Kalshi Geopolitical Trading Bot

## Project Context

News-driven prediction market bot for Kalshi. Monitors RSS/Reddit for geopolitical events,
matches them to open markets, estimates probability shifts via LLM, and places Kelly-sized bets.

**Machines:**
| Machine | Path | How it runs |
|---------|------|-------------|
| MacBook (primary dev) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` |
| Windows Gaming Desktop | `e:/VS_Code/kalshi-bot/` | NSSM service (`kalshi-bot`) |
| Mac Studio (incoming) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` (planned) |

**Rule:** Always `git pull` before starting a session on any machine.

**Bot is currently in paper trading mode.** Do not go live without Jake's explicit instruction
and the `--go-live` confirmation gate.

---

## How I Work on This Project

### Plan Mode
- Enter plan mode for any non-trivial task (3+ steps, architectural decisions, or anything
  touching the trading/execution pipeline)
- If something goes wrong mid-task, STOP and re-plan — do not keep pushing
- Write a plan in `tasks/todo.md` before starting significant work

### Task Management
1. Write the plan in `tasks/todo.md` with checkable items
2. Mark items complete as I go
3. After any correction from Jake, update `tasks/lessons.md` with the pattern
4. Review `tasks/lessons.md` at the start of each session

### Verification
- Never mark a task complete without confirming it works
- For trading logic changes: trace the full path from news → signal → bet sizing → execution
- Check logs for silent failures — this codebase has async tasks that can fail quietly

### Elegance
- For non-trivial changes: ask "is there a more elegant solution?"
- For simple fixes: just fix it, don't over-engineer
- The codebase is intentionally lean — keep it that way

---

## Architecture Overview

### Runtime
5 concurrent async tasks:
1. RSS monitor — polls 19 feeds every 60s
2. Reddit monitor — polls 29 subreddits, 10s stagger, 300s cycle
3. WebSocket client — real-time Kalshi price feed
4. Market cache refresh — every 30 min (refresh takes ~3 min in thread pool)
5. Daily reporter — writes report file every 24h

### LLM Stack
`Ollama qwen2.5:7b` (primary, local) → `Claude Haiku` (fallback, requires `ANTHROPIC_API_KEY`)
→ keyword scoring (final fallback, always available)

**Critical:** LLM result is used directly — do NOT blend with keyword scores. Blending was
removed because it manufactured bets the LLM explicitly said weren't market-moving.

### Market Discovery
`KALSHI_GEOPOLITICAL_SERIES` is obsolete — Kalshi retired those series. Current flow:
1. Fetch all ~9k series from `/series` endpoint
2. Keyword-match series titles via `_GEO_SERIES_KEYWORDS` → ~1,400 geo candidates
3. Apply sports/non-geo prefix blocklist (`MARKET_SERIES_BLOCKLIST_PREFIXES`) → ~443 markets
4. Cache for 30 min (`MARKET_CACHE_TTL_SECONDS = 1800`)

### Match Gate (find_candidates)
Tiered headline gate — not just similarity score:
- A single **named geo entity** (country, person in `_GEO_NAMED_ENTITIES`) passes alone
- **Generic words** ("bank", "attack", "war") require 2+ overlaps to pass
- Market must contain at least one token from `_GEOPOLITICAL_BOOST`

---

## Critical Technical Facts

### Authentication
- **RSA-PSS** signing (NOT PKCS1v15, NOT HMAC-SHA256) for both REST and WebSocket
- REST base URL: `https://api.elections.kalshi.com/trade-api/v2`
- WS URL: `wss://api.elections.kalshi.com/trade-api/ws/v2`
- Market status from API is `"active"` (not `"open"`) — executor checks both

### WebSocket Header Kwarg
`websockets` has renamed this parameter multiple times:
| Version | Kwarg |
|---------|-------|
| < 10.0 | `extra_headers` |
| 10–11 | `additional_headers` |
| 12–13 | `extra_headers` |
| 14+ | `additional_headers` |

Code detects at import time (`_WS_HEADER_KWARG`) and selects correct kwarg dynamically.
**Never hardcode the kwarg name.** See `docs/websocket_fix.md` for full details.

### PEM Key (.env)
Store as a single line with literal `\n` escape sequences (not real newlines):
```
KALSHI_API_KEY_SECRET=-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----
```
`_normalize_pem()` handles conversion before loading.

### Config / Env Vars
| Var | Notes |
|-----|-------|
| `BANKROLL` | Starting notional bankroll |
| `MAX_BET_HARD_CAP` | Hard ceiling per bet (NOT `MAX_BET_DOLLARS`) |
| `BET_PCT_BANKROLL` | % of bankroll per bet (default 5%) |
| `MIN_BET_DOLLARS` | Floor per bet |
| `KELLY_FRACTION` | Half-Kelly = 0.5 |
| `MIN_EDGE` | Min edge before live bet (default 0.04) |
| `OLLAMA_MODEL` | Model name (default `qwen2.5:3b`) |
| `OLLAMA_BASE_URL` | Default `http://localhost:11434/v1` |

Dynamic bet sizing: `cfg.dynamic_max_bet(notional)` = `min(MAX_BET_HARD_CAP, BET_PCT_BANKROLL * notional)`

### Paper Trading
- Mode stored in `data/paper_trades.db` → `bot_state` table, key `notional_bankroll`
- `PAPER_MAX_CANDIDATES = 1` — top match only, one trade per article (clean signal data)
- `PAPER_MIN_EDGE = 0.02` (vs live `0.04`) — wider net for data collection
- DB is local to each machine — NOT synced between Mac and Windows

### Windows Service
- NSSM wraps `python main.py`, auto-restart on failure, starts at boot
- Service name: `kalshi-bot`
- Logs: `e:/VS_Code/kalshi-bot/logs/bot.log`
- Control (elevated PowerShell): `Start/Stop/Restart-Service kalshi-bot`
- `WINDOWS_COMMANDS.md` has the full reference

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
| `trading/paper_trader.py` | Paper trade tracking, credibility, bankroll |
| `data/paper_trades.db` | SQLite — paper trades + bot state (not synced to git) |
| `tasks/lessons.md` | Hard-won lessons — READ THIS before making changes |
| `tasks/todo.md` | Current work backlog |
| `docs/future_plans.md` | Roadmap: Mac Studio, equity bot, OpenClaw |
| `docs/websocket_fix.md` | Detailed websocket version history and fix |

---

## What NOT to Do
- Do not blend LLM probability with keyword scores — removed intentionally
- Do not use `KALSHI_GEOPOLITICAL_SERIES` allowlist — it's obsolete, zero open markets
- Do not use PKCS1v15 or HMAC for signing — Kalshi requires RSA-PSS
- Do not hardcode `extra_headers` or `additional_headers` — use `_WS_HEADER_KWARG`
- Do not commit `.env`, `data/`, or `logs/` — gitignored
- Do not run the bot on both Mac and Windows simultaneously in live mode — same API key
