#!/usr/bin/env bash
# Pre-Day-7-close dry-run wrapper for operator gates.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

REPORT=""
STRICT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report) REPORT="${2:-}"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/pre_day7_smoke.sh [--report PATH] [--strict]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$REPORT" ]]; then
  REPORT="$REPO_ROOT/logs/app/pre_day7_smoke_$(date -u +%Y%m%dT%H%M%SZ).md"
fi

mkdir -p "$(dirname "$REPORT")"

FAIL=0
WARN=0

run_gate() {
  local name="$1"
  local severity="$2"
  shift 2

  {
    echo
    echo "## $name"
    echo
    echo '```text'
    echo "$ $*"
  } >> "$REPORT"

  set +e
  "$@" >> "$REPORT" 2>&1
  local rc=$?
  set -e

  {
    echo "exit=$rc"
    echo '```'
  } >> "$REPORT"

  if [[ "$rc" -eq 0 ]]; then
    echo "PASS $name"
  elif [[ "$severity" == "warn" ]]; then
    WARN=1
    echo "WARN $name rc=$rc"
  else
    FAIL=1
    echo "FAIL $name rc=$rc"
  fi
}

{
  echo "# Pre-Day-7 Smoke"
  echo
  echo "- generated_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- repo_root: $REPO_ROOT"
  echo "- head: $(git rev-parse --short HEAD)"
  echo "- mode: dry-run"
} > "$REPORT"

run_gate "launchd template render" hard bash ops/launchd/install.sh --print
run_gate "launchd plist drift audit" warn bash scripts/launchd_plist_drift_audit.sh --json
run_gate "doc cross-reference audit" warn .venv/bin/python scripts/doc_xref_audit.py
run_gate "Wave-1 commit chain acceptance dry-run" hard bash scripts/wave1_commit_chain_acceptance_audit.sh --dry-run
run_gate "soak invariant gate 7" hard bash scripts/check_soak_invariant.sh --json
run_gate "pre-soak-close branch backup dry-run" hard bash scripts/pre_soak_close_branch_backup.sh --dry-run --no-push
run_gate "DB backup health audit" warn bash scripts/db_backup_health_audit.sh --json
run_gate "Wave-1 post-deploy smoke pre-deploy sentinel" warn bash scripts/wave1_post_deploy_smoke.sh

{
  echo
  echo "## Summary"
  echo
  echo "- fail: $FAIL"
  echo "- warn: $WARN"
  echo "- strict: $STRICT"
} >> "$REPORT"

echo "report: $REPORT"

if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
if [[ "$STRICT" -eq 1 && "$WARN" -ne 0 ]]; then
  exit 1
fi
exit 0
