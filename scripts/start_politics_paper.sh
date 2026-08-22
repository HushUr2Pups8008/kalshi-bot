#!/bin/bash
# Isolated politics/event-news paper process.
# Does not replace the KXDJI 16:00 freeze LaunchAgent.
# load_dotenv does not override these exports.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export KALSHI_MACRO_ONLY=false
export RESEARCH_PREWARM_SCOPE=non_sports_shadow
export RESEARCH_PREWARM_DESK=inherit
export ENABLE_RESEARCH_PREWARM_TASK=false
export RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK=KXTRUTHSOCIAL
export PAPER_COHORT_ID=kalshi-event-news-20260820
export PAPER_COHORT_KIND=active
export PAPER_ACTIVE_COHORT_BANKROLL=50.00
export PAPER_ACTIVE_COHORT_MAX_DAYS_TO_CLOSE=14
export ENABLE_LLM_ROUTING_FILTER=true
export LLM_ALLOWED_PRICE_BANDS=0.55-0.99
export LLM_EXCLUDED_PRICE_BANDS=0.00-0.35
export LIVE_TRADING_ENABLED=false
export POLYMARKET_US_ENABLED=false
export POLYMARKET_US_LIVE_TRADING_ENABLED=false
export ENABLE_WEATHER_SHADOW_CAPTURE=false
export BOT_RUNTIME_LOCK_NAME=bot_runtime.kalshi-event-news-20260820.lock
# Default 24 recently-updated series yielded 6 open markets. Raise coverage
# for politics/domestic-policy without touching the DJI freeze process.
export KALSHI_MATCHER_SERIES_LIMIT=200
# Freeze .env disables google_news_query; do not inherit that on this desk.
export DISABLED_SOURCE_FAMILIES_EXTRA=
export OLLAMA_MODEL=qwen2.5:7b
export OLLAMA_KEEP_ALIVE=-1
export LLM_MAX_CONCURRENT=4
export NEWS_CONSUMER_WORKERS=4
# Pin the 7B model in Ollama so Metal is warm before the first research call.
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5:7b","prompt":"ok","keep_alive":-1,"stream":false,"options":{"num_predict":1}}' \
  >/tmp/ollama-politics-warmup.json || true
exec /usr/bin/caffeinate -dimsu "$ROOT/.venv/bin/python" main.py
