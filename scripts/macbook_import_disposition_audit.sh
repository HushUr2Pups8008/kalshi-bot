#!/usr/bin/env bash
# Assess mac_archive/macbook_2026-05-01_import for keep-vs-archive disposition.

set -euo pipefail

JSON=0
IMPORT_REL="mac_archive/macbook_2026-05-01_import"
MAX_KEEP_DAYS=7

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    --path) IMPORT_REL="$2"; shift 2 ;;
    --max-keep-days) MAX_KEEP_DAYS="$2"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/macbook_import_disposition_audit.sh [--json] [--path RELPATH] [--max-keep-days N]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
IMPORT_PATH="$REPO_ROOT/$IMPORT_REL"
NOW="$(date +%s)"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

if [[ ! -d "$IMPORT_PATH" ]]; then
  if [[ "$JSON" == "1" ]]; then
    printf '{"status":"missing","path":"%s","recommendation":"no_action"}\n' "$(json_escape "$IMPORT_REL")"
  else
    echo "# MacBook Import Disposition Audit"
    echo
    echo "Status: missing"
    echo "Recommendation: no_action"
  fi
  exit 0
fi

FILE_COUNT="$(find "$IMPORT_PATH" -type f | wc -l | awk '{print $1}')"
DIR_COUNT="$(find "$IMPORT_PATH" -type d | wc -l | awk '{print $1}')"
SIZE_BYTES="$(du -sk "$IMPORT_PATH" | awk '{print $1 * 1024}')"
DB_COUNT="$(find "$IMPORT_PATH" -type f \( -name '*.db' -o -name '*.sqlite' \) | wc -l | awk '{print $1}')"
JSONL_COUNT="$(find "$IMPORT_PATH" -type f -name '*.jsonl' | wc -l | awk '{print $1}')"
PY_COUNT="$(find "$IMPORT_PATH" -type f -name '*.py' | wc -l | awk '{print $1}')"
NEWEST_MTIME="$(find "$IMPORT_PATH" -type f -print0 | xargs -0 stat -f '%m' 2>/dev/null | sort -nr | head -1 || true)"
OLDEST_MTIME="$(find "$IMPORT_PATH" -type f -print0 | xargs -0 stat -f '%m' 2>/dev/null | sort -n | head -1 || true)"

age_days() {
  local mtime="$1"
  if [[ -z "$mtime" ]]; then
    echo "null"
  else
    awk -v now="$NOW" -v then="$mtime" 'BEGIN { printf "%.2f", (now-then)/86400 }'
  fi
}

NEWEST_AGE_DAYS="$(age_days "$NEWEST_MTIME")"
OLDEST_AGE_DAYS="$(age_days "$OLDEST_MTIME")"

LIVE_DB_PRESENT=0
if [[ -f "$REPO_ROOT/data/paper_trades.db" || -f "$REPO_ROOT/data/evidence_store.db" ]]; then
  LIVE_DB_PRESENT=1
fi

REFERENCE_COUNT="$(rg -n "macbook_2026-05-01_import" "$REPO_ROOT" --glob '!mac_archive/macbook_2026-05-01_import/**' 2>/dev/null | wc -l | awk '{print $1}')"

RECOMMENDATION="archive_after_close"
if [[ "$FILE_COUNT" -eq 0 ]]; then
  RECOMMENDATION="remove_empty_dir"
elif [[ "$REFERENCE_COUNT" -gt 0 || "$DB_COUNT" -gt 0 || "$JSONL_COUNT" -gt 0 ]]; then
  RECOMMENDATION="keep_through_wave1_close_then_archive"
fi

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"pass","path":"%s","file_count":%s,"dir_count":%s,"size_bytes":%s,"db_count":%s,"jsonl_count":%s,"python_count":%s,"newest_age_days":%s,"oldest_age_days":%s,"live_db_present":%s,"repo_reference_count":%s,"max_keep_days":%s,"recommendation":"%s"}\n' \
    "$(json_escape "$IMPORT_REL")" "$FILE_COUNT" "$DIR_COUNT" "$SIZE_BYTES" "$DB_COUNT" "$JSONL_COUNT" "$PY_COUNT" "$NEWEST_AGE_DAYS" "$OLDEST_AGE_DAYS" "$LIVE_DB_PRESENT" "$REFERENCE_COUNT" "$MAX_KEEP_DAYS" "$RECOMMENDATION"
else
  echo "# MacBook Import Disposition Audit"
  echo
  echo "Status: pass"
  echo "Path: $IMPORT_REL"
  echo "Recommendation: $RECOMMENDATION"
  echo
  echo "- files: $FILE_COUNT"
  echo "- dirs: $DIR_COUNT"
  echo "- size_bytes: $SIZE_BYTES"
  echo "- db_count: $DB_COUNT"
  echo "- jsonl_count: $JSONL_COUNT"
  echo "- python_count: $PY_COUNT"
  echo "- newest_age_days: $NEWEST_AGE_DAYS"
  echo "- oldest_age_days: $OLDEST_AGE_DAYS"
  echo "- live_db_present: $LIVE_DB_PRESENT"
  echo "- repo_reference_count: $REFERENCE_COUNT"
fi
