#!/usr/bin/env bash
# Run the edge-replay pipeline with safe local defaults.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

OUT_DIR="logs/edge_replay/$(date -u +%Y%m%dT%H%M%SZ)"
LIVE_KALSHI=0
MAX_PAGES_PER_STATUS=2
SLEEP_SECONDS=0.50

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --live-kalshi) LIVE_KALSHI=1; shift ;;
    --max-pages-per-status) MAX_PAGES_PER_STATUS="${2:-}"; shift 2 ;;
    --sleep-seconds) SLEEP_SECONDS="${2:-}"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/edge_replay/run_full_replay.sh [--live-kalshi] [--out-dir PATH]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_DIR"

if [[ "$LIVE_KALSHI" -eq 1 ]]; then
  .venv/bin/python scripts/edge_replay/fetch_resolved_markets.py \
    --live-kalshi \
    --intersect-evidence-store \
    --evidence-store-db data/evidence_store.db \
    --page-limit 100 \
    --max-pages-per-status "$MAX_PAGES_PER_STATUS" \
    --sleep-seconds "$SLEEP_SECONDS" \
    --output "$OUT_DIR/resolved_markets_full.json"
else
  .venv/bin/python scripts/edge_replay/fetch_resolved_markets.py \
    --from-paper-trades-db data/paper_trades.db \
    --output "$OUT_DIR/resolved_markets_full.json"
fi

.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets "$OUT_DIR/resolved_markets_full.json" \
  --paper-trades-db data/paper_trades.db \
  --evidence-store-db data/evidence_store.db \
  --trade-log 'logs/**/*.jsonl' \
  --output "$OUT_DIR/replay_dataset.jsonl"

.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset "$OUT_DIR/replay_dataset.jsonl" \
  --output "$OUT_DIR/counterfactual_scores.json"

.venv/bin/python scripts/edge_replay/ingestion_freshness_check.py \
  --db data/evidence_store.db \
  --max-age-hours 6 \
  --json > "$OUT_DIR/ingestion_freshness.json"

echo "edge_replay_out_dir=$OUT_DIR"
