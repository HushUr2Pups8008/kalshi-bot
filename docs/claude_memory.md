# Project Memory — kalshi_bot & Future Systems
*Last updated: March 2026 — reflects Mac Studio planning session*

---

## Who Is Jake

Builder, not a consumer. Goal: generate passive income so his wife (a nurse) doesn't have to work anymore. Every system here is in service of that mission. Primary dev machine is a MacBook. Windows gaming desktop runs the bot as a service. Mac Studio M4 Max (128GB) is incoming as the permanent headless inference server.

---

## Active Project: kalshi_bot

| Machine | Path | How it runs |
|---------|------|-------------|
| MacBook (primary dev) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` in terminal |
| Windows Gaming Desktop | `e:/VS_Code/kalshi-bot/` | NSSM Windows Service (`kalshi-bot`) |
| Mac Studio (incoming) | `/Users/Jake/vscode/kalshi_bot` | `python main.py` (planned) |

**Git repo:** `https://gitlab.com/HushUr2Pups8008/kalshi-bot` — remote `origin/main`
**Rule:** Always `git pull` before starting a session on any machine.

---

## Kalshi Bot — Architecture

### Runtime
- 5 async tasks: RSS monitor, Reddit monitor, WebSocket client, daily reporter, market cache refresh
- LLM stack: Ollama `qwen2.5:7b` (primary) → Claude Haiku via `ANTHROPIC_API_KEY` (fallback) → keyword scoring (final fallback)
- Paper trades DB: `data/paper_trades.db` — local to each machine, NOT synced between Mac and Windows

### Auth
- RSA-PSS signing (NOT PKCS1v15) for both REST and WebSocket
- REST base URL: `https://api.elections.kalshi.com/trade-api/v2`
- WS URL: `wss://api.elections.kalshi.com/trade-api/ws/v2`
- Market status: Kalshi returns `"active"` (not `"open"`) — executor checks both

### WebSocket Header Kwarg History
| websockets version | correct kwarg |
|--------------------|---------------|
| < 10.0 | `extra_headers` |
| 10.x – 11.x | `additional_headers` |
| 12.0 – 13.x | `extra_headers` |
| 14.0+ | `additional_headers` |

Code detects version at import (`_WS_HEADER_KWARG`) and selects correct kwarg dynamically — supports websockets 13–16+. `requirements.txt` uses `websockets>=13.0` (not pinned).

### PEM Key
`.env` stores single-line PEM with literal `\n` escapes. `_normalize_pem()` in `kalshi/websocket_client.py` converts to real newlines before loading.

### Market Discovery (current as of 2026-03)
Kalshi retired organised geo series (KXUKR, KXINTL, etc.) — all 0 open markets. Current approach:
1. Fetch all ~9k series from `/series`
2. Keyword-match titles → ~1,400 geo/political candidates
3. Apply sports prefix blocklist (pass 2)
4. Fetch open markets per matched series → ~443 active geo markets

`KALSHI_GEOPOLITICAL_SERIES` in `config.py` is now historical/unused.

### Other Key Details
- Market blocklist checks BOTH `series_ticker` AND `ticker` prefix (API may return empty `series_ticker`)
- Paper trading bankroll: `data/paper_trades.db` → `bot_state` table, key `notional_bankroll`
- Config env vars: `BANKROLL`, `MAX_BET_HARD_CAP`, `KELLY_FRACTION`, `MIN_EDGE` (NOT `MAX_BET_DOLLARS`)
- `MARKET_CACHE_TTL_SECONDS = 1800` (30 min refresh, ~3 min to complete in thread pool)
- Reddit monitor: 29 subreddits, public JSON endpoints (no API creds), 10s stagger, 300s cycle

---

## Kalshi Bot — Low-Priority Bug Backlog

Fix these in a dedicated Claude Code session:

1. **Silent exception swallow on shutdown** — `main.py:293`
   - `except Exception: pass` silently drops report generation errors
   - Fix: `log.warning("Report generation failed: %s", exc)`

2. **`cfg.is_paper_trading` mutability** — `config.py:207`, `trading/paper_trader.py:115/118/158`
   - Global singleton mutated directly during runtime, no async locking
   - Low risk (set at startup only) but architecturally unsound

3. **Duplicate parse logic** — `analysis/signal_analyzer.py`
   - Ollama and Anthropic backends have identical direction/magnitude → probability math written twice
   - Fix: extract into shared `_parse_llm_json(parsed, market)` helper

