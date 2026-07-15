from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trading.fees import (
    DIRECT_ACCOUNT_PRECISION,
    KALSHI_GENERAL_2026_07_07,
    NON_DIRECT_ACCOUNT_PRECISION,
    POLYMARKET_US_2026_07_01,
    FeeContext,
    FeeRole,
    FeeUnscorableError,
    quote_fee,
    quote_kalshi_rounding,
)
from trading.venue import Venue


D = Decimal
UTC = timezone.utc
FIXTURE_MANIFEST = Path(__file__).parent / "fixtures" / "fees" / "manifest.json"


def _context(
    *,
    venue: Venue = Venue.KALSHI,
    role: FeeRole = FeeRole.TAKER,
    quantity: str = "100",
    price: str = "0.50",
    signed_revenue: str = "-50.00",
    multiplier: str = "1",
    coefficient: str = "0.07",
    account_precision: Decimal | None = DIRECT_ACCOUNT_PRECISION,
    accumulator: str = "0",
    timestamp: datetime = datetime(2026, 7, 14, 12, tzinfo=UTC),
) -> FeeContext:
    schedule_id = (
        KALSHI_GENERAL_2026_07_07
        if venue is Venue.KALSHI
        else POLYMARKET_US_2026_07_01
    )
    return FeeContext(
        schedule_id=schedule_id,
        role=role,
        quantity=D(quantity),
        price=D(price),
        signed_revenue=D(signed_revenue),
        order_id="order-1",
        accumulator=D(accumulator),
        multiplier=D(multiplier),
        coefficient=D(coefficient),
        account_precision=account_precision,
        timestamp=timestamp,
    )


def test_pinned_manifest_matches_implemented_schedule_ids():
    manifest = json.loads(FIXTURE_MANIFEST.read_text())
    by_id = {item["schedule_id"]: item for item in manifest["schedules"]}

    assert by_id[KALSHI_GENERAL_2026_07_07.name]["artifact_sha256"] == (
        KALSHI_GENERAL_2026_07_07.artifact_sha256
    )
    assert by_id[POLYMARKET_US_2026_07_01.name]["artifact_sha256"] == (
        POLYMARKET_US_2026_07_01.artifact_sha256
    )


def test_kalshi_general_taker_fee_uses_decimal_formula_and_direct_precision():
    quote = quote_fee(_context())

    assert quote.base_fee == D("1.7500")
    assert quote.trade_fee == D("1.7500")
    assert quote.balance_rounding_fee == D("0")
    assert quote.rebate == D("0")
    assert quote.net_fee == D("1.7500")
    assert quote.next_accumulator == D("0")


def test_kalshi_non_direct_rounding_matches_one_contract_fee_table():
    quote = quote_fee(
        _context(
            quantity="1",
            signed_revenue="-0.50",
            account_precision=NON_DIRECT_ACCOUNT_PRECISION,
        )
    )

    assert quote.base_fee == D("0.0175")
    assert quote.trade_fee == D("0.0175")
    assert quote.balance_rounding_fee == D("0.0025")
    assert quote.net_fee == D("0.0200")


def test_kalshi_maker_requires_explicit_schedule_coefficient_and_multiplier():
    quote = quote_fee(
        _context(
            role=FeeRole.MAKER,
            coefficient="0.0175",
            multiplier="1",
        )
    )

    assert quote.base_fee == D("0.4375")
    assert quote.net_fee == D("0.4375")


def test_kalshi_official_rounding_example_carries_accumulator_across_fills():
    accumulator = D("0")
    expected = [
        (D("0.0065"), D("0"), D("0.0065"), D("0.0150")),
        (D("0.0065"), D("0.0100"), D("0.0030"), D("0.0050")),
        (D("0.0065"), D("0"), D("0.0095"), D("0.0150")),
    ]

    for rounding_fee, rebate, next_accumulator, net_fee in expected:
        result = quote_kalshi_rounding(
            trade_fee=D("0.0085"),
            signed_revenue=D("-0.0550"),
            account_precision=NON_DIRECT_ACCOUNT_PRECISION,
            accumulator=accumulator,
        )
        assert result.rounding_fee == rounding_fee
        assert result.rebate == rebate
        assert result.next_accumulator == next_accumulator
        assert result.net_fee == net_fee
        accumulator = result.next_accumulator


def test_kalshi_direct_precision_avoids_non_direct_rounding_fee():
    result = quote_kalshi_rounding(
        trade_fee=D("0.0085"),
        signed_revenue=D("-0.0550"),
        account_precision=DIRECT_ACCOUNT_PRECISION,
        accumulator=D("0"),
    )

    assert result.rounding_fee == D("0")
    assert result.net_fee == D("0.0085")


def test_polymarket_taker_uses_current_theta_and_half_even_cents():
    quote = quote_fee(
        _context(
            venue=Venue.POLYMARKET_US,
            quantity="1000",
            price="0.10",
            signed_revenue="-100.00",
            coefficient="0.06",
            account_precision=None,
        )
    )

    assert quote.base_fee == D("5.4000")
    assert quote.trade_fee == D("5.40")
    assert quote.net_fee == D("5.40")


def test_polymarket_maker_rebate_uses_half_even_cents():
    quote = quote_fee(
        _context(
            venue=Venue.POLYMARKET_US,
            role=FeeRole.MAKER,
            quantity="1000",
            price="0.10",
            signed_revenue="100.00",
            coefficient="-0.0125",
            account_precision=None,
        )
    )

    assert quote.base_fee == D("-1.125000")
    assert quote.trade_fee == D("-1.12")
    assert quote.net_fee == D("-1.12")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", D("0")),
        ("quantity", D("NaN")),
        ("price", D("0")),
        ("price", D("1")),
        ("price", D("Infinity")),
        ("multiplier", D("-1")),
        ("accumulator", D("0.01")),
    ],
)
def test_invalid_fee_inputs_are_unscorable(field, value):
    with pytest.raises(FeeUnscorableError):
        quote_fee(replace(_context(), **{field: value}))


def test_schedule_gap_is_unscorable():
    with pytest.raises(FeeUnscorableError, match="not effective"):
        quote_fee(_context(timestamp=datetime(2026, 7, 6, 12, tzinfo=UTC)))


def test_schedule_hash_mismatch_is_unscorable():
    bad_schedule = replace(KALSHI_GENERAL_2026_07_07, artifact_sha256="0" * 64)

    with pytest.raises(FeeUnscorableError, match="unsupported schedule"):
        quote_fee(replace(_context(), schedule_id=bad_schedule))


def test_coefficient_mismatch_is_unscorable():
    with pytest.raises(FeeUnscorableError, match="coefficient"):
        quote_fee(_context(coefficient="0.05"))


def test_revenue_must_match_quantity_times_price():
    with pytest.raises(FeeUnscorableError, match="signed revenue"):
        quote_fee(_context(signed_revenue="-49.99"))


def test_naive_timestamp_is_unscorable():
    with pytest.raises(FeeUnscorableError, match="timezone-aware"):
        quote_fee(_context(timestamp=datetime(2026, 7, 14, 12)))
