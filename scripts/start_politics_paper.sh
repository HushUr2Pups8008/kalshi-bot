#!/bin/bash
# Isolated politics/event-news paper process.
# Does not replace the KXDJI 16:00 freeze LaunchAgent.
# load_dotenv does not override these exports — list every money-path
# knob here so the desk cannot inherit freeze .env leaks.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Desk isolation
export KALSHI_MACRO_ONLY=false
export PAPER_COHORT_ID=kalshi-event-news-20260820
export PAPER_COHORT_KIND=active
export PAPER_ACTIVE_COHORT_BANKROLL=50.00
export PAPER_ACTIVE_COHORT_MAX_DAYS_TO_CLOSE=14
export BOT_RUNTIME_LOCK_NAME=bot_runtime.kalshi-event-news-20260820.lock

# Live / second venue stay locked. Polymarket needs an isolated
# polymarket-* paper cohort before POLYMARKET_US_ENABLED can be true.
export LIVE_TRADING_ENABLED=false
export POLYMARKET_US_ENABLED=false
export POLYMARKET_US_LIVE_TRADING_ENABLED=false

# Favorite-band official prewarm (the money path)
export ENABLE_RESEARCH_PREWARM_TASK=true
export ENABLE_FEE_NET_PAPER_ACCOUNTING=true
export ENABLE_LLM_ROUTING_FILTER=true
export LLM_ALLOWED_PRICE_BANDS=0.55-0.99
export LLM_EXCLUDED_PRICE_BANDS=0.00-0.35
export RESEARCH_PREWARM_SCOPE=non_sports_shadow
export RESEARCH_PREWARM_DESK=inherit
export RESEARCH_PREWARM_INTERVAL_SECONDS=300
export RESEARCH_PREWARM_MAX_MARKETS=25
export RESEARCH_PREWARM_CONCURRENCY=2
export RESEARCH_PREWARM_MAX_PAGES=5
export RESEARCH_PREWARM_TARGET_COOLDOWN_SECONDS=300
export RESEARCH_PREWARM_SOURCEABLE_SERIES_FALLBACK=KXTRUTHSOCIAL
# Empty blocks freeze .env KXDJI. Parser maps empty to code default KXCPI,
# which non_sports_shadow already excludes. Runtime also zeros this on
# the event-news cohort.
export RESEARCH_PREWARM_FORECAST_REFRESH_SERIES=
export RESEARCH_EVENT_SEARCH_PROVIDER_ORDER=legacy
export REAL_WEB_RESEARCH_MODE=production
export REAL_WEB_RESEARCH_MAX_QUERIES=10
export REAL_WEB_RESEARCH_TIMEOUT_SECONDS=18.0
export GENERIC_SEARCH_CIRCUIT_MODE=enforce

# News / LLM (do not inherit freeze Google News disable)
export DISABLED_SOURCE_FAMILIES_EXTRA=
export REDDIT_ENABLED=false
export FADE_TWEET_FEED_URLS=
export OLLAMA_MODEL=qwen2.5:7b
export OLLAMA_KEEP_ALIVE=-1
export LLM_MAX_CONCURRENT=4
export NEWS_CONSUMER_WORKERS=4
export KALSHI_MATCHER_SERIES_LIMIT=200

# Explicit shared sizing (same numbers freeze uses; listed so they are not silent)
export KELLY_FRACTION=0.5
export MIN_EDGE=0.04
export MAX_BET_PCT_BANKROLL=0.15
export MAX_BET_HARD_CAP=25.00
export MIN_BET_DOLLARS=2.00
export MAX_TICKER_EXPOSURE_PCT=0.25
export ENABLE_CANONICAL_PERSISTED_SETTLEMENT_RECONCILIATION=true

# Dead / killed strategies — keep off
export ENABLE_WEATHER_SHADOW_CAPTURE=false
export ENABLE_NEWS_EDGE_PRIORITIZATION=false
export ENABLE_MARKET_FIRST_QUERY_SHADOW=false
export MARKET_SOURCE_HINTS_QUERY_MODE=off
export ENABLE_MARKET_SOURCE_HINT_SHADOW_CAPTURE=false
export ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW=false
export ENABLE_G7_SKIP_EVIDENCE_CAPTURE=false
export ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE=false
export ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION=false
export ENABLE_PRE_LLM_MATCH_GATE=false

# Pin the 7B model in Ollama so Metal is warm before the first research call.
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5:7b","prompt":"ok","keep_alive":-1,"stream":false,"options":{"num_predict":1}}' \
  >/tmp/ollama-politics-warmup.json || true
exec /usr/bin/caffeinate -dimsu "$ROOT/.venv/bin/python" main.py
