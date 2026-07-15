#!/usr/bin/env bash
# Online-safe daily snapshot of bot DBs to the output-root backup directory.
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
INCLUDE_WEATHER=0

sqlite_dot_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

sqlite_rw_uri() {
  local value="$1"
  value="${value//%/%25}"
  value="${value//#/%23}"
  value="${value//\?/%3F}"
  value="${value// /%20}"
  printf 'file:%s?mode=rw' "$value"
}

sqlite_backup() {
  local source="$1"
  local destination="$2"
  local quoted_destination
  local fallback_error
  local readonly_error
  local source_uri
  quoted_destination="$(sqlite_dot_quote "$destination")"
  readonly_error="$destination.readonly-error"
  if sqlite3 -readonly "$source" ".backup $quoted_destination" 2>"$readonly_error" \
      && [[ ! -s "$readonly_error" && -s "$destination" ]]; then
    rm -f "$readonly_error"
    return 0
  fi

  if [[ ! -f "$source" ]]; then
    cat "$readonly_error" >&2
    rm -f "$readonly_error"
    return 1
  fi
  rm -f "$destination" "$destination-wal" "$destination-shm"
  source_uri="$(sqlite_rw_uri "$source")"
  fallback_error="$destination.rw-error"
  if sqlite3 "$source_uri" ".backup $quoted_destination" 2>"$fallback_error" \
      && [[ ! -s "$fallback_error" && -s "$destination" ]]; then
    rm -f "$readonly_error" "$fallback_error"
    return 0
  fi
  cat "$fallback_error" >&2
  cat "$readonly_error" >&2
  rm -f "$readonly_error" "$fallback_error"
  return 1
}

atomic_publish_dir() {
  local source="$1"
  local destination="$2"
  "$PUBLISH_PYTHON" - "$source" "$destination" <<'PY'
import os
import sys

source, destination = sys.argv[1:]
try:
    os.rename(source, destination)
except OSError as exc:
    print(f"{exc.strerror}: {destination}", file=sys.stderr)
    raise SystemExit(1) from None
PY
}

sqlite_restore() {
  local destination="$1"
  local source="$2"
  local quoted_source
  quoted_source="$(sqlite_dot_quote "$source")"
  sqlite3 "$destination" ".restore $quoted_source"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --include-weather) INCLUDE_WEATHER=1; shift ;;
    -h|--help)
      cat <<EOF
usage: bash scripts/db_snapshot_backup.sh [--retention-days N] [--dry-run] [--include-weather]

Snapshots data/paper_trades.db and data/evidence_store.db to
logs/backups/db_snapshots/YYYY-MM-DDThhmmZ/ via sqlite3 .backup by default.

Options:
  --retention-days N   prune snapshots older than N days (default 7)
  --dry-run            describe what would happen; no writes
  --include-weather    also snapshot weather_shadow.db when present

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
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PUBLISH_PYTHON="$REPO_ROOT/.venv/bin/python"
elif PUBLISH_PYTHON="$(command -v python3 2>/dev/null)" && [[ -n "$PUBLISH_PYTHON" ]]; then
  :
else
  echo "ERROR: Python is required for atomic snapshot publish" >&2
  exit 3
fi

PAPER_DB="$REPO_ROOT/data/paper_trades.db"
EVIDENCE_DB="$REPO_ROOT/data/evidence_store.db"
WEATHER_DB="$REPO_ROOT/data/weather_shadow.db"
OUTPUT_ROOT="${KALSHI_OUTPUT_ROOT:-${KALSHI_LOG_ROOT:-$REPO_ROOT/logs}}"
ARCHIVE_ROOT="$OUTPUT_ROOT/backups/db_snapshots"

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
  if [[ "$INCLUDE_WEATHER" == "1" ]]; then
    if [[ -f "$WEATHER_DB" ]]; then
      echo "[dry-run] would snapshot: $WEATHER_DB → $DEST/weather_shadow.db"
    else
      echo "[dry-run] weather_shadow.db: skipped (not found)"
    fi
  fi
  echo "[dry-run] would prune snapshots older than $RETENTION_DAYS days under $ARCHIVE_ROOT"
  exit 0
fi

mkdir -p "$ARCHIVE_ROOT"
if [[ -e "$DEST" ]]; then
  echo "ERROR: snapshot destination already exists: $DEST" >&2
  exit 2
fi
if ! SNAPSHOT_WORK_DIR="$(mktemp -d "$ARCHIVE_ROOT/.${UTC_STAMP}.tmp.XXXXXX")"; then
  echo "ERROR: snapshot staging directory creation failed" >&2
  exit 2
fi
WEATHER_RESTORE_DB=""
PUBLISHED_DEST=""
cleanup() {
  if [[ -n "$WEATHER_RESTORE_DB" ]]; then
    rm -f "$WEATHER_RESTORE_DB" "$WEATHER_RESTORE_DB-wal" "$WEATHER_RESTORE_DB-shm"
  fi
  if [[ -n "$SNAPSHOT_WORK_DIR" ]]; then
    rm -rf "$SNAPSHOT_WORK_DIR"
  fi
  if [[ -n "$PUBLISHED_DEST" ]]; then
    rm -rf "$PUBLISHED_DEST"
  fi
}
trap cleanup EXIT

