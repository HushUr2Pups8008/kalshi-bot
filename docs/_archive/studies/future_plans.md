# Future Plans & Architecture Roadmap

> **ARCHIVED 2026-04-26.** This document is preserved as historical context only. The kalshi-bot-relevant content has been split as follows:
>
> | Section in this doc | Where it lives now |
> |---|---|
> | **Phase 1 — Mac Studio infrastructure** | Hardware landed. No active work item. |
> | **Phase 3 — Multi-agent architecture (kalshi-side governance)** | Realised by the **governance agent project** — see [`docs/governance/`](../../governance/) and the Phase 1 / Phase 2 plans in [`docs/superpowers/plans/`](../../superpowers/plans/). The "dynamic adaptation" intent that Phase 6 below sketches is the same intent the governance agent now implements. |
> | **Phase 5 — LLM pipeline upgrades** (3-stage pipeline, consensus voting) | [`docs/ROADMAP.md`](../../ROADMAP.md) **Appendix C**, items C.1 and C.2. |
> | **Phase 6 — Feedback loop upgrades** | Phase 6.1 (dynamic keyword weighting) is **absorbed into the governance agent**; Phase 6.2 (drift-triggered LLM re-analysis) and Phase 6.3 (Loop A threshold tuning) remain as ROADMAP **Appendix C** items C.4 and C.5. |
> | **Phase 2 — Equity Trading Bot (Alpaca)** | Cross-project; **not** part of kalshi-bot. Preserved here for historical reference only. |
> | **Phase 4 — OpenClaw Personal Assistant** | Cross-project; **not** part of kalshi-bot. Preserved here for historical reference only. |
>
> **If a future agent is editing this file looking for active backlog**, stop and use [`docs/ROADMAP.md`](../../ROADMAP.md) Appendix C and [`docs/governance/`](../../governance/) instead. This file is frozen; corrections to the live roadmap go in those documents.

*Original draft: March 2026 — reflects Mac Studio planning session + OpenClaw research.*

---

## Current State: Kalshi-Bot ✅ Working

- Paper trading on geopolitical markets (~443 active markets)
- Running as NSSM Windows service on gaming desktop
- LLM: Ollama `qwen2.5:7b` → Claude Haiku fallback → keyword scoring
- 5 async tasks: RSS monitor, Reddit monitor, WebSocket client, daily reporter, market cache refresh

### Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Run `python main.py --go-live` and type `CONFIRM`
- [ ] Verify Kalshi API key is current and account is funded

### Known Low-Priority Bugs (Claude Code session)
1. Silent exception swallow — `main.py:293`
2. `cfg.is_paper_trading` mutability — `config.py:207`
3. Duplicate parse logic in `analysis/signal_analyzer.py` — extract shared helper

