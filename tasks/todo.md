# Todo
*Current work backlog. Updated each session.*

---

## Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Investigate "fade the Kalshi tweet" signal (see lessons.md) — assess before going live
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

---

## Signal Quality Bugs (from 2026-03-14 analysis)

- [ ] **Multi-position guard** — `trading/executor.py`  ← CONFIRMED BUG (2026-03-16)
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

- [ ] **LLM actor disambiguation** — `analysis/signal_analyzer.py` prompt
  Crown prince trade (2026-03-14): model saw "Iran's exiled crown prince + Trump contact"
  and called `dir=yes` without recognizing the actor has no official standing.
  Fix (low-effort): add to LLM prompt — "Consider whether the named actors have actual
  decision-making power over the market event. Opposition figures, exiles, and unofficial
  contacts should not move the probability."

- [ ] **`asyncio not defined` error in RSS callbacks** — `feeds/rss_monitor.py`
  23 occurrences confirmed. Pattern: always at service restarts (4–8 errors/restart for same
  item hashes — these are headlines in-flight at shutdown that can't complete their callback).
  Likely a callback closure that references `asyncio` from an outer scope that's torn down
  during NSSM service restart before the callback fires.
  Fix: grep for bare `asyncio.` calls in rss_monitor.py, confirm import is at module level
  AND that callbacks don't close over the event loop reference.

---

## Known Bugs (Low Priority)

- [ ] **Silent exception swallow on shutdown** — `main.py:322`
  `except Exception: pass` silently drops report generation errors at shutdown.
  Fix: `log.warning("Report generation failed: %s", exc)`

- [ ] **`cfg.is_paper_trading` mutability** — `config.py`, `trading/paper_trader.py`
  Global singleton mutated directly during runtime; no async locking.
  Low risk (only set at startup) but architecturally unsound.

- [ ] **Duplicate LLM parse logic** — `analysis/signal_analyzer.py`
  `_ollama_estimate()` and `_anthropic_estimate()` have identical direction/magnitude →
  probability math written twice.
  Fix: extract into shared `_parse_llm_response(parsed, market)` helper.

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
