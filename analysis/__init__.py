from dataclasses import dataclass, field
from typing import Optional

from feeds import NewsItem
from kalshi import KalshiMarket


@dataclass
class SignalAnalysis:
    """Result of analyzing a news item against a specific market."""
    news_item:              NewsItem
    market:                 KalshiMarket
    estimated_probability:  float        # our estimate of YES probability
    market_yes_price:       float        # current market price (cents)
    edge:                   float        # estimated_probability - market_yes_price/100
    side:                   str          # "yes" | "no"
    kelly_fraction:         float        # raw Kelly fraction
    kelly_dollars:          float        # Kelly-sized bet in dollars
    capped_dollars:         float        # after $50 hard cap
    keywords_matched:       list[str]    = field(default_factory=list)
    reasoning:              str          = ""
    confidence:             float        = 0.5   # 0–1 overall confidence in the estimate
