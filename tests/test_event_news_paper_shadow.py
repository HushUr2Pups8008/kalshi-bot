"""Live event-news candidates also record on the paper book."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import config as config_module
from trading.executor import TradeExecutor
from trading.portfolio import PAPER_SHADOW_SIGNAL_TYPE, Portfolio, Position
from utils.event_news_research import EVENT_NEWS_COHORT_ID


def _executor(monkeypatch: pytest.MonkeyPatch) -> tuple[TradeExecutor, MagicMock, MagicMock]:
    capture_path = Path(tempfile.mkdtemp(prefix="politics-oos-")) / "corpus_politics_oos.jsonl"
    monkeypatch.setattr(
        "utils.event_news_oos_capture.politics_oos_corpus_path",
        lambda root=None: capture_path,
    )
    monkeypatch.setattr(config_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr(config_module.cfg, "enable_fee_net_paper_accounting", False)
    monkeypatch.setattr(config_module.cfg, "max_open_positions_per_prefix", 2)
    rest = MagicMock()
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 50.0
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []
    paper.record_trade.return_value = "paper-shadow-1"
    executor = TradeExecutor(rest, paper)
    assert executor._is_paper is False
    return executor, rest, paper


def _analysis(side: str, price: int) -> SimpleNamespace:
    market = SimpleNamespace(
        ticker="KXTRUMPSAY-26OCT20-TEST",
        series_ticker="KXTRUMPSAY",
        event_ticker="KXTRUMPSAY-26OCT20",
        status="active",
        rules="",
        rules_primary="",
        settlement_source_url="",
    )
    return SimpleNamespace(
        market=market,
        side=side,
        edge=0.08,
        executed_price_cents=price,
        capped_dollars=10.0,
        confidence=0.9,
        estimated_probability=0.7,
        signal_meta={},
        llm_direction=None,
        llm_magnitude=None,
        llm_confidence=None,
    )


@pytest.mark.asyncio
async def test_live_candidate_records_paper_and_does_not_post(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    trade_id = await executor._record_event_news_paper_shadow(_analysis("no", 60))
    assert trade_id == "paper-shadow-1"
    paper.record_trade.assert_called_once()
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_risk_cap_shrinks_the_paper_row_and_restores_the_live_budget(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    analysis = _analysis("yes", 85)
    trade_id = await executor._record_event_news_paper_shadow(analysis)
    assert trade_id == "paper-shadow-1"
    terms = paper.record_trade.call_args.kwargs["execution_terms"]
    assert terms.contracts == 7
    assert terms.price_cents == 85
    assert analysis.capped_dollars == 10.0
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()


@pytest.mark.asyncio
async def test_recorded_shadow_appends_a_prospective_capture(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 0)
    from utils.event_news_oos_capture import politics_oos_corpus_path

    trade_id = await executor._record_event_news_paper_shadow(_analysis("no", 60))
    assert trade_id == "paper-shadow-1"
    path = politics_oos_corpus_path()
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["evidence_class"] == "prospective_capture"
    assert rows[0]["evidence_class"] != "registered_oos"
    assert rows[0]["oos_registration_id"] == "politics-oos"
    assert rows[0]["trade_id"] == "paper-shadow-1"
    assert rows[0]["ticker"] == "KXTRUMPSAY-26OCT20-TEST"
    assert "client_order_id" not in rows[0]
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    again = await executor._record_event_news_paper_shadow(_analysis("no", 60))
    assert again == "paper-shadow-1"
    written = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(written) == 1
    paper.record_trade.assert_called()


@pytest.mark.asyncio
async def test_excluded_band_does_not_record(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    trade_id = await executor._record_event_news_paper_shadow(_analysis("no", 35))
    assert trade_id is None
    paper.record_trade.assert_not_called()
    rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_yes_below_55_does_not_record(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    trade_id = await executor._record_event_news_paper_shadow(_analysis("yes", 40))
    assert trade_id is None
    paper.record_trade.assert_not_called()
    rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_failure_does_not_raise(monkeypatch) -> None:
    executor, _rest, paper = _executor(monkeypatch)
    paper.record_trade.side_effect = RuntimeError("disk")
    await executor._shadow_live_candidate(_analysis("no", 60))


@pytest.mark.asyncio
async def test_other_cohort_does_not_record(monkeypatch) -> None:
    executor, _rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", "kalshi-macro-20260820")
    trade_id = await executor._record_event_news_paper_shadow(_analysis("no", 60))
    assert trade_id is None
    paper.record_trade.assert_not_called()


def _fee_net_on(monkeypatch: pytest.MonkeyPatch, paper: MagicMock) -> str:
    monkeypatch.setattr(config_module.cfg, "enable_fee_net_paper_accounting", True)
    lifecycle_id = "lc-" + "ab" * 16
    paper.record_trade_result.return_value = SimpleNamespace(
        trade_id="paper-shadow-fee",
        created=True,
    )
    return lifecycle_id


@pytest.mark.asyncio
async def test_fee_net_records_with_existing_lifecycle_and_does_not_post(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    lifecycle_id = _fee_net_on(monkeypatch, paper)
    analysis = _analysis("no", 60)
    analysis.signal_meta = {"lifecycle_id": lifecycle_id}
    trade_id = await executor._record_event_news_paper_shadow(analysis)
    assert trade_id == "paper-shadow-fee"
    paper.record_trade.assert_not_called()
    paper.record_trade_result.assert_called_once()
    assert paper.record_trade_result.call_args.kwargs["entry_request_id"].endswith(lifecycle_id)
    assert analysis.signal_meta == {"lifecycle_id": lifecycle_id}
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_fee_net_mints_a_stable_lifecycle_when_missing(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 0)
    _fee_net_on(monkeypatch, paper)
    seen: list[str] = []

    def _record(analysis, **kwargs):
        seen.append(analysis.signal_meta["lifecycle_id"])
        assert kwargs["entry_request_id"].endswith(seen[-1])
        return SimpleNamespace(trade_id="paper-shadow-fee", created=True)

    paper.record_trade_result.side_effect = _record
    first = _analysis("no", 60)
    second = _analysis("no", 60)
    assert await executor._record_event_news_paper_shadow(first) == "paper-shadow-fee"
    assert await executor._record_event_news_paper_shadow(second) == "paper-shadow-fee"
    assert seen[0] == seen[1]
    assert seen[0].startswith("lc-") and len(seen[0]) == 35
    assert first.signal_meta == {}
    assert second.signal_meta == {}
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_row_does_not_block_or_resize_the_next_live_check(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "max_ticker_exposure_pct", 0.25)
    portfolio = Portfolio()
    paper.portfolio = portfolio
    paper.get_effective_sizing_bankroll.return_value = 10.0
    paper.get_notional_bankroll.return_value = 50.0
    paper._starting_bankroll = 50.0
    rest.get_balance.return_value = 1000.0
    seen: dict[str, str] = {}

    def _record(analysis, **kwargs):
        del kwargs
        seen["signal_type"] = str(analysis.signal_type)
        portfolio.add(
            Position(
                trade_id="paper-shadow-1",
                ticker=analysis.market.ticker,
                side=analysis.side,
                contracts=4,
                cost_dollars=40.0,
                price_cents=analysis.executed_price_cents,
                estimated_prob=analysis.estimated_probability,
                entry_price_cents=float(analysis.executed_price_cents),
                ts="2026-10-13T00:00:00+00:00",
                paper_shadow=analysis.signal_type == PAPER_SHADOW_SIGNAL_TYPE,
            )
        )
        return "paper-shadow-1"

    paper.record_trade.side_effect = _record
    analysis = _analysis("no", 60)
    assert await executor._record_event_news_paper_shadow(analysis) == "paper-shadow-1"
    assert seen["signal_type"] == PAPER_SHADOW_SIGNAL_TYPE
    assert analysis.signal_type is None
    assert portfolio.open_positions()[0].paper_shadow is True
    assert executor._validate(analysis) is None
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


def test_paper_exposure_adds_open_cost_back_onto_residual_cash(monkeypatch) -> None:
    """Residual notional already excludes open cost. The cap must not count it twice."""
    executor, _rest, paper = _executor(monkeypatch)
    paper.get_notional_bankroll.return_value = 46.0
    paper.portfolio.open_positions.return_value = [
        SimpleNamespace(cost_dollars=4.0, ticker="KXTRUMPSAY-26OCT20-OLD", paper_shadow=True)
    ]
    assert executor._paper_shadow_book_cents() == (5000, 400)


def test_shadow_row_is_not_live_admission_exposure(monkeypatch) -> None:
    """A paper shadow row must not block the next live research admission."""
    from main import TradingBot

    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    portfolio = Portfolio()
    portfolio.add(
        Position(
            trade_id="shadow",
            ticker="KXTRUTHSOCIAL-26OCT20-B9",
            side="no",
            contracts=4,
            cost_dollars=2.4,
            price_cents=60,
            estimated_prob=0.7,
            entry_price_cents=60.0,
            ts="2026-10-13T00:00:00+00:00",
            paper_shadow=True,
        )
    )
    portfolio.add(
        Position(
            trade_id="live-book",
            ticker="KXTRUMPACT-26OCT20-T9",
            side="yes",
            contracts=1,
            cost_dollars=0.6,
            price_cents=60,
            estimated_prob=0.7,
            entry_price_cents=60.0,
            ts="2026-10-13T00:00:00+00:00",
            paper_shadow=False,
        )
    )
    bot = SimpleNamespace(
        paper=SimpleNamespace(portfolio=portfolio),
        _trading_queue=None,
        rest=SimpleNamespace(get_open_positions=lambda strict=True: []),
    )
    assert TradingBot._event_news_has_paper_exposure(bot, "KXTRUTHSOCIAL-26OCT20-B9") is False
    assert TradingBot._event_news_has_paper_exposure(bot, "KXTRUMPACT-26OCT20-T9") is True
    tickers = TradingBot._event_news_open_paper_tickers(bot)
    assert "KXTRUTHSOCIAL-26OCT20-B9" not in tickers
    assert "KXTRUMPACT-26OCT20-T9" in tickers
    queued = SimpleNamespace(
        fast_lane_analysis=SimpleNamespace(
            market=SimpleNamespace(ticker="KXTRUTHSOCIAL-26OCT20-B9")
        )
    )
    bot._trading_queue = SimpleNamespace(_queue=[queued])
    assert TradingBot._event_news_has_paper_exposure(bot, "KXTRUTHSOCIAL-26OCT20-B9") is True


def test_reloaded_shadow_row_stays_out_of_the_live_book() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE paper_trades (
            trade_id TEXT, ticker TEXT, side TEXT, contracts INTEGER,
            cost_dollars REAL, price_cents INTEGER, estimated_prob REAL,
            entry_price_cents REAL, ts TEXT, resolved INTEGER, venue TEXT,
            venue_market_id TEXT, price_source TEXT, price_method TEXT,
            signal_type TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO paper_trades VALUES
        ('shadow', 'KXTRUMPSAY-26OCT20-TEST', 'no', 4, 2.4, 60, 0.7, 60,
         '2026-10-13T00:00:00+00:00', 0, 'kalshi', 'KXTRUMPSAY-26OCT20-TEST',
         'rest', 'book', 'event_news_paper_shadow'),
        ('live-book', 'KXTRUMPACT-26OCT20-T9', 'yes', 1, 0.6, 60, 0.7, 60,
         '2026-10-13T00:00:00+00:00', 0, 'kalshi', 'KXTRUMPACT-26OCT20-T9',
         'rest', 'book', 'news')
        """
    )
    portfolio = Portfolio()
    portfolio.load_from_db(conn)
    by_id = {pos.trade_id: pos.paper_shadow for pos in portfolio.open_positions()}
    assert by_id == {"shadow": True, "live-book": False}


