#!/usr/bin/env bash
# Check Mac Studio bot DB backup presence, cadence, and retention.

set -euo pipefail

JSON=0
MAX_AGE_HOURS=24
MIN_RETENTION=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    --max-age-hours) MAX_AGE_HOURS="$2"; shift 2 ;;
    --min-retention) MIN_RETENTION="$2"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/db_backup_health_audit.sh [--json] [--max-age-hours N] [--min-retention N]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
PAPER_DB="$REPO_ROOT/data/paper_trades.db"
EVIDENCE_DB="$REPO_ROOT/data/evidence_store.db"
ARCHIVE_ROOT="$REPO_ROOT/mac_archive"
NOW="$(date +%s)"

count_backups() {
  find "$ARCHIVE_ROOT" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.tar.gz' \) 2>/dev/null | wc -l | awk '{print $1}'
}

newest_backup_age_hours() {
  local newest
  newest="$(find "$ARCHIVE_ROOT" -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.tar.gz' \) -print0 2>/dev/null |
    xargs -0 stat -f '%m %N' 2>/dev/null |
    sort -nr |
    head -1 |
    awk '{print $1}')"
  if [[ -z "${newest:-}" ]]; then
    echo ""
  else
    awk -v now="$NOW" -v then="$newest" 'BEGIN { printf "%.2f", (now-then)/3600 }'
  fi
}

BACKUP_COUNT="$(count_backups)"
AGE_HOURS="$(newest_backup_age_hours)"
PAPER_EXISTS="$([[ -f "$PAPER_DB" ]] && echo 1 || echo 0)"
EVIDENCE_EXISTS="$([[ -f "$EVIDENCE_DB" ]] && echo 1 || echo 0)"
RETENTION_OK="$([[ "$BACKUP_COUNT" -ge "$MIN_RETENTION" ]] && echo 1 || echo 0)"
AGE_OK=0
if [[ -n "$AGE_HOURS" ]]; then
  AGE_OK="$(awk -v age="$AGE_HOURS" -v max="$MAX_AGE_HOURS" 'BEGIN { print (age <= max) ? 1 : 0 }')"
fi

STATUS="pass"
if [[ "$PAPER_EXISTS" != "1" || "$EVIDENCE_EXISTS" != "1" || "$RETENTION_OK" != "1" || "$AGE_OK" != "1" ]]; then
  STATUS="fail"
fi

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","databases":{"data/paper_trades.db":%s,"data/evidence_store.db":%s},"backup":{"root":"%s","count":%s,"newest_age_hours":%s,"max_age_hours":%s,"min_retention":%s}}\n' \
    "$STATUS" "$PAPER_EXISTS" "$EVIDENCE_EXISTS" "$ARCHIVE_ROOT" "$BACKUP_COUNT" "${AGE_HOURS:-null}" "$MAX_AGE_HOURS" "$MIN_RETENTION"
else
  echo "# DB Backup Health Audit"
  echo
  echo "Status: $STATUS"
  echo
  echo "- data/paper_trades.db exists: $PAPER_EXISTS"
  echo "- data/evidence_store.db exists: $EVIDENCE_EXISTS"
  echo "- mac_archive backup count: $BACKUP_COUNT"
  echo "- newest backup age hours: ${AGE_HOURS:-none}"
  echo "- max age hours: $MAX_AGE_HOURS"
  echo "- min retention: $MIN_RETENTION"
fi

[[ "$STATUS" == "pass" ]]
