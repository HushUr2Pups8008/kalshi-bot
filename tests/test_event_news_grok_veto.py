"""Politics Grok YES/NO veto: only YES may call place_limit_order."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

import config as _cfg_module
from trading.executor import TradeExecutor
from utils.event_news_grok_veto import (
    EventNewsGrokVetoPacket,
    event_news_grok_veto_allows_live_post,
    event_news_grok_veto_decision,
    event_news_grok_veto_packet,
)
from utils.event_news_research import EVENT_NEWS_COHORT_ID


def _analysis(*, ticker="KXTRUTHSOCIAL-26OCT17-B129", side="no", edge=0.24, p=0.12, ask=85):
    market = MagicMock()
    market.ticker = ticker
    market.yes_ask_cents = 100 - ask
    market.no_ask_cents = ask
    market.yes_ask = 100 - ask
    market.no_ask = ask
    market.price_available = True
    news = MagicMock()
    news.headline = "Truth Social posts"
    news.source = "official"
    news.url = "https://assets.kalshi.com/contract_terms/TRUTH.pdf"
    news.metric_name = "post_count"
    analysis = MagicMock()
    analysis.market = market
    analysis.news_item = news
    analysis.side = side
    analysis.edge = edge
    analysis.estimated_probability = p
    analysis.executed_price_cents = ask
    analysis.capped_dollars = 6.80
    analysis.confidence = 0.85
    analysis.kelly_fraction = 0.5
    analysis.kelly_dollars = 6.80
    analysis.llm_direction = None
    analysis.llm_magnitude = None
    analysis.signal_meta = {
        "blend_bypass_reason": "official_p",
        "source_lane": "research_official_p",
        "metric_name": "post_count",
        "evidence_urls": ["https://truthsocial.com/@realDonaldTrump"],
    }
    return analysis


def _packet() -> EventNewsGrokVetoPacket:
    return event_news_grok_veto_packet(_analysis(), contracts=8)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _chat_payload(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture
def politics_cohort(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "llm_excluded_price_bands", [(0.00, 0.35)])


def test_packet_fields_from_analysis(politics_cohort):
    packet = event_news_grok_veto_packet(_analysis(), contracts=8)
    assert packet.ticker == "KXTRUTHSOCIAL-26OCT17-B129"
    assert packet.side == "no"
    assert packet.yes_ask_cents == 15
    assert packet.no_ask_cents == 85
    assert packet.official_p == 0.12
    assert packet.metric_name == "post_count"
    assert "https://truthsocial.com/@realDonaldTrump" in packet.evidence_urls
    assert packet.fee_net_edge == 0.24
    assert packet.proposed_contracts == 8


def test_missing_key_is_timeout_and_does_not_post(politics_cohort, caplog):
    posts = []

    def _post(*args, **kwargs):
        posts.append(1)
        raise AssertionError("must not call xAI without a key")

    with caplog.at_level(logging.INFO):
        result = event_news_grok_veto_decision(
            _packet(),
            environ={},
            post=_post,
        )
    assert result == "timeout"
    assert posts == []
    assert any("EVENT_NEWS_GROK_VETO" in r.message and "result=timeout" in r.message for r in caplog.records)


def test_yes_parse(politics_cohort):
    def _post(*args, **kwargs):
        assert "Bearer secret-key" in kwargs["headers"]["Authorization"]
        return _FakeResponse(200, _chat_payload("YES"))

    result = event_news_grok_veto_decision(
        _packet(),
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert result == "yes"


def test_no_parse(politics_cohort, caplog):
    def _post(*args, **kwargs):
        return _FakeResponse(200, _chat_payload("NO"))

    with caplog.at_level(logging.INFO):
        result = event_news_grok_veto_decision(
            _packet(),
            environ={"XAI_API_KEY": "secret-key"},
            post=_post,
        )
    assert result == "no"
    assert any("result=no" in r.message for r in caplog.records)
    assert "secret-key" not in caplog.text


def test_timeout_exception(politics_cohort, caplog):
    def _post(*args, **kwargs):
        raise requests.Timeout("read timed out")

    with caplog.at_level(logging.INFO):
        result = event_news_grok_veto_decision(
            _packet(),
            environ={"XAI_API_KEY": "secret-key"},
            post=_post,
        )
    assert result == "timeout"
    assert any("result=timeout" in r.message for r in caplog.records)


def test_parse_fail_is_timeout(politics_cohort):
    def _post(*args, **kwargs):
        return _FakeResponse(200, _chat_payload("maybe later"))

    result = event_news_grok_veto_decision(
        _packet(),
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert result == "timeout"


def test_excluded_band_does_not_call_grok(politics_cohort):
    analysis = _analysis(side="no", ask=33, edge=0.24)
    analysis.executed_price_cents = 33
    analysis.market.no_ask_cents = 33
    analysis.market.no_ask = 33
    analysis.market.yes_ask_cents = 67
    called = []

    def _post(*args, **kwargs):
        called.append(1)
        return _FakeResponse(200, _chat_payload("YES"))

    allowed = event_news_grok_veto_allows_live_post(
        analysis,
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert allowed is False
    assert called == []


def test_non_official_p_does_not_call_grok(politics_cohort):
    analysis = _analysis()
    analysis.signal_meta = {"source_lane": "blend"}
    called = []

    def _post(*args, **kwargs):
        called.append(1)
        return _FakeResponse(200, _chat_payload("YES"))

    allowed = event_news_grok_veto_allows_live_post(
        analysis,
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert allowed is False
    assert called == []


def test_no_edge_does_not_call_grok(politics_cohort):
    analysis = _analysis(edge=-0.01)
    analysis.signal_meta["skip_reason"] = "no_edge"
    called = []

    def _post(*args, **kwargs):
        called.append(1)
        return _FakeResponse(200, _chat_payload("YES"))

    allowed = event_news_grok_veto_allows_live_post(
        analysis,
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert allowed is False
    assert called == []


def _live_executor(monkeypatch, tmp_path):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 500.0)
    monkeypatch.setattr(
        "trading.executor.LIVE_FILL_LOG_PATH",
        tmp_path / "live-fill-log.md",
    )
    rest = MagicMock()
    result = MagicMock()
    result.error = None
    result.order_id = "ord-1"
    result.status = "executed"
    result.filled = 8
    result.http_status = 201
    rest.place_limit_order.return_value = result
    paper = MagicMock()
    ex = TradeExecutor(
        rest,
        paper,
        live_submission_hold_path=tmp_path / "holds.json",
    )
    return ex, rest


@pytest.mark.asyncio
async def test_execute_live_no_does_not_place_limit_order(
    politics_cohort, monkeypatch, tmp_path, caplog
):
    ex, rest = _live_executor(monkeypatch, tmp_path)
    analysis = _analysis()

    def _decision(*args, **kwargs):
        return "no"

    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_decision",
            side_effect=_decision,
        ),
        patch("trading.executor.trade_log"),
        caplog.at_level(logging.INFO),
    ):
        order_id = await ex._execute_live(analysis)
    assert order_id is None
    rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_live_timeout_does_not_place_limit_order(
    politics_cohort, monkeypatch, tmp_path
):
    ex, rest = _live_executor(monkeypatch, tmp_path)

    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_decision",
            return_value="timeout",
        ),
        patch("trading.executor.trade_log"),
    ):
        order_id = await ex._execute_live(_analysis())
    assert order_id is None
    rest.place_limit_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_live_yes_is_only_path_that_places(
    politics_cohort, monkeypatch, tmp_path
):
    ex, rest = _live_executor(monkeypatch, tmp_path)

    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_decision",
            return_value="yes",
        ),
        patch("trading.executor.trade_log"),
    ):
        order_id = await ex._execute_live(_analysis())
    assert order_id == "ord-1"
    rest.place_limit_order.assert_called_once()
    kwargs = rest.place_limit_order.call_args.kwargs
    assert kwargs["ticker"] == "KXTRUTHSOCIAL-26OCT17-B129"
    assert kwargs["count"] == 8
    assert kwargs["client_order_id"]
