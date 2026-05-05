#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DRY_RUN=0
RUN=0
SOURCE_REL="mac_archive/macbook_2026-05-01_import"
DEST_REL="mac_archive/compressed"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --run)
      RUN=1
      shift
      ;;
    -h|--help)
      echo "usage: bash scripts/macbook_import_archive_migration.sh [--repo-root PATH] [--dry-run|--run]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

SOURCE="$REPO_ROOT/$SOURCE_REL"
DEST_DIR="$REPO_ROOT/$DEST_REL"
ARCHIVE="$DEST_DIR/macbook_2026-05-01_import.tar.gz"

if [[ ! -d "$SOURCE" ]]; then
  echo "source missing: $SOURCE" >&2
  exit 1
fi

echo "source: $SOURCE_REL"
echo "archive: $DEST_REL/macbook_2026-05-01_import.tar.gz"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY-RUN: would create archive and remove source after successful compression"
  exit 0
fi

if [[ "$RUN" != "1" ]]; then
  echo "refusing write: pass --run after Wave-1 close is tagged" >&2
  exit 2
fi

mkdir -p "$DEST_DIR"
tar -C "$REPO_ROOT/mac_archive" -czf "$ARCHIVE" "macbook_2026-05-01_import"
rm -rf "$SOURCE"
echo "archived: $ARCHIVE"
