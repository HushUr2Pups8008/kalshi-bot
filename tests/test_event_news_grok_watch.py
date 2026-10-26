"""Open-position Grok watch logs CONTRADICT and does not place an order."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import requests

import config as _cfg_module
from utils.event_news_grok_veto import XAI_CHAT_URL
from utils.event_news_grok_watch import (
    event_news_grok_watch_decision,
    event_news_watch_series_record,
    run_event_news_grok_watch_once,
)

_SECRET = "xai-watch-key-do-not-log"
_NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)
_ORDERS: list[dict] = []


def _position(**overrides):
    base = {
        "ticker": "KXTRUTHSOCIAL-26OCT17-B129",
        "position_fp": "-8.00",
        "market_exposure_dollars": "6.80",
    }
    base.update(overrides)
    return base


def _market():
    return SimpleNamespace(
        contract_terms_url="https://kalshi.com/contracts/truth-social-weekly",
        settlement_sources=(
            SimpleNamespace(url="https://rollcall.com/factbase/trump/topic/social/"),
        ),
    )


class _Response:
    def __init__(self, text: str):
        self.status_code = 200
        self._text = text

    def json(self):
        return {"choices": [{"message": {"content": self._text}}]}


def test_contradict_does_not_call_place_limit_order(politics_cohort, caplog, tmp_path):
    _ORDERS.clear()
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if "orders" in url:
            _ORDERS.append({"url": url})
        return _Response("thinking\nCONTRADICT")

    with caplog.at_level(logging.INFO):
        results = run_event_news_grok_watch_once(
            [_position()],
            market_lookup=lambda _ticker: _market(),
            state_path=tmp_path / "event_news_grok_watch.json",
            now=_NOW,
            environ={"XAI_API_KEY": _SECRET},
            post=_post,
        )
    assert _ORDERS == []
    assert len(calls) == 1
    assert calls[0]["url"] == XAI_CHAT_URL
    assert "/portfolio/events/orders" not in calls[0]["url"]
    assert results[0].result == "contradict"
    packet = calls[0]["json"]["messages"][1]["content"]
    assert "KXTRUTHSOCIAL-26OCT17-B129" in packet
    assert '"side":"no"' in packet
    assert "0.85" in packet
    assert "https://rollcall.com/factbase/trump/topic/social/" in packet
    assert any(
        "EVENT_NEWS_GROK_WATCH" in record.message
        and "result=contradict" in record.message
        and "reply=thinking CONTRADICT" in record.message
        for record in caplog.records
    )
    saved = (tmp_path / "event_news_grok_watch.json").read_text(encoding="utf-8")
    assert "thinking\\nCONTRADICT" in saved
    assert _SECRET not in " ".join(record.message for record in caplog.records)
    source = Path("utils/event_news_grok_watch.py").read_text(encoding="utf-8")
    assert "place_limit_order" not in source
    assert "trading.executor" not in source


def test_same_utc_day_does_not_call_again(politics_cohort, tmp_path):
    calls = []

    def _post(*_args, **_kwargs):
        calls.append(1)
        return _Response("OK")

    path = tmp_path / "event_news_grok_watch.json"
    kwargs = dict(
        market_lookup=lambda _ticker: _market(),
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    first = run_event_news_grok_watch_once([_position()], **kwargs)
    second = run_event_news_grok_watch_once([_position()], **kwargs)
    assert first[0].result == "ok"
    assert second == []
    assert calls == [1]


def test_longshot_fill_zero_position_and_lookup_failure_do_not_post(politics_cohort, tmp_path):
    calls = []

    def _post(*_args, **_kwargs):
        calls.append(1)
        return _Response("CONTRADICT")

    path = tmp_path / "watch.json"
    run_event_news_grok_watch_once(
        [_position(market_exposure_dollars="2.80")],
        market_lookup=lambda _ticker: _market(),
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    run_event_news_grok_watch_once(
        [_position(position_fp="0.00")],
        market_lookup=lambda _ticker: _market(),
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )

    def _missing(_ticker):
        raise RuntimeError("lookup down")

    run_event_news_grok_watch_once(
        [_position(ticker="KXTRUMPACT-26OCT11-T5")],
        market_lookup=_missing,
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert calls == []
    assert not path.exists()


def test_missing_key_does_not_mark_or_post(politics_cohort, tmp_path, caplog):
    def _post(*_args, **_kwargs):
        raise AssertionError("missing key must not post")

    path = tmp_path / "watch.json"
    with caplog.at_level(logging.INFO):
        results = run_event_news_grok_watch_once(
            [_position()],
            market_lookup=lambda _ticker: _market(),
            state_path=path,
            now=_NOW,
            environ={},
            post=_post,
        )
    assert results[0].result == "timeout"
    assert results[0].reason == "missing_key"
    assert not path.exists()
    assert _SECRET not in " ".join(record.message for record in caplog.records)


def test_timeout_marks_the_utc_day(politics_cohort, tmp_path):
    def _post(*_args, **_kwargs):
        raise requests.Timeout("slow")

    path = tmp_path / "watch.json"
    results = run_event_news_grok_watch_once(
        [_position()],
        market_lookup=lambda _ticker: _market(),
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert results[0].result == "timeout"
    assert results[0].reason == "timeout"
    saved = path.read_text(encoding="utf-8")
    assert "KXTRUTHSOCIAL-26OCT17-B129" in saved
    assert "2026-10-11" in saved


def test_freeze_cohort_does_not_call(monkeypatch, tmp_path):
    monkeypatch.setattr(_cfg_module.cfg, "paper_cohort_id", "kalshi-macro-20260820")
    calls = []

    def _post(*_args, **_kwargs):
        calls.append(1)
        return _Response("CONTRADICT")

    results = run_event_news_grok_watch_once(
        [_position()],
        market_lookup=lambda _ticker: _market(),
        state_path=tmp_path / "watch.json",
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    direct = event_news_grok_watch_decision(
        {"ticker": "KXTRUTHSOCIAL-26OCT17-B129", "side": "no", "fill_price": 0.85},
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert results[0].result == "contradict"
    assert calls == [1, 1]
    assert direct.result == "contradict"


def _empty_market():
    return SimpleNamespace(
        series_ticker="",
        event_ticker="KXTRUTHSOCIAL-26OCT17",
        settlement_sources=(),
        contract_terms_url="",
    )


def test_empty_url_list_does_not_call_grok(politics_cohort, tmp_path, caplog):
    def _post(*_args, **_kwargs):
        raise AssertionError("empty sources must not call Grok")

    def _series(_market):
        return SimpleNamespace(settlement_sources=(), contract_terms_url="")

    path = tmp_path / "watch.json"
    with caplog.at_level(logging.INFO):
        results = run_event_news_grok_watch_once(
            [_position()],
            market_lookup=lambda _ticker: _empty_market(),
            series_lookup=_series,
            state_path=path,
            now=_NOW,
            environ={"XAI_API_KEY": _SECRET},
            post=_post,
        )
    assert results[0].result == "skipped"
    assert results[0].reason == "no_sources"
    assert not path.exists()
    assert any(
        "result=skipped" in record.message and "reason=no_sources" in record.message
        for record in caplog.records
    )


def test_watch_search_stays_off_until_grounding_is_explicit():
    captured: dict[str, dict] = {}

    def _post(*_args, **kwargs):
        captured["json"] = kwargs["json"]
        return _Response("OK")

    result = event_news_grok_watch_decision(
        {"ticker": "KXTRUTHSOCIAL-26OCT17-B129", "side": "no", "fill_price": 0.85},
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert result.result == "ok"
    assert "search_parameters" not in captured["json"]
    assert "tools" not in captured["json"]

    def _flag_on(*_args, **kwargs):
        captured["flag_on"] = kwargs["json"]
        return _Response("OK")

    flagged = event_news_grok_watch_decision(
        {"ticker": "KXTRUTHSOCIAL-26OCT17-B129", "side": "no", "fill_price": 0.85},
        environ={"XAI_API_KEY": _SECRET, "EVENT_NEWS_GROK_GROUNDING": "true"},
        post=_flag_on,
        grounded=True,
    )
    assert flagged.result == "ok"
    assert "search_parameters" not in captured["flag_on"]
    assert "tools" not in captured["flag_on"]
    assert captured["flag_on"]["messages"][0]["content"].startswith(
        "Answer only CONTRADICT or OK."
    )


def test_series_url_included_when_market_field_empty(politics_cohort, tmp_path):
    calls = []
    looked_up = []

    def _event_series(event_ticker):
        looked_up.append(("event", event_ticker))
        return "KXTRUTHSOCIAL"

    def _series_metadata(series_ticker):
        looked_up.append(("series", series_ticker))
        return SimpleNamespace(
            settlement_sources=(
                SimpleNamespace(url="https://rollcall.com/factbase/trump/topic/social/"),
            ),
            contract_terms_url="https://assets.kalshi.com/contract_terms/POLITICALSTAT.pdf",
        )

    def _series(market):
        return event_news_watch_series_record(
            market,
            event_series_ticker=_event_series,
            series_metadata=_series_metadata,
        )

    def _post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return _Response("OK")

    results = run_event_news_grok_watch_once(
        [_position()],
        market_lookup=lambda _ticker: _empty_market(),
        series_lookup=_series,
        state_path=tmp_path / "watch.json",
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert results[0].result == "ok"
    assert looked_up == [
        ("event", "KXTRUTHSOCIAL-26OCT17"),
        ("series", "KXTRUTHSOCIAL"),
    ]
    packet = calls[0]["messages"][1]["content"]
    assert "https://rollcall.com/factbase/trump/topic/social/" in packet
    assert "https://assets.kalshi.com/contract_terms/POLITICALSTAT.pdf" in packet
    assert "place_limit_order" not in Path("utils/event_news_grok_watch.py").read_text(
        encoding="utf-8"
    )


def test_legacy_date_mark_does_not_call_again(politics_cohort, tmp_path):
    path = tmp_path / "watch.json"
    path.write_text(
        '{"KXTRUTHSOCIAL-26OCT17-B129": "2026-10-11"}\n',
        encoding="utf-8",
    )

    def _post(*_args, **_kwargs):
        raise AssertionError("a row already watched today must not call Grok")

    results = run_event_news_grok_watch_once(
        [_position()],
        market_lookup=lambda _ticker: _market(),
        state_path=path,
        now=_NOW,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert results == []
    assert path.read_text(encoding="utf-8") == (
        '{"KXTRUTHSOCIAL-26OCT17-B129": "2026-10-11"}\n'
    )


def test_watch_call_logs_usage_and_keeps_ok(tmp_path, monkeypatch):
    call_path = tmp_path / "calls.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)

    class _Usage:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                    "cost_in_usd_ticks": 500000000,
                },
            }

    def _post(*_args, **kwargs):
        assert "search_parameters" not in kwargs["json"]
        return _Usage()

    missing = event_news_grok_watch_decision(
        {"ticker": "KXTEST-WATCH", "side": "yes"},
        environ={},
        post=_post,
    )
    assert missing.reason == "missing_key"
    assert not call_path.exists()
    result = event_news_grok_watch_decision(
        {"ticker": "KXTEST-WATCH", "side": "yes"},
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
    )
    assert result.result == "ok"
    row = json.loads(call_path.read_text(encoding="utf-8"))
    assert row["kind"] == "watch"
    assert row["verdict"] == "ok"
    assert row["grounded"] is False
    assert row["input_tokens"] == 8
    assert row["output_tokens"] == 1
    assert row["cost_usd"] == "0.05"
    assert row["cost_source"] == "provider"
    assert row["rationale"] == ""
    assert row["prompt_hash"] == ""
    assert _SECRET not in call_path.read_text(encoding="utf-8")
