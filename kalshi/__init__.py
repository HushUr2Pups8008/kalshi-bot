from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from kalshi.series_metadata import SettlementSource


@dataclass
class KalshiMarket:
    ticker:           str
    title:            str
    yes_bid:          float
    yes_ask:          float
    yes_price:        float
    volume:           int
    open_interest:    int
    close_time:       str
    status:           str
    series_ticker:    str = ""
    event_ticker:     str = ""
    subtitle:         str = ""
    result:           str = ""
    regime_weights:   dict[str, float] = field(default_factory=dict)
    market_metadata:  dict[str, str] = field(default_factory=dict)
    rules_primary:    str = ""
    rules_secondary:  str = ""
    settlement_sources: tuple[SettlementSource, ...] = field(default_factory=tuple)
    contract_terms_url: str = ""
    settlement_timer_seconds: Optional[int] = None
    early_close_condition: str = ""
    expected_expiration_time: str = ""
    expiration_time:  str = ""

    # ------------------------------------------------------------------
    # Post-P0 pricing surface (LD-2). All Optional with defaults so legacy
    # construction stays valid.
    # ------------------------------------------------------------------
    yes_bid_cents:           Optional[int] = None
    yes_ask_cents:           Optional[int] = None
    no_bid_cents:            Optional[int] = None
    no_ask_cents:            Optional[int] = None
    yes_bid_size:            Optional[Decimal] = None
    yes_ask_size:            Optional[Decimal] = None
    no_bid_size:             Optional[Decimal] = None
    no_ask_size:             Optional[Decimal] = None
    last_price_cents:        Optional[int] = None
    previous_price_cents:    Optional[int] = None
    volume_fp:               Optional[Decimal] = None
    volume_24h_fp:           Optional[Decimal] = None
    open_interest_fp:        Optional[Decimal] = None
    liquidity_dollars:       Optional[Decimal] = None
    notional_value_dollars:  Optional[Decimal] = None
    exchange_active:         Optional[bool] = None
    trading_active:          Optional[bool] = None
    price_available:         bool = False
    price_source:            str = "unavailable"
    price_method:            str = "none"
    price_retrieved_at:      Optional[datetime] = None
    raw_payload_hash:        Optional[str] = None
    market_type:             str = ""
    price_level_structure:   str = ""
    fractional_trading_enabled: bool = False
    created_time:            Optional[datetime] = None
    updated_time:            Optional[datetime] = None
    fee_multiplier:          Optional[Decimal] = None
    fee_type:                Optional[str] = None
    fee_effective_at:        Optional[datetime] = None
    quantity_step:           Optional[Decimal] = None
    price_tick:              Optional[Decimal] = None
    fill_role:               Optional[str] = None
    outcome_side:            Optional[str] = None
    yes_bid_levels:          tuple[tuple[Decimal, Decimal], ...] = ()
    no_bid_levels:           tuple[tuple[Decimal, Decimal], ...] = ()
    book_as_of:              Optional[datetime] = None
    book_payload_hash:       Optional[str] = None
    book_error:              Optional[str] = None
    fee_provenance_hash:     Optional[str] = None
    report_venue:            Optional[str] = None
    report_venue_market_id:  Optional[str] = None
    unsupported_payload_contract: bool = False

    # Legacy price fields guarded post-P0 (CR-C). Reads of `yes_price`,
    # `yes_bid`, `yes_ask` raise ValueError when `price_available=False`
    # so callers cannot silently consume a stale or zero midpoint.
    # Construction via the dataclass-generated `__init__` continues to
    # work because `__init__` writes via `__setattr__`, not
    # `__getattribute__`.
    def __getattribute__(self, name: str):
        if name in ("yes_price", "yes_bid", "yes_ask"):
            try:
                available = object.__getattribute__(self, "price_available")
            except AttributeError:
                available = False
            if not available:
                try:
                    ticker = object.__getattribute__(self, "ticker")
                except AttributeError:
                    ticker = "<unknown>"
                raise ValueError(
                    f"{name} unavailable for {ticker!r}: price_available=False "
                    f"(LD-2 / CR-C guard). Read executed_price_cents or guard "
                    f"via market.is_tradeable() before access."
                )
        return object.__getattribute__(self, name)

    @property
    def yes_prob(self) -> float:
        """Mid price as a probability (0-1). Raises ValueError when
        price_available=False (LD-2 / CR-C)."""
        if not self.price_available:
            raise ValueError(
                f"yes_prob unavailable for {self.ticker!r}: price_available=False"
            )
        return self.yes_price / 100.0

    @property
    def no_prob(self) -> float:
        """Implied NO probability. Inherits the CR-C guard via yes_prob."""
        return 1.0 - self.yes_prob

    def is_tradeable(self) -> bool:
        """Post-P0 tradeability gate (LD-2). True only when the cents fields
        are populated with executable asks, price_available=True, and the
        spread/complement invariants hold (yes_bid<=yes_ask, no_bid<=no_ask,
        yes_bid+no_ask<=100).
        Construction-time clients (`_make_post_p0_market` in tests) bypass
        the normalizer's parse-time invariant guard, so we enforce it here
        as well to preserve the post-P0 fail-closed contract."""
        if not self.price_available:
            return False
        if (
            self.yes_bid_cents is None
            or self.yes_ask_cents is None
            or self.no_bid_cents is None
            or self.no_ask_cents is None
        ):
            return False
        if not all(
            self._is_executable_ask_cents(price)
            for price in (self.yes_ask_cents, self.no_ask_cents)
        ):
            return False
        if self.yes_bid_cents > self.yes_ask_cents:
            return False
        if self.no_bid_cents > self.no_ask_cents:
            return False
        if self.yes_bid_cents + self.no_ask_cents > 100:
            return False
        return True

    @staticmethod
    def _is_executable_ask_cents(price: object) -> bool:
        return (
            not isinstance(price, bool)
            and isinstance(price, int)
            and 0 < price < 100
        )

    def executable_yes_price(self) -> int:
        """Returns the executable YES ask cents (LD-2). Raises ValueError
        when price_available=False or yes_ask_cents is None — never returns
        a silent 50¢ fallback."""
        if not self.price_available:
            raise ValueError(
                f"executable_yes_price unavailable for {self.ticker!r}: "
                f"price_available=False (source={self.price_source!r})"
            )
        if self.yes_ask_cents is None:
            raise ValueError(
                f"executable_yes_price unavailable for {self.ticker!r}: "
                f"yes_ask_cents is None"
            )
        if not self._is_executable_ask_cents(self.yes_ask_cents):
            raise ValueError(
                f"executable_yes_price unavailable for {self.ticker!r}: "
                f"yes_ask_cents={self.yes_ask_cents!r} is not executable"
            )
        return self.yes_ask_cents

    def executable_no_price(self) -> int:
        """Returns the executable NO ask cents (LD-2). Raises ValueError
        when price_available=False or no_ask_cents is None — never returns
        a silent 50¢ fallback."""
        if not self.price_available:
            raise ValueError(
                f"executable_no_price unavailable for {self.ticker!r}: "
                f"price_available=False (source={self.price_source!r})"
            )
        if self.no_ask_cents is None:
            raise ValueError(
                f"executable_no_price unavailable for {self.ticker!r}: "
                f"no_ask_cents is None"
            )
        if not self._is_executable_ask_cents(self.no_ask_cents):
            raise ValueError(
                f"executable_no_price unavailable for {self.ticker!r}: "
                f"no_ask_cents={self.no_ask_cents!r} is not executable"
            )
        return self.no_ask_cents


@dataclass
class ExchangeState:
    """Normalized exchange status (LD-4). `is_open` is recomputed in
    __post_init__ so callers cannot construct an inconsistent state."""
    exchange_active: bool
    trading_active: bool
    is_open: bool = False

    def __post_init__(self) -> None:
        # Always derive is_open from the two flags — any user-supplied value
        # is intentionally overridden to preserve the invariant.
        self.is_open = bool(self.exchange_active) and bool(self.trading_active)


@dataclass
class OrderResult:
    order_id:    str
    ticker:      str
    side:        str
    contracts:   int
    price_cents: int
    status:      str
    filled:      int = 0
    error:       Optional[str] = None
