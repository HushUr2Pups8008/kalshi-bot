from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import config as config_module
from analysis import SignalAnalysis
from analysis.decision_blender import BlendResult
from feeds import NewsItem
from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.models import PolymarketMarket
from polymarket.paper_trader import PolymarketPaperTrader
from tasks.blend_task import BlendTask
from trading.paper_trader import PaperTrader
from trading.venue import Venue


@pytest.fixture()
def paper_trader(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(config_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(config_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(config_module.cfg, "kelly_fraction", 0.5)
    return PaperTrader(tmp_path / "paper_trades.db", startup_context="test")


def _base_analysis(side: str = "yes") -> SignalAnalysis:
    return SignalAnalysis(
        news_item=NewsItem(
            headline="Example headline",
            url="https://example.test/story",
            source="example",
            published=datetime(2026, 1, 1, tzinfo=timezone.utc),
            body="",
        ),
        market=None,
        estimated_probability=0.64,
        executed_price_cents=40,
        edge=0.24,
        side=side,
        kelly_fraction=0.1,
        kelly_dollars=10.0,
        capped_dollars=10.0,
        keywords_matched=["example"],
        reasoning="test",
        confidence=0.7,
        match_score=0.2,
        signal_type="news",
        llm_direction="yes",
        llm_magnitude="moderate",
        llm_confidence=0.8,
        signal_meta={"source": "test"},
    )


def _polymarket_market(**overrides) -> PolymarketMarket:
    values = {
        "venue": Venue.POLYMARKET_US,
        "market_id": "will-example-happen-2026",
        "title": "Will example happen in 2026?",
        "status": "open",
        "yes_ask_cents": 42,
        "no_ask_cents": 59,
        "volume_dollars": 1200.0,
        "open_interest_dollars": 340.0,
        "close_time": "2026-12-31T23:59:59Z",
        "is_binary": True,
    }
    values.update(overrides)
    return PolymarketMarket(**values)


class _BlendStore:
    async def get_dossier(self, market_ticker: str):
        return None

    async def get_structural_prior(self, market_ticker: str):
        return None

    async def get_recent_evidence(self, market_ticker: str, *, limit: int = 100):
        return []


class _BlendLogger:
    def __init__(self) -> None:
        self.blend_decisions: list[dict] = []
        self.skips: list[dict] = []
        self.gate_summaries: list[dict] = []
        self.lane_skips: list[dict] = []

    def log_blend_decision(self, **kwargs) -> None:
        self.blend_decisions.append(kwargs)

    def log_skipped(self, **kwargs) -> None:
        self.skips.append(kwargs)

    def log_gate_summary(self, **kwargs) -> None:
        self.gate_summaries.append(kwargs)

    def log_lane_skipped(self, **kwargs) -> None:
        self.lane_skips.append(kwargs)


def test_adapts_yes_side_analysis_to_polymarket_execution_contract():
    adapted = adapt_polymarket_analysis(_base_analysis(side="yes"), _polymarket_market())

    assert adapted.market.ticker == "will-example-happen-2026"
    assert adapted.market.series_ticker == "polymarket_us"
    assert adapted.market.venue == Venue.POLYMARKET_US
    assert adapted.market.title == "Will example happen in 2026?"
    assert adapted.market.price_source == "polymarket_us_rest"
    assert adapted.market.price_method == "binary_ask"
    assert adapted.market.regime_weights == {
        "fast": 1.0,
        "interpretation": 0.0,
        "structural": 0.0,
    }
    assert adapted.venue == "polymarket_us"
    assert adapted.side == "yes"
    assert adapted.executed_price_cents == 42
    assert adapted.edge == pytest.approx(0.22)
    assert adapted.signal_meta["venue"] == "polymarket_us"
    assert adapted.signal_meta["polymarket_market_id"] == "will-example-happen-2026"


def test_adapts_no_side_analysis_to_polymarket_execution_contract():
    adapted = adapt_polymarket_analysis(_base_analysis(side="no"), _polymarket_market())

    assert adapted.side == "no"
    assert adapted.executed_price_cents == 59
    assert adapted.edge == pytest.approx(-0.23)


def test_rejects_non_tradeable_polymarket_market():
    with pytest.raises(ValueError, match="not tradeable"):
        adapt_polymarket_analysis(
            _base_analysis(side="yes"),
            _polymarket_market(yes_ask_cents=None),
        )


@pytest.mark.asyncio
async def test_adapted_polymarket_analysis_routes_through_shared_blend_contract():
    def blocked_blender(fast, accumulation, structural, **kwargs):
        return BlendResult(
            blended_p=0.30,
            blended_confidence=0.01,
            disagreement_score=0.0,
            blend_mode="blocked_for_venue_regression",
            readiness_gate_min_edge_override=None,
            trade_blocked_reason="G1_blended_confidence",
            fast_lane_p=fast.p,
            fast_lane_confidence=fast.confidence,
            accumulation_p=None,
            accumulation_confidence=None,
            structural_p=None,
            structural_confidence=None,
        )

    logger = _BlendLogger()
    task = BlendTask(
        trading_queue=asyncio.Queue(),
        store=_BlendStore(),
        logger=logger,
        blender=blocked_blender,
        is_paper_mode=True,
        now=lambda: datetime(2026, 6, 9, tzinfo=timezone.utc),
    )
    adapted = adapt_polymarket_analysis(
        _base_analysis(side="yes"),
        _polymarket_market(),
    )

    result = await task.process_fast_lane_result(adapted)

    assert result.market_ticker == "will-example-happen-2026"
    assert logger.blend_decisions[0]["venue"] == "polymarket_us"
    assert logger.blend_decisions[0]["regime_weights"] == {
        "fast": 1.0,
        "interpretation": 0.0,
        "structural": 0.0,
    }
    assert logger.skips[0]["venue"] == "polymarket_us"


def test_adapted_analysis_records_polymarket_paper_row(paper_trader):
    adapted = adapt_polymarket_analysis(_base_analysis(side="yes"), _polymarket_market())
    trade_id = PolymarketPaperTrader(paper_trader).record_trade(adapted)

    row = paper_trader._conn.execute(
        """
        SELECT ticker, venue, side, price_cents, entry_price_cents, edge,
               price_source, price_method
        FROM paper_trades
        WHERE trade_id=?
        """,
        (trade_id,),
    ).fetchone()

    assert row is not None
    assert row["ticker"] == "will-example-happen-2026"
    assert row["venue"] == "polymarket_us"
    assert row["side"] == "yes"
    assert row["price_cents"] == 42
    assert row["entry_price_cents"] == 42.0
    assert row["edge"] == pytest.approx(0.22)
    assert row["price_source"] == "polymarket_us_rest"
    assert row["price_method"] == "binary_ask"
