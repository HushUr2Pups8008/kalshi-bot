"""Politics desk risk flags.

Each flag defaults on for a paper order and off for a live order.
An explicit ``true`` or ``false`` wins in both modes. A positive integer
turns that flag on and replaces its numeric default. Any other text
leaves the behavior off.

The live plist sets EVENT_NEWS_YES_FLOOR_CENTS. The other names stay
unset, so those live paths stay off. The paper risk cap defaults to
600 cents. The plist does not set EVENT_NEWS_MAX_RISK_CENTS. The 36c
band, the 8-contract cap, and the existing family lock stay in force.
"""

from __future__ import annotations

import os
from typing import Mapping

from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS

EVENT_NEWS_YES_FLOOR = "EVENT_NEWS_YES_FLOOR_CENTS"
EVENT_NEWS_MAX_RISK = "EVENT_NEWS_MAX_RISK_CENTS"
EVENT_NEWS_MAX_EXPOSURE_PCT = "EVENT_NEWS_MAX_EXPOSURE_PCT"
EVENT_NEWS_ONE_PER_FAMILY = "EVENT_NEWS_ONE_PER_FAMILY"
EVENT_NEWS_MAKER_FIRST = "EVENT_NEWS_MAKER_FIRST"
EVENT_NEWS_MAKER_WAIT_S = "EVENT_NEWS_MAKER_WAIT_S"
EVENT_NEWS_GROK_RULES_CHECK = "EVENT_NEWS_GROK_RULES_CHECK"
EVENT_NEWS_GROK_EDGE_SCAN = "EVENT_NEWS_GROK_EDGE_SCAN"
EVENT_NEWS_GROK_EXIT_WATCH = "EVENT_NEWS_GROK_EXIT_WATCH"
EVENT_NEWS_SESSION_HALT = "EVENT_NEWS_SESSION_HALT"

DEFAULT_YES_FLOOR_CENTS = 55
DEFAULT_MAX_RISK_CENTS = 600
DEFAULT_MAX_EXPOSURE_PCT = 25
DEFAULT_MAKER_WAIT_S = 180
HARD_MAX_CONTRACTS = 8
DESK_FAMILIES = ("KXTRUMPACT", "KXTRUMPSAY", "KXTRUTHSOCIAL")

_DESK_FLAG_NAMES = (
    EVENT_NEWS_YES_FLOOR,
    EVENT_NEWS_MAX_RISK,
    EVENT_NEWS_MAX_EXPOSURE_PCT,
    EVENT_NEWS_ONE_PER_FAMILY,
    EVENT_NEWS_MAKER_FIRST,
    EVENT_NEWS_GROK_RULES_CHECK,
    EVENT_NEWS_SESSION_HALT,
)


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _raw_flag(name: str, environ: Mapping[str, str] | None) -> str | None:
    env = _env(environ)
    if name not in env:
        return None
    return str(env.get(name) or "").strip().lower()