# sqlite3 .backup is online-safe — does not block writers, no torn reads
sqlite_backup "$PAPER_DB" "$SNAPSHOT_WORK_DIR/paper_trades.db" || {
  echo "ERROR: paper_trades.db backup failed" >&2
  exit 2
}
sqlite_backup "$EVIDENCE_DB" "$SNAPSHOT_WORK_DIR/evidence_store.db" || {
  echo "ERROR: evidence_store.db backup failed" >&2
  exit 2
}

WEATHER_BACKED_UP=0
if [[ "$INCLUDE_WEATHER" == "1" && -f "$WEATHER_DB" ]]; then
  sqlite_backup "$WEATHER_DB" "$SNAPSHOT_WORK_DIR/weather_shadow.db" || {
    echo "ERROR: weather_shadow.db backup failed" >&2
    exit 2
  }
  WEATHER_BACKED_UP=1
fi

# Sanity check the snapshots are non-empty + sqlite-valid
for f in "$SNAPSHOT_WORK_DIR/paper_trades.db" "$SNAPSHOT_WORK_DIR/evidence_store.db"; do
  if [[ ! -s "$f" ]]; then
    echo "ERROR: snapshot empty: $f" >&2
    exit 2
  fi
  sqlite3 "$f" "PRAGMA integrity_check;" >/dev/null || {
    echo "ERROR: snapshot integrity check failed: $f" >&2
    exit 2
  }
done

if [[ "$WEATHER_BACKED_UP" == "1" ]]; then
  if [[ ! -s "$SNAPSHOT_WORK_DIR/weather_shadow.db" ]]; then
    echo "ERROR: snapshot empty: $SNAPSHOT_WORK_DIR/weather_shadow.db" >&2
    exit 2
  fi
  if ! WEATHER_RESTORE_DB="$(mktemp "${TMPDIR:-/tmp}/kalshi-weather-restore.XXXXXX")"; then
    echo "ERROR: weather_shadow.db restore temp creation failed" >&2
    exit 2
  fi
  sqlite_restore "$WEATHER_RESTORE_DB" "$SNAPSHOT_WORK_DIR/weather_shadow.db" || {
    echo "ERROR: weather_shadow.db restore validation failed" >&2
    exit 2
  }
  WEATHER_INTEGRITY="$(sqlite3 "$WEATHER_RESTORE_DB" "PRAGMA integrity_check;")" || {
    echo "ERROR: weather_shadow.db integrity check failed" >&2
    exit 2
  }
  if [[ "$WEATHER_INTEGRITY" != "ok" ]]; then
    echo "ERROR: weather_shadow.db integrity check returned: $WEATHER_INTEGRITY" >&2
    exit 2
  fi
  rm -f "$WEATHER_RESTORE_DB" "$WEATHER_RESTORE_DB-wal" "$WEATHER_RESTORE_DB-shm"
  WEATHER_RESTORE_DB=""
fi

# Remove any WAL/SHM sidecars that integrity_check may have created.
# The .db file is the canonical self-contained snapshot per sqlite3
# .backup; sidecars are transient and should not be retained or counted
# by retention/health audits.
rm -f "$SNAPSHOT_WORK_DIR"/*.db-wal "$SNAPSHOT_WORK_DIR"/*.db-shm

PAPER_SIZE="$(stat -f '%z' "$SNAPSHOT_WORK_DIR/paper_trades.db")"
EVID_SIZE="$(stat -f '%z' "$SNAPSHOT_WORK_DIR/evidence_store.db")"
if [[ "$WEATHER_BACKED_UP" == "1" ]]; then
  WEATHER_SIZE="$(stat -f '%z' "$SNAPSHOT_WORK_DIR/weather_shadow.db")"
fi
if ! atomic_publish_dir "$SNAPSHOT_WORK_DIR" "$DEST"; then
  echo "ERROR: snapshot publish failed: $DEST" >&2
  exit 2
fi
PUBLISHED_DEST="$DEST"
SNAPSHOT_WORK_DIR=""
for required in paper_trades.db evidence_store.db; do
  if [[ ! -s "$DEST/$required" ]]; then
    echo "ERROR: published snapshot missing canonical file: $DEST/$required" >&2
    exit 2
  fi
done
if [[ "$WEATHER_BACKED_UP" == "1" && ! -s "$DEST/weather_shadow.db" ]]; then
  echo "ERROR: published snapshot missing canonical file: $DEST/weather_shadow.db" >&2
  exit 2
fi
PUBLISHED_DEST=""
echo "snapshot ok: $DEST"
echo "  paper_trades.db: $PAPER_SIZE bytes"
echo "  evidence_store.db: $EVID_SIZE bytes"
if [[ "$WEATHER_BACKED_UP" == "1" ]]; then
  echo "  weather_shadow.db: $WEATHER_SIZE bytes"
elif [[ "$INCLUDE_WEATHER" == "1" ]]; then
  echo "  weather_shadow.db: skipped (not found)"
fi

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
