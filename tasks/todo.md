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
- [x] Portfolio state object — `trading/portfolio.py` ✓ v0.6.0
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

---


---

## Bugs & Issues — Logged 2026-03-19 (48h Review)

### CRITICAL — Ollama Permanently in Circuit-Open State
- [ ] **Ollama never recovering from circuit-open** -- After 7 consecutive 60s timeouts spread
      across 48h, the circuit breaker is permanently open and the bot has been running
      keyword-only for the entire period. The probe fires every 5 min but also times out.
      - Root cause unknown: qwen3.5:4b may be slower than qwen2.5:7b, or Ollama is
        genuinely overloaded/unresponsive on this machine
      - Investigate: check Ollama server.log, verify qwen3.5:4b is actually loaded,
        compare inference speed between models
      - Possible fixes: reduce timeout from 60s to 30s (force faster failure + more frequent
        probes), swap back to qwen2.5:7b, or reduce probe interval from 5m to 2m
      - Impact: ALL 6 paper trades placed since Mar 13 used keyword-only or LLM was down;
        only 1 trade (2026-03-19 KXZELENSKYYOUT NO) used keyword-only explicitly

### BUG — Same-Side Duplicate Position Allowed
- [ ] **Duplicate NO on KXZELENSKYYOUT-26APR01** -- Two open NO positions on the same market:
      - 2026-03-13: NO at est=0.440
      - 2026-03-19: NO at est=0.380 (keyword-only: "de-escalation")
      - The same-side guard uses +-2% probability threshold. These are 6% apart so it passed.
      - Decision needed: during paper phase, should we block ANY same-side same-ticker
        regardless of probability delta? Goal is clean signal data, not hedging gains.
      - Recommended fix: add `PAPER_BLOCK_SAME_SIDE_ANY_TICKER = True` flag that blocks
        any duplicate same-side position during paper phase, regardless of prob delta.

### BUG — "Just In News" Feed Serving Stale Content
- [ ] **Stale news waste from "Just In News" source** -- 301 of 740 stale-skipped items were
      6+ hours old; max staleness was 3,923,743s (~45 days). Median staleness 3.4h.
      This feed appears to aggregate old articles and re-serve them as new.
      - Impact: wastes queue processing time; no trades placed but LLM/keyword cycles wasted
      - Recommended fix: add per-feed max-age config; flag feeds that consistently deliver
        stale content; consider removing "Just In News" from the feed list entirely
      - Check: grep `signal_source` in paper_trades.db -- if zero good trades came from
        "Just In News", remove it

### MINOR — Double Market Cache Refresh
- [ ] **Market cache refreshing twice within ~60s** -- 8 occurrences in 48h. Two tasks
      appear to trigger a cache refresh in quick succession (7-54s apart). Each refresh
      fetches all ~9k series from the Kalshi API (~3 min, expensive).
      - Likely cause: scheduled refresh task + a signal-triggered refresh happening
        simultaneously; no mutex on the refresh operation
      - Fix: add a refresh lock/debounce -- if a refresh completed within the last 60s,
        skip the next trigger

### MINOR — service_stderr.log Growing Unbounded
- [ ] **service_stderr.log has no rotation** -- Currently 10MB, contains ~2,511
      pre-fix PermissionError tracebacks (all from before v0.6.2 on Mar 17) plus
      mirrored INFO lines from bot.log. Will grow indefinitely.
      - Fix: configure NSSM AppStdoutRotateBytes / AppStderrRotateBytes, OR pipe stderr
        to a logger with rotation in main.py to avoid the separate file entirely
      - Note: zero PermissionErrors after v0.6.2 fix -- log rotation is working correctly

## Near-Term Backlog

### Signal Quality
- [x] **Priority queue** — swap `asyncio.Queue` for `asyncio.PriorityQueue` in `main.py` ✓ v0.5.7

- [x] **Market snapshot at decision time** — `market_snapshot` JSON column added to `paper_trades` ✓ v0.5.8

### Data Sources
- ~~**Reddit OAuth**~~ — **DROPPED**: Reddit's 2023 API policy requires apps to benefit the Reddit community; a trading bot does not qualify. The 16 blocked subreddits stay 403'd. r/worldnews, r/ukraine, r/geopolitics (public JSON) remain active and provide adequate signal.

### Logging System (deferred -- not blocking go-live)
- [ ] **Date-stamped daily log rotation** -- replace size-based `bot.log.1/.2` with
      time-based `bot-YYYY-MM-DD.log` (one file per day). Instant grep for "what happened
      yesterday"; no guessing which .N backup covers which time window.
      - Use `TimedRotatingFileHandler` with `when='midnight'`, override `doRollover()` with
        the same copy+truncate strategy from `_WindowsSafeRotatingFileHandler` (WinError 32
        still applies on time-based rotate).
      - Retention: keep 90 days, auto-delete older files on rotate.
      - Separate `errors-YYYY-MM-DD.log` at WARNING+ only -- quick triage without grepping
        through full DEBUG output.
      - Emit a startup banner into each new file: VERSION, Ollama model, KALSHI_ENV,
        Python version. Makes post-mortem analysis self-contained per file.
      - `service_stderr.log` (NSSM artifact) stays as-is; no changes needed there.


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
