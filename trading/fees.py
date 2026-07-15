from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
from enum import StrEnum

from trading.venue import Venue


CENT = Decimal("0.01")
CENTICENT = Decimal("0.0001")
DIRECT_ACCOUNT_PRECISION = CENTICENT
NON_DIRECT_ACCOUNT_PRECISION = CENT


class FeeUnscorableError(ValueError):
    """The fee cannot be calculated from complete, pinned provenance."""


class FeeRole(StrEnum):
    TAKER = "taker"
    MAKER = "maker"


@dataclass(frozen=True)
class FeeScheduleId:
    name: str
    venue: Venue
    effective_from: datetime
    effective_to: datetime | None
    artifact_sha256: str
    supporting_artifact_sha256: tuple[str, ...] = ()


KALSHI_GENERAL_2026_07_07 = FeeScheduleId(
    name="kalshi-general-2026-07-07",
    venue=Venue.KALSHI,
    effective_from=datetime.fromisoformat("2026-07-07T00:00:00-04:00"),
    effective_to=None,
    artifact_sha256=(
        "815e2d5127d02d2fb90773d1a3844dc15a987696171eddc4e58de87b59c6124c"
    ),
    supporting_artifact_sha256=(
        "c9b8c7efd50df6512a4528e5a86044ad40699e17ababc7f9c42497c722829796",
    ),
)

POLYMARKET_US_2026_07_01 = FeeScheduleId(
    name="polymarket-us-2026-07-01",
    venue=Venue.POLYMARKET_US,
    effective_from=datetime.fromisoformat("2026-07-01T00:00:00-04:00"),
    effective_to=None,
    artifact_sha256=(
        "83580a99558f43d350051847edda2918410481a47c1b4a2a13fca90b1e0c8451"
    ),
)

_SUPPORTED_SCHEDULES = frozenset(
    (KALSHI_GENERAL_2026_07_07, POLYMARKET_US_2026_07_01)
)
_SCHEDULE_COEFFICIENTS = {
    KALSHI_GENERAL_2026_07_07: {
        FeeRole.TAKER: Decimal("0.07"),
        FeeRole.MAKER: Decimal("0.0175"),
    },
    POLYMARKET_US_2026_07_01: {
        FeeRole.TAKER: Decimal("0.06"),
        FeeRole.MAKER: Decimal("-0.0125"),
    },
}


def fee_coefficient_for(schedule_id: FeeScheduleId, role: FeeRole) -> Decimal:
    if schedule_id not in _SUPPORTED_SCHEDULES:
        raise FeeUnscorableError("unknown or unpinned fee schedule")
    coefficient = _SCHEDULE_COEFFICIENTS[schedule_id].get(role)
    if coefficient is None:
        raise FeeUnscorableError("unsupported fee role")
    return coefficient


@dataclass(frozen=True)
class FeeContext:
    schedule_id: FeeScheduleId
    role: FeeRole
    quantity: Decimal
    price: Decimal
    signed_revenue: Decimal
    order_id: str
    accumulator: Decimal
    multiplier: Decimal
    coefficient: Decimal
    account_precision: Decimal | None
    timestamp: datetime


@dataclass(frozen=True)
class KalshiRoundingQuote:
    rounding_fee: Decimal
    rebate: Decimal
    net_fee: Decimal
    next_accumulator: Decimal


@dataclass(frozen=True)
class FeeQuote:
    schedule_id: FeeScheduleId
    role: FeeRole
    base_fee: Decimal
    trade_fee: Decimal
    rounding_adjustment: Decimal
    balance_rounding_fee: Decimal
    rebate: Decimal
    net_fee: Decimal
    previous_accumulator: Decimal
    next_accumulator: Decimal


def _require_finite(name: str, value: Decimal) -> None:
    if not value.is_finite():
        raise FeeUnscorableError(f"{name} must be finite")


