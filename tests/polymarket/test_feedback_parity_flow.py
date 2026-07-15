from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config as _cfg_module
from feeds import NewsItem
from polymarket.models import PolymarketMarket
from polymarket.paper_runtime import PolymarketPaperRuntime
from polymarket.settlement_reconciler import SettlementReconciler
from scripts.pipeline_feedback_report import summarize_events
from tasks.calibration_task import CalibrationTask
from tasks.stats.source_stats import SourceStats
from trading.paper_trader import PaperTrader
from trading.venue import Venue


class _FakeClient:
    def __init__(self, markets):
        self.markets = markets

    def get_markets(self, *, limit: int):
        return self.markets[:limit], None


class _FakeSettlementSource:
    def __init__(self, settlements):
        self.settlements = settlements

    def get_settlement(self, market_id: str):
        return self.settlements[market_id]


def _news() -> NewsItem:
    return NewsItem(
        headline="Kansas governor election tightens after new polling",
        url="https://example.test/kansas-governor",
        source="Example Wire",
        published=datetime.now(timezone.utc),
        body="Kansas governor election polling moved the race.",
    )


def _market() -> PolymarketMarket:
    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="ewc-usgub-ks-2026-11-03-dem",
        venue_market_id="8595",
        title="Democratic Party",
        question="Kansas Governor Election Winner",
        subtitle="2026 race",
        category="politics",
        status="open",
        yes_ask_cents=42,
        no_ask_cents=59,
        volume_dollars=1000.0,
        open_interest_dollars=100.0,
        close_time="2026-12-31T23:59:59Z",
        is_binary=True,
    )


@pytest.mark.asyncio
async def test_polymarket_candidate_closes_shared_feedback_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(_cfg_module.cfg, "max_ticker_exposure_pct", 0.25)
    monkeypatch.setattr(_cfg_module.cfg, "kelly_fraction", 0.5)

    db_path = tmp_path / "paper_trades.db"
    calibration_task = CalibrationTask()
    paper = PaperTrader(
        db_path=db_path,
        startup_context="test",
        calibration_task=calibration_task,
    )
    paper._set_state("notional_bankroll", "500.0")
    source_stats = SourceStats(db_path=db_path)
    source_stats.increment_posts("Example Wire")

    async def estimate_probability(news, market, *, keyword_stats=None, match_meta=None):
        assert match_meta["pre_llm_quality_pass"] is True
        assert match_meta["matched_tokens"] == ["election", "governor", "kansas", "race"]
        return 0.65, 0.8, [], "reason", "yes", "moderate", 0.8

    async def route_analysis(analysis, **kwargs):
        assert kwargs == {"accumulate": True, "watch": False}
        analysis.signal_meta = {
            **(analysis.signal_meta or {}),
            "fast_lane_p": analysis.estimated_probability,
            "fast_lane_confidence": analysis.confidence,
        }
        trade_id = paper.record_trade(analysis)
        return SimpleNamespace(enqueued=True, trade_id=trade_id)

    runtime = PolymarketPaperRuntime(
        client=_FakeClient([_market()]),
        route_analysis=route_analysis,
        keyword_stats=None,
        source_stats=source_stats,
        estimate_probability_fn=estimate_probability,
        market_limit=10,
    )

    assert await runtime.process_news(_news()) == 1
    source_stats.flush()

    trade = paper._conn.execute(
        "SELECT ticker, venue, venue_market_id, identity_status, series_ticker, "
        "resolved FROM paper_trades"
    ).fetchone()
    assert trade["ticker"] == "ewc-usgub-ks-2026-11-03-dem"
    assert trade["venue"] == "polymarket_us"
    assert trade["venue_market_id"] == "8595"
    assert trade["identity_status"] == "mapped"
    # PROFIT-VENUE-PARITY V03/V15: persisted series_ticker is now per-family
    # (so calibration/keyword_outcomes key per-family); venue stays the venue.
    assert trade["series_ticker"] == "polymarket_us:ewc-usgub-ks"
    assert trade["resolved"] == 0

    source_row = paper._conn.execute(
        "SELECT posts_seen, signals FROM source_stats WHERE source = ?",
        ("Example Wire",),
    ).fetchone()
    assert dict(source_row) == {"posts_seen": 1, "signals": 1}

    settlement = SettlementReconciler(
        source=_FakeSettlementSource(
            {
                "ewc-usgub-ks-2026-11-03-dem": {
                    "settled": True,
                    "resolvedOutcome": "YES",
                }
            }
        ),
        resolver=paper,
    ).reconcile()
    await paper.record_resolution_calibration_events(settlement.lane_events)

    assert settlement.resolved == 1
    resolved_trade = paper._conn.execute(
        "SELECT resolved, resolved_yes FROM paper_trades"
    ).fetchone()
    assert resolved_trade["resolved"] == 1
    assert resolved_trade["resolved_yes"] == 1

    keyword_row = paper._conn.execute(
        "SELECT series_ticker, correct FROM keyword_outcomes WHERE keyword = ?",
        ("election",),
    ).fetchone()
    # PROFIT-VENUE-PARITY V18: keyword_outcomes now keys per-family for PM.
    assert keyword_row["series_ticker"] == "polymarket_us:ewc-usgub-ks"
    assert keyword_row["correct"] in (0, 1)
    assert calibration_task._state.lanes["fast"].sample_count == 1

    log_path = tmp_path / "trade_events.jsonl"
    log_path.write_text(
        '{"type":"SIGNAL_ANALYSIS_DETAIL","ticker":"ewc-usgub-ks-2026-11-03-dem",'
        '"series_ticker":"polymarket_us","source_class":"newswire"}\n',
        encoding="utf-8",
    )
    report = summarize_events([log_path])
    assert report["market_mix"]["by_prefix"]["polymarket_us"][
        "SIGNAL_ANALYSIS_DETAIL"
    ] == 1
