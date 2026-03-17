# Todo
*Current work backlog. Updated each session.*

---

## Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Validate fade signal paper trades — query DB after 2-4 weeks:
      `SELECT substr(reasoning,1,30) tag, count(*) trades, sum(case when pnl>0 then 1 else 0 end) wins FROM paper_trades WHERE reasoning LIKE '[FADE%' GROUP BY tag ORDER BY trades DESC;`
      Compare win rate: [FADE/GEO] vs [FADE/SPORTS], and per-account (@Kalshi vs @Polymarket).
      All categories positive → go live. If sports wins < 50% → disable sports from fade pipeline.
      Self-host RSSHub before going live (public instance may be blocked by X).
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

---

## Signal Quality Bugs (from 2026-03-14 analysis)

- [x] **Multi-position guard** — `trading/executor.py`  ✓ FIXED (commit 9f1d783, 2026-03-16)
  Two failures observed:
  1. Bot took YES and NO on `KXTRUMPIRAN` (opposing signals 24h apart) → costless hedge.
  2. Bot added a THIRD position (2nd NO) on `KXTRUMPIRAN` on Mar 16 — same-signal guard only
     checked the MOST RECENT open trade (the YES), saw delta > 2%, and passed it. The
     existing NO at est=0.444 was ignored. Now holding 3 positions on one ticker.
  Root cause: `get_last_open_trade()` returns only 1 row. Guard needs to check ALL open trades.
  Fix: add `get_all_open_trades(ticker)` to `paper_trader.py`, returning all rows where
  `resolved=0`. In `_validate()`, loop all open trades — if ANY has:
    (a) opposite side → skip ("opposing position exists")
    (b) prob_delta < 0.02 AND price_delta < 2.0 → skip ("same-signal, existing position")
  This replaces the current single-row check entirely.

- [x] **LLM actor disambiguation** — `analysis/signal_analyzer.py` prompt  ✓ FIXED (commit 89e4dc2, 2026-03-16)
  Crown prince trade (2026-03-14): model saw "Iran's exiled crown prince + Trump contact"
  and called `dir=yes` without recognizing the actor has no official standing.
  Fix (low-effort): add to LLM prompt — "Consider whether the named actors have actual
  decision-making power over the market event. Opposition figures, exiles, and unofficial
  contacts should not move the probability."

- [x] **`asyncio not defined` error in RSS callbacks** — `analysis/signal_analyzer.py`  ✓ FIXED (commit 0473154, 2026-03-16)
  Root cause: `asyncio` was not imported in signal_analyzer.py, but `_ollama_estimate()`
  catches `asyncio.TimeoutError`. On restart, Ollama cold-starts (model loads in 10-30s),
  the 60s timeout fires, and the NameError propagated up to poll_feed's catch block.
  Fix: added `import asyncio` to signal_analyzer.py.

---

## Known Bugs (Low Priority)

- [x] **Silent exception swallow on shutdown** — `main.py:322`  ✓ FIXED
  `except Exception: pass` → `except Exception as exc: log.warning("Report generation failed: %s", exc)`

- [x] **`cfg.is_paper_trading` mutability** — `config.py`, `trading/paper_trader.py`  ✓ FIXED
  Added `BotConfig.set_paper_mode(paper: bool)` as the single mutation point.
  `paper_trader.py` now calls `cfg.set_paper_mode()` instead of `cfg.is_paper_trading = ...`.

- [x] **Duplicate LLM parse logic** — `analysis/signal_analyzer.py`  ✓ FIXED
  Extracted `_parse_llm_response(parsed, market)` shared helper.
  Both `_ollama_estimate()` and `_anthropic_estimate()` now call it.

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
