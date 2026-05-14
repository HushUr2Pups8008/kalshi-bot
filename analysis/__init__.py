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
    executed_price_cents:   Optional[int] = None  # canonical post-P0 entry price for chosen side
    edge:                   float = 0.0   # estimated_probability - executable_ask/100
    side:                   str   = "yes" # "yes" | "no"
    kelly_fraction:         float = 0.0   # raw Kelly fraction
    kelly_dollars:          float = 0.0   # Kelly-sized bet in dollars
    capped_dollars:         float = 0.0   # after dynamic per-bet cap (cfg.dynamic_max_bet(notional); see MAX_BET_HARD_CAP)
    keywords_matched:       list[str]    = field(default_factory=list)
    reasoning:              str          = ""
    confidence:             float        = 0.5   # 0-1 overall confidence in the estimate
    match_score:            float        = 0.0   # Jaccard similarity at match time
    signal_type:            str          = "news"  # "news" | "price_fade" | "fade_tweet"
    llm_direction:          Optional[str]   = None  # raw LLM direction field
    llm_magnitude:          Optional[str]   = None  # raw LLM magnitude field
    llm_confidence:         Optional[float] = None  # raw LLM confidence field
    signal_meta:            Optional[dict]  = None  # blend metadata (set by executor for blended candidates)

