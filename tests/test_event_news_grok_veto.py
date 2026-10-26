"""Politics Grok YES/NO veto: only YES may call place_limit_order."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

import config as _cfg_module
from trading.executor import TradeExecutor
from utils.event_news_grok_veto import (
    GROK_MAX_TOKENS,
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
    EventNewsGrokVetoPacket,
    event_news_grok_veto_allows_live_post,
    event_news_grok_veto_decision,
    event_news_grok_veto_packet,
    event_news_paper_grounding_skip,
)
from utils import event_news_grounding_log as grounding_log
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
    assert any("elapsed_ms=" in r.message for r in caplog.records)


def test_yes_parse(politics_cohort):
    captured = {}

    def _post(*args, **kwargs):
        captured["json"] = kwargs["json"]
        assert "Bearer secret-key" in kwargs["headers"]["Authorization"]
        assert kwargs["timeout"] == GROK_TIMEOUT_SECONDS
        return _FakeResponse(200, _chat_payload("YES"))

    result = event_news_grok_veto_decision(
        _packet(),
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert result == "yes"
    assert captured["json"]["model"] == GROK_MODEL
    assert captured["json"]["max_tokens"] == GROK_MAX_TOKENS
    assert "search_parameters" not in captured["json"]
    assert "tools" not in captured["json"]
    assert GROK_MODEL == "grok-4.20-0309-non-reasoning"
    assert GROK_TIMEOUT_SECONDS == 45.0
    assert GROK_MAX_TOKENS == 256


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
    assert any("result=no" in r.message and "elapsed_ms=" in r.message for r in caplog.records)
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
    assert any("result=timeout" in r.message and "elapsed_ms=" in r.message for r in caplog.records)


def test_parse_fail_is_timeout(politics_cohort):
    def _post(*args, **kwargs):
        return _FakeResponse(200, _chat_payload("maybe later"))

    result = event_news_grok_veto_decision(
        _packet(),
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    )
    assert result == "timeout"


def test_parse_final_yes_no_token(politics_cohort):
    def _yes(*args, **kwargs):
        return _FakeResponse(200, _chat_payload("reasoning dump\nYES"))

    def _no(*args, **kwargs):
        return _FakeResponse(200, _chat_payload("YES then the packet fails NO"))

    assert (
        event_news_grok_veto_decision(
            _packet(),
            environ={"XAI_API_KEY": "secret-key"},
            post=_yes,
        )
        == "yes"
    )
    assert (
        event_news_grok_veto_decision(
            _packet(),
            environ={"XAI_API_KEY": "secret-key"},
            post=_no,
        )
        == "no"
    )


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


@pytest.mark.asyncio
async def test_execute_live_slow_yes_still_places(
    politics_cohort, monkeypatch, tmp_path
):
    ex, rest = _live_executor(monkeypatch, tmp_path)

    def _slow_yes(*args, **kwargs):
        time.sleep(0.25)
        return "yes"

    with (
        patch(
            "utils.event_news_grok_veto.event_news_grok_veto_decision",
            side_effect=_slow_yes,
        ),
        patch("trading.executor.trade_log"),
    ):
        started = time.monotonic()
        order_id = await ex._execute_live(_analysis())
        elapsed = time.monotonic() - started
    assert elapsed >= 0.25
    assert order_id == "ord-1"
    rest.place_limit_order.assert_called_once()


def test_paper_grounding_skip_does_not_call_chat(politics_cohort):
    """The chat search path is gone, so paper admission does not post or skip."""

    def _post(*_args, **_kwargs):
        raise AssertionError("chat grounding must not post")

    assert event_news_paper_grounding_skip(
        _analysis(),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key", "EVENT_NEWS_GROK_GROUNDING": "true"},
        post=_post,
    ) is False


def test_paper_grounding_timeout_and_missing_key_do_not_skip(
    politics_cohort, tmp_path, monkeypatch
):
    monkeypatch.setattr(grounding_log, "GROUNDING_LOG_PATH", tmp_path / "grounding.jsonl")
    def _post(*_args, **_kwargs):
        raise requests.Timeout("slow")

    assert event_news_paper_grounding_skip(
        _analysis(),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    ) is False

    def _unused(*_args, **_kwargs):
        raise AssertionError("missing key must not call xAI")

    assert event_news_paper_grounding_skip(
        _analysis(),
        contracts=8,
        environ={},
        post=_unused,
    ) is False


def _usage_body(text: str, ticks: int) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 4,
            "total_tokens": 104,
            "cost_in_usd_ticks": ticks,
            "server_side_tool_usage_details": {"web_search_calls": 1},
        },
    }


def test_veto_logs_chat_usage_and_one_decision_per_market_day(tmp_path, monkeypatch):
    """A second same-day call is logged again. The first allow/block row stays."""
    call_path = tmp_path / "calls.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)
    posted: list[dict] = []
    replies = ("YES", "NO", "YES")

    def _post(*_args, **kwargs):
        posted.append(kwargs["json"])
        text = replies[len(posted) - 1]
        ticks = 1250000000 if text == "YES" else 2000000000
        return _FakeResponse(200, _usage_body(text, ticks))

    analysis = _analysis()
    analysis.market.no_ask_size_fp = "4.00"
    analysis.market.yes_ask_size_fp = 99
    moment = datetime(2026, 10, 25, 15, tzinfo=timezone.utc)
    env = {"XAI_API_KEY": "secret-key"}
    assert event_news_grok_veto_allows_live_post(
        analysis, contracts=8, environ=env, post=_post, now=moment,
    ) is True
    assert event_news_grok_veto_allows_live_post(
        analysis, contracts=8, environ=env, post=_post, now=moment,
    ) is False
    bare = _analysis(ticker="KXOTHER-NODEPTH")
    assert event_news_grok_veto_allows_live_post(
        bare, contracts=8, environ=env, post=_post, now=moment,
    ) is True
    rows = [json.loads(line) for line in call_path.read_text(encoding="utf-8").splitlines()]
    assert [row["verdict"] for row in rows] == ["yes", "no", "yes"]
    assert rows[0]["kind"] == "veto"
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 4
    assert rows[0]["total_tokens"] == 104
    assert rows[0]["cost_in_usd_ticks"] == 1250000000
    assert rows[0]["cost_usd"] == "0.125"
    assert rows[0]["cost_source"] == "provider"
    assert rows[0]["web_search_calls"] == 1
    assert rows[0]["prompt_hash"] == ""
    assert rows[0]["rationale"] == ""
    assert rows[1]["cost_usd"] == "0.2"
    logged = call_path.read_text(encoding="utf-8")
    assert "Answer only YES" not in logged
    assert "secret-key" not in logged
    user = posted[0]["messages"][1]["content"]
    assert "depth" not in user
    assert "ask_size" not in user
    assert "search_parameters" not in posted[0]
    saved = [json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines()]
    assert [(row["ticker"], row["decision"]) for row in saved] == [
        ("KXTRUTHSOCIAL-26OCT17-B129", "allow"),
        ("KXOTHER-NODEPTH", "allow"),
    ]
    assert saved[0]["ask_cents"] == 85
    assert saved[0]["depth"] == "4.00"
    assert saved[0]["fee_net_edge"] == 0.24
    assert saved[0]["contracts"] == 8
    assert saved[0]["date"] == "2026-10-25"
    assert saved[0]["side"] == "no"
    assert saved[1]["depth"] is None


def test_timeout_is_unavailable_and_model_no_stays_a_block(tmp_path, monkeypatch):
    """A transport failure is not a model block. A parsed NO still is."""
    call_path = tmp_path / "calls.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)
    moment = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)

    def _timeout(*_args, **kwargs):
        assert "search_parameters" not in kwargs["json"]
        raise requests.Timeout("slow")

    assert event_news_grok_veto_allows_live_post(
        _analysis(),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key", "EVENT_NEWS_GROK_GROUNDING": "true"},
        post=_timeout,
        now=moment,
    ) is False

    def _no(*_args, **kwargs):
        assert "search_parameters" not in kwargs["json"]
        return _FakeResponse(200, _usage_body("NO", 1000000000))

    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KXTRUMPACT-26OCT25-T6"),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_no,
        now=moment,
    ) is False
    saved = [json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines()]
    assert [(row["ticker"], row["decision"]) for row in saved] == [
        ("KXTRUTHSOCIAL-26OCT17-B129", "unavailable"),
        ("KXTRUMPACT-26OCT25-T6", "block"),
    ]


def test_missing_key_and_early_gate_write_no_veto_rows(tmp_path, monkeypatch):
    call_path = tmp_path / "calls.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)

    def _post(*_args, **_kwargs):
        raise AssertionError("this gate must not post")

    assert event_news_grok_veto_allows_live_post(
        _analysis(),
        contracts=8,
        environ={},
        post=_post,
        now=datetime(2026, 10, 25, tzinfo=timezone.utc),
    ) is False
    gated = _analysis(edge=-0.01)
    gated.signal_meta["skip_reason"] = "no_edge"
    assert event_news_grok_veto_allows_live_post(
        gated,
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    ) is False
    assert not call_path.exists()
    assert not decision_path.exists()


def test_timeout_post_logs_unavailable_and_a_log_failure_stays_yes(tmp_path, monkeypatch):
    call_path = tmp_path / "calls.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)

    def _post(*_args, **_kwargs):
        raise requests.Timeout("slow")

    assert event_news_grok_veto_allows_live_post(
        _analysis(),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
        now=datetime(2026, 10, 25, tzinfo=timezone.utc),
    ) is False
    row = json.loads(call_path.read_text(encoding="utf-8"))
    assert row["cost_usd"] == "0"
    assert row["cost_source"] == "none"
    assert row["skip_reason"] == "timeout"
    assert row["verdict"] == "timeout"
    saved = json.loads(decision_path.read_text(encoding="utf-8"))
    assert saved["decision"] == "unavailable"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", tmp_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", tmp_path)

    def _yes(*_args, **_kwargs):
        return _FakeResponse(200, _chat_payload("YES"))

    assert event_news_grok_veto_decision(
        _packet(),
        environ={"XAI_API_KEY": "secret-key"},
        post=_yes,
    ) == "yes"
    assert event_news_grok_veto_allows_live_post(
        _analysis(),
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_yes,
    ) is True
