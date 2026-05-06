#!/usr/bin/env bash

set -euo pipefail

ARCHIVE_ROOT=""
NOW=""
RETENTION_DAYS=7
JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive-root) ARCHIVE_ROOT="${2:-}"; shift 2 ;;
    --now) NOW="${2:-}"; shift 2 ;;
    --retention-days) RETENTION_DAYS="${2:-}"; shift 2 ;;
    --json) JSON=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/db_snapshot_retention_audit.sh [--archive-root PATH] [--now ISO] [--retention-days N] [--json]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
ARCHIVE_ROOT="${ARCHIVE_ROOT:-$REPO_ROOT/mac_archive/db_snapshots}"
NOW="${NOW:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
NOW_EPOCH="$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$NOW" +%s 2>/dev/null || date -j -u -f "%Y-%m-%dT%H:%M:%S%z" "$NOW" +%s 2>/dev/null)"
MAX_AGE_SECONDS=$((RETENTION_DAYS * 24 * 3600))

OLD=()
COUNT=0
if [[ -d "$ARCHIVE_ROOT" ]]; then
  while IFS= read -r dir; do
    base="$(basename "$dir")"
    snap_epoch="$(date -j -u -f "%Y-%m-%dT%H%MZ" "$base" +%s 2>/dev/null || true)"
    [[ -n "$snap_epoch" ]] || continue
    COUNT=$((COUNT + 1))
    if (( NOW_EPOCH - snap_epoch > MAX_AGE_SECONDS )); then
      OLD+=("$base")
    fi
  done < <(find "$ARCHIVE_ROOT" -mindepth 1 -maxdepth 1 -type d -name '????-??-??T????Z' 2>/dev/null | sort)
fi

STATUS="pass"
[[ "${#OLD[@]}" -eq 0 ]] || STATUS="fail"

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","archive_root":"%s","snapshot_count":%s,"retention_days":%s,"old_snapshots":[' "$STATUS" "$ARCHIVE_ROOT" "$COUNT" "$RETENTION_DAYS"
  first=1
  if [[ "${#OLD[@]}" -gt 0 ]]; then
    for item in "${OLD[@]}"; do
      [[ "$first" == "1" ]] || printf ','
      first=0
      printf '"%s"' "$item"
    done
  fi
  printf ']}\n'
else
  echo "# DB snapshot retention audit"
  echo
  echo "Status: $STATUS"
  echo "- archive_root: $ARCHIVE_ROOT"
  echo "- snapshot_count: $COUNT"
  echo "- retention_days: $RETENTION_DAYS"
  if [[ "${#OLD[@]}" -gt 0 ]]; then
    for item in "${OLD[@]}"; do
      echo "- old: $item"
    done
  fi
fi

[[ "$STATUS" == "pass" ]]
