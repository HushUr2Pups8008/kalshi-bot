# Windows Historical Analysis Notes

Import: `2026-04-18_import` | Source: Windows machine | Derived: 2026-04-18

## Coverage Summary

| Dataset | Start | End | Records |
|---------|-------|-----|---------|
| `trades_all.jsonl` | 2026-03-11 | 2026-04-18T16:23 UTC | 416,595 events |
| `bot_all.log` | 2026-03-11 | 2026-04-18T16:25 UTC | ~1.9 M lines, 290 MB |
| `paper_trades_analysis.db` | 2026-03-13 | 2026-04-14 | 15 trades |

## Known Coverage Gaps in `trades_all.jsonl`

Two gaps exist due to log-restructure transitions. No data was lost — the gaps
reflect cutover timing between source files, not dropped events.

| Gap | Duration | Cause |
|-----|----------|-------|
| 2026-04-11 13:04 → 16:07 UTC | ~3 hours | Migration tool compressed pre-Apr-11 data out of the monolithic file; the new monolithic picks up ~3 hrs later |
| 2026-04-16 19:16 → 19:34 UTC | ~18 minutes | Log restructure cutover from monolithic `trades.jsonl` to daily-partitioned format |

## Known Anomaly in `bot_all.log`

`bot.log.2026-04-17` (file named for Apr 17) contains UTC timestamps spanning
**2026-04-16 22:18 → 2026-04-18 05:59 UTC**. This is expected: the Windows
service ran in Mountain Time (UTC-6), so "midnight local Apr 17" corresponded
to 06:00 UTC Apr 17. The file accumulated from the prior day's late UTC hours.

File boundary markers are injected as `# ===== SOURCE: <filename> =====` lines.
These are comments, not log entries. Grep-based tools should filter them with
`grep -v '^# ====='` if clean log parsing is needed.

## Script Compatibility

### Trade-log scripts (accept `--path`)

All scripts in `scripts/` that accept `--path` can target `trades_all.jsonl`
directly (single-file mode) or the raw archive tree (tree mode):

```bash
# Single consolidated file — fastest for full-history scans
python scripts/signal_edge_diagnostics.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/decision_funnel_summary.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/freshness_diagnostics.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/match_quality_diagnostics.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/keyword_feedback.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/match_suppression_audit.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/pipeline_impact_audit.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

python scripts/daily_review.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl

# Or raw tree — lets the reader walk the archive structure natively
python scripts/signal_edge_diagnostics.py \
  --path windows_archive/raw/2026-04-18_import/logs/trades/
```

### DB-bound scripts (accept `--path`)

```bash
# Full paper-trade P&L drilldown — safe to target analysis copy
python scripts/paper_performance_drilldown.py \
  --path windows_archive/analysis/2026-04-18_import/db/paper_trades_analysis.db

# Source scorecard — uses DB; point at analysis copy
python scripts/source_scorecard.py
# (source_scorecard.py reads from DB configured in config.py — see note below)
```

> **Note:** `daily_review.py` and `performance_analysis.py` hardcode
> `data/paper_trades.db` with no override flag. For full Windows-era DB
> analysis, use `paper_performance_drilldown.py --path ...` instead.

### App-log scripts (accept `--log`)

```bash
# Full scan across all Windows-era app logs
python scripts/ollama_error_audit.py \
  --log windows_archive/analysis/2026-04-18_import/consolidated/bot_all.log

# Per-day targeted scan (faster for specific dates; avoids loading 290 MB)
python scripts/ollama_error_audit.py \
  --log windows_archive/raw/2026-04-18_import/logs/app/bot.log.2026-04-07

# Iterate all daily files
for f in windows_archive/raw/2026-04-18_import/logs/app/bot.log.2026-*; do
  echo "=== $(basename "$f") ===" && \
  python scripts/ollama_error_audit.py --log "$f" --brief 2>/dev/null || true
done
```

### Date-range filtering

Most trade-log scripts accept `--since` / `--until` (or similar). Pass these
when you want to scope analysis to a specific period within the full history:

```bash
python scripts/signal_edge_diagnostics.py \
  --path windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl \
  --since 2026-04-01 --until 2026-04-17
```

## Safety Reminders

- Do not pass `windows_archive/` paths to scripts that also write output to live
  `data/` or `logs/` paths.
- Do not copy `paper_trades_analysis.db` back to `data/paper_trades.db`.
- `bot_all.log` is 290 MB. Use individual raw log files for targeted single-day
  analysis to avoid loading the full file.
- The `trades_all.jsonl` contains events from the Windows-era only
  (through 2026-04-18T16:23 UTC). For Mac-era data use `logs/trades/live/trades.jsonl`.

## Cold Storage

`trades_all.jsonl` and `bot_all.log` are stored gzip-compressed as of 2026-04-18.
Before running any script that reads them, decompress in place:

```bash
gunzip windows_archive/analysis/2026-04-18_import/consolidated/trades_all.jsonl.gz
gunzip windows_archive/analysis/2026-04-18_import/consolidated/bot_all.log.gz
```

Compressed sizes: `trades_all.jsonl.gz` ~17 MB, `bot_all.log.gz` ~42 MB.
Original sizes: `trades_all.jsonl` ~105 MB, `bot_all.log` ~290 MB.
Re-compress after use: `gzip <file>` removes the uncompressed original.

## Derivation Provenance

These artifacts were derived from:

```
windows_archive/raw/2026-04-18_import/   (extracted from transfer/decision-support-sync-2026-04-18.tar.gz)
```

Derivation method: ordered concatenation only. No records were modified,
filtered, or enriched. The tarball is the canonical source.
