#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DETACH=0
EXACT_COMMAND=0
RUN_ID=""
LOG_FILE=""
META_FILE=""
NO_TEE="${SAFE_TEST_NO_TEE:-0}"
DETACHED_CHILD="${SAFE_TEST_DETACHED:-0}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_tests.sh [--detach] [pytest args...]
  scripts/run_tests.sh [--detach] -- <command> [args...]

Examples:
  scripts/run_tests.sh
  scripts/run_tests.sh --detach
  scripts/run_tests.sh -- tests/test_budget_manager.py -q
  scripts/run_tests.sh --detach -- python -m pytest tests -q

Logs:
  logs/tests/pytest_YYYYMMDD_HHMMSS.log
  logs/tests/pytest_YYYYMMDD_HHMMSS.json
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --detach)
      DETACH=1
      shift
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --log-file)
      LOG_FILE="$2"
      shift 2
      ;;
    --meta-file)
      META_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      EXACT_COMMAND=1
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$EXACT_COMMAND" -eq 1 ]]; then
  CMD=("$@")
elif [[ "$#" -gt 0 ]]; then
  CMD=(python -m pytest "$@")
else
  CMD=(python -m pytest)
fi

if [[ "${#CMD[@]}" -eq 0 ]]; then
  echo "No command provided."
  usage
  exit 2
fi

mkdir -p logs/tests

if [[ -z "$RUN_ID" ]]; then
  RUN_ID="$(date -u +%Y%m%d_%H%M%S)"
fi
if [[ -z "$LOG_FILE" ]]; then
  LOG_FILE="logs/tests/pytest_${RUN_ID}.log"
fi
if [[ -z "$META_FILE" ]]; then
  META_FILE="logs/tests/pytest_${RUN_ID}.json"
fi

if [[ "$DETACH" -eq 1 ]]; then
  nohup env SAFE_TEST_NO_TEE=1 SAFE_TEST_DETACHED=1 "$0" \
    --run-id "$RUN_ID" \
    --log-file "$LOG_FILE" \
    --meta-file "$META_FILE" \
    -- "${CMD[@]}" </dev/null >/dev/null 2>&1 &
  pid="$!"
  echo "Detached test run started."
  echo "PID: $pid"
  echo "Log: $LOG_FILE"
  echo "Metadata: $META_FILE"
  echo "Tail: tail -n 80 -f $LOG_FILE"
  exit 0
fi

START_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
child_pid=""
finished=0

write_metadata() {
  local end_time="$1"
  local exit_status="$2"
  local interrupted="$3"
  python - "$META_FILE" "$RUN_ID" "$START_TIME" "$end_time" "$exit_status" "$interrupted" "$GIT_COMMIT" "$LOG_FILE" "${CMD[@]}" <<'PY'
import json
import sys

meta_file = sys.argv[1]
run_id = sys.argv[2]
start_time = sys.argv[3]
end_time = sys.argv[4] or None
exit_status = None if sys.argv[5] == "" else int(sys.argv[5])
interrupted = sys.argv[6].lower() == "true"
git_commit = sys.argv[7]
log_file = sys.argv[8]
command = sys.argv[9:]

metadata = {
    "run_id": run_id,
    "start_time_utc": start_time,
    "end_time_utc": end_time,
    "exit_status": exit_status,
    "interrupted": interrupted,
    "git_commit": git_commit,
    "command": command,
    "log_file": log_file,
}

with open(meta_file, "w", encoding="utf-8") as fh:
    json.dump(metadata, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

timestamp_stream='
{
  print strftime("[%Y-%m-%dT%H:%M:%SZ]"), $0;
  fflush();
}
'

if [[ "$NO_TEE" == "1" ]]; then
  exec > >(TZ=UTC awk "$timestamp_stream" >> "$LOG_FILE") 2>&1
else
  exec > >(TZ=UTC awk "$timestamp_stream" | tee -a "$LOG_FILE") 2>&1
fi

on_interrupt() {
  local signal="$1"
  if [[ "$finished" -eq 1 ]]; then
    exit 130
  fi
  finished=1
  echo "===== RUN INTERRUPTED signal=${signal} utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  write_metadata "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 130 true
  echo "===== RUN END status=130 utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  exit 130
}

trap 'on_interrupt INT' INT
trap 'on_interrupt TERM' TERM
if [[ "$DETACHED_CHILD" == "1" ]]; then
  trap '' HUP
else
  trap 'on_interrupt HUP' HUP
fi

write_metadata "" "" false

echo "===== RUN START id=${RUN_ID} utc=${START_TIME} ====="
echo "git_commit=${GIT_COMMIT}"
echo "command=${CMD[*]}"
echo "log_file=${LOG_FILE}"
echo "metadata_file=${META_FILE}"

"${CMD[@]}" &
child_pid="$!"
wait "$child_pid"
status="$?"
child_pid=""
finished=1

END_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "===== RUN END status=${status} utc=${END_TIME} ====="
write_metadata "$END_TIME" "$status" false

exit "$status"