*(Bugs previously listed as #1 `set.pop()` LRU and #4 Reddit array indexing were fixed in commit 58194b9)*

---

## Kalshi Bot — Go-Live Prerequisites

- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live at a time.

---

## Windows-Specific Setup

- **Python:** 3.14 (Mac uses 3.11/3.12) — requires `aiohttp>=3.10.0` (3.9.5 has no cp314 wheel)
- **Service:** NSSM wraps `python main.py`, auto-restart, boot start
- **Service name:** `kalshi-bot`
- **Log rotation:** NSSM at 10MB
- **Logs:** `e:/VS_Code/kalshi-bot/logs/bot.log`
- **Service control** (elevated PowerShell): `Start/Stop/Restart-Service kalshi-bot`
- **Tail logs** (Git Bash): `tail -f logs/bot.log`
- **Power plan:** Sleep/hibernate disabled on AC; display off 15 min (`setup_service.ps1`)
- **Gitignored Windows files:** `WINDOWS_COMMANDS.md`, `setup_service.ps1`, `.env`
- GitLab PAT in Windows Credential Manager (no PAT file on disk)

---

## Incoming Hardware: Mac Studio M4 Max 128GB

- **Spec:** 16-core CPU, 40-core GPU, 128GB unified memory, 1TB SSD — $3,699
- **Memory bandwidth:** 546 GB/s (vs 273 GB/s Mac Mini M4 Pro) — critical for LLM inference
- **Role:** Always-on headless inference server
- **Access:** Static LAN IP via SSH + Tailscale for remote access from anywhere
- **Phone access:** Termius app for SSH from iPhone; `tail -f logs/bot.log` and `python main.py --report` work from phone
- **Mac Studio path:** `/Users/Jake/vscode/kalshi_bot`
- **Why 128GB:** Can run 70B models at Q6/Q8, multiple simultaneous models, full 5-agent equity stack with 70GB headroom remaining
- **Financing:** 0% Apple financing available (~$308/month for 12 months)

### Mac Studio Setup Checklist (on first boot)
- [ ] Enable Remote Login (SSH) in System Settings
- [ ] Enable Screen Sharing in System Settings
- [ ] Set static IP via router
- [ ] Install Tailscale
- [ ] Install Homebrew
- [ ] Install Python, Node, Ollama
- [ ] Clone kalshi-bot repo
- [ ] Test SSH from MacBook: `ssh jake@192.168.1.X`

---

## LLM Stack — Current & Planned

### Current (Windows)
- Ollama `qwen2.5:7b` via `http://localhost:11434/v1`
- ~5-10 tok/s on AMD 5700XT (Ollama unsupported GPU — CPU fallback)

### Planned (Mac Studio)
- Upgrade to **Qwen3** series as available in Ollama
- 70B models at Q6/Q8: ~15-20 tok/s
- 32B models at Q4_K_M: ~25-30 tok/s
- Config supports model swap with no code changes (just update model name in `.env`)

---

## Anthropic API Key Strategy

Jake has deliberately avoided creating an Anthropic API key to avoid costs during development. Plan:
- Continue using local Ollama models for all development and paper trading
- Create API key only at final go-live verification phase
- Use Claude Haiku/Sonnet for highest-conviction signal escalation only
- Estimated ongoing cost once live: **~$10-20/month** (Kalshi escalation + OpenClaw assistant)
- Verification phase one-time cost: **under $15 total**

---

## Future Phase 2: Equity Trading Bot (Alpaca)

### Broker: Alpaca
- Commission-free for stocks/ETFs via API
- Paper trading built in, free, real-time
- Developer-first, clean Python SDK
- No hostility to algo order flow
- **Cost: $0** (no commissions, no API fees, no account minimum)

### Focus: 10-20 Tickers
- **Small/advanced nuclear:** NuScale, Oklo, Kairos, peers
- **AI infrastructure:** picks-and-shovels AI buildout plays
- Alpha source: NRC filings, DOE loan announcements, Congressional energy committee activity — most retail traders not monitoring systematically

### Code Reuse from Kalshi-bot
| Target | Reuse | Notes |
|--------|-------|-------|
| Kalshi → Polymarket | ~70-80% | Same binary structure, swap REST/WS client |
| Kalshi → Alpaca equities | ~40-50% | `feeds/` fully reusable, execution layer rebuilt |

---

## Future Phase 3: Multi-Agent Equity Architecture

### Agent Stack (Mac Studio 128GB)

| Agent | Model | RAM | Role |
|-------|-------|-----|------|
| Watchlist | Qwen3 4B | ~3GB | Ticker universe, earnings calendars, volume anomalies |
| News Router | Qwen3 8B | ~5GB | RSS/Reddit/SEC EDGAR/NRC filings → classifies and routes |
| Signal Assessment | Qwen3 32B | ~20GB | Deep CoT reasoning on news → ticker impact. Core alpha engine. |
| Risk Management | Qwen3 14B | ~9GB | Circuit breaker. Independent. Cannot be overridden. Kill switch. |
| Execution | Qwen3 8B | ~5GB | Alpaca API, order logic, position sizing, stop losses, cooldowns |

### Compute Budget
| Allocation | RAM |
|---|---|
| All 5 agents | ~42GB |
| macOS overhead | ~6GB |
| KV cache + context | ~10GB |
| **Total used** | **~58GB** |
| **Remaining** | **~70GB free** |

Remaining 70GB headroom: 70B research model on demand, Kalshi-bot simultaneously, experimental agents.

### Claude API Escalation
Signal Assessment agent escalates highest-conviction calls to Claude API (Haiku or Sonnet) for second opinion before execution. Belt-and-suspenders on trades that matter. Cost: pennies/day.

---

## OpenClaw — Personal AI Assistant (Future)

### What It Is
Open-source personal AI assistant (by Peter Steinberger, now at OpenAI). Runs locally, interfaces via Telegram/WhatsApp/Discord. "AI that actually does things."

### Status as of March 2026
- Latest stable: v2026.3.8
- VirusTotal scanning live on ClawHub marketplace
- CVE-2026-25253 (CVSS 8.8 RCE) patched in v2026.1.29 — **must be on current version**
- Peter Steinberger joined OpenAI Feb 14, 2026 — project moving to independent open-source foundation, OpenAI-backed
- Update cadence: healthy, small fast PRs, community maintainers active

### Security Rules (non-negotiable)
1. Bind to `127.0.0.1` (loopback) only — never `0.0.0.0`
2. Use Tailscale for remote access (already planned for Mac Studio)
3. Install **zero ClawHub skills** initially — core built-in functionality only
4. If skills needed later: only VirusTotal-verified badge, read source before installing
5. Do NOT run on primary MacBook — dedicated Mac Studio only
6. Use strong model (32B+) for tool-enabled agents — smaller models are vulnerable to prompt injection
7. **Do not connect financial accounts** (Kalshi API keys, Alpaca keys) to OpenClaw instance

### Recommended Model for OpenClaw
- Primary: **Qwen3-Coder:32B** — community consensus, ~20GB at Q4_K_M, stable tool calling
- Fallback: **GLM-4.7 Flash** — lightweight, reliable for routine tasks
- Runtime: **LM Studio** preferred over Ollama for OpenClaw (better tool call streaming handling)

### Pre-Install Checklist
- [ ] Mac Studio set up and stable
- [ ] Kalshi-bot running on Mac Studio
- [ ] Telegram account created
- [ ] Telegram bot created via @BotFather — save token
- [ ] Node 24 confirmed on Mac Studio
- [ ] Decide on model: Anthropic API key or local Qwen3-Coder:32B via LM Studio

### Install Command (when ready)
```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```
Installer handles Node detection, installation, and onboarding wizard automatically.

### Deployment Priority
OpenClaw comes **after** Kalshi-bot is live and stable. Do not rush this. Sequence:
1. Fix known bugs in Kalshi-bot
2. Accumulate paper trades, validate edge
3. Go live on Kalshi
4. Set up Mac Studio as headless server
5. **Then** install OpenClaw

---

## Guiding Principles

1. **Paper trade everything first.** No real money until documented positive edge.
2. **Narrow and deep beats wide and shallow.** 15 tickers you understand well > 200 you don't.
3. **Risk management is not optional.** The risk agent has kill-switch authority and cannot be overridden.
4. **The hardware is the foundation.** Mac Studio running 24/7 headless is the engine.
5. **Don't break what works.** Kalshi-bot goes live when it has edge. Equity bot is parallel, not replacement.
6. **Security is not optional.** Especially for OpenClaw — dedicated machine, no financial integrations, no unvetted skills.
