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


def event_news_grok_grounding_for_mode(
    live: bool | None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Paper defaults on. Live chat grounding stays off.

    An explicit true or false wins for paper only. Live and an unknown
    mode stay off even when EVENT_NEWS_GROK_GROUNDING=true. Veto and watch
    chat calls do not read this value.
    """
    if live is None or live is True:
        return False
    env = os.environ if environ is None else environ
    if EVENT_NEWS_GROK_GROUNDING in env:
        raw = str(env.get(EVENT_NEWS_GROK_GROUNDING) or "").strip().lower()
        if raw:
            return raw == "true"
    return True


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


# 0c through 35c is the excluded band. 36c is the first allowed cent.
EVENT_NEWS_BAND_FLOOR_CENTS = 36
EVENT_NEWS_FLAGS_FATAL = "[EVENT_NEWS_FLAGS] FATAL band guard off in LIVE"


def _event_news_on_off(enabled: bool) -> str:
    return "on" if enabled else "off"


def event_news_flags_boot_line(
    environ: Mapping[str, str] | None = None,
) -> str:
    """One boot line. Values come from the same flag functions the executor uses."""
    from utils.event_news_desk_flags import event_news_desk_flags_boot_suffix

    return (
        "[EVENT_NEWS_FLAGS] "
        f"REQUOTE_BAND_CHECK={_event_news_on_off(event_news_requote_band_check_enabled(environ))} "
        f"EXCLUDE_EXACT_35={_event_news_on_off(event_news_exclude_exact_35_enabled(environ))} "
        f"GROK_GROUNDING={_event_news_on_off(event_news_grok_grounding_enabled(environ))} "
        "GROK_GROUNDING_MODE "
        f"live={_event_news_on_off(event_news_grok_grounding_for_mode(True, environ))} "
        f"paper={_event_news_on_off(event_news_grok_grounding_for_mode(False, environ))} "
        f"GROK_TRACE={_event_news_on_off(event_news_grok_trace_enabled(environ))} "
        f"GROK_WATCH_SKIP_SETTLED={_event_news_on_off(event_news_watch_skip_settled_enabled(environ))} "
        f"FILL_LOG_DETAIL={_event_news_on_off(event_news_fill_log_detail_enabled(environ))} "
        f"band_floor_cents={EVENT_NEWS_BAND_FLOOR_CENTS}"
        f"{event_news_desk_flags_boot_suffix(environ)}"
    )


def event_news_live_band_guard_off(
    live_trading_enabled: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when live trading is on and either band flag resolved off.

    The live path refuses the POST and stays up. A false result leaves
    the existing order path unchanged. Paper does not call this to block.
    """
    if live_trading_enabled is not True:
        return False
    return (not event_news_requote_band_check_enabled(environ)) or (
        not event_news_exclude_exact_35_enabled(environ)
    )


def event_news_market_status_settled(status: object) -> bool:
    return str(status or "").strip().lower() in _SETTLED_MARKET_STATUSES
