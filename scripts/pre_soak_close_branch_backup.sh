#!/usr/bin/env bash
# Pre-soak-close branch + log backup script.
#
# Run on the day-13 (or day-14) operator runbook step BEFORE the Wave-1 base-stack
# deploy commit. Creates the rollback anchors that the post-soak-rollback-runbook
# refers to. All operations are reversible up to the point the script exits.
#
# Outputs:
#   1. Annotated git tag `pre-wave-1-deploy-${DATE}` pointing at HEAD
#   2. Pushed branch `backup/pre-wave-1-deploy-${DATE}` mirroring HEAD
#   3. Compressed tarball of logs/governance/decisions.jsonl + logs/trades/live/trades.jsonl
#      written to mac_archive/pre_wave1_${DATE}/
#
# Usage:
#   bash scripts/pre_soak_close_branch_backup.sh
#   bash scripts/pre_soak_close_branch_backup.sh --dry-run    # preview without state changes
#   bash scripts/pre_soak_close_branch_backup.sh --no-push    # local artifacts only (no remote push)
#
# Idempotency:
#   - Tag creation is idempotent (refuses to overwrite existing tag)
#   - Branch push is idempotent (`-u` plus refusal on diverging upstream)
#   - Archive tarball is timestamped per-second so re-running creates a new tarball
#
# Failure handling: any step's failure aborts the script with a non-zero exit code.
# No partial cleanup; operator inspects state.

set -euo pipefail

DRY_RUN=0
NO_PUSH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-push) NO_PUSH=1; shift ;;
    -h|--help)
      head -25 "$0" | tail -23
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DATE="$(date -u +%Y-%m-%d)"
TS="$(date -u +%Y-%m-%dT%H%M%SZ)"
TAG_NAME="pre-wave-1-deploy-${DATE}"
BRANCH_NAME="backup/pre-wave-1-deploy-${DATE}"
ARCHIVE_DIR="mac_archive/pre_wave1_${DATE}"
ARCHIVE_TARBALL="${ARCHIVE_DIR}/logs_${TS}.tar.gz"

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] $*"
  else
    echo "[RUN] $*"
    eval "$@"
  fi
}

# Pre-flight checks
echo "==> Pre-flight"
echo "  date_utc: ${DATE}"
echo "  ts_utc:   ${TS}"
echo "  HEAD:     $(git rev-parse --short HEAD)"
echo "  branch:   $(git rev-parse --abbrev-ref HEAD)"
echo "  tag:      ${TAG_NAME}"
echo "  branch:   ${BRANCH_NAME}"
echo "  archive:  ${ARCHIVE_TARBALL}"
echo "  dry-run:  ${DRY_RUN}"
echo "  no-push:  ${NO_PUSH}"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: working tree has uncommitted changes. Commit or stash before running." >&2
  exit 1
fi

if git rev-parse --verify "${TAG_NAME}" >/dev/null 2>&1; then
  echo "ERROR: tag ${TAG_NAME} already exists. Refusing to overwrite. Either delete it or wait until tomorrow's UTC date." >&2
  exit 1
fi

# Step 1: tag HEAD
echo
echo "==> Step 1: tag HEAD as ${TAG_NAME}"
run "git tag -a '${TAG_NAME}' -m 'pre-wave-1-deploy rollback anchor; created by pre_soak_close_branch_backup.sh on ${TS}'"

# Step 2: create + (optionally) push backup branch
echo
echo "==> Step 2: create backup branch ${BRANCH_NAME}"
run "git branch '${BRANCH_NAME}' HEAD"
if [[ "$NO_PUSH" == "0" ]]; then
  run "git push origin '${BRANCH_NAME}' '${TAG_NAME}'"
else
  echo "[skip] no-push set; branch + tag local only"
fi

# Step 3: archive tarball of governance + trade logs
echo
echo "==> Step 3: archive logs to ${ARCHIVE_TARBALL}"
run "mkdir -p '${ARCHIVE_DIR}'"
LOGS_TO_ARCHIVE=()
[[ -f logs/governance/decisions.jsonl ]] && LOGS_TO_ARCHIVE+=("logs/governance/decisions.jsonl")
[[ -f logs/trades/live/trades.jsonl ]]    && LOGS_TO_ARCHIVE+=("logs/trades/live/trades.jsonl")

if [[ ${#LOGS_TO_ARCHIVE[@]} -eq 0 ]]; then
  echo "WARNING: no log files found at logs/governance/decisions.jsonl or logs/trades/live/trades.jsonl"
  echo "         skipping tarball creation"
else
  run "tar czf '${ARCHIVE_TARBALL}' ${LOGS_TO_ARCHIVE[*]}"
  if [[ "$DRY_RUN" == "0" ]]; then
    echo "  archived size: $(du -h "${ARCHIVE_TARBALL}" | cut -f1)"
  fi
fi

# Done
echo
echo "==> Done"
echo "Rollback anchors created:"
echo "  - tag:    ${TAG_NAME}      (${TAG_NAME}^{commit} = $(git rev-parse --short HEAD))"
echo "  - branch: ${BRANCH_NAME}"
[[ -n "${ARCHIVE_TARBALL:-}" && -f "${ARCHIVE_TARBALL}" ]] && echo "  - logs:   ${ARCHIVE_TARBALL}"
echo
echo "To roll back later:"
echo "  git checkout ${TAG_NAME}              # detached HEAD at the anchor"
echo "  git reset --hard ${TAG_NAME}          # wipe local main back to anchor (DESTRUCTIVE)"
echo "  git push -f origin ${TAG_NAME}:main   # force-push main back to anchor (VERY DESTRUCTIVE; confirm first)"
echo
echo "See docs/governance/post-soak-rollback-runbook.md for the full rollback playbook."
