#!/usr/bin/env bash
#
# Canonical pytest entry point for kalshi-bot. Picks .venv/bin/python when
# present, writes run logs and metadata under logs/tests/, and registers
# each run in logs/tests/run_registry.jsonl. Use --detach to run in the
# background. See usage() below for argument forms.
#
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$ROOT_DIR/scripts/run_tests.sh"
cd "$ROOT_DIR"

DETACH=0
EXACT_COMMAND=0
RUN_ID=""
LOG_FILE=""
META_FILE=""
REGISTRY_FILE="logs/tests/run_registry.jsonl"
NO_TEE="${SAFE_TEST_NO_TEE:-0}"
DETACHED_CHILD="${SAFE_TEST_DETACHED:-0}"
REGISTRY_STARTED="${SAFE_TEST_REGISTRY_STARTED:-0}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_tests.sh [--detach] [pytest args...]
  scripts/run_tests.sh [--detach] -- <command> [args...]

Examples:
  scripts/run_tests.sh
  scripts/run_tests.sh --detach
  scripts/run_tests.sh tests/test_budget_manager.py -q
  scripts/run_tests.sh --detach -- python3 -m pytest tests -q

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
  CMD=("$PYTHON_BIN" -m pytest "$@")
else
  CMD=("$PYTHON_BIN" -m pytest)
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
  base_run_id="$RUN_ID"
  suffix=0
  while :; do
    if [[ "$suffix" -eq 0 ]]; then
      candidate_run_id="$base_run_id"
    else
      candidate_run_id="${base_run_id}_${suffix}"
    fi
    candidate_log_file="logs/tests/pytest_${candidate_run_id}.log"
    if (set -C; : > "$candidate_log_file") 2>/dev/null; then
      RUN_ID="$candidate_run_id"
      LOG_FILE="$candidate_log_file"
      break
    fi
    suffix=$((suffix + 1))
  done
fi
if [[ -z "$META_FILE" ]]; then
  META_FILE="logs/tests/pytest_${RUN_ID}.json"
fi

START_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

write_metadata() {
  local end_time="$1"
  local exit_status="$2"
  local interrupted="$3"
  "$PYTHON_BIN" - "$META_FILE" "$RUN_ID" "$START_TIME" "$end_time" "$exit_status" "$interrupted" "$GIT_COMMIT" "$LOG_FILE" "${CMD[@]}" <<'PY'
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

status_from_exit() {
  local exit_status="$1"
  local interrupted="$2"
  if [[ "$interrupted" == "true" ]]; then
    echo "interrupted"
  elif [[ "$exit_status" == "0" ]]; then
    echo "passed"
  else
    echo "failed"
  fi
}

append_registry() {
  local event="$1"
  local status="$2"
  local end_time="$3"
  local exit_status="$4"
  local detached="$5"
  "$PYTHON_BIN" - "$REGISTRY_FILE" "$event" "$RUN_ID" "pytest" "$START_TIME" "$end_time" "$status" "$exit_status" "$GIT_COMMIT" "$LOG_FILE" "$META_FILE" "$detached" "${CMD[@]}" <<'PY'
import json
import sys

registry_file = sys.argv[1]
event = sys.argv[2]
run_id = sys.argv[3]
kind = sys.argv[4]
start_time = sys.argv[5]
end_time = sys.argv[6] or None
status = sys.argv[7]
exit_status = None if sys.argv[8] == "" else int(sys.argv[8])
git_commit = sys.argv[9]
log_file = sys.argv[10]
metadata_file = sys.argv[11]
detached = sys.argv[12].lower() == "true"
command = sys.argv[13:]

row = {
    "event": event,
    "run_id": run_id,
    "kind": kind,
    "start_time_utc": start_time,
    "end_time_utc": end_time,
    "status": status,
    "exit_status": exit_status,
    "git_commit": git_commit,
    "command": command,
    "log_file": log_file,
    "metadata_file": metadata_file,
    "detached": detached,
}

with open(registry_file, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
    fh.write("\n")
PY
}

if [[ "$DETACH" -eq 1 ]]; then
  write_metadata "" "" false
  append_registry "start" "running" "" "" true
  printf '[%s] ===== DETACHED RUN REQUESTED id=%s utc=%s =====\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN_ID" "$START_TIME" >> "$LOG_FILE"
  printf '[%s] command=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${CMD[*]}" >> "$LOG_FILE"
  nohup env SAFE_TEST_NO_TEE=1 SAFE_TEST_DETACHED=1 SAFE_TEST_REGISTRY_STARTED=1 "$SCRIPT_PATH" \
    --run-id "$RUN_ID" \
    --log-file "$LOG_FILE" \
    --meta-file "$META_FILE" \
    -- "${CMD[@]}" </dev/null >> "$LOG_FILE" 2>&1 &
  pid="$!"
  echo "Detached test run started."
  echo "PID: $pid"
  echo "Log: $LOG_FILE"
  echo "Metadata: $META_FILE"
  echo "Tail: tail -n 80 -f $LOG_FILE"
  exit 0
fi

child_pid=""
finished=0

timestamp_lines() {
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line"
  done
}

LOG_FIFO="${TMPDIR:-/tmp}/kalshi_test_${RUN_ID}_$$.fifo"
mkfifo "$LOG_FIFO"
if [[ "$NO_TEE" == "1" ]]; then
  timestamp_lines < "$LOG_FIFO" >> "$LOG_FILE" &
  logger_pid="$!"
else
  timestamp_lines < "$LOG_FIFO" | tee -a "$LOG_FILE" &
  logger_pid="$!"
fi
exec > "$LOG_FIFO" 2>&1
rm -f "$LOG_FIFO"

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
  append_registry "finish" "interrupted" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" 130 "$([[ "$DETACHED_CHILD" == "1" ]] && echo true || echo false)"
  echo "===== RUN END status=130 utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  exec >/dev/null 2>&1
  wait "$logger_pid" 2>/dev/null || true
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
if [[ "$REGISTRY_STARTED" != "1" ]]; then
  append_registry "start" "running" "" "" "$([[ "$DETACHED_CHILD" == "1" ]] && echo true || echo false)"
fi

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
append_registry "finish" "$(status_from_exit "$status" false)" "$END_TIME" "$status" "$([[ "$DETACHED_CHILD" == "1" ]] && echo true || echo false)"

exec >/dev/null 2>&1
wait "$logger_pid" 2>/dev/null || true
exit "$status"
