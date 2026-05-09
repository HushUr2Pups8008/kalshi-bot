"""
Fade signal detectors.

Two fade strategies are implemented:

1. Tweet fade (detect_fade_pattern) -- original strategy.
   When @Kalshi tweets hype/ATH language, retail attention pushes the market
   above fair value. Buy the underpriced side.

2. Price fade (detect_price_fade) -- WebSocket-based, runs in parallel with tweet fade.
   When a geo market's mid-price crosses above 85c or below 15c, the market
   is likely over-extended. Fade the extremity directly from the live price feed,
   independent of the tweet feed.

Detection only. Direction inversion and trade execution are in main.py.
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
        "bullish" -- tweet is hyping the YES side; caller should buy NO
        None      -- not a recognized fade pattern; skip this tweet
    """
    text = f"{tweet.headline} {tweet.body or ''}".strip()
    if _BULLISH_RE.search(text):
        return "bullish"
    return None


def detect_price_fade(
    ticker: str,
    prev_mid: float,
    now_mid: float,
    high_threshold: float,
    low_threshold: float,
) -> Optional[str]:
    """
    Detect a threshold-crossing event on a market's mid-price.

    A 1-cent buffer around each threshold ensures the previous price was
    meaningfully on the other side -- suppresses noise from bid/ask oscillation
    right at the boundary.

    Returns:
        "high_cross" -- price just crossed above high_threshold -> caller should buy NO
        "low_cross"  -- price just crossed below low_threshold  -> caller should buy YES
        None         -- no crossing event
    """
    if prev_mid < high_threshold - 1 and now_mid >= high_threshold:
        return "high_cross"
    if prev_mid > low_threshold + 1 and now_mid <= low_threshold:
        return "low_cross"
    return None