def _ceil_to(value: Decimal, quantum: Decimal) -> Decimal:
    return (value / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _floor_to(value: Decimal, quantum: Decimal) -> Decimal:
    return (value / quantum).to_integral_value(rounding=ROUND_FLOOR) * quantum


def _validate_context(context: FeeContext) -> None:
    if context.schedule_id not in _SUPPORTED_SCHEDULES:
        raise FeeUnscorableError("unsupported schedule or artifact provenance")
    if context.timestamp.tzinfo is None or context.timestamp.utcoffset() is None:
        raise FeeUnscorableError("timestamp must be timezone-aware")
    if context.timestamp < context.schedule_id.effective_from:
        raise FeeUnscorableError("fee schedule is not effective at fill timestamp")
    if (
        context.schedule_id.effective_to is not None
        and context.timestamp >= context.schedule_id.effective_to
    ):
        raise FeeUnscorableError("fee schedule is not effective at fill timestamp")
    if not context.order_id.strip():
        raise FeeUnscorableError("order_id is required")

    for name, value in (
        ("quantity", context.quantity),
        ("price", context.price),
        ("signed revenue", context.signed_revenue),
        ("accumulator", context.accumulator),
        ("multiplier", context.multiplier),
        ("coefficient", context.coefficient),
    ):
        _require_finite(name, value)

    if context.quantity <= 0:
        raise FeeUnscorableError("quantity must be positive")
    if context.price <= 0 or context.price >= 1:
        raise FeeUnscorableError("price must be between zero and one")
    if context.multiplier < 0:
        raise FeeUnscorableError("multiplier must be non-negative")
    if context.accumulator < 0 or context.accumulator >= CENT:
        raise FeeUnscorableError("accumulator must be in [0, 0.01)")
    if context.signed_revenue == 0 or abs(context.signed_revenue) != (
        context.quantity * context.price
    ):
        raise FeeUnscorableError("signed revenue must equal quantity times price")

    expected_coefficient = _SCHEDULE_COEFFICIENTS[context.schedule_id].get(
        context.role
    )
    if expected_coefficient is None or context.coefficient != expected_coefficient:
        raise FeeUnscorableError("coefficient does not match pinned fee schedule")

    if context.schedule_id.venue is Venue.KALSHI:
        if context.account_precision not in (
            DIRECT_ACCOUNT_PRECISION,
            NON_DIRECT_ACCOUNT_PRECISION,
        ):
            raise FeeUnscorableError("unsupported Kalshi account precision")
    elif context.schedule_id.venue is Venue.POLYMARKET_US:
        if context.account_precision is not None:
            raise FeeUnscorableError("Polymarket does not use Kalshi account precision")
        if context.multiplier != 1 or context.accumulator != 0:
            raise FeeUnscorableError(
                "Polymarket fee context requires multiplier=1 and accumulator=0"
            )
    else:  # pragma: no cover - exhaustive enum defense
        raise FeeUnscorableError("unsupported venue")


def quote_kalshi_rounding(
    *,
    trade_fee: Decimal,
    signed_revenue: Decimal,
    account_precision: Decimal,
    accumulator: Decimal,
) -> KalshiRoundingQuote:
    for name, value in (
        ("trade_fee", trade_fee),
        ("signed revenue", signed_revenue),
        ("account precision", account_precision),
        ("accumulator", accumulator),
    ):
        _require_finite(name, value)
    if trade_fee < 0:
        raise FeeUnscorableError("trade_fee must be non-negative")
    if account_precision not in (
        DIRECT_ACCOUNT_PRECISION,
        NON_DIRECT_ACCOUNT_PRECISION,
    ):
        raise FeeUnscorableError("unsupported Kalshi account precision")
    if accumulator < 0 or accumulator >= CENT:
        raise FeeUnscorableError("accumulator must be in [0, 0.01)")

    balance_change = signed_revenue - trade_fee
    floored_balance_change = _floor_to(balance_change, account_precision)
    rounding_fee = balance_change - floored_balance_change
    accumulated_rounding = accumulator + rounding_fee
    rebate = _floor_to(accumulated_rounding, CENT)
    next_accumulator = accumulated_rounding - rebate
    net_fee = trade_fee + rounding_fee - rebate
    return KalshiRoundingQuote(
        rounding_fee=rounding_fee,
        rebate=rebate,
        net_fee=net_fee,
        next_accumulator=next_accumulator,
    )


def quote_fee(context: FeeContext) -> FeeQuote:
    _validate_context(context)
    raw_fee = (
        context.multiplier
        * context.coefficient
        * context.quantity
        * context.price
        * (Decimal("1") - context.price)
    )

    if context.schedule_id.venue is Venue.KALSHI:
        trade_fee = _ceil_to(raw_fee, CENTICENT)
        assert context.account_precision is not None  # validated above
        rounded = quote_kalshi_rounding(
            trade_fee=trade_fee,
            signed_revenue=context.signed_revenue,
            account_precision=context.account_precision,
            accumulator=context.accumulator,
        )
        return FeeQuote(
            schedule_id=context.schedule_id,
            role=context.role,
            base_fee=raw_fee,
            trade_fee=trade_fee,
            rounding_adjustment=(trade_fee - raw_fee) + rounded.rounding_fee,
            balance_rounding_fee=rounded.rounding_fee,
            rebate=rounded.rebate,
            net_fee=rounded.net_fee,
            previous_accumulator=context.accumulator,
            next_accumulator=rounded.next_accumulator,
        )

    if context.schedule_id.venue is Venue.POLYMARKET_US:
        trade_fee = raw_fee.quantize(CENT, rounding=ROUND_HALF_EVEN)
        return FeeQuote(
            schedule_id=context.schedule_id,
            role=context.role,
            base_fee=raw_fee,
            trade_fee=trade_fee,
            rounding_adjustment=trade_fee - raw_fee,
            balance_rounding_fee=Decimal("0"),
            rebate=Decimal("0"),
            net_fee=trade_fee,
            previous_accumulator=Decimal("0"),
            next_accumulator=Decimal("0"),
        )

    raise FeeUnscorableError("unsupported venue")
