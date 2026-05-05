#!/usr/bin/env bash
# Fire-time smoke wrapper for Day-7 close + first Wave-1 deploy checks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTEST="$REPO_ROOT/.venv/bin/pytest"

cd "$REPO_ROOT"

if [[ ! -x "$PYTEST" ]]; then
  echo "error: pytest not found at $PYTEST" >&2
  exit 2
fi

echo "==> Day-7 gate-7 soak invariant"
bash "$REPO_ROOT/scripts/check_soak_invariant.sh" --json
echo

echo "==> Wave-1 commit-1 OBS-005 cooldown sentinel smoke"
"$PYTEST" tests/test_executor.py::TestCooldownSentinelOBS005 -q
echo

echo "==> Wave-1 full post-deploy smoke"
bash "$REPO_ROOT/scripts/wave1_post_deploy_smoke.sh"
echo

echo "==> bothealth post-deploy report"
bash "$REPO_ROOT/scripts/bothealth.sh" --post-deploy
