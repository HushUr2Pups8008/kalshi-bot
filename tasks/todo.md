# Todo
*Current work backlog. Updated each session.*

---

## Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Validate fade signal paper trades — query DB after 2-4 weeks:
      `SELECT substr(reasoning,1,30) tag, count(*) trades, sum(case when pnl>0 then 1 else 0 end) wins FROM paper_trades WHERE reasoning LIKE '[FADE%' GROUP BY tag ORDER BY trades DESC;`
      Compare win rate: [FADE/GEO] vs [FADE/SPORTS], and per-account (@Kalshi vs @Polymarket vs @PolymarketMoney).
      All categories positive → go live. If sports wins < 50% → disable sports from fade pipeline.
      Self-host RSSHub before going live (public instance may be blocked by X).
- [ ] Portfolio state object — single source of truth for open positions/exposure before go-live
      Currently spread across SQLite DB, WebSocket cache, and in-memory cooldown dicts.
      Needed for: risk checks, position sizing against real exposure, pre-go-live audit.
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

---

## Near-Term Backlog

### Signal Quality
- [ ] **Priority queue** — swap `asyncio.Queue` for `asyncio.PriorityQueue` in `main.py`
      RSS/breaking news = priority 1, Reddit = priority 2.
      Prevents a burst of low-signal Reddit posts from delaying a high-impact RSS headline
      by up to 600s (10 posts × 60s Ollama = 10 min stall on the single consumer).
      Implementation: `(priority, timestamp, news_item)` tuples; `source_priority()` helper.

- [ ] **Market snapshot at decision time** — add `market_snapshot` JSON column to `paper_trades`
      Currently store `market_yes_price` but not `yes_bid`/`yes_ask`, `close_time`, or `status`.
      True replay requires decision-time prices, not today's prices.
      Minimal fix: serialize `KalshiMarket` fields to JSON at `record_trade()` time.

### LLM / Mac Studio (post-GPU)
- [ ] **3-stage LLM pipeline** — replace single combined prompt with:
      1. Relevance filter (binary, early exit)
      2. Novelty detector (binary, early exit)
      3. Impact estimator (direction + magnitude only)
      Only practical when inference < 5s (GPU or cloud). Defer to Mac Studio / Qwen3.

- [ ] **Consensus voting** — run 3 evaluations per signal, take majority vote on direction,
      median magnitude, mean confidence. Stabilizes borderline outputs.
      Same constraint as 3-stage: 3× inference time, needs sub-5s per call.

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

### Profit → Bitcoin Strategy (future — after Kalshi is live and generating consistent profit)
- [x] Decide on custody model: self-custody via Coinbase ✓
  - Transfer Robinhood BTC → Coinbase is NOT a taxable event (custody transfer, no sale)
  - Selling BTC back to USD IS taxable (capital gains; short-term <1yr, long-term >1yr)
  - Simply holding in Coinbase: no tax until sold
- [ ] Define trading float minimum (e.g. $500 bankroll + $200 buffer = $700 floor)
- [ ] Define sweep rule: e.g. "monthly, sweep 50% of balance above floor into BTC"
- [ ] Decide on entry strategy: pure DCA (fixed schedule, ignore price) vs. price-conditional (only buy below 200-day MA)
  - DCA is simpler and proven; timing is hard even for professionals
- [ ] Evaluate automation: script that checks Kalshi balance, calculates excess, triggers Robinhood buy
  - Robinhood has an unofficial API (robin_stocks library) — not officially supported, use with caution
  - Alternative: manual monthly review until amounts justify automation
- [ ] Tax planning: crypto buys/sells are taxable events — track cost basis from day one

### Polymarket Direct Trading (future — after Kalshi is live and stable)
- [ ] Research Polymarket API / SDK (blockchain-based: Polygon/USDC wallet, not API key)
- [ ] Set up Polygon wallet, fund with USDC
- [ ] Build Polymarket execution layer (separate from Kalshi executor)
- [ ] Validate fade signal win rate on Polymarket-matched markets before going live there
- [ ] Decide: unified bot or separate process per exchange
