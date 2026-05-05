#!/usr/bin/env bash
# Audit whether governance safety events surface through operator-visible reports.

set -euo pipefail

JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/operator_alert_routing_audit.sh [--json]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
GOV_LOG="$REPO_ROOT/logs/governance/decisions.jsonl"
BOTHEALTH="$REPO_ROOT/scripts/bothealth.sh"
REPORT_DIR="$REPO_ROOT/logs/app"
REPORT="$REPORT_DIR/operator_alert_routing_$(date -u +%Y%m%dT%H%M%SZ).md"
mkdir -p "$REPORT_DIR"

KILL_SWITCH_COUNT=0
VALIDATION_ERROR_COUNT=0
BATCH_ABORTED_COUNT=0
if [[ -f "$GOV_LOG" ]]; then
  KILL_SWITCH_COUNT="$(grep -c 'KILL_SWITCH' "$GOV_LOG" || true)"
  VALIDATION_ERROR_COUNT="$(grep -c 'VALIDATION_ERROR' "$GOV_LOG" || true)"
  BATCH_ABORTED_COUNT="$(grep '"GOVERNANCE_CYCLE_END"' "$GOV_LOG" | grep -c '"batch_aborted": true' || true)"
fi

BOTHEALTH_SURFACES=0
if grep -q 'KILL_SWITCH' "$BOTHEALTH" && grep -q 'VALIDATION_ERROR' "$BOTHEALTH" && grep -q 'batch_aborted' "$BOTHEALTH"; then
  BOTHEALTH_SURFACES=1
fi

OSASCRIPT_AVAILABLE=0
if command -v osascript >/dev/null 2>&1; then
  OSASCRIPT_AVAILABLE=1
fi

STATUS="pass"
if [[ "$BOTHEALTH_SURFACES" != "1" ]]; then
  STATUS="fail"
fi

{
  echo "# Operator Alert Routing Audit"
  echo
  echo "Status: \`$STATUS\`"
  echo
  echo "## Safety Event Counts"
  echo
  echo "- KILL_SWITCH: \`$KILL_SWITCH_COUNT\`"
  echo "- VALIDATION_ERROR: \`$VALIDATION_ERROR_COUNT\`"
  echo "- batch_aborted: \`$BATCH_ABORTED_COUNT\`"
  echo
  echo "## Routing Surfaces"
  echo
  echo "- bothealth surfaces safety counters: \`$BOTHEALTH_SURFACES\`"
  echo "- osascript notification available: \`$OSASCRIPT_AVAILABLE\`"
} >"$REPORT"

if [[ "$OSASCRIPT_AVAILABLE" == "1" && "$STATUS" != "pass" ]]; then
  osascript -e "display notification \"operator alert routing audit: $STATUS\" with title \"kalshi-bot\"" 2>/dev/null || true
fi

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","report":"%s","counts":{"KILL_SWITCH":%s,"VALIDATION_ERROR":%s,"batch_aborted":%s},"surfaces":{"bothealth":%s,"osascript":%s}}\n' \
    "$STATUS" "$REPORT" "$KILL_SWITCH_COUNT" "$VALIDATION_ERROR_COUNT" "$BATCH_ABORTED_COUNT" "$BOTHEALTH_SURFACES" "$OSASCRIPT_AVAILABLE"
else
  echo "Report: $REPORT"
  echo "Status: $STATUS"
fi

[[ "$STATUS" == "pass" ]]
