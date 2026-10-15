"""A failed positions GET blocks Trump Say and Trump Act the same as Truth Social."""

from __future__ import annotations

import pytest

import config as cfg_module
from tests.test_executor import _make_analysis, _make_executor
from utils.event_news_research import EVENT_NEWS_COHORT_ID, EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN


def _politics(monkeypatch) -> None:
    monkeypatch.setattr(cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.delenv("EVENT_NEWS_SESSION_HALT", raising=False)
    monkeypatch.setattr(cfg_module.cfg, "llm_excluded_price_bands", [(0.00, 0.35)])


def _act_candidate(ticker: str):
    analysis = _make_analysis(
        ticker=ticker,
        side="no",
        yes_price=82.0,
        edge=0.16,
        estimated_prob=0.02,
        capped_dollars=2.0,
    )
    analysis.market.series_ticker = "KXTRUMPACT"
    analysis.market.event_ticker = "KXTRUMPACT-26OCT11"
    analysis.market.yes_ask_cents = 18
    analysis.market.no_ask_cents = 82
    analysis.market.yes_ask = 18
    analysis.market.no_ask = 82
    analysis.market.last_price_cents = 18
    analysis.market.last_price = 18
    analysis.executed_price_cents = 82
    return analysis


def test_live_positions_exception_blocks_trump_act_without_an_order(monkeypatch):
    _politics(monkeypatch)
    ex, rest, paper = _make_executor(monkeypatch)
    rest.get_open_positions.side_effect = RuntimeError("positions down")
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []

    reason = ex._validate(_act_candidate("KXTRUMPACT-26OCT11-T4"))

    assert reason is not None
    assert "event_news_family_lock" in reason
    assert EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN in reason
    rest.place_limit_order.assert_not_called()


def test_live_positions_exception_blocks_trump_say_without_an_order(monkeypatch):
    _politics(monkeypatch)
    ex, rest, paper = _make_executor(monkeypatch)
    rest.get_open_positions.side_effect = RuntimeError("positions down")
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []
    analysis = _act_candidate("KXTRUMPSAY-26OCT19-OTHER")
    analysis.market.series_ticker = "KXTRUMPSAY"
    analysis.market.event_ticker = "KXTRUMPSAY-26OCT19"

    reason = ex._validate(analysis)

    assert reason is not None
    assert EVENT_NEWS_LIVE_OCCUPANCY_UNKNOWN in reason
    rest.place_limit_order.assert_not_called()


def test_live_prefix_cap_reads_the_open_t5_row(monkeypatch):
    _politics(monkeypatch)
    ex, rest, paper = _make_executor(monkeypatch)
    rest.get_open_positions.return_value = [
        {
            "ticker": "KXTRUMPACT-26OCT11-T5",
            "position_fp": "-8.00",
            "market_exposure_dollars": "4.80",
        }
    ]
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []

    reason = ex._validate(_act_candidate("KXTRUMPACT-26OCT11-T4"))

    assert reason is not None
    assert "per-prefix cap" in reason
    assert "KXTRUMPACT-26OCT11-T5" in reason
    rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_does_not_place_when_positions_get_fails(monkeypatch):
    from unittest.mock import AsyncMock

    _politics(monkeypatch)
    ex, rest, paper = _make_executor(monkeypatch)
    rest.get_open_positions.side_effect = RuntimeError("positions down")
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []
    ex._execute_live = AsyncMock(return_value="live-order-id")
    monkeypatch.setattr("trading.executor.write_trade_log_async", AsyncMock())

    result = await ex.execute(_act_candidate("KXTRUMPACT-26OCT11-T4"))

    assert result is None
    ex._execute_live.assert_not_called()
    rest.place_limit_order.assert_not_called()
