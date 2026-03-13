# Todo
*Current work backlog. Updated each session.*

---

## Go-Live Prerequisites
- [ ] Accumulate valid paper trades on geopolitical markets (not sports)
- [ ] Review paper trade performance — confirm positive edge
- [ ] Run `python main.py --go-live` and type `CONFIRM` at prompt
- [ ] Verify Kalshi API key is current and account is funded

**Critical:** Mac and Windows share the same Kalshi API key. Only ONE instance goes live
at a time. Confirm Windows service is stopped before going live on Mac (or vice versa).

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
