#!/usr/bin/env bash
#
# Check for SQLite WAL/SHM sidecar files under data/ and optionally run
# PRAGMA wal_checkpoint(TRUNCATE) to consolidate them into the main DB.
# Manual maintenance utility -- do not run while the bot is active.
# See usage() below for flags and accepted paths.
#
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHECKPOINT=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/check_sqlite_wal.sh [--checkpoint] [db_path ...]

Checks for SQLite WAL/SHM sidecar files. With --checkpoint, runs:
  PRAGMA wal_checkpoint(TRUNCATE);

This is a manual maintenance utility. Do not run it while the bot is active.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --checkpoint)
      CHECKPOINT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$#" -gt 0 ]]; then
  DBS=("$@")
else
  DBS=(data/*.db)
fi

found_sidecars=0
checked=0

for db in "${DBS[@]}"; do
  if [[ ! -f "$db" ]]; then
    continue
  fi

  checked=$((checked + 1))
  wal="${db}-wal"
  shm="${db}-shm"
  has_sidecar=0

  if [[ -f "$wal" || -f "$shm" ]]; then
    found_sidecars=1
    has_sidecar=1
  fi

  echo "DB: $db"
  if [[ -f "$wal" ]]; then
    echo "  WAL: present ($(wc -c < "$wal" | tr -d ' ') bytes)"
  else
    echo "  WAL: absent"
  fi

  if [[ -f "$shm" ]]; then
    echo "  SHM: present ($(wc -c < "$shm" | tr -d ' ') bytes)"
  else
    echo "  SHM: absent"
  fi

  if [[ "$CHECKPOINT" -eq 1 ]]; then
    if ! command -v sqlite3 >/dev/null 2>&1; then
      echo "  checkpoint: skipped, sqlite3 not found"
    elif [[ "$has_sidecar" -eq 1 ]]; then
      echo "  checkpoint: running PRAGMA wal_checkpoint(TRUNCATE)"
      sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);"
    else
      echo "  checkpoint: skipped, no sidecars present"
    fi
  fi
done

if [[ "$checked" -eq 0 ]]; then
  echo "No SQLite DB files found."
  exit 0
fi

if [[ "$found_sidecars" -eq 1 ]]; then
  echo "SQLite sidecar files detected. If the bot is stopped, review or checkpoint manually."
else
  echo "No SQLite WAL/SHM sidecar files detected."
fi
