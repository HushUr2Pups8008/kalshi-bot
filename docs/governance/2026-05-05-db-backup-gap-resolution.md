# DB backup gap resolution — pre-Wave-1 hygiene

**Type:** operator-action record (Claude task; addresses cycle-4 Codex audit finding).
**Trigger:** `db_backup_health_audit.sh --json` returned `{"status":"fail","backup":{"count":0,...}}` on cycle 4.
**Drafted:** 2026-05-05.
**Audience:** operator + future-Claude reviewing why this commit landed.

## TL;DR

Cycle 4 Codex audit surfaced **0 backup artifacts in `mac_archive/`** within the 24h retention window. Both bot DBs (`paper_trades.db` 610 KB; `evidence_store.db` 1.99 MB) were live and unsnapshotted. **Resolved this commit:** added `scripts/db_snapshot_backup.sh` + launchd plist template + seeded archive. Audit now returns `{"status":"pass", ...}`.

**Operator action required:** copy plist to `~/Library/LaunchAgents/` + bootstrap. Otherwise scheduled backups don't fire.

## What was wrong

`mac_archive/` contained only `macbook_2026-05-01_import/` (the 2026-05-01 MacBook→Mac Studio cutover artifact). No recurring snapshot mechanism. If the Mac Studio's `data/` directory got corrupted between then and Wave-1 deploy, the only recovery path was re-importing from the 2026-05-01 cutover state — losing all post-cutover paper-trade history + governance soak data.

## What changed

### `scripts/db_snapshot_backup.sh`

Online-safe daily snapshot via `sqlite3 .backup` (no torn reads; doesn't block writers). Writes to `mac_archive/db_snapshots/YYYY-MM-DDThhmmZ/{paper_trades,evidence_store}.db`. Includes:

- Sanity checks: snapshot non-empty + `PRAGMA integrity_check` clean
- 7-day retention prune (configurable via `--retention-days N`)
- `--dry-run` mode for operator pre-bootstrap inspection
- Strict prune pattern (`????-??-??T????Z`) — never touches `mac_archive/macbook_2026-05-01_import/`

### `scripts/launchd/com.kalshi.db-backup.plist`

LaunchAgent template scheduling daily snapshot at 06:00 local time + RunAtLoad. NOT bootstrapped automatically — operator must copy + bootstrap (per project pattern of operator-controlled launchd state).

### Seed snapshot

This commit also includes the seed snapshot: `mac_archive/db_snapshots/2026-05-05T1359Z/` (610 KB + 1.99 MB). Audit now returns pass.

## Operator install (one-time, Mac Studio)

```bash
cp scripts/launchd/com.kalshi.db-backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.db-backup.plist
launchctl list | grep com.kalshi.db-backup        # confirm loaded
bash scripts/db_backup_health_audit.sh --json     # confirm status=pass
```

After bootstrap: backups fire at 06:00 local time daily; `RunAtLoad` also fires immediately on bootstrap (which seeds a second snapshot — fine; audit treats it as a second retention copy).

## Why this is pre-Wave-1 critical

If a Wave-1 deploy regresses + `db_snapshot_backup.sh` is not yet running, operator has no recovery path beyond `git revert` + the 2026-05-01 cutover state. The Wave-1 post-deploy observation plan's 14 monitoring rows watch for **trade-rate / SKIPPED-stream / classifier-distribution** regressions that revert the `data/*.db` state to the running-bot's DB; if the DB itself is corrupted (rare, but possible), a 24h-old snapshot is the difference between "5-min restore" and "lose 7 days of paper-trade history."

This is preventive hygiene, not reactive incident response. Do it once; it runs forever.

## Verification (post-commit)

```bash
$ bash scripts/db_backup_health_audit.sh --json
{"status":"pass","databases":{"data/paper_trades.db":1,"data/evidence_store.db":1},"backup":{"root":"/Users/jacobparenti/vscode/kalshi-bot/mac_archive","count":2,"newest_age_hours":0.00,"max_age_hours":24,"min_retention":1}}
```

`count=2` reflects the 2 DB files in the seed snapshot. After the launchd job runs again tomorrow morning, count grows to 4, then 6, etc., until 7-day retention prune steady-states at 14 (2 DBs × 7 days).

## Out of scope

- **Off-host backup.** Snapshots stay local on the Mac Studio. Cross-host replication (NAS / cloud) is a separate hygiene concern; out of pre-Wave-1 scope.
- **WAL file backup.** sqlite3 .backup captures committed state; in-flight WAL transactions are not snapshotted. Acceptable for this use case (paper-trade DB is append-mostly; transactional gaps are tolerable).
- **Backup encryption.** Local snapshots stored unencrypted. The DBs themselves are unencrypted on the live disk; backup adds no exposure.
- **Backup verification beyond `PRAGMA integrity_check`.** Restore-test (load snapshot DB into a fresh test environment + re-run a query) would be more rigorous; out of pre-Wave-1 scope.

## Cross-links

- `scripts/db_snapshot_backup.sh` — the snapshot script (this commit)
- `scripts/launchd/com.kalshi.db-backup.plist` — launchd template (this commit)
- `scripts/launchd/README.md` — install instructions (this commit)
- `scripts/db_backup_health_audit.sh` — the audit (cycle 4 Codex; surfaces this gap)
- `mac_archive/db_snapshots/2026-05-05T1359Z/` — seed snapshot (this commit)
