"""
Fade the Kalshi Tweet signal.

When @Kalshi tweets about a market at an all-time high or with "BREAKING" urgency,
retail attention drives the market above fair value. The edge is on the underpriced
side — fade the hype by buying the opposite of what the tweet implies.

This module only detects the tweet pattern. Direction inversion and trade execution
are handled in main.py:_process_kalshi_tweet().
"""

import re
from typing import Optional

from feeds import NewsItem

# Patterns that indicate @Kalshi is hyping the YES side of a market.
# All are case-insensitive and matched against headline + body text.
_BULLISH_PATTERNS = [
    r"breaking",
    r"all[- ]time high",
    r"\bath\b",          # ATH as standalone word
    r"surging",
    r"record high",
    r"highest ever",
    r"all time high",
    r"odds at \d+",      # "odds at 81%"
    r"just hit \d+",
    r"reaching \d+",
]
_BULLISH_RE = re.compile("|".join(_BULLISH_PATTERNS), re.IGNORECASE)


def detect_fade_pattern(tweet: NewsItem) -> Optional[str]:
    """
    Determine whether a tweet is a hype/ATH announcement worth fading.

    Returns:
        "bullish" — tweet is hyping the YES side; caller should buy NO
        None      — not a recognized fade pattern; skip this tweet
    """
    text = f"{tweet.headline} {tweet.body or ''}".strip()
    if _BULLISH_RE.search(text):
        return "bullish"
    return None
