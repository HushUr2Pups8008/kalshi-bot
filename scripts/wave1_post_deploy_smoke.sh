#!/usr/bin/env bash
# Wave-1 post-deploy smoke wrapper.
#
# Runs one focused regression block per Wave-1 deploy surface:
# OBS-005, MATCH-001, OBS-003, EXEC-002, GOV-002, and the consolidated
# Wave-1 landing ladder. This script is orchestration only; assertions live
# in pytest.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PYTEST="${REPO_ROOT}/.venv/bin/pytest"

if [[ ! -x "$PYTEST" ]]; then
  echo "error: pytest not found at $PYTEST" >&2
  exit 2
fi

cd "$REPO_ROOT"

run_check() {
  local label="$1"
  shift
  echo "==> ${label}"
  "$PYTEST" "$@" -q
  echo
}

run_check "OBS-005 cooldown sentinel" \
  tests/test_executor.py::TestCooldownSentinelOBS005

run_check "MATCH-001 B-prime suppression guard" \
  tests/test_market_matcher.py::TestLowQualityMatchSuppression \
  tests/test_market_matcher.py::TestSuppressionTokenGuardMATCH001

run_check "OBS-003 BlendTask SKIPPED stream" \
  tests/test_blend_task.py::test_obs003_blocked_path_writes_via_injected_logger_log_skipped \
  tests/test_blend_task.py::test_obs003_skipped_payload_carries_required_keys

run_check "EXEC-002 same-series correlation guard" \
  tests/test_blend_task.py::test_fisa_replay_three_same_series_only_one_enqueues \
  tests/test_blend_task.py::test_cross_series_burst_does_not_interfere \
  tests/test_blend_task.py::test_window_expiry_allows_second_same_series_candidate \
  tests/test_blend_task.py::test_window_override_zero_disables_guard

run_check "GOV-002 governance monitor validation counters" \
  tests/test_governance_monitor.py

run_check "wave1-ladder post-soak simulation forecast" \
  tests/test_post_soak_landing_simulation.py \
  tests/test_wave1_acceptance_ladder_forecast.py
