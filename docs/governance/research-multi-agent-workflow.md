# Research Multi-Agent Workflow

Purpose: prove the real-web research feature is collecting useful evidence without leaking capital or wasting scarce research budget.

Run after deploy or `restartbot`:

```bash
.venv/bin/python scripts/research_multi_agent_workflow.py \
  --home . \
  --trades-log logs/trades \
  --bot-log logs/app/bot.log \
  --window-hours 1
```

Use stricter promotion proof before any production-paper or live unlock:

```bash
.venv/bin/python scripts/research_multi_agent_workflow.py \
  --home . \
  --trades-log logs/trades \
  --bot-log logs/app/bot.log \
  --expected-version 0.33.22 \
  --require-live-cache
```

The workflow is read-only. It does not call research providers, mutate `.env`, write SQLite rows, restart services, or place orders.

## Agent Lanes

| Agent | Protects | Fails When |
| --- | --- | --- |
| `activation` | Prevents unsafe rollout | Research profile is inactive/mismatched or `LIVE_TRADING_ENABLED=true` |
| `signal_flow` | Confirms research path is alive | No recent research rows |
| `prewarm_quality` | Protects research budget | Repeated prewarm spend, synthetic probes, or non-Kalshi tickers enter prewarm |
| `dossier_evidence` | Confirms evidence is durable | Dossier DB missing/unreadable or no fresh evidence |
| `capital_safety` | Protects capital | Live orders appear, or paper trades appear without explicit allowance |
| `rollout_readiness` | Blocks promotion until proof exists | With `--require-live-cache`, no matched trade-candidate proof and live-cache dossier evidence |

## Decision Rule

Treat `PASS` as shadow-path health only unless `--require-live-cache` also passes.

Treat any `FAIL` as a capital-protection issue. Do not relax thresholds without showing how the change increases expected risk-adjusted profit or lowers loss risk.
