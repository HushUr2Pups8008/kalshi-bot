#!/usr/bin/env bash
# Reproduce Cycle-16D price coverage, replay scoring, and report rendering.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

OUT_DIR="logs/edge_replay/cycle16d"
MARKETS="logs/edge_replay/cycle13_live/resolved_markets_full.json"
POST_FIX_DB="data/dossier_updates_post_fix.db"
PRICES="$OUT_DIR/historical_prices_cycle16d.json"
SKIP_PRICE_BACKFILL=0
LIVE_PRICE_BACKFILL=0
SLEEP_SECONDS=0.50
LIMIT=24

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT_DIR="${2:-}"; PRICES="$OUT_DIR/historical_prices_cycle16d.json"; shift 2 ;;
    --markets) MARKETS="${2:-}"; shift 2 ;;
    --post-fix-db) POST_FIX_DB="${2:-}"; shift 2 ;;
    --historical-prices) PRICES="${2:-}"; shift 2 ;;
    --skip-price-backfill) SKIP_PRICE_BACKFILL=1; shift ;;
    --live-price-backfill) LIVE_PRICE_BACKFILL=1; shift ;;
    --sleep-seconds) SLEEP_SECONDS="${2:-}"; shift 2 ;;
    --limit) LIMIT="${2:-}"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/edge_replay/run_cycle16d_replay.sh [--skip-price-backfill|--live-price-backfill] [--out-dir PATH]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$OUT_DIR"

if [[ "$SKIP_PRICE_BACKFILL" -eq 1 && "$LIVE_PRICE_BACKFILL" -eq 1 ]]; then
  echo "--skip-price-backfill and --live-price-backfill are mutually exclusive" >&2
  exit 2
fi

if [[ "$SKIP_PRICE_BACKFILL" -eq 0 || "$LIVE_PRICE_BACKFILL" -eq 1 ]]; then
  .venv/bin/python scripts/edge_replay/fetch_historical_prices.py \
    --markets "$MARKETS" \
    --output "$PRICES" \
    --limit "$LIMIT" \
    --sleep-seconds "$SLEEP_SECONDS"
elif [[ ! -s "$PRICES" ]]; then
  echo "missing historical prices: $PRICES" >&2
  exit 1
fi

.venv/bin/python scripts/edge_replay/price_coverage_audit.py \
  --dataset logs/edge_replay/cycle15b/replay_dataset.jsonl \
  --historical-prices "$PRICES" \
  --post-fix-db "$POST_FIX_DB" \
  --output "$OUT_DIR/coverage_audit.json"

.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets "$MARKETS" \
  --paper-trades-db /private/tmp/kalshi_cycle16d_no_paper_trades.db \
  --trade-log '/private/tmp/kalshi_cycle16d_no_trade_logs/*.jsonl' \
  --evidence-store-db "$POST_FIX_DB" \
  --historical-prices "$PRICES" \
  --output "$OUT_DIR/replay_dataset.jsonl"

.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset "$OUT_DIR/replay_dataset.jsonl" \
  --readiness-confidence 0.05 \
  --output "$OUT_DIR/counterfactual_scores.json"

.venv/bin/python scripts/edge_replay/render_cycle16d_report.py \
  --dataset "$OUT_DIR/replay_dataset.jsonl" \
  --scores "$OUT_DIR/counterfactual_scores.json" \
  --coverage "$OUT_DIR/coverage_audit.json" \
  --output docs/governance/edge-replay-cycle16d-report.md

echo "cycle16d_out_dir=$OUT_DIR"
