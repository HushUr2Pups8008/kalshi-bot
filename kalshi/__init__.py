from dataclasses import dataclass, field
from typing import Optional


@dataclass
class KalshiMarket:
    ticker:           str
    title:            str
    yes_bid:          float   # cents (0–100)
    yes_ask:          float   # cents (0–100)
    yes_price:        float   # mid-price cents
    volume:           int
    open_interest:    int
    close_time:       str     # ISO8601
    status:           str     # "open", "closed", etc.
    series_ticker:    str = ""
    subtitle:         str = ""
    result:           str = ""    # "yes", "no", or "" if not yet settled

    @property
    def yes_prob(self) -> float:
        """Mid price as a probability (0–1)."""
        return self.yes_price / 100.0

    @property
    def no_prob(self) -> float:
        return 1.0 - self.yes_prob


@dataclass
class OrderResult:
    order_id:    str
    ticker:      str
    side:        str       # "yes" | "no"
    contracts:   int
    price_cents: int
    status:      str
    filled:      int = 0
    error:       Optional[str] = None
