# Todo
*Current work backlog. Updated each session.*

---

## Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Validate price-fade paper trades — query DB after 2-4 weeks:
      `SELECT substr(reasoning,1,30) tag, count(*) trades, sum(case when pnl>0 then 1 else 0 end) wins FROM paper_trades WHERE reasoning LIKE '[PRICE_FADE%' GROUP BY tag;`
      Positive win rate → go live.
- [x] Portfolio state object — `trading/portfolio.py` ✓ v0.6.0
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

---

## LLM Improvements (post-GPU — Mac Studio)
- [ ] **3-stage LLM pipeline** — replace single combined prompt with:
      1. Relevance filter (binary, early exit)
      2. Novelty detector (binary, early exit)
      3. Impact estimator (direction + magnitude only)
      Only practical when inference < 5s. Defer to Mac Studio / Qwen3.

- [ ] **Consensus voting** — run 3 evaluations per signal, take majority vote on direction,
      median magnitude, mean confidence. Stabilizes borderline outputs.
      Same constraint: 3× inference time, needs sub-5s per call.

---

## Infrastructure Roadmap

### Mac Studio M4 Max 128GB (incoming)
- [ ] Enable Remote Login (SSH) in System Settings
- [ ] Enable Screen Sharing
- [ ] Set static LAN IP via router
- [ ] Install Tailscale
- [ ] Install Homebrew, Python, Node 24, Ollama
- [ ] Clone kalshi-bot repo, confirm it runs
- [ ] Upgrade Ollama model to Qwen3 when available
- [ ] Migrate NSSM service from Windows → launchd on Mac Studio

### OpenClaw (after Kalshi-bot is live and stable)
- [ ] Mac Studio set up and stable
- [ ] Telegram account on phone
- [ ] Telegram bot created via @BotFather — token saved
- [ ] Node 24 confirmed on Mac Studio
- [ ] Decide on model: Anthropic API or local Qwen3-Coder:32B via LM Studio

### Profit → Bitcoin Strategy (after Kalshi is live and profitable)
- [x] Decide on custody model: self-custody via Coinbase ✓
  - Transfer Robinhood BTC → Coinbase is NOT a taxable event
  - Selling BTC back to USD IS taxable (capital gains)
- [ ] Define trading float minimum (e.g. $500 bankroll + $200 buffer = $700 floor)
- [ ] Define sweep rule (e.g. monthly, sweep 50% of balance above floor into BTC)
- [ ] Decide on entry strategy: pure DCA vs. price-conditional (below 200-day MA)
- [ ] Tax planning: track cost basis from day one

### Polymarket Direct Trading (future — after Kalshi is live and stable)
- [ ] Research Polymarket API / SDK (blockchain-based: Polygon/USDC wallet)
- [ ] Set up Polygon wallet, fund with USDC
- [ ] Build Polymarket execution layer (separate from Kalshi executor)
- [ ] Validate fade signal win rate on Polymarket-matched markets before going live
- [ ] Decide: unified bot or separate process per exchange
