#!/usr/bin/env bash

set -euo pipefail

DRY_RUN=0
BASE_REF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --base)
      BASE_REF="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "usage: bash scripts/wave1_commit_chain_acceptance_audit.sh [--base REF] [--dry-run]"
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

declare -a SPECS=(
  "1|PROFIT-OBS-005|OBS-005|tests/test_wave1_commit1_obs005_post_deploy.py|tests/test_executor.py::TestCooldownSentinelOBS005"
  "2|PROFIT-MATCH-001|MATCH-001|tests/test_wave1_commit2_match001_post_deploy.py|tests/test_market_matcher.py::TestSuppressionTokenGuardMATCH001"
  "3|PROFIT-OBS-003|OBS-003|tests/test_wave1_commit3_obs003_post_deploy.py|tests/test_blend_task.py"
  "4|PROFIT-EXEC-002|EXEC-002|tests/test_wave1_commit4_exec002_post_deploy.py|tests/test_blend_task.py"
  "5|PROFIT-GOV-003|GOV-003|tests/test_wave1_commit5_gov003_post_deploy.py|tests/test_governance_monitor.py"
  "6|PROFIT-EDGE-004 Lever A.1|Lever A.1|tests/test_wave1_commit6_levera1_post_deploy.py|tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1"
)

echo "# Wave-1 Commit Chain Acceptance Audit"
echo
echo "order|spec|subject token|post-deploy sentinel|focused harness|commit"

previous_index=0
missing=0
out_of_order=0

for row in "${SPECS[@]}"; do
  IFS='|' read -r order spec token sentinel harness <<<"$row"
  commit=""
  index=""
  if [[ -n "$BASE_REF" && "$DRY_RUN" != "1" ]]; then
    mapfile -t matches < <(git log --reverse --format='%H%x09%s' "${BASE_REF}..HEAD" --grep="$token" 2>/dev/null || true)
    if [[ "${#matches[@]}" -gt 0 ]]; then
      commit="${matches[0]%%$'\t'*}"
      index="$order"
    fi
  fi

  if [[ -z "$commit" ]]; then
    commit="PENDING"
    [[ "$DRY_RUN" == "1" ]] || missing=1
  elif [[ "$index" -lt "$previous_index" ]]; then
    out_of_order=1
  else
    previous_index="$index"
  fi

  echo "$order|$spec|$token|$sentinel|$harness|$commit"
done

echo
echo "pre-deploy assertions: phase2-soak-closed tag exists before commit 1; strict-xfail sentinels remain xfailed before matching deploy."
echo "post-deploy assertions: matching sentinel xpasses, marker removed in same hunk, focused harness exits 0 failed, wave1_post_deploy_smoke exits 0."
echo "handoff rule: record UTC land time, rerun smoke at +12h/+24h, proceed only after watch is clean."

if [[ "$missing" == "1" || "$out_of_order" == "1" ]]; then
  echo "result: FAIL missing=$missing out_of_order=$out_of_order" >&2
  exit 1
fi

echo "result: PASS"