def event_news_desk_flag_enabled(
    name: str,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Paper defaults on. Live defaults off. Explicit true or false wins."""
    raw = _raw_flag(name, environ)
    if raw is None or raw == "":
        return not live
    if raw == "true" or raw.isdigit():
        return True
    return False


def event_news_any_live_desk_flag(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True only when a live order should take a new desk path."""
    return any(
        event_news_desk_flag_enabled(name, live=True, environ=environ)
        for name in _DESK_FLAG_NAMES
    )


def _positive_int(name: str, default: int, environ: Mapping[str, str] | None) -> int:
    raw = _raw_flag(name, environ)
    if raw and raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return default


def event_news_yes_floor_cents(
    environ: Mapping[str, str] | None = None,
) -> int:
    value = _positive_int(EVENT_NEWS_YES_FLOOR, DEFAULT_YES_FLOOR_CENTS, environ)
    if value > 99:
        return DEFAULT_YES_FLOOR_CENTS
    return value


def event_news_max_risk_cents(
    environ: Mapping[str, str] | None = None,
) -> int:
    return _positive_int(EVENT_NEWS_MAX_RISK, DEFAULT_MAX_RISK_CENTS, environ)


def event_news_max_exposure_pct(
    environ: Mapping[str, str] | None = None,
) -> int:
    value = _positive_int(
        EVENT_NEWS_MAX_EXPOSURE_PCT,
        DEFAULT_MAX_EXPOSURE_PCT,
        environ,
    )
    if value > 100:
        return DEFAULT_MAX_EXPOSURE_PCT
    return value


def event_news_maker_wait_s(
    environ: Mapping[str, str] | None = None,
) -> int:
    return _positive_int(EVENT_NEWS_MAKER_WAIT_S, DEFAULT_MAKER_WAIT_S, environ)


def event_news_yes_floor_skip_reason(
    side: object,
    ask_cents: object,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """YES below the floor skips. NO keeps the 36c band."""
    if not event_news_desk_flag_enabled(EVENT_NEWS_YES_FLOOR, live=live, environ=environ):
        return None
    if str(side or "").strip().lower() != "yes":
        return None
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return "yes_below_floor"
    if ask_cents < event_news_yes_floor_cents(environ):
        return "yes_below_floor"
    return None


def event_news_risk_capped_contracts(
    kelly_contracts: int,
    ask_cents: int,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> int:
    """min(Kelly size, 8, floor(cap / ask)). The 8-lot cap always applies."""
    if isinstance(kelly_contracts, bool) or not isinstance(kelly_contracts, int):
        sized = 0
    else:
        sized = max(0, min(kelly_contracts, HARD_MAX_CONTRACTS))
    if not event_news_desk_flag_enabled(EVENT_NEWS_MAX_RISK, live=live, environ=environ):
        return sized
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int) or ask_cents <= 0:
        return 0
    affordable = event_news_max_risk_cents(environ) // ask_cents
    return max(0, min(sized, affordable))


def event_news_exposure_skip_reason(
    open_cost_cents: int,
    order_cost_cents: int,
    equity_cents: int,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Open cost plus the new order must stay within the equity percent."""
    if not event_news_desk_flag_enabled(
        EVENT_NEWS_MAX_EXPOSURE_PCT,
        live=live,
        environ=environ,
    ):
        return None
    if (
        isinstance(open_cost_cents, bool)
        or isinstance(order_cost_cents, bool)
        or isinstance(equity_cents, bool)
        or not isinstance(open_cost_cents, int)
        or not isinstance(order_cost_cents, int)
        or not isinstance(equity_cents, int)
        or equity_cents <= 0
        or open_cost_cents < 0
        or order_cost_cents < 0
    ):
        return "exposure_cap"
    limit = equity_cents * event_news_max_exposure_pct(environ) / 100
    if open_cost_cents + order_cost_cents > limit:
        return "exposure_cap"
    return None


def event_news_series_family(ticker: object) -> str:
    return str(ticker or "").strip().split("-", 1)[0].strip().upper()


def event_news_family_skip_reason(
    ticker: object,
    open_tickers: list[str] | tuple[str, ...] | None,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """At most one open row in KXTRUMPACT, KXTRUMPSAY, or KXTRUTHSOCIAL."""
    if not event_news_desk_flag_enabled(
        EVENT_NEWS_ONE_PER_FAMILY,
        live=live,
        environ=environ,
    ):
        return None
    family = event_news_series_family(ticker)
    if family not in DESK_FAMILIES:
        return None
    from utils.event_news_research import EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN

    # A failed live read inserts this sentinel. Block every desk family.
    if EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN in {str(raw or "").strip() for raw in open_tickers or ()}:
        return "one_per_family"
    for raw in open_tickers or ():
        occupied = str(raw or "").strip()
        if not occupied or occupied == str(ticker or "").strip():
            continue
        if event_news_series_family(occupied) == family:
            return "one_per_family"
    return None


def event_news_maker_limit_cents(
    ask_cents: int,
    *,
    floor_cents: int,
) -> int | None:
    """One cent better than the ask, never through the floor or the 0-35c band."""
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return None
    if isinstance(floor_cents, bool) or not isinstance(floor_cents, int):
        return None
    limit = ask_cents - 1
    band_floor = max(floor_cents, EVENT_NEWS_BAND_FLOOR_CENTS)
    if limit < band_floor or limit > 99:
        return None
    return limit


def _on_off(enabled: bool) -> str:
    return "on" if enabled else "off"


def event_news_desk_flags_boot_suffix(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Live and paper resolutions for the one boot line."""

    def pair(name: str) -> str:
        live = event_news_desk_flag_enabled(name, live=True, environ=environ)
        paper = event_news_desk_flag_enabled(name, live=False, environ=environ)
        return f"live={_on_off(live)} paper={_on_off(paper)}"

    return (
        f" YES_FLOOR {pair(EVENT_NEWS_YES_FLOOR)}"
        f" cents={event_news_yes_floor_cents(environ)}"
        f" RISK_CAP {pair(EVENT_NEWS_MAX_RISK)}"
        f" cents={event_news_max_risk_cents(environ)}"
        f" EXPOSURE_PCT {pair(EVENT_NEWS_MAX_EXPOSURE_PCT)}"
        f" pct={event_news_max_exposure_pct(environ)}"
        f" ONE_PER_FAMILY {pair(EVENT_NEWS_ONE_PER_FAMILY)}"
        f" MAKER_FIRST {pair(EVENT_NEWS_MAKER_FIRST)}"
        f" wait_s={event_news_maker_wait_s(environ)}"
        f" RULES_CHECK {pair(EVENT_NEWS_GROK_RULES_CHECK)}"
        f" EDGE_SCAN {pair(EVENT_NEWS_GROK_EDGE_SCAN)}"
        f" EXIT_WATCH {pair(EVENT_NEWS_GROK_EXIT_WATCH)}"
        f" SESSION_HALT {pair(EVENT_NEWS_SESSION_HALT)}"
    )
