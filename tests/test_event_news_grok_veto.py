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


def _refusal_row(tmp_path, monkeypatch, analysis) -> dict:
    call_path = tmp_path / "calls.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
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
    return json.loads(call_path.read_text(encoding="utf-8"))


def test_excluded_band_logs_side_skip_and_does_not_call_grok(politics_cohort, tmp_path, monkeypatch):
    analysis = _analysis(side="no", ask=33, edge=0.24)
    analysis.executed_price_cents = 33
    analysis.market.no_ask_cents = 33
    analysis.market.no_ask = 33
    analysis.market.yes_ask_cents = 67
    row = _refusal_row(tmp_path, monkeypatch, analysis)
    assert row["kind"] == "veto"
    assert row["skip_reason"] == "side_skip"
    assert row["cost_usd"] == "0"
    assert row["cost_source"] == "none"
    assert row["verdict"] == ""
    assert row["grounded"] is False


def test_non_official_p_logs_and_does_not_call_grok(politics_cohort, tmp_path, monkeypatch):
    analysis = _analysis()
    analysis.signal_meta = {"source_lane": "blend"}
    row = _refusal_row(tmp_path, monkeypatch, analysis)
    assert row["skip_reason"] == "no_official_p"
    assert row["kind"] == "veto"
    assert row["cost_source"] == "none"


def test_no_edge_logs_and_does_not_call_grok(politics_cohort, tmp_path, monkeypatch):
    analysis = _analysis(edge=-0.01)
    analysis.signal_meta["skip_reason"] = "no_edge"
    row = _refusal_row(tmp_path, monkeypatch, analysis)
    assert row["skip_reason"] == "no_edge"
    assert row["ticker"] == "KXTRUTHSOCIAL-26OCT17-B129"


def test_nonpositive_edge_logs_and_does_not_call_grok(politics_cohort, tmp_path, monkeypatch):
    analysis = _analysis(edge=0)
    row = _refusal_row(tmp_path, monkeypatch, analysis)
    assert row["skip_reason"] == "nonpositive_edge"
    assert row["cost_usd"] == "0"


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


def test_veto_logs_chat_usage_and_every_same_day_attempt(tmp_path, monkeypatch):
    """A later same-day verdict is kept. The first row is no longer the only row."""
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
    assert [(row["ticker"], row["decision"], row["verdict"]) for row in saved] == [
        ("KXTRUTHSOCIAL-26OCT17-B129", "allow", "yes"),
        ("KXTRUTHSOCIAL-26OCT17-B129", "block", "no"),
        ("KXOTHER-NODEPTH", "allow", "yes"),
    ]
    assert saved[0]["ask_cents"] == 85
    assert saved[0]["depth"] == "4.00"
    assert saved[0]["total_tokens"] == 104
    assert saved[0]["cost_usd"] == "0.125"
    assert saved[1]["cost_usd"] == "0.2"
    assert "fee_net_edge" not in saved[0]
    assert "contracts" not in saved[0]
    assert saved[0]["date"] == "2026-10-25"
    assert saved[0]["side"] == "no"
    assert saved[0]["ts"] == "2026-10-25T15:00:00Z"
    assert saved[2]["depth"] is None
    assert len({row["attempt_id"] for row in saved}) == 3
    assert all(len(row["attempt_id"]) == 32 for row in saved)


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
    assert saved[0]["cost_usd"] == "0"
    assert saved[0]["total_tokens"] is None
    assert saved[1]["total_tokens"] == 104
    assert saved[1]["cost_usd"] == "0.1"


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
    rows = [json.loads(line) for line in call_path.read_text(encoding="utf-8").splitlines()]
    assert [row["skip_reason"] for row in rows] == ["no_edge"]
    assert rows[0]["kind"] == "veto"
    assert rows[0]["cost_source"] == "none"
    assert rows[0]["verdict"] == ""
    assert not decision_path.exists()
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", tmp_path)
    assert event_news_grok_veto_allows_live_post(
        gated,
        contracts=8,
        environ={"XAI_API_KEY": "secret-key"},
        post=_post,
    ) is False


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


