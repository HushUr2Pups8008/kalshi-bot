#!/bin/bash
# Paper-only Grok sidecar. Does not start the trading process.
# Does not use the politics lock, the politics database, or the freeze label.
# LIVE_TRADING_ENABLED is forced false. Order POST and DELETE raise in this
# process. Do not export these flags from scripts/start_politics_paper.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

export LIVE_TRADING_ENABLED=false
export KALSHI_GROK_PAPER_SIDECAR=true
export PAPER_COHORT_ID=kalshi-event-news-20260820
export BOT_RUNTIME_LOCK_NAME=bot_runtime.kalshi-grok-paper.lock
export EVENT_NEWS_GROK_EDGE_SCAN=true
export EVENT_NEWS_GROK_EXIT_WATCH=true
export EVENT_NEWS_GROK_GROUNDING=true
export GROK_DAILY_USD_CAP=3

PY="$ROOT/.venv/bin/python"
SCRIPT="$ROOT/scripts/grok_paper_sidecar.py"
if [[ "$(uname -s)" == "Darwin" && -x /usr/bin/caffeinate ]]; then
  exec /usr/bin/caffeinate -dimsu "$PY" "$SCRIPT" "$@"
fi
exec "$PY" "$SCRIPT" "$@"
