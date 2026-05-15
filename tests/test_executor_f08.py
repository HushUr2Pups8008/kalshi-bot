"""
F-08 regression tests for trading/executor.py:_validate

Pins the fail-closed behavior introduced by audit finding F-08:
_validate must reject (not fall back to YES midpoint) when
executed_price_cents is None and price_available=True.

TDD cycle: these tests are written BEFORE the fix.
Pre-fix: tests 1, 4, 5 fail (current code uses midpoint fallback).
Post-fix: all 5 pass.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import config as _cfg_module
from trading.executor import TradeExecutor


# ---------------------------------------------------------------------------
# Shared helpers (mirror the minimal subset needed from test_executor.py
# rather than importing them, to keep this file self-contained)
# ---------------------------------------------------------------------------

def _make_paper_executor(monkeypatch):
    """Return a paper-mode executor with permissive defaults."""
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "live_loss_limit_pct", 0.10)
    monkeypatch.setattr(_cfg_module.cfg, "live_ticker_cooldown", 0)
    monkeypatch.setattr(_cfg_module.cfg, "paper_ticker_cooldown", 0)
    monkeypatch.setattr(_cfg_module.cfg, "min_edge", 0.04)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 1.0)

    rest = MagicMock()
    rest.get_market.return_value = None

    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 500.0
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    paper.portfolio.exposure.return_value = 0.0
    paper.portfolio.tickers.return_value = []

    ex = TradeExecutor(rest, paper)
    return ex, rest, paper


def _make_analysis_no_executed_price(price_available: bool, yes_price: float = 50.0):
    """
    Build a minimal SignalAnalysis-like mock where executed_price_cents is None.
    price_available controls whether the market reports a tradeable price.
    yes_price is the midpoint that the old code would have fallen back to.
    """
    market = MagicMock()
    market.ticker = "KXTEST-F08"
    market.yes_price = yes_price
    market.status = "active"
    market.price_available = price_available
    market.is_tradeable = lambda: price_available

    news = MagicMock()
    news.headline = "F-08 test headline"
    news.source = "test"

    a = MagicMock()
    a.market = market
    a.news_item = news
    a.side = "yes"
    a.edge = 0.10            # well above any threshold
    a.estimated_probability = 0.60
    a.executed_price_cents = None   # <-- the F-08 condition under test
    a.capped_dollars = 10.0
    a.confidence = 0.8
    a.kelly_fraction = 0.5
    a.kelly_dollars = 10.0
    a.llm_direction = None
    a.llm_magnitude = None
    a.llm_confidence = None
    return a


def _make_analysis_with_executed_price(executed_price_cents: int = 42):
    """Build a mock with executed_price_cents properly set (canonical post-P0 path)."""
    market = MagicMock()
    market.ticker = "KXTEST-F08"
    market.yes_price = float(executed_price_cents)
    market.status = "active"
    market.price_available = True
    market.is_tradeable = lambda: True

    news = MagicMock()
    news.headline = "F-08 canonical test"
    news.source = "test"

    a = MagicMock()
    a.market = market
    a.news_item = news
    a.side = "yes"
    a.edge = 0.10
    a.estimated_probability = 0.60
    a.executed_price_cents = executed_price_cents   # canonical field set
    a.capped_dollars = 10.0
    a.confidence = 0.8
    a.kelly_fraction = 0.5
    a.kelly_dollars = 10.0
    a.llm_direction = None
    a.llm_magnitude = None
    a.llm_confidence = None
    return a


# ---------------------------------------------------------------------------
# F-08 tests (RED before fix, GREEN after)
# ---------------------------------------------------------------------------

class TestValidateF08:
    """
    Five tests that pin the fail-closed executed_price_cents contract.

    Tests 1 and 4 expose the pre-fix midpoint-fallback bug:
      - pre-fix: validate passes (midpoint fallback) -> tests FAIL
      - post-fix: validate returns new skip reason -> tests PASS

    Tests 2 and 3 are regression guards for existing paths.
    Test 5 covers the full execute path via log_skipped emission.
    """

    # -----------------------------------------------------------------------
    # Test 1: executed_price_cents=None + price_available=True -> new skip
    # -----------------------------------------------------------------------
    def test_validate_returns_skip_when_executed_price_cents_none_and_price_available_true(
        self, monkeypatch
    ):
        """
        F-08 core case: producer set price_available=True but never populated
        executed_price_cents. Pre-fix: validate fell back to YES midpoint and
        passed. Post-fix: validate must return the new structured skip reason.

        WHY: silent midpoint fallback masked side-selector bugs, producing
        OPPORTUNITY log rows with no corresponding SQL trade (F-06 ALARM root
        cause: 4 opportunities, 0 paper trades, 11.5h gap).
        """
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis_no_executed_price(price_available=True, yes_price=50.0)

        skip = ex._validate(analysis)

        assert skip is not None, (
            "Expected _validate to return a skip reason when executed_price_cents is None "
            "and price_available=True, but it returned None (i.e., trade passed)."
        )
        assert "executed_price_cents is None" in skip, (
            f"Skip reason should name the missing field. Got: {skip!r}"
        )
        assert "price_available=True" in skip, (
            f"Skip reason should include price_available=True context. Got: {skip!r}"
        )

    # -----------------------------------------------------------------------
    # Test 2: executed_price_cents=None + price_available=False -> existing skip
    # -----------------------------------------------------------------------
    def test_validate_returns_skip_when_executed_price_cents_none_and_price_unavailable(
        self, monkeypatch
    ):
        """
        Regression guard: when price_available=False the existing
        'price_unavailable: market.price_available=False' reason must still fire.
        This path predates F-08 and must not be disturbed by the fix.
        """
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis_no_executed_price(price_available=False)

        skip = ex._validate(analysis)

        assert skip is not None
        assert "price_available=False" in skip, (
            f"Expected original price-unavailable reason. Got: {skip!r}"
        )

    # -----------------------------------------------------------------------
    # Test 3: executed_price_cents set -> validate passes (canonical path)
    # -----------------------------------------------------------------------
    def test_validate_passes_when_executed_price_cents_set(self, monkeypatch):
        """
        Regression guard: when executed_price_cents is properly set, _validate
        must return None (trade proceeds). The canonical post-P0 path must not
        be broken by the F-08 fail-closed change.
        """
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis_with_executed_price(executed_price_cents=42)

        skip = ex._validate(analysis)

        assert skip is None, (
            f"Expected _validate to return None (pass) for canonical analysis. "
            f"Got skip reason: {skip!r}"
        )

    # -----------------------------------------------------------------------
    # Test 4: midpoint-fallback regression direction pin
    # -----------------------------------------------------------------------
    def test_validate_does_not_use_midpoint_fallback_for_price_sanity(
        self, monkeypatch
    ):
        """
        Pins the regression direction of F-08.

        Pre-fix: an analysis with yes_price=50 (sane midpoint) but
        executed_price_cents=None passed _validate — the executor silently
        substituted the YES midpoint as the price. This masked side-selector
        producer bugs and caused the F-06 ALARM (4 opportunities, 0 trades).

        Post-fix: the same analysis must be rejected regardless of yes_price
        being a sane value. The midpoint must NEVER be used as a price fallback.
        """
        ex, _, _ = _make_paper_executor(monkeypatch)
        # yes_price=50 is a perfectly sane midpoint — pre-fix this would pass
        analysis = _make_analysis_no_executed_price(price_available=True, yes_price=50.0)

        skip = ex._validate(analysis)

        assert skip is not None, (
            "Post-fix: _validate must reject when executed_price_cents=None even "
            "when yes_price is a sane midpoint (50). Pre-fix this passed; "
            "if this test is failing, the midpoint fallback has NOT been removed."
        )

    # -----------------------------------------------------------------------
    # Test 5: full execute path emits log_skipped for new reason
    # -----------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_execute_trade_skip_emits_log_skipped_for_new_reason(
        self, monkeypatch
    ):
        """
        Full-path integration: execute() with a None-executed_price_cents analysis
        must invoke trade_log.log_skipped(...) with the new F-08 skip reason.

        Confirms the skip-log emission path in executor.execute() (lines ~128-160)
        already covers the new reason without any additional wiring.
        WHY: structured log_skipped rows are how the F-06 watcher distinguishes
        "no opportunities" from "opportunities rejected by executor" — the watcher
        must be able to see the new reason to downgrade ALARM to DEGRADED.
        """
        ex, _, _ = _make_paper_executor(monkeypatch)
        analysis = _make_analysis_no_executed_price(price_available=True, yes_price=50.0)

        # Patch write_trade_log_async so we can intercept log_skipped calls
        # without touching the real JSONL trade log.
        with patch("trading.executor.write_trade_log_async", new_callable=AsyncMock) as mock_write:
            result = await ex.execute(analysis)

        assert result is None, "execute() must return None (skipped) for F-08 condition"

        # write_trade_log_async must have been called with trade_log.log_skipped
        # and the new F-08 reason embedded in the 'reason' kwarg.
        assert mock_write.called, "write_trade_log_async was not called — skip was not logged"

        # Extract the 'reason' kwarg from the call. Use the modern .kwargs
        # property rather than the deprecated positional [1] form.
        reason = mock_write.call_args.kwargs.get("reason", "")
        # Pin BOTH substrings so the downstream skip-rate-by-reason analytics
        # grep keeps working even if a future regression rewords one half:
        # the canonical reason carries `price_unavailable` for the bucket key
        # and `executed_price_cents is None` for the cause sub-classification.
        assert "price_unavailable" in reason, (
            f"log_skipped reason must contain 'price_unavailable' for "
            f"downstream bucketing. Got: {reason!r}"
        )
        assert "executed_price_cents is None" in reason, (
            f"log_skipped reason must contain 'executed_price_cents is None' "
            f"for cause sub-classification. Got: {reason!r}"
        )
