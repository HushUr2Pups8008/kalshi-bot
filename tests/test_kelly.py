"""
Tests for analysis/kelly.py

Covers: kelly_bet sizing regressions, contracts_from_dollars, time discount,
        zero-bet conditions, confidence and source multiplier interaction.
"""

import pytest
from analysis.kelly import kelly_bet, contracts_from_dollars, _time_discount


# ---------------------------------------------------------------------------
# _time_discount
# ---------------------------------------------------------------------------

class TestTimeDiscount:
    def test_no_discount_within_3_days(self):
        assert _time_discount(0) == 1.0
        assert _time_discount(1) == 1.0
        assert _time_discount(3) == 1.0

    def test_decay_beyond_3_days(self):
        d7  = _time_discount(7)
        d14 = _time_discount(14)
        d21 = _time_discount(21)
        assert d7  < 1.0
        assert d14 < d7
        assert d21 < d14

    def test_floor_enforced(self):
        # Very long duration should hit the floor (default 0.20)
        assert _time_discount(9999) == pytest.approx(0.20, abs=1e-6)

    def test_custom_floor(self):
        val = _time_discount(9999, half_life=14.0, floor=0.30)
        assert val == pytest.approx(0.30, abs=1e-6)

    def test_14_day_market_roughly_half(self):
        # At half_life=14, t=14 days should give ~0.5 discount (from the 3-day mark)
        # Actually decay from day 3, so at day 17 we'd hit 0.5. At day 14 it should be > 0.5.
        val = _time_discount(14, half_life=14.0, floor=0.20)
        assert 0.5 < val < 1.0


# ---------------------------------------------------------------------------
# kelly_bet
# ---------------------------------------------------------------------------

class TestKellyBet:
    def _bet(self, **kwargs):
        defaults = dict(
            estimated_probability=0.70,
            market_price_cents=55.0,
            bankroll=500.0,
            kelly_fraction=0.5,
            max_bet_dollars=75.0,
            min_bet_dollars=2.0,
            min_edge=0.04,
            source_multiplier=1.0,
            confidence=1.0,
            days_to_close=0.0,  # no time discount
        )
        defaults.update(kwargs)
        return kelly_bet(**defaults)

    def test_positive_edge_produces_bet(self):
        frac, dollars, capped = self._bet()
        assert frac > 0
        assert dollars > 0
        assert capped > 0

    def test_below_min_edge_returns_zero(self):
        frac, dollars, capped = self._bet(estimated_probability=0.57, market_price_cents=55.0)
        # edge = 0.57 - 0.55 = 0.02 < min_edge 0.04
        assert frac == 0.0
        assert dollars == 0.0
        assert capped == 0.0

    def test_capped_at_max_bet(self):
        # Large edge with high bankroll -- should hit max_bet_dollars cap
        _, _, capped = self._bet(
            estimated_probability=0.90,
            market_price_cents=50.0,
            bankroll=10_000.0,
            max_bet_dollars=25.0,
        )
        assert capped == pytest.approx(25.0, abs=0.01)

    def test_zero_confidence_returns_zero(self):
        frac, dollars, capped = self._bet(confidence=0.0)
        assert frac == 0.0
        assert dollars == 0.0
        assert capped == 0.0

    def test_source_multiplier_scales_bet(self):
        _, d1, _ = self._bet(source_multiplier=1.0)
        _, d2, _ = self._bet(source_multiplier=0.5)
        _, d3, _ = self._bet(source_multiplier=1.5)
        assert d2 < d1 < d3

    def test_no_edge_yes_direction(self):
        # Market price == estimated probability => no edge => no bet
        frac, dollars, capped = self._bet(
            estimated_probability=0.55,
            market_price_cents=55.0,
        )
        assert frac == 0.0

    def test_no_edge_no_direction(self):
        frac, dollars, capped = self._bet(
            estimated_probability=0.45,
            market_price_cents=45.0,
        )
        assert frac == 0.0

    def test_time_discount_reduces_bet(self):
        _, d_short, _ = self._bet(days_to_close=1.0)
        _, d_long,  _ = self._bet(days_to_close=60.0)
        assert d_long < d_short

    def test_negative_edge_bets_no(self):
        # estimated_prob < market_price => bet NO => still a valid positive bet
        frac, dollars, capped = self._bet(
            estimated_probability=0.40,
            market_price_cents=55.0,
        )
        assert frac > 0
        assert dollars > 0


# ---------------------------------------------------------------------------
# contracts_from_dollars
# ---------------------------------------------------------------------------

class TestContractsFromDollars:
    def test_basic_conversion(self):
        # $10 at 50 cents => 20 contracts
        assert contracts_from_dollars(10.0, 50.0) == 20

    def test_rounds_down(self):
        # $10 at 30 cents => 33.3 => 33 contracts
        assert contracts_from_dollars(10.0, 30.0) == 33

    def test_minimum_one_contract(self):
        # Very small dollar amount still yields at least 1
        assert contracts_from_dollars(0.01, 99.0) == 1

    def test_zero_price_returns_zero(self):
        assert contracts_from_dollars(100.0, 0.0) == 0
