# scripts/launchd/

Mac Studio launchd plist templates checked into git so operator-side
launchd state has a source of truth.

## Files

| plist | purpose | install |
|---|---|---|
| `com.kalshi.db-backup.plist` | daily online-safe DB snapshot via `db_snapshot_backup.sh`; 7-day retention | see below |

## com.kalshi.db-backup install (one-time, Mac Studio only)

```bash
# 1. Copy the plist into LaunchAgents
cp scripts/launchd/com.kalshi.db-backup.plist ~/Library/LaunchAgents/

# 2. Bootstrap the job (RunAtLoad fires immediately → seeds the archive)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.db-backup.plist

# 3. Verify the job is loaded
launchctl list | grep com.kalshi.db-backup
# Expected: <PID-or-0> 0 com.kalshi.db-backup
# (PID may be 0 between fires; exit code 0 = healthy)

# 4. Confirm archive seeded
ls mac_archive/db_snapshots/

# 5. Confirm health audit now passes
bash scripts/db_backup_health_audit.sh --json
# Expected: {"status":"pass", ...}
```

## Schedule semantics

- `StartCalendarInterval` Hour=6, Minute=0 → fires daily at **06:00 local time**.
- `RunAtLoad=true` → also fires once when the plist is loaded (covers first install + post-reboot scenarios).
- macOS launchd uses **local time** for StartCalendarInterval. In May NZST (UTC+12), 06:00 NZ = 18:00 UTC the previous day. This is intentionally outside the bot's high-activity window. **If operator timezone differs**, adjust `Hour` accordingly so the fire happens during a low-Kalshi-volume hour.

## Retention

`db_snapshot_backup.sh` prunes snapshot directories older than 7 days. Override:

```bash
launchctl unload ~/Library/LaunchAgents/com.kalshi.db-backup.plist
# Edit ProgramArguments to add --retention-days N
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.db-backup.plist
```

The cutover-import directory `mac_archive/macbook_2026-05-01_import/` is **never pruned** — the script's prune pattern matches only `YYYY-MM-DDThhmmZ` snapshot directories.

## Uninstall

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.db-backup.plist
rm ~/Library/LaunchAgents/com.kalshi.db-backup.plist
```

Existing snapshots in `mac_archive/db_snapshots/` are preserved.

## Health monitoring

`scripts/db_backup_health_audit.sh --json` should return `{"status":"pass", ...}` once the plist is bootstrapped.

The audit is invoked via `scripts/bothealth.sh` and the cycle 4 operator-runbook smoke wrappers — operator gets the failure-signal automatically if the schedule drifts or the script fails.
