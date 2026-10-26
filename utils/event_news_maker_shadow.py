"""Hypothetical resting bid. No order is placed.

Queue-ahead volume is not stored, so this does not grade a fill from the tape.
"""

from __future__ import annotations

from utils.event_news_desk_flags import event_news_maker_limit_cents
from utils.event_news_grok_flags import EVENT_NEWS_BAND_FLOOR_CENTS


def resting_bid_cents(ask_cents: object) -> int | None:
    if isinstance(ask_cents, bool) or not isinstance(ask_cents, int):
        return None
    return event_news_maker_limit_cents(
        ask_cents, floor_cents=EVENT_NEWS_BAND_FLOOR_CENTS
    )
