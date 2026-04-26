# kalshi-bot

[![Version](https://img.shields.io/badge/version-0.29.58-blue)](CHANGELOG.md) [![Tests](https://img.shields.io/badge/tests-1383%20passing-brightgreen)](tests/) [![Mode](https://img.shields.io/badge/mode-paper-yellow)](#paper-trading)

A 24/7 automated paper/live trading bot for [Kalshi](https://kalshi.com) geopolitical prediction markets.

Monitors RSS news feeds (wire services + government press feeds + regional / OSINT desks), Bluesky journalist timelines, and Reddit for breaking geopolitical events; matches them against open Kalshi markets via the multi-lane decision pipeline (fast / accumulation / structural lanes blended under a market-regime classifier); estimates probability shifts using a local LLM (Ollama `qwen2.5:7b`); evaluates a six-gate readiness contract (G1–G6) plus a twelve-gate executor (E1–E12); and records paper trades. Live trading requires explicit opt-in.

> **Status (2026-04-26):** Paper-mode operator-stopped pending the v0.29.58 stack restart. The 2026-04-26 PROFIT-EDGE-001/002/003 fix stack lowered G4 (regime confidence) and G1 (scaled confidence) thresholds, added 21 categorical priors for the policy/geopolitical series we engage, and closed the line-688 LLM-positive-but-no-keywords kill. Simulation harness in [`scripts/simulations/`](scripts/simulations/) predicts the readiness + executor gates clear cleanly post-fix. Restart launches the v0.29.58 code path for the first time.

See [CLAUDE.md](CLAUDE.md) for project-local agent rules + critical gotchas (Kalshi RSA-PSS signing, websockets-version-dependent header kwarg, market `status="active"`, etc). See [AGENTS.md](AGENTS.md) for the global agent contract.

---

## Where things live

| Topic | Document | Status |
|---|---|---|
| **Active work plan** | [`docs/ROADMAP.md`](docs/ROADMAP.md) | Stages S0–S5; Appendix A (news sources, Tiers 1–2 integrated; Tier 3 deferred); Appendix B (Polymarket dual-venue, blocked on retail waitlist); Appendix C (post-Mac-Studio LLM/feedback backlog) |
| **Unified debt tracking** | [`docs/profit_path_debt_log.md`](docs/profit_path_debt_log.md) | Authoritative item-by-item log (per CLAUDE.md). PROFIT-EDGE-001/002/003 closed 2026-04-26. |
| **Implementation contract** | [`docs/IMPLEMENTATION_CONTRACT.md`](docs/IMPLEMENTATION_CONTRACT.md) | Binding invariants + boundary rules across `/feeds`, `/analysis`, `/tasks`, `/trading` |
| **Governance agent (Phase 2 in flight)** | [`docs/governance/`](docs/governance/) | Operator manual + Phase 2 runbook (v0.29.55). Shadow-mode for ≥14 days. |
| **Simulation harness** | [`scripts/simulations/README.md`](scripts/simulations/README.md) | Three captured simulations (G1/G4 calibration, readiness gate, executor); buildout plan for seven more in [`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md) |
| **Recent dated plans** | [`docs/superpowers/plans/`](docs/superpowers/plans/) | Governance Phase 1/2; simulation buildout |
| **Archive** | [`docs/_archive/`](docs/_archive/) | Closed plans + investigations; not load-bearing |
| **Release history** | [`CHANGELOG.md`](CHANGELOG.md) | Current through v0.29.58 |
| **Platform matrix** | [`PLATFORMS.md`](PLATFORMS.md) | Windows / macOS / Linux support notes |

---

## How It Works

1. **News ingestion** — RSS (wire services + Tier 1 government press feeds + Tier 2 regional / OSINT desks; full active set in [`config.py`](config.py)), Bluesky journalist timelines, and Reddit (degraded per `PROFIT-SOURCE-001`) are polled continuously. Cross-source dedup suppresses near-identical stories within a 15-minute TTL window.
2. **Queue + consumer** — New items are placed on a bounded async queue (non-blocking). A single consumer drains the queue, preventing feed pollers from stalling during LLM inference.
3. **Market matching** — Each headline is matched against cached open Kalshi markets using Jaccard token similarity with a geopolitical keyword boost. The pre-LLM gate is currently disabled (`ENABLE_PRE_LLM_MATCH_GATE=false`, diagnostics-only).
4. **Signal analysis** — `qwen2.5:7b` via Ollama produces categorical output (relevance, novelty, direction, magnitude, confidence). Code maps magnitude → deterministic probability shifts (small=8pp, moderate=15pp, large=25pp), scaled by confidence.
5. **Multi-lane blend** — The fast lane (LLM signal), accumulation lane (dossier-tracked evidence history), and structural lane (long-horizon prior) are combined by [`analysis/decision_blender.py`](analysis/decision_blender.py) under per-market regime weights from the regime classifier. The blend produces `blended_p`, `blended_confidence`, and `disagreement_score`.
6. **Readiness gate (G1–G6)** — [`tasks/trade_readiness_gate.py`](tasks/trade_readiness_gate.py) enforces six gates: scaled-confidence floor (G1), source-class diversity (G2), lane-disagreement ceiling (G3), regime-confidence floor (G4), drift-suspect block (G5), recency floor (G6). Fast-lane-only candidates are exempt from G2/G5/G6.
7. **Executor (E1–E12)** — [`trading/executor.py:_validate`](trading/executor.py) enforces twelve additional gates including PAPER_MIN_EDGE (0.02), price boundaries, the 4-hour ticker cooldown, opposing-position guard, same-signal guard, and concentration limits. Paper mode records to SQLite; live mode requires `--go-live` + typed `CONFIRM`.
8. **Fade signal** — Separately monitors @Kalshi / @Polymarket / @PolymarketMoney tweets via RSSHub. Detects hype/ATH language and fades it: bullish tweet → buy NO. Pattern-matching only — no LLM call.

The full readiness/executor gate calibration (PROFIT-EDGE-002 / EDGE-003) is documented inline in [`tasks/trade_readiness_gate.py`](tasks/trade_readiness_gate.py) with the empirical production data that motivated each threshold.

---

## Architecture

```
main.py                              — Async entry point; ~10 concurrent tasks
  feeds/
    rss_monitor.py                   — Wire services + Tier 1/2 government & regional press
    reddit_monitor.py                — Public-JSON polling (degraded per PROFIT-SOURCE-001)
    bluesky_monitor.py               — Journalist timeline (replaces Reddit firsthand content)
    search_news_monitor.py           — Per-market query lane (currently disabled)
    dedup.py                         — Cross-source headline dedup (rapidfuzz, 15-min TTL)
  analysis/
    signal_analyzer.py               — LLM + keyword probability estimation
    market_matcher.py                — Jaccard market match + sport-prefix blocklist
    decision_blender.py              — Three-lane blend (fast / accumulation / structural)
    regime_classifier.py             — Series-prefix → regime weight tuple (40+ priors)
    structural_prior.py              — Pure structural prior synthesis (S3.1)
    kelly.py                         — Half-Kelly bet sizing
    source_credibility.py            — Per-source win/loss multiplier (0.5–1.5x)
  tasks/
    blend_task.py                    — process_fast_lane_result(): blend + readiness gate + queue
    structural_task.py               — Periodic structural-prior recompute
    trade_readiness_gate.py          — Stateless G1–G6 readiness contract
    evidence_store.py                — SQLite-backed dossier + evidence + structural priors
  trading/
    executor.py                      — TradeExecutor: E1–E12 validation, paper/live routing
    paper_trader.py                  — SQLite paper trading engine + bot_state + bankroll
  governance/                        — Phase 2 LLM governance agent (shadow mode)
    agent.py                         — `python -m governance --cadence fast|deep|weekly_review`
    adapter.py                       — KalshiGovernanceAdapter Protocol seam
    decision.py                      — Decision dataclass + audit/override converters
    llm.py                           — LLMClient Protocol + LocalQwenLLM + FakeLLM
  kalshi/
    rest_client.py                   — Kalshi REST API (RSA-PSS auth)
    websocket_client.py              — Real-time price feed + auth-upgrade
  scripts/
    simulations/                     — Behavioural simulation harness (G1/G4/executor)
    daily_review.py                  — Daily performance + signal-quality report
    setup_launchd.sh                 — macOS LaunchAgent installer
  config.py                          — Configuration, env var bindings, source registries
```

**Concurrent tasks (paper mode):**

| Task | Role |
|---|---|
| `rss_monitor` | Polls RSS feeds, enqueues new items |
| `reddit_monitor` | Polls Reddit (degraded mode without OAuth) |
| `bluesky_monitor` | Polls Bluesky journalist firehose |
| `news_consumer` | Drains queue → match → LLM → blend → readiness → trade queue |
| `trading_queue_consumer` | Drains trade queue → executor.execute |
| `websocket` | Real-time Kalshi price feed |
| `market_refresh` | Refreshes geo market cache every 30 min |
| `structural_recompute` | Periodic structural-prior recompute |
| `daily_report` | Writes performance report every 24 h |
| `fade_tweets` | *(conditional)* Polls @Kalshi/@Polymarket RSSHub feeds |

**State:** `data/paper_trades.db` (paper trades + bankroll + source credibility) and `data/evidence_store.db` (dossiers + evidence + structural priors).

---

## Requirements

- Python 3.14+ (pinned to **3.14.4** in production; `aiohttp >= 3.10.0` required for cp314 wheel — see CLAUDE.md gotcha)
- [Ollama](https://ollama.com) with `qwen2.5:7b` (signal analyzer) and `qwen3:8b` (governance agent) pulled
- Kalshi account with API key (RSA key pair)

---

## Setup

### 1. Clone and create virtualenv

```bash
git clone https://gitlab.com/HushUr2Pups8008/kalshi-bot.git
cd kalshi-bot
python3.14 --version           # confirm 3.14+
python3.14 -m venv .venv
source .venv/bin/activate      # macOS/Linux
pip install -r requirements.txt
```

### 2. Dev/test environment

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest               # 1383 tests, ~5 s
```

For long-running regression runs use the safe wrapper so output and run metadata survive terminal or VS Code crashes:

```bash
scripts/run_tests.sh                  # attached, timestamped log
scripts/run_tests.sh --detach         # keeps running if the editor exits
scripts/run_tests.sh tests -q         # pass pytest args
tail -n 80 -f logs/tests/pytest_*.log
scripts/show_run_registry.py --limit 10
scripts/show_run_registry.py --failed
```

Each run writes `logs/tests/pytest_YYYYMMDD_HHMMSS.log` plus a JSON metadata file with start/end time, exit status, git commit, and command. Runs are appended to `logs/tests/run_registry.jsonl` for history lookups.

### 3. Configure `.env`

```bash
cp .env.example .env
```

Edit with Kalshi credentials, bankroll, and trading parameters. `LIVE_TRADING_ENABLED=false` is the hard kill-switch — even with `go_live_confirmed=true` in the DB, the bot stays in paper mode unless this env var explicitly flips. See `.env.example` for all options.

> **CLAUDE.md gotcha:** the `KALSHI_API_KEY_SECRET` PEM key is stored as a single line with literal `\n` sequences (Windows `.env` cannot store multi-line values reliably). `_normalize_pem()` in [`kalshi/rest_client.py`](kalshi/rest_client.py) and [`kalshi/websocket_client.py`](kalshi/websocket_client.py) converts `\n` → real newlines before loading. Do not change.

### 4. Install Ollama and pull both models

```bash
# Download from https://ollama.com/download
ollama pull qwen2.5:7b           # signal analyzer
ollama pull qwen3:8b             # governance agent
```

### 5. Run

```bash
.venv/bin/python main.py                   # paper trading mode (default)
.venv/bin/python -m main --report          # print performance report
.venv/bin/python -m main --credibility     # print source credibility table
.venv/bin/python -m main --resolve TICKER YES   # manually resolve a paper trade
.venv/bin/python -m main --go-live         # interactive prompt to enable live trading
```

`data/bot_runtime.lock` is a runtime-only lockfile used for duplicate-start protection (file-handle locked via `fcntl.flock`; self-healing on stale PIDs). It is recreated automatically and should never be committed.

---

## macOS LaunchAgent (24/7 unattended)

The canonical 24/7 startup path is via a per-user LaunchAgent. Plist locations:

| Plist | Label | Trigger |
|---|---|---|
| `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` | `com.jake.kalshi-bot` | RunAtLoad + KeepAlive on non-success exit |
| `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist` | `com.kalshibot.dailyreview` | Daily 09:00 |
| `ops/launchd/com.kalshi.governance.fast.plist` | `com.kalshi.governance.fast` | Every 2 h (when symlinked into `~/Library/LaunchAgents/`) |
| `ops/launchd/com.kalshi.governance.deep.plist` | `com.kalshi.governance.deep` | Daily 09:00 |

Bot plist invocation: `caffeinate -dimsu .venv/bin/python main.py` (caffeinate prevents the system from sleeping while the bot runs).

### Control commands

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Verify
launchctl list | grep kalshi
ps aux | grep -i "python.*kalshi_bot/main" | grep -v grep
tail -f logs/app/bot.log
```

Installation is automated via `scripts/setup_launchd.sh`. Before first launch, ensure Ollama is running (it autostarts on macOS via the Ollama tray app).

---

## Multi-lane decision pipeline

The bot uses a three-lane signal model:

1. **Fast lane** — LLM signal on the immediate news event. Latency-sensitive.
2. **Accumulation lane** — dossier-tracked evidence history aggregated over time. Reduces single-event noise.
3. **Structural lane** — long-horizon prior synthesised from market metadata + accumulated evidence + optional LLM synthesis.

Each lane outputs `(p, confidence)`. The blender weights effective lane confidence by per-market regime weights (`{fast, interpretation, structural}` summing to 1.0) produced by [`analysis/regime_classifier.py`](analysis/regime_classifier.py). Categorical priors cover sports, polling, central-bank, crypto, weather, entertainment, Trump-say markets, and 21 policy/geopolitical event series added in PROFIT-EDGE-002. Markets without a categorical prior fall through to a time-to-close fallback.

The readiness gate then enforces six conditions before a candidate reaches the executor:

| Gate | Threshold | Notes |
|---|---|---|
| **G1** scaled confidence | `blended_conf × regime_conf ≥ 0.05` (failsafe 0.10) | Recalibrated 2026-04-26 from production P90 distribution |
| **G2** source class diversity | ≥ 2 distinct source classes | Dossier-only |
| **G3** lane disagreement | `disagreement_score ≤ 0.20` (failsafe 0.15) | Mid-band override raises `min_edge` 1.5× |
| **G4** regime confidence | `regime_conf ≥ 0.20` | Recalibrated 2026-04-26; same data-driven approach as G1 |
| **G5** dossier drift | `drift_suspect=False` (or `in_recovery=True`) | Dossier-only |
| **G6** recency | `recency_score ≥ 0.30` | Dossier-only |

Inline rationale + production data tables for the EDGE-002 (G4) and EDGE-003 (G1) calibrations live at the constants in [`tasks/trade_readiness_gate.py`](tasks/trade_readiness_gate.py).

---

## Behavioural simulations

The pipeline has a permanent simulation harness at [`scripts/simulations/`](scripts/simulations/). Three simulations are captured:

| Simulation | Pipeline stage |
|---|---|
| `threshold_calibration.py` | G4 priors audit + G1 production `scaled_confidence` distribution sweep |
| `readiness_gate_events.py` | Readiness gate end-to-end against the 5 canonical LLM-positive events from PROFIT-EDGE-001 |
| `executor_validate.py` | Executor `_validate()` (E1–E12) against the same 5 events, in independent + sequential passes |

Smoke tests in [`tests/test_simulations_smoke.py`](tests/test_simulations_smoke.py) (13 cases) keep the harnesses themselves green under code changes. Read-only — never mutate `paper_trades.db`, `evidence_store.db`, or the trade-log archive. Safe to run while the bot is active. Plan for the remaining seven pipeline-stage simulations: [`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md).

---

## LLM Probability Estimation

Signal quality priority:

1. **Ollama** (local, free) — `qwen2.5:7b` via `http://localhost:11434/v1`
2. **Anthropic Claude Haiku** (paid fallback) — set `ANTHROPIC_API_KEY` in `.env`
3. **Keyword scoring** (always available) — deterministic fallback, no external calls

The LLM answers categorical questions (relevant? new information? direction? magnitude?) rather than outputting a raw probability. Code maps magnitude to deterministic shifts (small=8pp, moderate=15pp, large=25pp), scaled by confidence. Keywords serve as a match gate when the LLM is silent (i.e. `magnitude="none"`); per the PROFIT-EDGE-001 fix, an LLM-emitted directional signal with empty keywords now passes through (was killed pre-fix at `main.py:688`).

LLM calls are serialised via `asyncio.Semaphore(1)` — only one Ollama call runs at a time to avoid CPU latency spikes from concurrent inference.

---

## Fade Signal

When @Kalshi or @Polymarket tweet "BREAKING", "all-time high", or similar hype language, the market is often overpriced from retail attention. The bot fades these signals: bullish tweet → buy NO.

Configure via `.env`:
```
FADE_TWEET_FEED_URLS=https://rsshub.app/twitter/user/Kalshi,https://rsshub.app/twitter/user/Polymarket
```

Requires a running [RSSHub](https://rsshub.app) instance (self-hosted recommended for production — public instances can be rate-limited by X).

---

## Paper Trading

Paper mode is active by default and is the hard kill-switch path: even if `go_live_confirmed=true` is set in `bot_state`, the bot stays in paper mode unless `LIVE_TRADING_ENABLED=true` in `.env`. Trades are recorded to `data/paper_trades.db` with full reasoning. The notional bankroll grows/shrinks as markets resolve — `bot_state.notional_bankroll` is the runtime-tracked authoritative value (initialised from `BANKROLL` only on first run).

```bash
# Wipe paper trade history and start fresh
rm data/paper_trades.db

# Reset bankroll without wiping trade history
.venv/bin/python -c "import sqlite3; db=sqlite3.connect('data/paper_trades.db'); db.execute(\"UPDATE bot_state SET value='50.00' WHERE key='notional_bankroll'\"); db.commit()"
```

---

## Live Trading

Live trading is disabled until **two** explicit unlocks:

1. `LIVE_TRADING_ENABLED=true` in `.env` (env-level kill-switch).
2. `python main.py --go-live` interactive flow, typing `CONFIRM` (DB-level confirmation).

Without both, the bot stays in paper mode regardless of any other state. Live mode adds tighter edge thresholds (`MIN_EDGE` defaults 0.04 vs paper's 0.02), a live balance check before each order via the Kalshi REST API, a per-ticker cooldown of 10 minutes, and a session loss limit (`LIVE_LOSS_LIMIT_PERCENT` default 0.10) that halts all trading on excessive drawdown.

---

## Key Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `KALSHI_ENV` | `demo` | `demo` or `prod` |
| `KALSHI_API_KEY_ID` | _(required)_ | Kalshi API key UUID |
| `KALSHI_API_KEY_SECRET` | _(required)_ | RSA private key PEM (single-line, literal `\n` sequences) |
| `LIVE_TRADING_ENABLED` | `false` | Hard kill-switch for live trading |
| `BANKROLL` | `50.00` | Initial paper bankroll (`bot_state.notional_bankroll` after first run) |
| `MAX_BET_HARD_CAP` | `25.00` | Hard ceiling per bet in dollars (CLAUDE.md gotcha: this is the env name, **not** `MAX_BET_DOLLARS`) |
| `MAX_BET_PCT_BANKROLL` | `0.15` | Max bet as % of notional bankroll |
| `MAX_TICKER_EXPOSURE_PCT` | `0.25` | Per-ticker exposure cap |
| `KELLY_FRACTION` | `0.5` | Kelly fraction (0.5 = half-Kelly) |
| `MIN_EDGE` | `0.04` | Minimum edge for a live trade (paper uses `PAPER_MIN_EDGE=0.02`) |
| `LIVE_LOSS_LIMIT_PERCENT` | `0.10` | Live session-loss halt threshold |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Signal analyzer model |
| `GOVERNANCE_LLM_MODEL` | `qwen3:8b` | Governance agent model (set in launchd plist `EnvironmentVariables`) |
| `MAX_NEWS_AGE_SECONDS` | `1800` | Max age of a queued news item before skipping (30 min) |
| `EARLY_MAX_NEWS_AGE_SECONDS` | `3000` | Early-stale-detection threshold (50 min) |
| `ANTHROPIC_API_KEY` | _(unset)_ | Enables Claude Haiku fallback |
| `FADE_TWEET_FEED_URLS` | _(unset)_ | Comma-separated RSSHub URLs for fade signal |
| `ENABLE_PRE_LLM_MATCH_GATE` | `false` | Pre-LLM match gate (currently disabled by design) |
| `PRE_LLM_MATCH_GATE_DIAGNOSTICS_ONLY` | `true` | Diagnostics-only mode for the pre-LLM gate |

Full env reference: [`config.py`](config.py).

---

## Git Workflow

Default repo workflow (per [AGENTS.md](AGENTS.md) + `~/.claude/rules/git_workflow.md`):

- Review first: run `git status`, `git diff`, and `git diff --staged`.
- Stage intentionally by logical change group, not with blind `git add .`.
- For `VERSION`-shifting changes, bump `VERSION` and `CHANGELOG.md` in the same commit.
- Keep commits understandable and reversible; split unrelated work into multiple commits.
- Run relevant validation/tests before push.
- Confirm a clean working tree and sensible commit history before pushing.
