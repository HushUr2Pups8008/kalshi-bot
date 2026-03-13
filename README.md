# kalshi-bot

A 24/7 automated paper/live trading bot for [Kalshi](https://kalshi.com) geopolitical prediction markets.

Monitors RSS news feeds and Reddit for breaking geopolitical events, matches them against open Kalshi markets, estimates probability shifts using a local LLM (Ollama) or keyword scoring, sizes bets with half-Kelly, and executes paper trades automatically. Live trading requires explicit opt-in.

---

## How It Works

1. **News ingestion** — RSS (Reuters, AP, BBC, Al Jazeera) and Reddit (r/worldnews, r/geopolitics, r/news) are polled continuously for new headlines.
2. **Market matching** — Each headline is matched against cached open Kalshi markets using Jaccard token similarity with a geopolitical keyword boost.
3. **Probability estimation** — A local LLM (Ollama `qwen2.5:7b`) classifies each signal with categorical output: relevance, novelty, direction (yes/no/neutral), and magnitude (none/small/moderate/large). Code applies deterministic probability shifts from these categories. Falls back to keyword scoring if Ollama is unavailable.
4. **Bet sizing** — Half-Kelly criterion, capped at 5% of notional bankroll and a hard dollar cap.
5. **Execution** — Paper mode records trades to SQLite. Live mode requires `--go-live` + typing `CONFIRM`.

---

## Architecture

```
main.py                   — Async entry point; 5 concurrent tasks
  feeds/
    rss_monitor.py        — Polls RSS feeds every 60s
    reddit_monitor.py     — Polls Reddit public JSON API every 300s
  analysis/
    signal_analyzer.py    — LLM + keyword probability estimation
    market_matcher.py     — Jaccard similarity market matching
    kelly.py              — Half-Kelly bet sizing
    source_credibility.py — Per-source win/loss multiplier (0.5-1.5x)
  trading/
    executor.py           — Validation gate, routes to paper or live
    paper_trader.py       — SQLite paper trading engine
  kalshi/
    rest_client.py        — Kalshi REST API (RSA-PSS auth)
    websocket_client.py   — Real-time price feed
  config.py               — All configuration, env var bindings, keyword lists
```

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

---

## Windows Service (24/7)

Uses [NSSM](https://nssm.cc) to run as a Windows service. See `WINDOWS_COMMANDS.md` for service control, log watching, and management commands.

```powershell
# Install (elevated PowerShell)
Set-ExecutionPolicy Bypass -Scope Process -Force
& "E:\VS_Code\kalshi-bot\setup_service.ps1"
```

> Ollama must be running before starting the service. On a fresh boot it starts automatically via the Ollama tray app (Windows startup).

---

## LLM Probability Estimation

Signal quality priority:

1. **Ollama** (local, free) — `qwen2.5:7b` via `http://localhost:11434/v1`
2. **Anthropic Claude Haiku** (paid fallback) — set `ANTHROPIC_API_KEY` in `.env`
3. **Keyword scoring** (always available) — deterministic fallback, no external calls

The LLM answers categorical questions (relevant? new information? direction? magnitude?) rather than outputting a raw probability. Code maps magnitude to deterministic shifts (small=8pp, moderate=15pp, large=25pp), scaled by confidence. Keywords serve as an initial match gate but do not influence the final probability when the LLM is available.

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
| `MAX_BET_HARD_CAP` | `50.00` | Maximum single bet in dollars |
| `KELLY_FRACTION` | `0.5` | Kelly fraction (0.5 = half-Kelly) |
| `MIN_EDGE` | `0.04` | Minimum edge to place a live trade |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base URL |
| `ANTHROPIC_API_KEY` | _(unset)_ | Enables Claude Haiku fallback |