def test_weekly_cap_fails_closed_and_ignores_veto_rows(tmp_path):
    from decimal import Decimal

    from utils.event_news_grok_veto import non_veto_weekly_block, weekly_xai_spend

    path = tmp_path / "calls.jsonl"
    moment = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    assert non_veto_weekly_block(environ={}, call_log_path=path, now=moment) == "weekly_cap_unset"
    assert (
        non_veto_weekly_block(
            environ={"GROK_WEEKLY_USD_CAP": "nope"},
            call_log_path=path,
            now=moment,
        )
        == "weekly_cap_unset"
    )
    assert (
        non_veto_weekly_block(
            environ={"GROK_WEEKLY_USD_CAP": "1"},
            call_log_path=path,
            now=moment,
        )
        is None
    )
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-11-03T00:00:00Z",
                        "kind": "veto",
                        "cost_usd": "0.80",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-11-04T00:00:00Z",
                        "kind": "source",
                        "cost_usd": "0.20",
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-10-01T00:00:00Z",
                        "kind": "scan",
                        "cost_usd": "9",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    non_veto, veto = weekly_xai_spend(path, moment)
    assert non_veto == Decimal("0.20")
    assert veto == Decimal("0.80")
    assert (
        non_veto_weekly_block(
            environ={"GROK_WEEKLY_USD_CAP": "0.20"},
            call_log_path=path,
            now=moment,
        )
        == "weekly_cap"
    )
    assert (
        non_veto_weekly_block(
            environ={"GROK_WEEKLY_USD_CAP": "0.21"},
            call_log_path=path,
            now=moment,
        )
        is None
    )
    path.write_text("{not json}\n", encoding="utf-8")
    assert (
        non_veto_weekly_block(
            environ={"GROK_WEEKLY_USD_CAP": "1"},
            call_log_path=path,
            now=moment,
        )
        == "weekly_cap_unreadable"
    )


def test_production_edge_call_does_not_post_or_log(tmp_path, monkeypatch):
    """Production research has no poster. Unset cap and a set cap both stay off HTTP."""
    from decimal import Decimal

    from utils.event_news_grok_edge import _call_grok

    def _post(*_args, **_kwargs):
        raise AssertionError("responses client must not run")

    monkeypatch.setattr("utils.event_news_grok_edge.requests.post", _post)
    calls = tmp_path / "calls.jsonl"
    moment = datetime(2026, 11, 5, tzinfo=timezone.utc)
    for kind in ("scan", "series", "source", "watch", "exit", "position_score"):
        blocked = _call_grok(
            ticker="KXTEST",
            prompt="p",
            system="s",
            grounded=True,
            environ={},
            post=None,
            call_log_path=calls,
            spend_path=tmp_path / "spend.json",
            now=moment,
            kind=kind,
        )
        assert blocked is not None
        assert blocked.skip_reason == "weekly_cap_unset"
        assert blocked.cost_usd == Decimal("0")
        assert not calls.exists()
    allowed_cap = _call_grok(
        ticker="KXTEST",
        prompt="p",
        system="s",
        grounded=True,
        environ={"GROK_WEEKLY_USD_CAP": "1"},
        post=None,
        call_log_path=calls,
        spend_path=tmp_path / "spend.json",
        now=moment,
    )
    assert allowed_cap is not None
    assert allowed_cap.skip_reason == "xai_research_disabled"
    assert not calls.exists()


def test_only_explicit_yes_allows_the_live_post(tmp_path, monkeypatch):
    """NO, a malformed answer, and a timeout stay False. Only YES is True."""
    call_path = tmp_path / "calls.jsonl"
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)
    posted: list[str] = []

    def _post(*_args, **_kwargs):
        text = ("NO", "maybe later", "YES")[len(posted)]
        posted.append(text)
        return _FakeResponse(200, _chat_payload(text))

    env = {"XAI_API_KEY": "secret-key"}
    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-NO"), contracts=8, environ=env, post=_post,
    ) is False
    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-MALFORMED"), contracts=8, environ=env, post=_post,
    ) is False
    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-YES"), contracts=8, environ=env, post=_post,
    ) is True

    def _timeout(*_args, **_kwargs):
        raise requests.Timeout("slow")

    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-TIMEOUT"), contracts=8, environ=env, post=_timeout,
    ) is False


