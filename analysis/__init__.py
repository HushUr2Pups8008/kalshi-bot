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
    # DEPRECATED — semantically executed_price_cents post-P0; remove in P1.
    # The DT-2b alias is populated by `__post_init__` from
    # `executed_price_cents` when the latter is set and `market_yes_price`
    # is 0.0/None at construction. Callers should prefer the canonical
    # field below.
    market_yes_price:       float = 0.0   # current market price (cents)
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

    def __post_init__(self) -> None:
        """DT-2b: market_yes_price is a deprecated alias for executed_price_cents.
        Always mirror canonical → alias when canonical is set, regardless of
        whether the alias was passed explicitly. Stale aliases from explicit
        construction must not silently survive."""
        if self.executed_price_cents is not None:
            self.market_yes_price = float(self.executed_price_cents)
