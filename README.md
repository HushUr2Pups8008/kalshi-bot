# kalshi-bot

[![Version](https://img.shields.io/badge/version-0.33.6-blue)](CHANGELOG.md)
[![Mode](https://img.shields.io/badge/default-paper%20trading-orange)](.env.example)

Automated Kalshi paper-trading research bot for geopolitical and US-political
prediction markets. The bot ingests news, matches headlines to open Kalshi
markets, asks a local LLM for a directional probability estimate, blends that
signal with regime priors and evidence stores, and routes only eligible
candidates through the paper/live executor gates.

The project is currently operated in paper mode. Live trading remains gated by
configuration, paper-readiness evidence, and operator approval.

## Current State

As of `v0.33.0`:

- Runtime host: Mac Studio via macOS `launchd`.
- Default mode: paper trading.
- Current Track B increment: Reddit ingestion is disabled by default
  (`REDDIT_ENABLED=false`) because Reddit app approval was denied and no OAuth
  path exists.
- Replacement-feed work: B3.1 high-yield publisher desks are live:
  NYT Politics, NYT US, The Hill News, The Hill Senate, and Guardian US.
- Current readiness posture: the bot can reach the paper-trade path, but live
  readiness remains blocked by a structurally small edge surface and low
  realized opportunity throughput. The next readout is opportunity/day over the
  `v0.33.0` soak.

Canonical project tracking lives in
[docs/profit_path_debt_log.md](docs/profit_path_debt_log.md). Do not create new
parallel tracking docs unless the repo rules explicitly allow it.

## System Overview

1. **Ingest news**
   - RSS feeds from wire services, government and policy sources, regional
     desks, OSINT-style sources, and the B3.1 publisher desks.
   - Bluesky journalist timelines where configured.
   - Reddit code remains present but is disabled by default.

2. **Normalize and filter**
   - Freshness gates reject stale backfill.
   - Low-quality match suppression removes minimal-overlap and single-entity
     false positives.

3. **Match markets**
   - Headlines are matched against cached open Kalshi markets.
   - Candidate markets pass through match-score and relevance filters before
     LLM analysis.

4. **Analyze direction**
   - A local Ollama-backed LLM estimates event direction and confidence.
   - Evidence is recorded for later replay, calibration, and readiness checks.

5. **Blend and gate**
   - Decision blending combines fast, interpretation, and structural lanes.
   - Readiness gates validate confidence, regime support, edge, market state,
     cooldowns, and exposure.

6. **Execute**
   - Paper mode writes simulated trades and state to local SQLite/log files.
   - Live mode is disabled unless `LIVE_TRADING_ENABLED=true` and the go-live
     gates are explicitly satisfied.

## Repository Map

| Path | Purpose |
| --- | --- |
| [main.py](main.py) | Runtime orchestrator, async tasks, signal pipeline |
| [config.py](config.py) | Feed lists, thresholds, env-backed config |
| [feeds/](feeds/) | RSS, Bluesky, Reddit, search/news monitors |
| [analysis/](analysis/) | LLM parsing, regime classification, blending |
| [trading/](trading/) | Executor, paper trader, risk and exposure checks |
| [kalshi/](kalshi/) | Kalshi API client and normalization helpers |
| [tasks/](tasks/) | Daily reports, stats, health, replay, aggregators |
| [scripts/](scripts/) | Operational scripts, launchd setup, migrations |
| [governance/](governance/) | Governance agent and review adapters |
| [tests/](tests/) | Unit, integration, replay, shell, and contract tests |
| [docs/](docs/) | Canonical planning, debt log, runbooks, archived studies |
| [.github/workflows/](.github/workflows/) | CI and replay gate workflows |

## Outputs And State

The output contract was consolidated so runtime artifacts have a single home.
Use `KALSHI_OUTPUT_ROOT` to override the root. `KALSHI_LOG_ROOT` remains a
backward-compatible alias.

| Path | Contents |
| --- | --- |
| `logs/app/` | Runtime bot logs and errors |
| `logs/reports/` | Daily, health, performance, and evaluation reports |
| `logs/state/` | Runtime state snapshots and derived state |
| `logs/backups/` | Database snapshots and backup artifacts |
| `logs/tests/` | Timestamped test logs and run metadata |
| `data/paper_trades.db` | Paper trades, bankroll, state, source stats |
| `data/evidence_store.db` | Dossiers, evidence, structural priors |
| `data/matcher_token_weights.json` | Live matcher-weight state; treat as runtime churn |

Do not commit runtime DBs, logs, backups, state directories, or live matcher
weight churn unless a task explicitly asks for that artifact.

## Requirements

- macOS for the managed LaunchAgent deployment path.
- Python 3.11, matching `.python-version` and the repo tooling config.
- Ollama running locally.
- Ollama model for signal analysis, default `qwen2.5:7b`.
- Kalshi API credentials for authenticated market access.
- Optional governance model, currently documented in
  [docs/governance/](docs/governance/).

## Setup

```bash
git clone https://github.com/HushUr2Pups8008/kalshi-bot.git
cd kalshi-bot

python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Copy and edit the environment template:

```bash
cp .env.example .env
$EDITOR .env
```

Minimum local configuration:

```dotenv
KALSHI_ENV=demo
KALSHI_API_KEY_ID=...
KALSHI_API_KEY_SECRET=-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----
BANKROLL=50.00
LIVE_TRADING_ENABLED=false
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen2.5:7b
REDDIT_ENABLED=false
```

The RSA private key is stored as one line with literal `\n` sequences. This is
intentional and keeps `.env` portable across macOS and Windows editors.

Install/pull local LLM models:

```bash
ollama pull qwen2.5:7b
```

Governance runs may require an additional model; see
[docs/governance/PHASE2_RUNBOOK.md](docs/governance/PHASE2_RUNBOOK.md).

## Run Locally

Paper mode is the default:

```bash
.venv/bin/python main.py
```

Manual paper-trade helpers:

```bash
.venv/bin/python -m main --resolve TICKER YES
.venv/bin/python -m main --go-live
```

`--go-live` is still subject to config and readiness gates. Do not treat it as
a shortcut around operator approval.

## macOS LaunchAgent Operation

The production paper runtime is managed by `launchd`.

Install or refresh LaunchAgents:

```bash
scripts/setup_launchd.sh
```

Main bot controls:

```bash
# Start or restart after code changes
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Stop and hold down for maintenance
launchctl bootout gui/501 ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# Inspect
launchctl print gui/501/com.jake.kalshi-bot
```

Common helper checks:

```bash
make botcheck
tail -f logs/app/bot.log
tail -f logs/app/errors.log
```

Do not restart the service for docs-only changes. Restart only when deploying
runtime code or config changes that need to be loaded by the running process.

## Tests And Checks

Fast local checks:

```bash
make lint
.venv/bin/pytest
```

Useful focused commands:

```bash
make botcheck
make decision-funnel
make trade-summary
make freshness
make pipeline-impact
make governance-monitor
```

CI runs the standard test suite plus the p0, replay, and simulation smoke gates.
Some tests are intentionally xfailed for known future gates. A dirty
`data/matcher_token_weights.json` can also appear during live operation; keep it
out of code commits unless the task is specifically about matcher-weight state.

## Current Operational Notes

- `PROFIT-SOURCE-001`: Reddit is permanently unavailable for planning purposes.
  Runtime polling is disabled by default in `v0.33.0`; source-health reporting
  still needs explicit "disabled/permanently unavailable" classification.
- `PROFIT-THRUPUT-001`: The immediate throughput work is source replacement and
  edge-surface expansion without loosening safety gates.
- `PROFIT-BLENDER-002`: The accumulation-lane regime-key defect was fixed and
  deployed before `v0.33.0`; the inert-0.5 dossier follow-up remains separate.
- B2 market-driven retrieval remains the deeper deferred lever and should be
  replay-EV-gated before it changes signal-generating behavior.

## Safety Rules

- Default to paper mode. Keep `LIVE_TRADING_ENABLED=false` unless the operator
  explicitly approves a live cutover.
- Do not lower readiness gates or freshness/match thresholds just to create
  volume.
- Treat signal-generating ingestion, executor logic, sizing, bankroll, launchd,
  DB mutation, and paper/live transitions as high-risk changes requiring PR
  review and operator approval.
- Keep runtime state out of normal code commits.
- Use [docs/profit_path_debt_log.md](docs/profit_path_debt_log.md) for active
  project tracking.

## Git Workflow

`main` is protected. Normal landing path:

1. Create a focused branch.
2. Commit only the intended files.
3. Open a PR with summary and validation.
4. Wait for CI.
5. Merge through GitHub.
6. Fast-forward local `main`.
7. Restart only if runtime code/config changed and operator approves.

Docs-only changes do not require a bot restart.
