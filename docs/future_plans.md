# Future Plans & Architecture Roadmap
*Documented from planning session — March 12, 2026*

---

## Current State: Kalshi-Bot

A working, paper-trading prediction market bot running as a service on Windows (NSSM), with the following architecture:

- **5 async tasks:** RSS monitor, Reddit monitor, WebSocket client, daily reporter, market cache refresh
- **LLM stack:** Ollama `qwen2.5:7b` (primary) → Claude Haiku (fallback) → keyword scoring (final fallback)
- **Market scope:** ~443 geopolitical markets filtered from ~9k series
- **Auth:** RSA-PSS signing for REST and WebSocket
- **Paper trades DB:** `data/paper_trades.db`

### Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets
- [ ] Review paper trade performance — confirm positive edge
- [ ] Run `python main.py --go-live` and type `CONFIRM`
- [ ] Verify Kalshi API key is current and account is funded

### Known Low-Priority Bugs (fix in Claude Code session)
1. `set.pop()` LRU approximation — `feeds/rss_monitor.py:78`, `feeds/reddit_monitor.py:135`
2. Silent exception swallow — `main.py:293`
3. `cfg.is_paper_trading` mutability — `config.py:207`
4. Reddit monitor array indexing — `feeds/reddit_monitor.py:86`

---

## Phase 1: Infrastructure Upgrade

### New Hardware: Mac Studio M4 Max, 128GB
- **Spec:** 16-core CPU, 40-core GPU, 128GB unified memory, 1TB SSD
- **Why:** 546 GB/s memory bandwidth. LLM inference is memory-bandwidth bound, not compute bound. This machine can run 70B models at Q6/Q8 quality comfortably, plus multiple simultaneous models.
- **Role:** Always-on headless server. SSH via static LAN IP + Tailscale for remote access from anywhere (including phone via Termius).
- **Project path:** `/Users/Jake/vscode/kalshi_bot`

### Model Upgrade
Move from `qwen2.5:7b` to **Qwen3** series models as they become available in Ollama. The architecture supports swapping models via config with no code changes.

---

## Phase 2: Equity Trading Bot

### Concept
Extend the existing news-driven pipeline (RSS → LLM assessment → execution) from binary Kalshi prediction markets to equity trading. The core architecture is market-agnostic — the ingestion, signal analysis, and probability estimation layers all transfer.

### Focused Scope (do not boil the ocean)
**10–20 tickers** in two high-signal domains:
- **Small/advanced nuclear:** NuScale, Oklo, Kairos, and peers
- **AI infrastructure:** picks-and-shovels plays in the AI buildout

These sectors have strong news signal (NRC filings, DOE loan announcements, earnings, Congressional energy committee activity) that most retail traders are not monitoring systematically. That's the edge.

### Broker: Alpaca (not Robinhood)
- Designed specifically for algorithmic trading
- Clean Python SDK, developer-first
- Commission-free for stocks/ETFs via API
- Paper trading built in, free, real-time
- No historical hostility to algo order flow
- **Cost: $0** to run (no commissions, no API fees, no account minimum)

---

## Phase 3: Multi-Agent Architecture

### Overview
Rather than one monolithic bot, the equity system uses specialized agents — each with its own model sized to the complexity of its task. This mirrors how professional quant shops are structured.

### Agent Stack

| Agent | Model | RAM | Role |
|---|---|---|---|
| Watchlist | Qwen3 4B | ~3GB | Maintains ticker universe, tracks earnings calendars, volume anomalies, flags elevated-attention tickers |
| News Router | Qwen3 8B | ~5GB | Monitors RSS, Reddit, SEC EDGAR, NRC regulatory filings. Classifies incoming news, routes to downstream agents |
| Signal Assessment | Qwen3 32B | ~20GB | Deep CoT reasoning on news → ticker impact. Domain expertise in nuclear regulatory timelines, DOE loan programs, AI capex cycles. This is the core alpha engine. |
| Risk Management | Qwen3 14B | ~9GB | Runs independently. Cannot be overridden. Monitors total exposure, drawdown thresholds, position concentration. Acts as circuit breaker — kills everything if conditions warrant. |
| Execution | Qwen3 8B | ~5GB | Interfaces with Alpaca API. Handles order logic, position sizing, stop losses, cooldown enforcement, partial fills. |

### Compute Budget on Mac Studio 128GB

| Allocation | RAM |
|---|---|
| All 5 agents | ~42GB |
| macOS overhead | ~6GB |
| KV cache + context buffers | ~10GB |
| **Total used** | **~58GB** |
| **Remaining headroom** | **~70GB** |

The remaining 70GB allows:
- A 70B model loaded on demand for deep research tasks
- Kalshi-bot running simultaneously alongside the equity system
- Experimental/staging agents without disrupting production

### Claude API Escalation Layer
The Signal Assessment agent escalates its highest-conviction trade signals to Claude API (Haiku or Sonnet) for a second opinion before execution. This adds a belt-and-suspenders check on the trades that matter most, at minimal cost (pennies per day at realistic signal frequency).

---

## Code Reuse Assessment

| Target Platform | Estimated Reuse | Notes |
|---|---|---|
| Kalshi → Polymarket | ~70–80% | Same binary event structure, swap REST/WS client |
| Kalshi → Alpaca equities | ~40–50% | News ingestion fully reusable, execution layer rebuilt, binary → continuous price logic |

The existing `feeds/` directory (RSS monitor, Reddit monitor) and the LLM probability assessment pipeline are the most reusable components across all future targets.

---

## Guiding Principles

1. **Paper trade everything first.** No real money until there is documented positive edge. This applied to Kalshi and applies equally to equities.
2. **Narrow and deep beats wide and shallow.** 15 tickers you understand well outperforms 200 tickers you don't.
3. **Risk management is not optional.** The risk agent has kill-switch authority and cannot be overridden by any other agent.
4. **The hardware is the foundation.** The Mac Studio running 24/7 headless is the engine. Everything else is software on top of it.
5. **Don't break what works.** Kalshi-bot goes live when it has edge. Equity bot is a separate system built in parallel, not a replacement.

---

## 6–12 Month Roadmap

- **Now:** Fix known bugs in Kalshi-bot. Accumulate paper trades. Validate edge.
- **Near-term:** Go live on Kalshi with small position sizes. Set up Mac Studio as headless server.
- **Mid-term:** Build equity watchlist agent + news router. Paper trade on Alpaca.
- **Longer-term:** Bring up signal assessment and risk management agents. Connect execution layer to Alpaca paper trading.
- **When edge is confirmed:** Go live on equities with small capital, scale gradually.
