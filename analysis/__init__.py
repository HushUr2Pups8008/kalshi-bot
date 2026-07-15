from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from feeds import NewsItem
from kalshi import KalshiMarket


@dataclass(frozen=True)
class DecisionFinancialProvenance:
    """Immutable decision-time inputs required to replay sizing and fees."""

    sizing_bankroll_dollars: Decimal
    max_position_dollars: Decimal
    max_ticker_exposure_dollars: Decimal
    fee_account_precision_dollars: Decimal | None
    fee_accumulator_dollars: Decimal

    def __post_init__(self) -> None:
        values = {
            "sizing_bankroll_dollars": self.sizing_bankroll_dollars,
            "max_position_dollars": self.max_position_dollars,
            "max_ticker_exposure_dollars": self.max_ticker_exposure_dollars,
            "fee_accumulator_dollars": self.fee_accumulator_dollars,
        }
        for name, value in values.items():
            if not isinstance(value, Decimal) or not value.is_finite():
                raise TypeError(f"{name} must be a finite Decimal")
        if self.sizing_bankroll_dollars <= 0:
            raise ValueError("sizing_bankroll_dollars must be positive")
        if self.max_position_dollars < 0 or self.max_ticker_exposure_dollars < 0:
            raise ValueError("decision-time position caps must be non-negative")
        precision = self.fee_account_precision_dollars
        if precision is not None and (
            not isinstance(precision, Decimal)
            or not precision.is_finite()
            or precision <= 0
        ):
            raise ValueError("fee_account_precision_dollars must be positive")
        if not Decimal("0") <= self.fee_accumulator_dollars < Decimal("0.01"):
            raise ValueError("fee_accumulator_dollars must be in [0, 0.01)")


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
    decision_financial_provenance: Optional[DecisionFinancialProvenance] = None
