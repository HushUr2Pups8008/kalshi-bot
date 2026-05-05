#!/usr/bin/env bash
# Validate that the repo pre-commit hook path and VERSION sync hook are wired.

set -euo pipefail

JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/precommit_hook_health_audit.sh [--json]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_PATH="$(git -C "$REPO_ROOT" config --get core.hooksPath || true)"
HOOK="$REPO_ROOT/.githooks/pre-commit"
SYNC_SCRIPT="$REPO_ROOT/scripts/sync_readme_version.py"

PASS=1
CHECKS=()

add_check() {
  local name="$1"
  local passed="$2"
  local detail="$3"
  CHECKS+=("${name}|${passed}|${detail}")
  if [[ "$passed" != "1" ]]; then
    PASS=0
  fi
}

add_check "core.hooksPath" "$([[ "$HOOKS_PATH" == ".githooks" ]] && echo 1 || echo 0)" "expected .githooks, got ${HOOKS_PATH:-<unset>}"
add_check ".githooks/pre-commit exists" "$([[ -f "$HOOK" ]] && echo 1 || echo 0)" "$HOOK"
add_check ".githooks/pre-commit executable" "$([[ -x "$HOOK" ]] && echo 1 || echo 0)" "$HOOK"
add_check "hook syntax" "$(bash -n "$HOOK" >/dev/null 2>&1 && echo 1 || echo 0)" "bash -n .githooks/pre-commit"
add_check "sync script exists" "$([[ -f "$SYNC_SCRIPT" ]] && echo 1 || echo 0)" "$SYNC_SCRIPT"
add_check "sync script executable" "$([[ -x "$SYNC_SCRIPT" ]] && echo 1 || echo 0)" "$SYNC_SCRIPT"
add_check "hook references sync script" "$(grep -q "scripts/sync_readme_version.py" "$HOOK" && echo 1 || echo 0)" "scripts/sync_readme_version.py"
add_check "hook restages README" "$(grep -q "git add README.md" "$HOOK" && echo 1 || echo 0)" "git add README.md"

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","checks":[' "$([[ "$PASS" == "1" ]] && echo pass || echo fail)"
  first=1
  for check in "${CHECKS[@]}"; do
    IFS='|' read -r name passed detail <<<"$check"
    [[ "$first" == "1" ]] || printf ','
    first=0
    python3 -c 'import json,sys; print(json.dumps({"name":sys.argv[1],"passed":sys.argv[2]=="1","detail":sys.argv[3]},separators=(",",":")), end="")' "$name" "$passed" "$detail"
  done
  printf ']}\n'
else
  echo "# Pre-Commit Hook Health Audit"
  echo
  echo "Status: $([[ "$PASS" == "1" ]] && echo pass || echo fail)"
  echo
  echo "| check | status | detail |"
  echo "|---|---|---|"
  for check in "${CHECKS[@]}"; do
    IFS='|' read -r name passed detail <<<"$check"
    echo "| $name | $([[ "$passed" == "1" ]] && echo PASS || echo FAIL) | $detail |"
  done
fi

[[ "$PASS" == "1" ]]