*(Bugs #1 set.pop() LRU and #4 Reddit array indexing fixed in commit 58194b9)*

---

## Phase 1: Infrastructure Upgrade 🔜

### Mac Studio M4 Max, 128GB — $3,699
- 546 GB/s memory bandwidth — LLM inference is memory-bandwidth bound
- Runs 70B models at Q6/Q8 comfortably + multiple simultaneous models
- Always-on headless server: SSH + Tailscale + Termius (phone)
- 0% Apple financing: ~$308/month for 12 months

### Sequence on Arrival
1. Enable SSH + Screen Sharing
2. Set static LAN IP, configure Tailscale
3. Install Homebrew, Python, Node 24, Ollama
4. Clone kalshi-bot, confirm it runs
5. Upgrade Ollama model to Qwen3 when available
6. Migrate service from Windows → Mac Studio

### Model Upgrade Path
Current → Target: `qwen2.5:7b` → **Qwen3-32B** (primary) or **Qwen3-70B** (on 128GB)
Config supports model swap with no code changes — just update model name.

---

## Phase 2: Equity Trading Bot (Alpaca) 📋 Planned

### Why Alpaca (not Robinhood)
- Commission-free for stocks/ETFs via API
- Developer-first, clean Python SDK
- Paper trading free and built-in
- No hostility to algo order flow
- **Total cost: $0**

### Target Universe: 10-20 Tickers
- **Small/advanced nuclear:** NuScale, Oklo, Kairos, peers
- **AI infrastructure:** picks-and-shovels plays
- **Alpha source:** NRC filings, DOE loan announcements, Congressional energy committee activity
- Most retail traders not monitoring these systematically — that's the edge

### Architecture
News-driven pipeline — same core concept as Kalshi-bot:
`RSS/Reddit/SEC EDGAR/NRC → LLM signal assessment → probability → bet sizing → execution`

### Code Reuse from Kalshi-bot
| Component | Reuse | Notes |
|-----------|-------|-------|
| `feeds/` directory | ~100% | RSS + Reddit monitors transfer directly |
| LLM assessment pipeline | ~80% | Adapt prompts for continuous vs binary |
| Execution layer | Rebuild | Binary → continuous price, new order logic |
| Risk management | Rebuild | More complex for equities |

---

## Phase 3: Multi-Agent Architecture 🔮 Future

### Why Multi-Agent
- Each task has different complexity → right-size the model
- Agents run simultaneously on Mac Studio's 128GB
- Mirrors how serious quant shops structure systems
- Risk Management agent cannot be overridden — ever

### Agent Stack

| Agent | Model | RAM | Role |
|-------|-------|-----|------|
| Watchlist | Qwen3 4B | ~3GB | Ticker universe, earnings calendars, volume anomalies |
| News Router | Qwen3 8B | ~5GB | RSS/Reddit/SEC EDGAR/NRC → classifies, routes |
| Signal Assessment | Qwen3 32B | ~20GB | Deep CoT on news → ticker impact. Core alpha engine. |
| Risk Management | Qwen3 14B | ~9GB | Circuit breaker. Independent. Kill switch. |
| Execution | Qwen3 8B | ~5GB | Alpaca API, order logic, sizing, stop losses, cooldowns |

### Mac Studio 128GB Compute Budget
| Allocation | RAM |
|---|---|
| All 5 agents running | ~42GB |
| macOS overhead | ~6GB |
| KV cache + context | ~10GB |
| **Total** | **~58GB** |
| **Free headroom** | **~70GB** |

Free headroom allows: 70B research model on demand + Kalshi-bot + experimental agents simultaneously.

### Claude API Escalation Layer
Signal Assessment escalates highest-conviction calls to Claude API for second-opinion before execution.
- Cost: ~$0.20/day at realistic signal frequency (~$6/month)
- Belt-and-suspenders on the trades that matter most

---

## Phase 4: OpenClaw Personal Assistant 🦞 Future

> **2026-05-05 update:** The original Phase 4 below was written as a cross-project personal-assistant idea and remains archived as historical context. A narrower `kalshi-bot`-specific use case is now captured in `docs/governance/2026-05-05-openclaw-orchestrator-integration-note.md`: OpenClaw as a multi-agent operator interface / workflow orchestrator over repo-local scripts, docs, tests, and reports. That use case is an ops control plane, not a trading-loop component, and should wait until after Day-7 close plus a green Wave-1 24 h watch before any daemon/channel installation.

### What It Is
Open-source personal AI assistant. Runs locally on Mac Studio, interfaces via Telegram on phone.
Jarvis-style: "AI that actually does things" — email, calendar, reminders, task automation.

### Current Status (March 2026)
- Latest stable: v2026.3.8 — actively maintained by community
- Peter Steinberger (creator) joined OpenAI Feb 14; project moving to independent foundation with OpenAI backing
- VirusTotal scanning live on skill marketplace
- Installation now takes ~15 minutes (dramatically improved from early days)

### Recommended Stack for Jake's Setup
- **Model:** Qwen3-Coder:32B via LM Studio (better tool call handling than Ollama)
- **Interface:** Telegram (fastest setup, most reliable)
- **Skills:** None initially — core built-in functionality only
- **RAM:** ~20-26GB — fits comfortably in remaining headroom after trading agents

### Security Requirements
1. Bind to `127.0.0.1` only — never expose to public internet
2. Tailscale for remote access (already planned)
3. Zero ClawHub skills installed until security matures further
4. Do NOT integrate financial accounts (Kalshi, Alpaca API keys)
5. Dedicated Mac Studio only — never on primary MacBook

### Pre-Install Checklist
- [ ] Mac Studio set up and Kalshi-bot stable on it
- [ ] Telegram account on phone
- [ ] Telegram bot created via @BotFather — token saved
- [ ] Node 24 confirmed on Mac Studio

---

## Cost Summary

### Infrastructure Costs
| Item | Cost |
|------|------|
| Mac Studio M4 Max 128GB | $3,699 (or $308/mo at 0%) |
| Alpaca trading API | $0 |
| Ollama local inference | $0 |
| Tailscale personal | $0 |
| OpenClaw software | $0 |

### Ongoing API Costs (once live)
| Item | Monthly |
|------|---------|
| Kalshi signal escalation (Claude API) | ~$6 |
| OpenClaw daily assistant use | ~$5-10 |
| **Total** | **~$10-20/month** |

### Go-Live Verification (one-time)
| Item | Cost |
|------|------|
| Kalshi-bot verification | ~$2-5 |
| OpenClaw testing sessions | ~$3-5 |
| **Total** | **under $15** |

---

## Sequenced Roadmap

### Now
- Fix 4 known low-priority bugs in Kalshi-bot
- Continue accumulating paper trades on geopolitical markets
- Validate edge is real before going live

### Near-Term (Mac Studio arrives)
- Set up Mac Studio as headless server
- Migrate Kalshi-bot from Windows → Mac Studio
- Upgrade to Qwen3 model
- Go live on Kalshi with small position sizes

### Mid-Term
- Create Alpaca account, set up paper trading
- Build equity watchlist agent + news router
- Paper trade on 10-15 nuclear/AI tickers
- Install OpenClaw once Kalshi-bot is stable

### Longer-Term
- Bring up signal assessment and risk management agents
- Connect execution layer to Alpaca paper trading
- Validate equity edge extensively

### When Edge Is Confirmed
- Go live on equities with small capital
- Scale position sizes gradually
- Expand ticker universe carefully

---

## Phase 5: LLM Pipeline Upgrades (post-Mac-Studio GPU)

These are deferred until inference latency is consistently < 5 s/call. Not worth attempting on CPU-bound `qwen2.5:7b`.

### 3-stage pipeline
Replace the single combined LLM prompt with three smaller stages, each with early-exit:
1. **Relevance filter** (binary: does this news item concern this market at all?)
2. **Novelty detector** (binary: does it add information vs. already-priced-in?)
3. **Impact estimator** (direction + magnitude only)

Rationale: each stage is a cheaper, more focused prompt; early exits cut total inference time for the ~75% of items that correctly resolve to `magnitude="none"` on stage 1 or 2. Only practical once per-call latency is low enough that three serial calls still fit the ingestion budget.

### Consensus voting
Run 3 evaluations per signal, take majority vote on direction, median magnitude, mean confidence. Stabilizes borderline outputs and makes calibration more honest. Same latency constraint as above (3× inference per evaluation).

---

## Phase 6: Feedback Loop Upgrades (post-Mac-Studio)

The four market feedback loops (A: price→news search, B: resolution→keyword outcomes, C: price-drift logging, D: new-market detection) are all live and implemented. Each has a documented post-Mac-Studio upgrade path that needs GPU-class inference latency to be worthwhile.

### Dynamic keyword weighting (Loop B upgrade)
Signal analyzer reads `paper_trades.db:keyword_outcomes` at startup and adjusts each keyword's `strength` multiplier based on historical accuracy. Sketch:

```python
# analysis/keyword_weights.py (new file)
def load_keyword_weights(db_path: Path, min_samples: int = 10) -> dict[str, float]:
    """{keyword: accuracy_ratio} for keywords with >= min_samples outcomes."""
    rows = conn.execute(
        "SELECT keyword, SUM(correct), COUNT(*) FROM keyword_outcomes "
        "GROUP BY keyword HAVING COUNT(*) >= ?",
        (min_samples,),
    ).fetchall()
    return {kw: wins / total for kw, wins, total in rows}

# At signal analyzer startup:
_keyword_weights = load_keyword_weights(DB_PATH)

# When applying GEOPOLITICAL_SIGNALS strength:
accuracy = _keyword_weights.get(keyword)
if accuracy is not None:
    # 50% accuracy (random) -> 0.5×; 80% accuracy -> 1.3×
    adjusted = base_strength * (accuracy / 0.5)
    strength = max(0.01, min(base_strength * 2, adjusted))
```

### Drift-triggered LLM re-analysis (Loop C upgrade)
A `POSITION_DRIFT` event today is log-only. With fast inference: fetch recent news for that ticker, re-run the signal analyzer, and if `estimated_prob` has flipped direction by > 0.15, emit `POSITION_REASSESSMENT` and optionally close + reverse. Requires sub-5s inference budget to keep the drift→decision loop under human-supervision cadence.

### Loop A threshold tuning (Loop A upgrade)
Current thresholds are conservative for CPU inference latency: `PRICE_MOVE_THRESHOLD_CENTS = 10`, `PRICE_SEARCH_COOLDOWN_SECS = 1800`. With GPU-class inference, lower to ~5 cents / ~600s cooldown so volatile markets hunt news more aggressively.

All three items have the same precondition as Phase 5: consistent sub-5s inference. Should be sequenced after Phase 5 has proven stable.

---

## Guiding Principles

1. **Paper trade everything first.** No real money until documented positive edge.
2. **Narrow and deep beats wide and shallow.** 15 tickers you understand well > 200 you don't.
3. **Risk management is not optional.** Kill-switch authority, cannot be overridden.
4. **The hardware is the foundation.** Mac Studio running 24/7 is the engine.
5. **Don't break what works.** Kalshi-bot goes live when it has edge. Equity bot runs parallel.
6. **Security first on OpenClaw.** Dedicated machine, no financial integrations, no unvetted skills.
