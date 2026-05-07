#!/usr/bin/env bash
# Run Cycle-14 calibration diagnostics. Diagnosis-only; no bot behavior changes.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DATASET="logs/edge_replay/cycle13_live/replay_dataset.jsonl"
OUT_DIR="logs/edge_replay/cycle14"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset) DATASET="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/edge_replay/run_calibration_diagnostic.sh [--dataset PATH] [--out-dir PATH]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_DIR"

.venv/bin/python scripts/edge_replay/calibration_audit.py \
  --dataset "$DATASET" \
  --output "$OUT_DIR/calibration_audit.json"

.venv/bin/python scripts/edge_replay/per_source_movement_audit.py \
  --dataset "$DATASET" \
  --output "$OUT_DIR/per_source_movement.json"

.venv/bin/python scripts/edge_replay/llm_split_audit.py \
  --dataset "$DATASET" \
  --output "$OUT_DIR/llm_split.json"

.venv/bin/python scripts/edge_replay/synthetic_injection_lanes.py \
  --output "$OUT_DIR/synthetic_lanes.json"

.venv/bin/python scripts/edge_replay/assemble_calibration_findings.py \
  --out-dir "$OUT_DIR" \
  --output "$OUT_DIR/structured_findings.json"

echo "calibration_diagnostic_out_dir=$OUT_DIR"
