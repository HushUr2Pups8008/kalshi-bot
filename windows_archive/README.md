# Windows Historical Archive

This directory contains the historical record of the Kalshi bot's operation on the
**Windows machine**, imported to this repo on **2026-04-18** for diagnostics,
decision-quality review, source evaluation, and decision-support analysis.

It is **entirely separate from live Mac runtime data** at `data/` and `logs/`.
No runtime bot code reads from this directory.

## Structure

```
windows_archive/
  README.md                              ← you are here
  raw/
    2026-04-18_import/
      PROVENANCE.md                      ← origin, coverage, directory map, usage guide
      data/                              ← Windows-era SQLite DB (gitignored, ~72 KB)
      logs/                              ← Windows-era app/service/trade/report logs
                                            (gitignored, ~400 MB uncompressed)
  analysis/
    2026-04-18_import/
      consolidated/
        trades_all.jsonl                 ← all 5 trade-log sources merged chronologically
                                            (gitignored, ~102 MB, 416 595 lines)
        bot_all.log                      ← all 29 app-log files merged chronologically
                                            (gitignored, ~295 MB)
        manifest.json                    ← provenance metadata for derived artifacts
        notes.md                         ← coverage gaps, anomalies, script usage guide
      db/
        paper_trades_analysis.db         ← copy of Windows DB with analysis-safe name
                                            (gitignored, prevents confusion with live DB)
```

## What Is Gitignored vs Committed

| Path | Git status | Reason |
|------|-----------|--------|
| `raw/2026-04-18_import/data/` | gitignored | re-creatable from tarball |
| `raw/2026-04-18_import/logs/` | gitignored | re-creatable from tarball |
| `analysis/2026-04-18_import/` (bulk files) | gitignored | re-derivable |
| `raw/2026-04-18_import/PROVENANCE.md` | **committed** | provenance record |
| `analysis/2026-04-18_import/consolidated/manifest.json` | **committed** | artifact metadata |
| `analysis/2026-04-18_import/consolidated/notes.md` | **committed** | coverage + usage |
| `README.md` (this file) | **committed** | orientation |

The canonical committed copy of the raw Windows data is the tarball:
`transfer/decision-support-sync-2026-04-18.tar.gz`

## Re-populating After a Fresh Clone

```bash
# Re-extract raw archive
mkdir -p windows_archive/raw/2026-04-18_import
tar -xzf transfer/decision-support-sync-2026-04-18.tar.gz \
    -C windows_archive/raw/2026-04-18_import/

# Re-build derived analysis artifacts
python scripts/build_windows_analysis.py
```

See `analysis/2026-04-18_import/consolidated/notes.md` for full operator guide.

## Data Separation Summary

| Layer | Path | Written by |
|-------|------|-----------|
| **Live Mac runtime** | `data/paper_trades.db` | Bot (active) |
| **Live Mac runtime** | `logs/trades/live/trades.jsonl` | Bot (active) |
| **Live Mac runtime** | `logs/app/bot.log` | Bot (active) |
| **Windows raw** | `windows_archive/raw/2026-04-18_import/` | Imported, read-only |
| **Windows derived** | `windows_archive/analysis/2026-04-18_import/` | Build script, read-only |

Never pass `windows_archive/` paths to scripts that also write output to live paths.
