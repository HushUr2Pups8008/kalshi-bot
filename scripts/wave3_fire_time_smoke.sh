#!/usr/bin/env bash
# Fire-time smoke wrapper for Wave-3 Lever B and Lever C deploys.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTEST="$REPO_ROOT/.venv/bin/pytest"

cd "$REPO_ROOT"

if [[ ! -x "$PYTEST" ]]; then
  echo "error: pytest not found at $PYTEST" >&2
  exit 2
fi

MODE="all"
case "${1:-}" in
  --lever-b) MODE="lever-b" ;;
  --lever-c) MODE="lever-c" ;;
  --all|"") MODE="all" ;;
  -h|--help)
    echo "usage: bash scripts/wave3_fire_time_smoke.sh [--all | --lever-b | --lever-c]"
    exit 0
    ;;
  *)
    echo "unknown arg: $1" >&2
    exit 2
    ;;
esac

if [[ "$MODE" == "all" || "$MODE" == "lever-b" ]]; then
  echo "==> Wave-3 Lever B G1=0.04 floor-lock harness"
  "$PYTEST" tests/test_lever_b_g1_floor_lock.py -q
  echo
fi

if [[ "$MODE" == "all" || "$MODE" == "lever-c" ]]; then
  echo "==> Wave-3 Lever C cross-series correlation harness"
  "$PYTEST" tests/test_lever_c_cross_series_correlation.py -q
  echo
fi

echo "==> Calibration drift wiring audit"
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/calibration_drift_wiring_audit.py" --json
echo

echo "==> Operator alert routing audit"
bash "$REPO_ROOT/scripts/operator_alert_routing_audit.sh" --json
echo

echo "==> bothealth post-deploy report"
bash "$REPO_ROOT/scripts/bothealth.sh" --post-deploy
echo

echo "Wave-3 watch handoff: monitor Lever B attribution, Lever C suppression rate, CALIBRATION_CHECK drift, KILL_SWITCH, VALIDATION_ERROR, and batch_aborted."
