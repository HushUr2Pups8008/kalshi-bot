"""Politics Grok paper flags.

Trace, grounding, settled-watch skip, and fill-log detail default off.
Absent, empty, and any value other than ``true`` leave those four off.

The requote band check and the exact-35c exclusion default on. An absent,
blank, or unrecognized value keeps the 0c-35c skip in force. Only the
exact value ``false`` turns either of those two flags off.
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


def _event_news_flag_default_on(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True unless the env value is exactly ``false``.

    Used by the executed-ask band. An unset politics process must keep
    the skip, so blank and unknown values stay on.
    """
    env = os.environ if environ is None else environ
    raw = env.get(name)
    if raw is None:
        return True
    return str(raw).strip().lower() != "false"


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
    return _event_news_flag_default_on(EVENT_NEWS_REQUOTE_BAND_CHECK, environ)


def event_news_exclude_exact_35_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return _event_news_flag_default_on(EVENT_NEWS_EXCLUDE_EXACT_35, environ)


def event_news_market_status_settled(status: object) -> bool:
    return str(status or "").strip().lower() in _SETTLED_MARKET_STATUSES
