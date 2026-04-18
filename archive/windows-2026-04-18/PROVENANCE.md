# Windows Historical Archive — Provenance Record

## Origin

| Field              | Value                                                     |
|--------------------|-----------------------------------------------------------|
| Source machine     | Windows environment (pre-Mac migration, NSSM-managed service) |
| Import date        | 2026-04-18                                                |
| Data coverage      | March 14, 2026 through April 17, 2026                     |
| Archive file       | `transfer/decision-support-sync-2026-04-18.tar.gz`        |
| Extracted by       | Claude Code, 2026-04-18, per operator instruction         |

## Purpose

This archive is a **read-only historical reference** for:

- Diagnostics
- Performance review
- Source evaluation
- Signal quality review
- Decision-support analysis

**This data is NOT used by the live bot.** No runtime code path reads from this
directory. All bot execution uses the live paths under `data/` and `logs/`.

## Directory Contents

```
archive/windows-2026-04-18/
  data/
    paper_trades.db           SQLite DB: trades, source credibility, source stats,
                              keyword outcomes, bot state — Windows-era snapshot
  logs/
    app/
      bot.log                 Windows "current" app log at sync time
      bot.log.2026-03-23      Daily-rotated app logs, Mar 23 – Apr 17
      bot.log.YYYY-MM-DD      (26 rotation files)
      errors.log              Windows "current" error log at sync time
      errors.log.YYYY-MM-DD   Daily-rotated error logs, Mar 23 – Apr 17
    service/
      ollama_stderr.log       Ollama service stderr (Windows NSSM)
      ollama_stdout.log       Ollama service stdout
      service_stderr-*.log    NSSM bot service rotation logs (Mar 22 – Apr 11)
      service_stderr.log      NSSM bot service current at sync time
      service_stdout.log      NSSM bot service stdout
    trades/
      live/
        trades.jsonl          Windows "current" trade JSONL at sync time
      archive/
        trades-202604.jsonl.gz  Compressed April 2026 monthly archive
        2026/04/
          2026-04-16.jsonl    Archived daily trade log (Apr 16)
          2026-04-17.jsonl    Archived daily trade log (Apr 17)
      trades.jsonl            Legacy pre-restructure monolithic trade log
    reports/
      report_20260314.txt     Older-format daily reports, Mar 14 – Apr 15
      report_YYYYMMDD.txt     (approx 20 files)
      analysis_YYYYMMDD_HHmm.txt  Ad-hoc analysis reports, Apr 6 – Apr 11
      daily_review_YYYYMMDD.txt   daily_review-format reports, Apr 11 – Apr 17
    service_stdout.log        Top-level NSSM stdout captured at sync time
```

## Relationship to Live Data

| | Live (Mac) | Archive (Windows) |
|---|---|---|
| Database | `data/paper_trades.db` | `archive/windows-2026-04-18/data/paper_trades.db` |
| Trade log | `logs/trades/live/trades.jsonl` | `archive/windows-2026-04-18/logs/trades/live/trades.jsonl` |
| App log | `logs/app/bot.log` | `archive/windows-2026-04-18/logs/app/bot.log` |
| Reports | `logs/reports/` | `archive/windows-2026-04-18/logs/reports/` |

These are distinct datasets. Never mix archive and live paths in the same analysis run.

## Using Archive Data with Diagnostic Scripts

All scripts default to live paths. Pass explicit flags to target this archive.

### Trade-log scripts (`--path`)

```bash
# Signal edge diagnostics against full Windows trade history
python scripts/signal_edge_diagnostics.py \
  --path archive/windows-2026-04-18/logs/trades/

# Decision funnel summary
python scripts/decision_funnel_summary.py \
  --path archive/windows-2026-04-18/logs/trades/

# Daily review (trade log side only — DB side uses live DB)
python scripts/daily_review.py \
  --path archive/windows-2026-04-18/logs/trades/

# Freshness diagnostics
python scripts/freshness_diagnostics.py \
  --path archive/windows-2026-04-18/logs/trades/

# Match quality
python scripts/match_quality_diagnostics.py \
  --path archive/windows-2026-04-18/logs/trades/

# Keyword feedback
python scripts/keyword_feedback.py \
  --path archive/windows-2026-04-18/logs/trades/
```

### DB-bound scripts (`--path` for DB)

```bash
# Paper performance drilldown (has --path flag)
python scripts/paper_performance_drilldown.py \
  --path archive/windows-2026-04-18/data/paper_trades.db

# Source scorecard (uses DB via config — see note below)
python scripts/source_scorecard.py
```

> **Note:** `daily_review.py` and `performance_analysis.py` hardcode
> `data/paper_trades.db` and do not currently accept a `--db` flag.
> For full Windows-era DB analysis, use `paper_performance_drilldown.py`.

### App-log scripts (`--log`)

```bash
# Ollama error audit
python scripts/ollama_error_audit.py \
  --log archive/windows-2026-04-18/logs/app/bot.log

# To scan all rotated Windows app logs sequentially:
for f in archive/windows-2026-04-18/logs/app/bot.log.2026-*; do
  echo "=== $f ===" && python scripts/ollama_error_audit.py --log "$f"
done
```

## Safety Reminders

- Do not copy files from this directory into `data/` or `logs/`.
- Do not pass archive DB paths to scripts that also write to the DB.
- Do not run `migrate_trade_logs.py` against archive paths (migration already reflected in archive layout).
- The canonical preserved copy is the tarball: `transfer/decision-support-sync-2026-04-18.tar.gz`.
  This directory can be re-extracted from it at any time.
