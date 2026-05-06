#!/usr/bin/env bash

set -euo pipefail

TRADES="logs/trades/live/trades.jsonl"
START=""
END=""
JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --trades) TRADES="${2:-}"; shift 2 ;;
    --start) START="${2:-}"; shift 2 ;;
    --end) END="${2:-}"; shift 2 ;;
    --json) JSON=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/wave2_branch_a_invariant_check.sh --start ISO --end ISO [--trades PATH] [--json]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$START" || -z "$END" ]]; then
  echo "error: --start and --end required" >&2
  exit 2
fi

COUNT="$(node - "$TRADES" "$START" "$END" <<'NODE'
const fs = require('fs');
const [file, startRaw, endRaw] = process.argv.slice(2);
const start = Date.parse(startRaw);
const end = Date.parse(endRaw);
let count = 0;
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      const t = Date.parse(row.ts || row.decided_at || row.created_at || '');
      if (!(t >= start && t < end)) continue;
      if (row.operator_initiated === true || row.operator_initiated === 'true') count++;
    } catch {}
  }
}
console.log(count);
NODE
)"

STATUS="pass"
[[ "$COUNT" == "0" ]] || STATUS="fail"

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","trades":"%s","start":"%s","end":"%s","operator_initiated_trades":%s}\n' "$STATUS" "$TRADES" "$START" "$END" "$COUNT"
else
  echo "# Wave-2 Branch A invariant check"
  echo
  echo "Status: $STATUS"
  echo "- trades: $TRADES"
  echo "- start: $START"
  echo "- end: $END"
  echo "- operator_initiated_trades: $COUNT"
fi

[[ "$STATUS" == "pass" ]]
