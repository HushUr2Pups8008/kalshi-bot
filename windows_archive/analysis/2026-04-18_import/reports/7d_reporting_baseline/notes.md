# 7-Day Reporting Baseline — Windows Historical Bundle

Generated: 2026-04-18 | Window: 2026-04-12 → 2026-04-18

## Window Rationale

Apr 12 is the first day with representative event volume (45,925 events vs 1,603 on Apr 11). The jump reflects v0.29.3–v0.29.5 observability improvements landing mid-Apr 11. The 7-day window covers 400,201 of 416,595 total Windows-era events (96%).

## Source Artifacts

| Artifact | Path |
|----------|------|
| Trade log | `windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl` |
| SQLite DB | `windows_archive/analysis/2026-04-18_import/db/paper_trades_analysis.db` |
| App logs (7d) | Concatenated from `windows_archive/raw/2026-04-18_import/logs/app/` (see below) |

## Re-Running Reports

All trade-log scripts accept `--path`, `--since`, and `--until`. Run from repo root with `PYTHONPATH=.`:

```bash
TRADES=windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl
# If cold-stored, first decompress: gunzip "$TRADES.gz"

DB=windows_archive/analysis/2026-04-18_import/db/paper_trades_analysis.db
BUNDLE=windows_archive/analysis/2026-04-18_import/reports/7d_reporting_baseline
SINCE=2026-04-12
UNTIL=2026-04-18
PY=.venv/bin/python3

PYTHONPATH=. $PY scripts/trade_log_summary.py        --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/decision_funnel_summary.py  --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/freshness_diagnostics.py    --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/signal_edge_diagnostics.py  --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/match_quality_diagnostics.py --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/match_suppression_audit.py  --path "$TRADES" --since $SINCE --until $UNTIL --exclude-test
PYTHONPATH=. $PY scripts/source_scorecard.py         --logs-path "$TRADES" --db-path "$DB" --since $SINCE --until $UNTIL
PYTHONPATH=. $PY scripts/paper_performance_drilldown.py --path "$DB"
```

### Re-running `ollama_error_audit.py`

This script has no date filter. Concatenate the 7-day raw app logs:

```bash
RAW_APP=windows_archive/raw/2026-04-18_import/logs/app
cat "$RAW_APP/bot.log.2026-04-12" \
    "$RAW_APP/bot.log.2026-04-13" \
    "$RAW_APP/bot.log.2026-04-14" \
    "$RAW_APP/bot.log.2026-04-15" \
    "$RAW_APP/bot.log.2026-04-16" \
    "$RAW_APP/bot.log.2026-04-17" \
    "$RAW_APP/bot.log" > /tmp/win_7d_app.log
PYTHONPATH=. $PY scripts/ollama_error_audit.py --log /tmp/win_7d_app.log
```

Note: `bot.log.2026-04-17` contains UTC timestamps spanning Apr 16 22:18 → Apr 18 05:59 UTC (Windows ran Mountain Time). `bot.log` is the sync-time snapshot ending Apr 18 16:25 UTC.

## `paper_performance_drilldown` Date Scope

This script has no date filter — it reads all 15 paper trades in the DB (2026-03-13 through 2026-04-14). The 7-day window does not apply here.

## Bug Fixed

`scripts/match_quality_diagnostics.py` line 13: `timezone` was missing from the `datetime` import. Fixed in this session.

## Cold-Storage Note

After this bundle was generated, `trades_all.jsonl` and `bot_all.log` in `consolidated/` were gzip-compressed. To restore before re-running:

```bash
gunzip windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl.gz
gunzip windows_archive/analysis/2026-04-18_import/consolidated/bot_all.log.gz
```