def test_reversals_repeated_decisions_and_utc_days_keep_every_attempt(tmp_path, monkeypatch):
    """Block then allow, allow then block, a repeat, and a next-UTC-day allow all stay."""
    from utils.event_news_grok_veto import (
        select_attempts_before_fills,
        select_veto_attempts,
        veto_same_day_reversals,
    )

    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", tmp_path / "calls.jsonl")
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)
    script = (
        ("KX-BLOCK-ALLOW", "yes", "NO", datetime(2026, 11, 2, 23, 13, tzinfo=timezone.utc), False),
        ("KX-BLOCK-ALLOW", "yes", "YES", datetime(2026, 11, 2, 23, 18, tzinfo=timezone.utc), True),
        ("KX-ALLOW-BLOCK", "no", "YES", datetime(2026, 11, 2, 10, tzinfo=timezone.utc), True),
        ("KX-ALLOW-BLOCK", "no", "NO", datetime(2026, 11, 2, 11, tzinfo=timezone.utc), False),
        ("KX-REPEAT", "yes", "NO", datetime(2026, 11, 2, 12, tzinfo=timezone.utc), False),
        ("KX-REPEAT", "yes", "NO", datetime(2026, 11, 2, 12, 5, tzinfo=timezone.utc), False),
        ("KX-NEXT-DAY", "no", "NO", datetime(2026, 11, 2, 23, 50, tzinfo=timezone.utc), False),
        ("KX-NEXT-DAY", "no", "YES", datetime(2026, 11, 3, 0, 10, tzinfo=timezone.utc), True),
    )
    cursor = {"n": 0}

    def _post(*_args, **_kwargs):
        text = script[cursor["n"]][2]
        cursor["n"] += 1
        return _FakeResponse(200, _chat_payload(text))

    for ticker, side, _text, moment, expected in script:
        assert event_news_grok_veto_allows_live_post(
            _analysis(ticker=ticker, side=side, ask=40 if side == "yes" else 85),
            contracts=8,
            environ={"XAI_API_KEY": "secret-key"},
            post=_post,
            now=moment,
        ) is expected
    saved = [json.loads(line) for line in decision_path.read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 8
    assert len({row["attempt_id"] for row in saved}) == 8
    assert [(row["decision"], row["verdict"], row["cost_usd"]) for row in saved] == [
        ("block", "no", "0"),
        ("allow", "yes", "0"),
        ("allow", "yes", "0"),
        ("block", "no", "0"),
        ("block", "no", "0"),
        ("block", "no", "0"),
        ("block", "no", "0"),
        ("allow", "yes", "0"),
    ]
    first = select_veto_attempts(saved, "first")
    last = select_veto_attempts(saved, "last")
    assert _decision_for(first, "KX-BLOCK-ALLOW", "yes") == "block"
    assert _decision_for(last, "KX-BLOCK-ALLOW", "yes") == "allow"
    assert _decision_for(first, "KX-ALLOW-BLOCK", "no") == "allow"
    assert _decision_for(last, "KX-ALLOW-BLOCK", "no") == "block"
    assert [row["decision"] for row in select_veto_attempts(saved, "all") if row["ticker"] == "KX-REPEAT"] == [
        "block",
        "block",
    ]
    next_day = [row for row in select_veto_attempts(saved, "all") if row["ticker"] == "KX-NEXT-DAY"]
    assert [(row["date"], row["decision"]) for row in next_day] == [
        ("2026-11-02", "block"),
        ("2026-11-03", "allow"),
    ]
    assert [(row["ticker"], row["from_decision"], row["to_decision"]) for row in veto_same_day_reversals(saved)] == [
        ("KX-ALLOW-BLOCK", "allow", "block"),
        ("KX-BLOCK-ALLOW", "block", "allow"),
    ]
    before_fill = select_attempts_before_fills(
        saved,
        [{"ticker": "KX-BLOCK-ALLOW", "side": "yes", "ts": "2026-11-02T23:18:00Z"}],
    )
    assert [(row["decision"], row["verdict"]) for row in before_fill] == [("allow", "yes")]
    earlier_fill = select_attempts_before_fills(
        saved,
        [{"ticker": "KX-BLOCK-ALLOW", "side": "yes", "ts": "2026-11-02T23:13:00Z"}],
    )
    assert [row["decision"] for row in earlier_fill] == ["block"]
    with pytest.raises(ValueError):
        select_veto_attempts(saved, "middle")


def _decision_for(rows, ticker: str, side: str) -> str:
    matched = [row["decision"] for row in rows if row["ticker"] == ticker and row["side"] == side]
    assert len(matched) == 1
    return matched[0]


def test_fee_net_needs_timestamp_ask_and_eight_contract_depth() -> None:
    """Depth of 4 cannot fill 8 contracts. Fifteen evidenced rows may show the if-filled number."""
    from decimal import Decimal

    from utils.event_news_desk_flags import HARD_MAX_CONTRACTS
    from utils.event_news_grok_veto import (
        grade_veto_decisions,
        veto_blocked_settlement_rows,
        veto_counterfactual_fee_net,
    )
    from utils.event_news_research import event_news_fee_net_edge

    thin = {
        "ts": "2026-11-02T23:13:12Z",
        "date": "2026-11-02",
        "ticker": "KXTRUTHSOCIAL-26NOV07-T360",
        "side": "no",
        "ask_cents": 67,
        "depth": "4.00",
        "decision": "block",
        "verdict": "no",
        "cost_usd": "0.0001",
        "attempt_id": "a" * 32,
    }
    # WHY: a blocked NO that settles NO would have won. The book still cannot fill 8.
    assert veto_counterfactual_fee_net(thin, "no") is None
    agreement = veto_blocked_settlement_rows([thin], {"KXTRUTHSOCIAL-26NOV07-T360": "no"})
    assert agreement[0]["agreement"] == "blocked side would have won"
    assert agreement[0]["fee_net_if_filled"] == "not estimable"
    lost = veto_blocked_settlement_rows(
        [{**thin, "side": "yes", "decision": "block"}],
        {"KXTRUTHSOCIAL-26NOV07-T360": "no"},
    )
    assert lost[0]["agreement"] == "blocked side would have lost"
    text = grade_veto_decisions([thin], {"KXTRUTHSOCIAL-26NOV07-T360": "no"}, selector="first")
    assert "review=first" in text
    assert "| blocked | 1 | not estimable |" in text
    assert "That is not fee-net EV." in text
    missing_time = {**thin, "depth": HARD_MAX_CONTRACTS}
    del missing_time["ts"]
    assert veto_counterfactual_fee_net(missing_time, "no") is None
    full = [
        {
            "ts": f"2026-10-{day:02d}T00:00:00Z",
            "date": f"2026-10-{day:02d}",
            "ticker": "KXTEST-A",
            "side": "yes",
            "ask_cents": 67,
            "depth": HARD_MAX_CONTRACTS,
            "decision": "allow",
        }
        for day in range(1, 16)
    ]
    scored = event_news_fee_net_edge(Decimal("0.5"), 67, HARD_MAX_CONTRACTS)
    assert scored is not None
    price = Decimal(67) / Decimal(100) * HARD_MAX_CONTRACTS
    one = Decimal(HARD_MAX_CONTRACTS) - price - scored[1]
    priced = grade_veto_decisions(full, {"KXTEST-A": "yes"}, selector="all")
    assert f"| allowed | 15 | {format(one * 15, 'f')} |" in priced
    assert "review=all" in priced
    before = grade_veto_decisions(
        full,
        {"KXTEST-A": "yes"},
        fills=[{"ticker": "KXTEST-A", "side": "yes", "ts": "2026-10-01T00:00:00Z"}],
    )
    assert "review=before_fill" in before
    assert "| allowed | 1 | not enough data (n=1) |" in before


def test_retired_rules_and_watch_flags_do_not_change_the_live_veto(tmp_path, monkeypatch):
    """The checker and the watch loop are gone. The names stay for the boot line and plist."""
    import importlib
    from pathlib import Path

    from utils.event_news_desk_flags import (
        EVENT_NEWS_GROK_RULES_CHECK,
        _DESK_FLAG_NAMES,
        event_news_any_live_desk_flag,
    )
    from utils.event_news_grok_flags import (
        EVENT_NEWS_GROK_WATCH_SKIP_SETTLED,
        event_news_flags_boot_line,
    )

    for name in ("utils.event_news_grok_watch", "utils.event_news_rules_check"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
    plist = Path("deploy/com.jake.kalshi-bot-politics.plist").read_text(encoding="utf-8")
    assert "EVENT_NEWS_GROK_WATCH_SKIP_SETTLED" in plist
    executor = Path("trading/executor.py").read_text(encoding="utf-8")
    assert "_rules_hard_timeout_s" not in executor
    assert "event_news_rules_check" not in executor
    assert "RULES_CHECK " in event_news_flags_boot_line({})
    assert "GROK_WATCH_SKIP_SETTLED=" in event_news_flags_boot_line({})
    assert EVENT_NEWS_GROK_RULES_CHECK not in _DESK_FLAG_NAMES
    assert event_news_any_live_desk_flag({EVENT_NEWS_GROK_RULES_CHECK: "true"}) is False
    decision_path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", tmp_path / "calls.jsonl")
    monkeypatch.setattr("utils.event_news_grok_veto.VETO_DECISION_LOG_PATH", decision_path)
    calls = {"n": 0}

    def _post(*_args, **_kwargs):
        calls["n"] += 1
        return _FakeResponse(200, _chat_payload("NO" if calls["n"] == 1 else "YES"))

    flagged = {
        "XAI_API_KEY": "secret-key",
        EVENT_NEWS_GROK_RULES_CHECK: "true",
        EVENT_NEWS_GROK_WATCH_SKIP_SETTLED: "true",
    }
    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-FLAG-NO"), contracts=8, environ=flagged, post=_post,
    ) is False
    assert event_news_grok_veto_allows_live_post(
        _analysis(ticker="KX-FLAG-YES"), contracts=8, environ=flagged, post=_post,
    ) is True
    assert calls["n"] == 2
