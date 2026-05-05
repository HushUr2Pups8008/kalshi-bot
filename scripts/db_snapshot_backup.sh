#!/usr/bin/env bash
# Online-safe daily snapshot of bot DBs to mac_archive/db_snapshots/.
#
# Uses `sqlite3 .backup` which is safe against concurrent writes on the
# running bot — no torn reads, no need to stop the bot. Each run creates
# a timestamped subdirectory; old snapshots are pruned beyond the
# retention window (default 7 days).
#
# Designed to be invoked daily by launchd
# (~/Library/LaunchAgents/com.kalshi.db-backup.plist).
#
# Exit codes:
#   0 — backup succeeded; both DBs snapshotted; old snapshots pruned
#   1 — usage error
#   2 — backup failure (sqlite3 .backup returned non-zero on either DB)
#   3 — repo / DB layout unexpected

set -euo pipefail

RETENTION_DAYS=7

while [[ $# -gt 0 ]]; do
  case "$1" in
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      cat <<EOF
usage: bash scripts/db_snapshot_backup.sh [--retention-days N] [--dry-run]

Snapshots data/paper_trades.db and data/evidence_store.db to
mac_archive/db_snapshots/YYYY-MM-DDThhmmZ/ via sqlite3 .backup.

Options:
  --retention-days N   prune snapshots older than N days (default 7)
  --dry-run            describe what would happen; no writes

Exit codes:
  0 success / 1 usage / 2 sqlite3 backup failure / 3 layout error
EOF
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "ERROR: not in a git repo" >&2
  exit 3
fi

PAPER_DB="$REPO_ROOT/data/paper_trades.db"
EVIDENCE_DB="$REPO_ROOT/data/evidence_store.db"
ARCHIVE_ROOT="$REPO_ROOT/mac_archive/db_snapshots"

if [[ ! -f "$PAPER_DB" ]]; then
  echo "ERROR: $PAPER_DB not found" >&2
  exit 3
fi
if [[ ! -f "$EVIDENCE_DB" ]]; then
  echo "ERROR: $EVIDENCE_DB not found" >&2
  exit 3
fi

UTC_STAMP="$(date -u +%Y-%m-%dT%H%MZ)"
DEST="$ARCHIVE_ROOT/$UTC_STAMP"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "[dry-run] would create: $DEST"
  echo "[dry-run] would snapshot: $PAPER_DB → $DEST/paper_trades.db"
  echo "[dry-run] would snapshot: $EVIDENCE_DB → $DEST/evidence_store.db"
  echo "[dry-run] would prune snapshots older than $RETENTION_DAYS days under $ARCHIVE_ROOT"
  exit 0
fi

mkdir -p "$DEST"

# sqlite3 .backup is online-safe — does not block writers, no torn reads
sqlite3 "$PAPER_DB" ".backup '$DEST/paper_trades.db'" || {
  echo "ERROR: paper_trades.db backup failed" >&2
  exit 2
}
sqlite3 "$EVIDENCE_DB" ".backup '$DEST/evidence_store.db'" || {
  echo "ERROR: evidence_store.db backup failed" >&2
  exit 2
}

# Sanity check the snapshots are non-empty + sqlite-valid
for f in "$DEST/paper_trades.db" "$DEST/evidence_store.db"; do
  if [[ ! -s "$f" ]]; then
    echo "ERROR: snapshot empty: $f" >&2
    exit 2
  fi
  sqlite3 "$f" "PRAGMA integrity_check;" >/dev/null || {
    echo "ERROR: snapshot integrity check failed: $f" >&2
    exit 2
  }
done

# Remove any WAL/SHM sidecars that integrity_check may have created.
# The .db file is the canonical self-contained snapshot per sqlite3
# .backup; sidecars are transient and should not be retained or counted
# by retention/health audits.
rm -f "$DEST"/*.db-wal "$DEST"/*.db-shm

PAPER_SIZE="$(stat -f '%z' "$DEST/paper_trades.db")"
EVID_SIZE="$(stat -f '%z' "$DEST/evidence_store.db")"
echo "snapshot ok: $DEST"
echo "  paper_trades.db: $PAPER_SIZE bytes"
echo "  evidence_store.db: $EVID_SIZE bytes"

# Retention prune — only prune our own snapshot directories (matching
# the YYYY-MM-DDThhmmZ pattern). Never touches mac_archive/macbook_*
# (the cutover import) or any other archive subtree.
PRUNED=0
if [[ -d "$ARCHIVE_ROOT" ]]; then
  while IFS= read -r dir; do
    rm -rf "$dir"
    PRUNED=$((PRUNED + 1))
    echo "pruned: $dir"
  done < <(find "$ARCHIVE_ROOT" -mindepth 1 -maxdepth 1 -type d \
              -name '????-??-??T????Z' \
              -mtime "+$RETENTION_DAYS" 2>/dev/null)
fi
echo "pruned $PRUNED snapshot directories older than $RETENTION_DAYS days"
