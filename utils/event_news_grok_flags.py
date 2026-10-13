"""Politics Grok paper flags. Every flag defaults off.

Absent, empty, and any value other than ``true`` leave the live path
unchanged. Do not set these in the running politics environment.
"""

from __future__ import annotations

import os
from typing import Mapping

EVENT_NEWS_GROK_TRACE = "EVENT_NEWS_GROK_TRACE"
EVENT_NEWS_GROK_GROUNDING = "EVENT_NEWS_GROK_GROUNDING"
EVENT_NEWS_GROK_WATCH_SKIP_SETTLED = "EVENT_NEWS_GROK_WATCH_SKIP_SETTLED"
EVENT_NEWS_FILL_LOG_DETAIL = "EVENT_NEWS_FILL_LOG_DETAIL"
EVENT_NEWS_REQUOTE_BAND_CHECK = "EVENT_NEWS_REQUOTE_BAND_CHECK"
EVENT_NEWS_EXCLUDE_EXACT_35 = "EVENT_NEWS_EXCLUDE_EXACT_35"

_SETTLED_MARKET_STATUSES = frozenset(
    {"closed", "determined", "settled", "finalized"}
)


def event_news_flag_enabled(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(name) or "").strip().lower() == "true"


def event_news_grok_trace_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_GROK_TRACE, environ)


def event_news_grok_grounding_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_GROK_GROUNDING, environ)


def event_news_watch_skip_settled_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_GROK_WATCH_SKIP_SETTLED, environ)


def event_news_fill_log_detail_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_FILL_LOG_DETAIL, environ)


def event_news_requote_band_check_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_REQUOTE_BAND_CHECK, environ)


def event_news_exclude_exact_35_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return event_news_flag_enabled(EVENT_NEWS_EXCLUDE_EXACT_35, environ)


def event_news_market_status_settled(status: object) -> bool:
    return str(status or "").strip().lower() in _SETTLED_MARKET_STATUSES
