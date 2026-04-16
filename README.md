# kalshi-bot

[![Version](https://img.shields.io/badge/version-0.6.3-blue)](CHANGELOG.md) 

A 24/7 automated paper/live trading bot for [Kalshi](https://kalshi.com) geopolitical prediction markets.

Monitors RSS news feeds and Reddit for breaking geopolitical events, matches them against open Kalshi markets, estimates probability shifts using a local LLM (Ollama) or keyword scoring, sizes bets with half-Kelly, and executes paper trades automatically. Live trading requires explicit opt-in.

See AGENTS.md for system rules and constraints.

---

## How It Works

1. **News ingestion** — RSS (Reuters, AP, BBC, Al Jazeera, and others) and Reddit (r/worldnews, r/geopolitics, r/news, and others) are polled continuously for new headlines. Cross-source dedup suppresses near-identical stories published by multiple outlets within 15 minutes.
2. **Queue + consumer** — New items are placed on a bounded async queue (non-blocking). A single consumer drains the queue, preventing feed pollers from stalling during LLM inference.
3. **Market matching** — Each headline is matched against cached open Kalshi markets using Jaccard token similarity with a geopolitical keyword boost.
4. **Probability estimation** — A local LLM (Ollama `qwen2.5:7b`) classifies each signal with categorical output: relevance, novelty, direction (yes/no/neutral), and magnitude (none/small/moderate/large). Code applies deterministic probability shifts from these categories. Falls back to keyword scoring if Ollama is unavailable.
5. **Fade signal** — Separately monitors @Kalshi, @Polymarket, and @PolymarketMoney tweets via RSSHub. Detects hype/ATH language and fades it: bullish tweet → buy NO. No LLM needed — pattern matching only.
6. **Bet sizing** — Half-Kelly criterion, capped at a configurable % of notional bankroll and a hard dollar cap.
7. **Execution** — Paper mode records trades to SQLite. Live mode requires `--go-live` + typing `CONFIRM`.

---

## Architecture

```
main.py                   — Async entry point; 6 concurrent tasks + optional fade_tweets
  feeds/
    rss_monitor.py        — Polls RSS feeds every 60s
    reddit_monitor.py     — Polls Reddit public JSON API every 300s
    dedup.py              — Cross-source headline dedup (rapidfuzz, 15-min TTL)
  analysis/
    signal_analyzer.py    — LLM + keyword probability estimation
    market_matcher.py     — Jaccard similarity market matching
    kelly.py              — Half-Kelly bet sizing
    source_credibility.py — Per-source win/loss multiplier (0.5–1.5x)
  trading/
    executor.py           — Validation gate, routes to paper or live
    paper_trader.py       — SQLite paper trading engine
  kalshi/
    rest_client.py        — Kalshi REST API (RSA-PSS auth)
    websocket_client.py   — Real-time price feed
  config.py               — All configuration, env var bindings, keyword lists
```

**Concurrent tasks:**
| Task | Role |
|------|------|
| `rss` | Polls RSS feeds, enqueues new items |
| `reddit` | Polls Reddit, enqueues new items |
| `news_consumer` | Drains queue, runs LLM pipeline |
| `websocket` | Real-time Kalshi price feed |
| `market_refresh` | Refreshes geo market cache every 30 min |
| `daily_report` | Writes performance report every 24h |
| `fade_tweets` | *(conditional)* Polls @Kalshi/@Polymarket RSSHub feeds |

**State:** `data/paper_trades.db` — SQLite with paper trades, bot state, and source credibility scores.

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with `qwen2.5:7b` pulled (free, local inference)
- Kalshi account with API key (RSA key pair)

---

## Setup

### 1. Clone and create virtualenv

```bash
git clone https://gitlab.com/HushUr2Pups8008/kalshi-bot.git
cd kalshi-bot
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` with your Kalshi API credentials and trading parameters. See `.env.example` for all options including the optional Anthropic API key for cloud LLM fallback.

### 3. Install Ollama and pull the model

```bash
# Download from https://ollama.com/download
ollama pull qwen2.5:7b
```

### 4. Run

```bash
python main.py                   # paper trading mode (default)
python main.py --report          # print performance report
python main.py --credibility     # print source credibility table
python main.py --resolve TICKER YES   # manually resolve a paper trade
python main.py --go-live         # interactive prompt to enable live trading
```

## Git Workflow

Default repo workflow:

- Review first: run `git status`, `git diff`, and `git diff --staged`
- Stage intentionally by logical change group, not with blind `git add .`
- Keep commits understandable and reversible; split unrelated work into multiple commits
- Run relevant validation/tests before push
- Confirm a clean working tree and sensible commit history before pushing

See [AGENTS.md](AGENTS.md) and [WINDOWS_COMMANDS.md](WINDOWS_COMMANDS.md) for the repo-specific operational workflow.

---

## Windows Service (24/7)

Uses [NSSM](https://nssm.cc) to run as a Windows service. See `WINDOWS_COMMANDS.md` for service control, log watching, and management commands.

```powershell
# Install (elevated PowerShell)
Set-ExecutionPolicy Bypass -Scope Process -Force
& "E:\VS_Code\kalshi-bot\setup_service.ps1"
```

> Ollama must be running before starting the service. On a fresh boot it starts automatically via the Ollama tray app (Windows startup).

**Note:** After installing new dependencies, always install them into the service venv explicitly:
```powershell
E:\VS_Code\kalshi-bot\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## LLM Probability Estimation

Signal quality priority:

1. **Ollama** (local, free) — `qwen2.5:7b` via `http://localhost:11434/v1`
2. **Anthropic Claude Haiku** (paid fallback) — set `ANTHROPIC_API_KEY` in `.env`
3. **Keyword scoring** (always available) — deterministic fallback, no external calls

The LLM answers categorical questions (relevant? new information? direction? magnitude?) rather than outputting a raw probability. Code maps magnitude to deterministic shifts (small=8pp, moderate=15pp, large=25pp), scaled by confidence. Keywords serve as an initial match gate but do not influence the final probability when the LLM is available.

LLM calls are serialized via an `asyncio.Semaphore(1)` — only one Ollama call runs at a time to avoid latency spikes from concurrent inference on CPU.

---

## Fade Signal

When @Kalshi or @Polymarket tweet "BREAKING", "all-time high", or similar hype language, the market is often overpriced from retail attention. The bot fades these signals: bullish tweet → buy NO.

Configure via `.env`:
```
FADE_TWEET_FEED_URLS=https://rsshub.app/twitter/user/Kalshi,https://rsshub.app/twitter/user/Polymarket
```

Requires a running [RSSHub](https://rsshub.app) instance (self-hosted recommended for production — public instances can be rate-limited by X).

---

## Paper Trading

Paper mode is active by default. Trades are recorded to `data/paper_trades.db` with full reasoning. The notional bankroll grows/shrinks as markets resolve.

```bash
# Wipe paper trade history and start fresh
rm data/paper_trades.db
```

---

## Live Trading

Live trading is disabled until explicitly enabled:

```bash
python main.py --go-live
# Type CONFIRM when prompted
```

Live mode adds tighter edge thresholds, a live balance check before each order, and a per-ticker cooldown of 10 minutes.

---

## Key Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `KALSHI_ENV` | `demo` | `demo` or `prod` |
| `BANKROLL` | `500.00` | Notional bankroll for Kelly sizing |
| `MAX_BET_HARD_CAP` | `25.00` | Hard ceiling per bet in dollars |
| `BET_PCT_BANKROLL` | `0.05` | Max bet as % of bankroll (5%) |
| `KELLY_FRACTION` | `0.5` | Kelly fraction (0.5 = half-Kelly) |
| `MIN_EDGE` | `0.04` | Minimum edge to place a live trade |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base URL |
| `MAX_NEWS_AGE_SECONDS` | `300` | Max age of a queued news item before skipping |
| `ANTHROPIC_API_KEY` | _(unset)_ | Enables Claude Haiku fallback |
| `FADE_TWEET_FEED_URLS` | _(unset)_ | Comma-separated RSSHub URLs for fade signal |