@pytest.mark.asyncio
async def test_fee_net_admission_error_does_not_raise(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    lifecycle_id = _fee_net_on(monkeypatch, paper)
    paper.record_trade_result.side_effect = RuntimeError("admission")
    analysis = _analysis("no", 60)
    analysis.signal_meta = {"lifecycle_id": lifecycle_id}
    await executor._shadow_live_candidate(analysis)
    assert analysis.signal_meta == {"lifecycle_id": lifecycle_id}
    rest.place_limit_order.assert_not_called()


def _hold_rules(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, list[str]]:
    """Block inside the paper rules check until the test releases it."""
    entered: list[str] = []
    release = threading.Event()

    def slow_rules(*_args: object, **_kwargs: object) -> bool:
        entered.append("rules")
        if len(entered) == 1:
            release.wait(2)
        return False

    monkeypatch.setattr(
        "utils.event_news_rules_check.event_news_rules_check_skip",
        slow_rules,
    )
    return release, entered


@pytest.mark.asyncio
async def test_rules_check_does_not_hold_the_live_caller(monkeypatch) -> None:
    """The next live decision must not wait on the paper Grok rules check."""
    executor, rest, paper = _executor(monkeypatch)
    release, entered = _hold_rules(monkeypatch)
    analysis = _analysis("no", 60)
    executor._schedule_paper_shadow(analysis)
    for _ in range(50):
        if entered:
            break
        await asyncio.sleep(0.02)
    assert entered == ["rules"]
    assert paper.record_trade.call_count == 0
    assert analysis.capped_dollars == 10.0
    assert getattr(analysis, "signal_type", None) is None
    release.set()
    await asyncio.wait(set(executor._paper_shadow_tasks))
    assert paper.record_trade.call_count == 1
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_queued_shadows_see_the_earlier_paper_row(monkeypatch) -> None:
    """A second paper row waits until the first row is on the paper book."""
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 14400)
    release, entered = _hold_rules(monkeypatch)

    def _record(analysis: SimpleNamespace, execution_terms: object = None) -> str:
        del execution_terms
        paper.portfolio.open_positions.return_value = [
            SimpleNamespace(ticker=analysis.market.ticker, paper_shadow=True)
        ]
        return "paper-shadow-1"

    paper.record_trade.side_effect = _record
    executor._schedule_paper_shadow(_analysis("no", 60))
    for _ in range(50):
        if entered:
            break
        await asyncio.sleep(0.02)
    assert entered == ["rules"]
    executor._schedule_paper_shadow(_analysis("no", 60))
    await asyncio.sleep(0.1)
    assert entered == ["rules"]
    release.set()
    await asyncio.wait(set(executor._paper_shadow_tasks))
    assert entered == ["rules"]
    assert paper.record_trade.call_count == 1
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_entry_window_allows_one_paper_row_per_ticker(monkeypatch) -> None:
    """The paper cooldown is the entry window. A second row waits it out."""
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 14400)
    analysis = _analysis("no", 60)
    analysis._event_news_paper_shadow_source = "prefix_cap"
    assert await executor._record_event_news_paper_shadow(analysis) == "paper-shadow-1"
    again = await executor._record_event_news_paper_shadow(analysis)
    assert again is None
    assert paper.record_trade.call_count == 1
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_paper_book_blocks_a_sibling_and_live_occupancy_does_not(monkeypatch) -> None:
    """Paper prefix and family use the paper book. A live open row is not in it."""
    executor, rest, paper = _executor(monkeypatch)
    monkeypatch.setattr(config_module.cfg, "paper_ticker_cooldown", 0)
    portfolio = Portfolio()
    paper.portfolio = portfolio
    portfolio.add(
        Position(
            trade_id="paper-shadow-open",
            ticker="KXTRUMPACT-26AUG30-T8",
            side="no",
            contracts=4,
            cost_dollars=2.4,
            price_cents=60,
            estimated_prob=0.7,
            entry_price_cents=60.0,
            ts="2026-10-13T00:00:00+00:00",
            paper_shadow=True,
        )
    )
    blocked = _analysis("no", 60)
    blocked.market.ticker = "KXTRUMPACT-26AUG30-T6"
    blocked.market.series_ticker = "KXTRUMPACT"
    blocked.market.event_ticker = "KXTRUMPACT-26AUG30"
    blocked._event_news_paper_shadow_source = "prefix_cap"
    assert await executor._record_event_news_paper_shadow(blocked) is None
    assert paper.record_trade.call_count == 0
    portfolio._positions.clear()
    assert await executor._record_event_news_paper_shadow(blocked) == "paper-shadow-1"
    assert paper.record_trade.call_count == 1
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_excluded_band_decision_does_not_schedule_shadow(monkeypatch) -> None:
    executor, rest, paper = _executor(monkeypatch)
    analysis = _analysis("yes", 35)
    # execute() logs the book before _validate. The fixture market is a
    # namespace, so the price guard has to exist or the skip is never reached.
    analysis.market.price_available = True
    analysis.market.yes_price = 35

    async def _from_candidate(_candidate: object) -> SimpleNamespace:
        return analysis

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(executor, "_analysis_from_candidate", _from_candidate)
    monkeypatch.setattr(executor, "_validate", lambda _analysis: "executed_ask_in_excluded_band")
    monkeypatch.setattr("trading.executor.write_trade_log_async", _noop)
    scheduled: list[str] = []
    monkeypatch.setattr(
        executor,
        "_schedule_paper_shadow",
        lambda *_args, **kwargs: scheduled.append(str(kwargs.get("source", "missing"))),
    )
    assert await executor.execute(object()) is None
    assert scheduled == []
    paper.record_trade.assert_not_called()
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()


@pytest.mark.asyncio
async def test_non_band_skip_schedules_decision_shadow(monkeypatch) -> None:
    """A live admit that skips for another reason still gets one paper row."""
    executor, rest, paper = _executor(monkeypatch)
    analysis = _analysis("no", 60)
    analysis.market.price_available = True
    analysis.market.yes_price = 40

    async def _from_candidate(_candidate: object) -> SimpleNamespace:
        return analysis

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(executor, "_analysis_from_candidate", _from_candidate)
    monkeypatch.setattr(executor, "_validate", lambda _analysis: "edge below min_edge")
    monkeypatch.setattr("trading.executor.write_trade_log_async", _noop)
    scheduled: list[str] = []
    monkeypatch.setattr(
        executor,
        "_schedule_paper_shadow",
        lambda *_args, **kwargs: scheduled.append(str(kwargs.get("source", "missing"))),
    )
    assert await executor.execute(object()) is None
    assert scheduled == ["decision"]
    rest.place_limit_order.assert_not_called()
    rest._request.assert_not_called()
    rest.cancel_order.assert_not_called()
    paper.record_trade.assert_not_called()
