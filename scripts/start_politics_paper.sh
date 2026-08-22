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
export LLM_ALLOWED_PRICE_BANDS=0.65-0.98
export LLM_EXCLUDED_PRICE_BANDS=0.00-0.35
export LIVE_TRADING_ENABLED=false
export POLYMARKET_US_ENABLED=false
export POLYMARKET_US_LIVE_TRADING_ENABLED=false
export ENABLE_WEATHER_SHADOW_CAPTURE=false
exec /usr/bin/caffeinate -dimsu "$ROOT/.venv/bin/python" main.py
