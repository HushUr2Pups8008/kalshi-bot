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

## Bugs & Issues (from v0.6.7 log review, 2026-03-29)

### P1 — Reddit Monitor IP-Blocked (403 escalation)
- [ ] Reddit shifted from 429 (rate limit) to hard 403 (access denied) starting 03-24
- 2,662 total 403s over the week; worst day 03-26 had 940 403s and 31 circuit breaker trips
- All 30 subreddits blocked simultaneously — this is IP-level, not subreddit-specific
- Circuit breaker works but 30-min cooldown is too short (blocks persist 6-14+ hours)
- **Root cause:** 30 subreddits polled via public JSON endpoint (no OAuth) = 360 req/hr from one IP
- **Fix options:**
  - [ ] Switch to Reddit OAuth2 (600 req/10min vs aggressive public API blocking)
  - [ ] Reduce poll frequency or stagger subreddits (poll subsets on rotation)
  - [ ] Increase circuit breaker cooldown from 30min to 2-4 hours when 100% fail
  - [ ] Add exponential backoff on the circuit breaker itself (not just per-subreddit)

### P1 — Ollama Offline Since ~Mar 27 (keyword-only fallback)
- [ ] LLM went from Ollama to keyword-only fallback around 03-27 and has not recovered
- All signals 03-27 through 03-29 used keyword-only analysis (low quality)
- One false positive trade placed 03-29: "bombing" keyword matched a Pakistan headline
  to KXVANCEPAKISTAN (Vance visiting Pakistan) — exactly what the LLM was preventing
- **Action:** Check Ollama tray app status; restart if needed; investigate why it went down
- **Related:** 03-26 19:05-19:06 had two restarts where Ollama was unreachable at startup
- **Related:** 03-27 04:31 Ollama circuit breaker tripped (3 consecutive failures)

### P2 — Zero Resolved Trades After 16 Days
- [ ] All 6 paper trades are on April 1 expiry markets — none have resolved yet
- Go-live assessment requires 10 resolved trades minimum
- Earliest possible resolution: April 1, 2026
- **Consider:** Target shorter-duration markets to get faster feedback on signal quality

### P3 — KXVANCEPAKISTAN False Positive (03-29)
- [ ] YES trade on "Will JD Vance visit Pakistan before Apr 1?" triggered by Guardian
  headline about Israeli strikes / US troop buildup in Pakistan
- Keyword "bombing" matched Pakistan in headline to the market — completely unrelated event
- This is a market-matching quality issue that LLM would have filtered out
- **Consider:** Tighter headline-to-market title matching when in keyword-only fallback mode

### Informational — WebSocket Health (OK)
- 7 disconnections over the week, all auto-recovered in 2-4 seconds
- Cluster on 03-26 (4 disconnections in 3 hours, 01:32-04:28 Denver time)
- All overnight (01:00-08:43) — likely Kalshi maintenance windows
- **No action needed** — reconnect logic is working perfectly

### Informational — Ollama Brief Outage on 03-26
- Two rapid restarts at 19:05 and 19:06 — Ollama not reachable at startup both times
- Bot fell back to keyword-only scoring during this window
- Ollama tray app auto-start may not be reliable across reboots/sleep cycles
- [ ] Verify Ollama tray app auto-start reliability; consider a health-check watchdog

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
