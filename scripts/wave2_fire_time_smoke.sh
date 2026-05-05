#!/usr/bin/env bash
# Fire-time smoke wrapper for Wave-2 Branch C deploy and 24h watch handoff.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTEST="$REPO_ROOT/.venv/bin/pytest"

cd "$REPO_ROOT"

if [[ ! -x "$PYTEST" ]]; then
  echo "error: pytest not found at $PYTEST" >&2
  exit 2
fi

echo "==> Wave-2 Branch C feed-selection harness"
"$PYTEST" tests/test_branch_c_feed_selection_rubric.py -q
echo

echo "==> Wave-2 A.1+ preload harness"
"$PYTEST" tests/test_wave2_preload_harnesses.py -q
echo

echo "==> Calibration drift wiring audit"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/calibration_drift_wiring_audit.py" --json
echo

echo "==> Operator alert routing audit"
bash "$REPO_ROOT/scripts/operator_alert_routing_audit.sh" --json
echo

echo "==> bothealth post-deploy report"
bash "$REPO_ROOT/scripts/bothealth.sh" --post-deploy
echo

echo "Wave-2 24h watch handoff: monitor Branch C feeds, CALIBRATION_CHECK drift, KILL_SWITCH, VALIDATION_ERROR, and batch_aborted."
