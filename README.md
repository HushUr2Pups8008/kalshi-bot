# kalshi-bot

[![Version](https://img.shields.io/badge/version-0.32.2-blue)](CHANGELOG.md) [![Tests](https://img.shields.io/badge/tests-1424%20passing-brightgreen)](tests/) [![Mode](https://img.shields.io/badge/mode-paper-yellow)](#paper-trading)

A 24/7 automated paper/live trading bot for [Kalshi](https://kalshi.com) geopolitical prediction markets.

Monitors RSS news feeds (wire services + government press feeds + regional / OSINT desks), Bluesky journalist timelines, and Reddit for breaking geopolitical events; matches them against open Kalshi markets via the multi-lane decision pipeline (fast / accumulation / structural lanes blended under a market-regime classifier); estimates probability shifts using a local LLM (Ollama `qwen2.5:7b`); evaluates a six-gate readiness contract (G1–G6) plus a twelve-gate executor (E1–E12); and records paper trades. Live trading requires explicit opt-in.

> **Status (2026-05-13): `v0.30.1` operative.** Tag `v0.30.0` was published broken — the P-7 packet inverted the Kalshi `/markets` request-side `status="open"` filter to `status="active"`, producing a `2726`-error `400 bad_request "invalid status filter"` storm on first restart. Hotfix `!14` restored `?status=open` at [`analysis/market_matcher.py`](analysis/market_matcher.py) lines 440 and 490 and shipped as `v0.30.1`. The broken `v0.30.0` tag is retained as the historical anchor; **do not deploy or reference `v0.30.0`**. The two-`status`-contract hazard is canonical in [`CLAUDE.md`](CLAUDE.md) under "Critical Gotchas → Kalshi API". The `v0.30.0` P0-release block immediately below remains an accurate description of the substrate that landed; only the request-side status-filter regression has been corrected by `v0.30.1`.
>
> **Status (2026-05-12 — v0.30.0 P0 release):** **Kalshi API contract stabilization shipped** (`PROFIT-API-001`, merge commit `0a513e4`, tag `v0.30.0`). Closes the 10-packet P0 closure roadmap at [`docs/_archive/governance/2026-05-11-kalshi-api-drift-pricing-correctness-roadmap.md`](docs/_archive/governance/2026-05-11-kalshi-api-drift-pricing-correctness-roadmap.md). Single canonical parse boundary at [`kalshi/normalizer.py`](kalshi/normalizer.py) replaces every `or 50` silent fallback in `kalshi/rest_client.py` and every WS midpoint mutation in `main.py`; `Decimal` + `ROUND_HALF_EVEN` for dollars→cents; `UnsupportedPayloadContractError` raised at parse boundary on unknown shapes; absolute-1 drift halt with manual clearance only. Two-sided executable YES/NO EV via new `SignalAnalysis.executed_price_cents` + `analysis/side_selection.py`; Kelly sizes against the executable side. Exchange-status fail-closed gate (one `rest.get_exchange_status()` per cycle); `analysis/market_matcher.py` filter corrected to `status="open"` for `/markets` requests. Paper-fill writes executable-side ask + `price_method`/`raw_dollars` provenance via custom `_market_to_jsonable` encoder. Replay cohort cut uses `bot_state.p0_price_fix_deployed_ts` ts-sentinel (LD-7, CR-F), not JSONL field presence; no `paper_trades` schema rename/backfill (P-6 adds idempotent provenance columns for forward writes only). Botcheck heartbeat surfaces `kalshi_drift:` + `bot_state:` lines. New `p0_gate` CI job runs sha256 fixture-pinning + the full P0 targeted suite in isolation. **PAPER-ONLY posture preserved**; no live trading enabled by this release. **Operator-visible implication:** pre-v0.30.0 replay verdicts (Cycle-13 through Cycle-16E in §2.1 Edge Verdict) were generated under the broken 50¢-midpoint parser; whether they need re-running under the post-P0 parser against the post-fix cohort is an open operator decision tracked in `PROFIT-API-001`. The pre-P0 −$7.50 / n=3 lifetime paper-trade ledger is preserved as-is (no backfill, LD-8); the POST_FIX_NEW cohort begins at the `bot_state.p0_price_fix_deployed_ts` sentinel.
>
> **Status (2026-05-16):** PROFIT-PHASE2-001 governance shadow-soak is closed. The close tag `phase2-soak-closed` points at commit `6d60b03`; Gate 5 passed on scheduled launchd cadence semantics with 185 scheduled cycles, max gap 2.00834h, 0 fast/deep cadence violations, one documented legacy manual cycle excluded, and one documented phase-reset transition. Gate 6 review was 2360/2360 reasonable and safety counters were 0. `PROFIT-CUTOVER-001` is also closed from live Studio evidence: `paper_trades.db` has resolved post-cutover Studio trades, bankroll continuity remains non-default (`bot_state.notional_bankroll=37.5`), VitalLaw remains `0W/3L/0.5x`, and governance produced 186/186 START/END pairs on the Studio. Active edge work is now `PROFIT-EDGE-012` POST_FIX_NEW readiness under the broader `PROFIT-EDGE-004` root-cause cluster; 2026-05-16 read-only audit is `NOT_READY` (0 post-clean-start rows vs 200-row resume floor).
>
> **Status (2026-05-01):** **Operational host migrated from MacBook to Mac Studio.** All 24/7 paper-mode + governance-agent workloads now run on the Mac Studio; the MacBook is archive-only and will not run live or paper trades again. The MacBook's last paper-mode boot ran v0.29.58 from 2026-04-27T13:03:19Z and was operator-stopped at 2026-05-01T13:05:54Z; the v0.29.58 stack (PROFIT-EDGE-001/002/003 fixes) is the code path the Mac Studio inherits. The 13-day MacBook paper soak (2026-04-18 → 2026-05-01) emitted 260 OPPORTUNITY events, 17 SKIPPED, **3 PAPER_TRADEs** (all on `KXFISAEXTEND-26APR-MAY0{1,2,3}` from VitalLaw.com, 0/3 wins, source-credibility multiplier auto-dropped to 0.5x); 248 EVIDENCE_INGESTION + 248 DOSSIER_UPDATE + 178 STRUCTURAL_PRIOR_RECOMPUTE + 3 CALIBRATION_CHECK events confirm the multi-lane pipeline is fully wired in production. Net: **the bot is no longer architecturally inert** (PROFIT-EDGE-004's "edge=0.0 across all lanes" diagnosis is sharpened — 255/260 OPPORTUNITY records still have `edge=0.0`, but two had **non-trivial positive edge** that produced no trade, which is fresh empirical evidence that the OPPORTUNITY → SKIPPED gap (`PROFIT-OBS-003`) is more material than originally scoped, and the "matcher quality / market-mix" investigation continues on the Studio with full per-event telemetry available in `logs/trades/`). **Governance Agent Phase 2 (shadow-mode) launchd jobs were never bootstrapped on the MacBook;** they were bootstrapped on the Mac Studio on 2026-05-01 at ~14:00 UTC against `qwen3:14b`, then closed on 2026-05-16 under tag `phase2-soak-closed` after §8.5 acceptance passed. The Mac Studio replays the MacBook's `paper_trades.db` (3 trades, 405-source `source_stats` registry, 2,508-row `subreddit_candidates` discovery state, 1-source `source_credibility` graduation entry) and `evidence_store.db` (32 dossiers / 248 dossier_updates / 7,510 dossier_update_evidence rows / 32 structural_priors) via the SQL-dump + restore pattern executed during the 2026-05-01 handoff. The handoff bundle (`transfer/macbook_handoff_2026-05-01/`, 110 MB) was rewritten out of `git` history on 2026-05-02 once the Studio confirmed the restore was clean; the `pre-filter-repo-2026-05-02` tag preserves the pre-rewrite history if the bundle ever needs to be recovered.

See [CLAUDE.md](CLAUDE.md) for project-local agent rules + critical gotchas (Kalshi RSA-PSS signing, websockets-version-dependent header kwarg, market `status="active"`, etc). See [AGENTS.md](AGENTS.md) for the global agent contract.

---

## Where things live

| Topic | Document | Status |
|---|---|---|
| **Active work plan** | [`docs/ROADMAP.md`](docs/ROADMAP.md) | Stages S0–S5; Appendix A (news sources, Tiers 1–2 integrated; Tier 3 deferred); Appendix B (Polymarket dual-venue, blocked on retail waitlist); Appendix C (post-Mac-Studio LLM/feedback backlog) |
| **Unified debt tracking** | [`docs/profit_path_debt_log.md`](docs/profit_path_debt_log.md) | Authoritative item-by-item log (per CLAUDE.md). PROFIT-EDGE-001/002/003 closed 2026-04-26; 2026-05-01 13-day MacBook paper soak summary appended (PROFIT-OBS-003 promoted to HIGH/NOW; PROFIT-EDGE-004 follow-up evidence; PROFIT-OBS-004 + PROFIT-CUTOVER-001 + PROFIT-PHASE2-001 added). |
| **Implementation contract** | [`docs/IMPLEMENTATION_CONTRACT.md`](docs/IMPLEMENTATION_CONTRACT.md) | Binding invariants + boundary rules across `/feeds`, `/analysis`, `/tasks`, `/trading` |
| **Governance agent (Phase 2 closed on Mac Studio)** | [`docs/governance/`](docs/governance/) | Operator manual + Phase 2 runbook (v0.29.55). 14-day soak started 2026-05-01 on Mac Studio (`qwen3:14b`) and closed 2026-05-16 under tag `phase2-soak-closed`. |
| **Simulation harness** | [`scripts/simulations/README.md`](scripts/simulations/README.md) | Captured simulations (G1/G4 calibration, readiness gate, executor) and the seven-harness pipeline buildout (PROFIT-EDGE-004) |
| **Recent dated plans** | [`docs/superpowers/plans/`](docs/superpowers/plans/) | Governance Phase 1/2; simulation buildout |
| **Archive** | [`docs/_archive/`](docs/_archive/) | Closed plans + investigations; not load-bearing |
| **Release history** | [`CHANGELOG.md`](CHANGELOG.md) | Current through v0.32.2 |
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

> **Bringing up a new host (Mac Studio or replacement workstation)?** Read [Mac Studio operational handoff](#mac-studio-operational-handoff-2026-05-01) below first. The historical bundle path was `transfer/macbook_handoff_2026-05-01/`; it now resolves only from the `pre-filter-repo-2026-05-02` tag. For a new migration, recreate that bundle pattern before first bot launch so SQLite state carries across.

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

## Mac Studio operational handoff (2026-05-01)

> **Historical record.** The handoff bundle (`transfer/macbook_handoff_2026-05-01/`, ~110 MB of SQL dumps, log tarballs, and reports) was rewritten out of `git` history on 2026-05-02 once the Studio cutover was confirmed clean. The `pre-filter-repo-2026-05-02` tag preserves the pre-rewrite history if the bundle ever needs to be recovered — `git checkout pre-filter-repo-2026-05-02 -- transfer/` restores it locally without touching `main`. The procedure below is preserved as a reference for any future host migration; expect to re-create the handoff bundle (Studio → next host) using the same pattern.

This section documents the cutover from MacBook to Mac Studio so anyone (or any future agent) coming in cold can re-establish or re-verify the production runtime. **Both hosts must never run the bot concurrently** — the bot's two SQLite stores (`data/paper_trades.db`, `data/evidence_store.db`) are designed for single-writer mode, the source-credibility multipliers diverge under concurrent updates, and Reddit / Kalshi will see the combined external IP signature as a rate-abuse pattern (the CLAUDE.md "Concurrent Mac + Windows instances" gotcha applies equally to two Macs sharing a network).

### Why the cutover happened

- The MacBook's 18 GB unified memory is the hardware ceiling for `qwen3:8b` governance + `qwen2.5:7b` signal-analyzer concurrency. The Mac Studio's larger memory footprint allows the governance LLM to run at `qwen3:14b` (per [`docs/governance/PHASE2_RUNBOOK.md`](docs/governance/PHASE2_RUNBOOK.md) "Model selection (hardware-conditional)"), which is the configuration the Phase 2 spec was designed against.
- Phase 2 governance shadow soak (per [`docs/governance/PHASE2_RUNBOOK.md`](docs/governance/PHASE2_RUNBOOK.md)) requires ≥14 days of clean shadow operation against `qwen3:14b` on Mac Studio. The launchd plists for `com.kalshi.governance.fast` (every 2 h) and `com.kalshi.governance.deep` (daily 09:00) were **never bootstrapped on the MacBook** — the Mac Studio was the intended host all along. Soak clock started 2026-05-01 ~14:00 UTC.
- The MacBook is preserved as an offline analysis workstation. Its data — 13 days of paper soak telemetry — is migrated via the SQL-dump pattern documented below so the Studio inherits the operational history rather than starting fresh.

### What gets carried across

The cutover preserves four bot-state pillars so the Studio's first paper run does not regress on already-learned behaviour:

1. **Paper-trade history.** `data/paper_trades.db.paper_trades` — three rows for `KXFISAEXTEND-26APR-MAY0{1,2,3}`. Lifetime P&L: −$7.50 from a $50 starting bankroll, current `bot_state.notional_bankroll = 42.50`. Dropping these would reset bankroll to the `BANKROLL` env default and erase the only resolved-trade evidence the calibration loop has.
2. **Source credibility graduations.** `data/paper_trades.db.source_credibility` — currently one row: `VitalLaw.com` at 0W/3L, multiplier 0.5×, auto-flagged `no (3/10)`. Without this row the LLM would re-trust VitalLaw at 1.0× and could repeat the same losing entries. The graduation table is the executor's institutional memory.
3. **Source statistics + Reddit discovery state.** `data/paper_trades.db.source_stats` (405 rows) records the lifetime funnel per source — 3,960 posts seen → 249 signals → 249 opportunities → 3 trades. `data/paper_trades.db.subreddit_candidates` (2,508 rows) is the governance-discovery state for adaptive sub-reddit registry expansion (memory note: `project_adaptive_governance_direction`). Both feed the Phase 2 governance agent's decision context.
4. **Dossier + structural-prior corpus.** `data/evidence_store.db` — 32 dossiers, 248 dossier_updates, **7,510 dossier_update_evidence rows**, 32 structural_priors. The structural lane and accumulation lane both depend on this corpus to produce non-uniform priors. A fresh DB would put every market back to the time-to-close fallback and re-invalidate the EDGE-002 categorical-prior fixes.

### Migration mechanics

`.gitignore` excludes `data/*.db` and `logs/` ("too large/personal for version control"). Following the existing `windows_archive/` precedent, the cutover uses **committed SQL dumps + a logs tarball + plaintext reports + a restore script** rather than committing binary DB files or the live log directories. The handoff is structured as three layers so the Studio operator pulls **everything** from the MacBook in a single `git clone` and never returns to the MacBook physically:

> *Note: the markdown links in the three Layers below point to `transfer/macbook_handoff_2026-05-01/`, which was rewritten out of git history on 2026-05-02 (see "Historical record" callout at the top of this section). The links are preserved as a structural reference; the targets resolve only against the `pre-filter-repo-2026-05-02` tag. The `scripts/restore_macbook_handoff.sh` reference at the end of Layer 3 is the only path that resolves on `main`.*

**Layer 1 — Runtime state** (required before first bot launch):
- `transfer/macbook_handoff_2026-05-01/paper_trades.sql` — `sqlite3 .dump` of the entire MacBook `paper_trades.db` (schema + data; 456 KB; restored to `data/paper_trades.db`).
- `transfer/macbook_handoff_2026-05-01/evidence_store.sql` — `sqlite3 .dump` of `evidence_store.db` (1.2 MB; restored to `data/evidence_store.db`).

**Layer 2 — Bulk log archive** (optional extract; canonical for any post-cutover audit):
- `transfer/macbook_handoff_2026-05-01/logs_app_and_trades.tar.gz` — 27 MB compressed (151 MB raw). Contains the entire 13-day `logs/app/` (33 files: bot logs, error logs, daily-report stdout, launchd stdout/stderr) + `logs/trades/` (14 files: JSONL decision-event archive that backs the `PROFIT-EDGE-004` / `PROFIT-OBS-003` / `PROFIT-RUNTIME-001` evidence). Extracts to `mac_archive/macbook_2026-05-01_import/logs/{app,trades}/` — a `.gitignore`d destination, separate from the Studio's own going-forward `logs/` so the archives don't co-mingle. The tarball is the **canonical committed copy**; the extracted tree is regenerable from it.

**Layer 3 — Plain-text reports** (committed directly, no extract step needed):
- `transfer/macbook_handoff_2026-05-01/reports/daily_review/` — 14 daily-review reports as `.txt` files (operator-facing rollups of trade-summary, signal-quality, match-quality, source-credibility, go-live-assessment per UTC day; readable in any editor).
- `transfer/macbook_handoff_2026-05-01/reports/code_review_eval/` — `summary.md` (5.8 KB) and 30 per-test-repo CSVs from the 2026-04-27 `code-review-graph` evaluation harness run.

**Documentation:**
- `transfer/macbook_handoff_2026-05-01/MANIFEST.md` — captured row counts, file hashes, source-machine identity, source date range, restore commands, paste-friendly verification checklist.
- `transfer/macbook_handoff_2026-05-01/PROVENANCE.md` — narrative provenance.

**Restore script:**
- [`scripts/restore_macbook_handoff.sh`](scripts/restore_macbook_handoff.sh) — restore script. Flags: `--force` (overwrite existing `data/*.db`), `--extract-logs` (also extract the Layer 2 tarball), `--extract-logs-only` (skip DB restore, just extract logs), `--dry-run`. Refuses to overwrite without `--force`; verifies post-restore row counts + key values; idempotent. macOS-default bash 3.2 compatible.

### Studio first-launch recipe

On a fresh Mac Studio (after `git clone`):

```bash
# 1. Standard setup — clone, virtualenv, dependencies (see "Setup" above)
git clone https://gitlab.com/HushUr2Pups8008/kalshi-bot.git
cd kalshi-bot
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure .env (Kalshi credentials, BANKROLL, etc.)
cp .env.example .env
$EDITOR .env                       # set KALSHI_API_KEY_*, leave LIVE_TRADING_ENABLED=false

# 3. Pull both Ollama models — Mac Studio gets qwen3:14b for governance
ollama pull qwen2.5:7b             # signal analyzer (unchanged from MacBook)
ollama pull qwen3:14b              # governance agent (Mac Studio model — see PHASE2_RUNBOOK.md "Model selection")

# 4. Restore the MacBook handoff state (BEFORE first bot launch)
#    Use --extract-logs to also expand the bot.log + trades JSONL archive into
#    mac_archive/macbook_2026-05-01_import/logs/. Drop the flag if you only
#    want the DBs restored and intend to consult the tarball later via
#    ./scripts/restore_macbook_handoff.sh --extract-logs-only
./scripts/restore_macbook_handoff.sh --extract-logs

# 5. Edit governance plists for qwen3:14b (per PHASE2_RUNBOOK.md "Model selection")
sed -i '' 's|qwen3:8b|qwen3:14b|g' ops/launchd/com.kalshi.governance.fast.plist
sed -i '' 's|qwen3:8b|qwen3:14b|g' ops/launchd/com.kalshi.governance.deep.plist

# 6. Bootstrap the bot LaunchAgent + governance LaunchAgents
cp ops/launchd/com.kalshi.governance.fast.plist ~/Library/LaunchAgents/
cp ops/launchd/com.kalshi.governance.deep.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.deep.plist
# (Bot LaunchAgent setup follows the same `scripts/setup_launchd.sh` flow as the MacBook.)

# 7. Smoke-test, then verify the soak clock is ticking
launchctl print gui/$(id -u)/com.kalshi.governance.fast | grep -E '(state|last|next)'
.venv/bin/python -m main --report
.venv/bin/python -m main --credibility    # should show VitalLaw.com 0W/3L / 0.5× — confirms restore worked
```

### What the MacBook keeps doing

After cutover, the MacBook is a **decommissioned operational host**. The handoff bundle in `transfer/macbook_handoff_2026-05-01/` carries the full 13-day v0.29.5 → v0.29.58 paper-era state across to the Studio (DBs + logs + reports), so the Studio operator never needs to physically return to the MacBook for any future analysis — `git pull` + `./scripts/restore_macbook_handoff.sh --extract-logs` reconstructs the full evidence base locally on the Studio. The MacBook's local copies of `data/` and `logs/` remain on its disk as a redundant fallback; treat them as superseded by the committed handoff. **Do not start the bot on the MacBook again** — the lockfile-based duplicate-start protection (`data/bot_runtime.lock` via `fcntl.flock`) is local-only and does not coordinate across machines, so a concurrent MacBook + Mac Studio run could corrupt the source-credibility table or trip Kalshi/Reddit IP-rate signals.

If the Mac Studio fails and the MacBook needs to take over temporarily, the failover sequence is:

```bash
# On Mac Studio (if reachable):
launchctl bootout gui/$(id -u)/com.kalshi.governance.fast
launchctl bootout gui/$(id -u)/com.kalshi.governance.deep
launchctl unload ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Reverse-direction handoff: dump Studio dbs to transfer/, commit, push
# Then pull on MacBook and restore via scripts/restore_macbook_handoff.sh

# On MacBook:
git pull
rm -f data/bot_runtime.lock                             # the 2026-04-29 stale lock from pid 793
./scripts/restore_macbook_handoff.sh --force            # restore from latest committed dump
launchctl load ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
```

This is the canonical "single-writer at a time" pattern. The bot is not designed for active-active operation; treat the failover as an explicit cutover, not a load-balanced fallback.

### Pre-cutover housekeeping that did not transfer

A few MacBook-side artifacts are intentionally **not** part of the handoff bundle (see `transfer/macbook_handoff_2026-05-01/MANIFEST.md` "Not included" for the full list with rationale):

- **`data/bot_runtime.lock`** — a runtime-only lockfile from the last MacBook bot session (pid 793, started 2026-04-30T04:00:58Z, now stale by tens of hours). It is `.gitignore`d and self-heals on stale PIDs. The Studio writes its own; do not copy or restore.
- **`data/evidence_store.db-shm` + `-wal`** — SQLite WAL artifacts. Not durable state; SQLite recreates them on first open of the restored `evidence_store.db`. Their contents at handoff time are already inside the SQL dump.
- **`evaluate/test_repos/`** — 172 MB of cloned external git repos (Express, FastAPI, Flask, Gin, httpx, nextjs) used as inputs to the `code-review-graph` evaluation harness. Trivially re-clonable; not bot data. The CSV outputs (Layer 3 of the handoff) are the unique results and ARE included.
- **`logs/coverage/`, `logs/tests/`** — pytest coverage artifacts and per-run pytest output. Regeneratable via `pytest --cov`; not load-bearing for any audit.
- **`.venv/`, `__pycache__/`, `.ruff_cache/`, `.hypothesis/`, `.code-review-graph/`** — derived/cached artifacts. Regenerate on first install / first run on the Studio.
- **`.env`** — secrets file; `.gitignore`d. The Studio operator sets up `.env` independently per `.env.example`.

Everything that *is* load-bearing — the two SQLite databases, the entire 13-day rotated bot.log + JSONL trade-log archive, the daily-review reports, and the code-review-graph eval outputs — ships through the handoff bundle. A fresh `git clone` on the Studio carries it all.

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
