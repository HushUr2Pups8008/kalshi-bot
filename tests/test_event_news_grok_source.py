"""Politics Grok source reader: a homepage is not official-p without a named count."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib import request as urllib_request

import pytest
import requests

from analysis.research_gate import (
    _BAD_OFFICIAL_FETCHES,
    _append_grok_source_evidence,
    _deterministic_decision_signal,
    _fetch_direct_source,
    _note_skipped_direct_source,
)
from analysis.truth_social_forecast import (
    TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
    remaining_threshold_probability,
)
from utils.event_news_grok_source import event_news_grok_source_evidence
from utils.event_news_grok_veto import GROK_MAX_TOKENS, GROK_MODEL, GROK_TIMEOUT_SECONDS
from utils.event_news_research import (
    event_news_google_counter_not_required,
    event_news_has_structured_official_metric,
    event_news_official_p_ready,
)

_SECRET = "xai-source-key-do-not-log"
_NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
_FACT_URL = "https://rollcall.com/factbase/trump/topic/social/"
_HOME = "https://www.whitehouse.gov/"


def _market(**overrides):
    base = dict(
        ticker="KXTRUTHSOCIAL-26AUG22-B149",
        title=(
            "Will Donald Trump make between 140 and 159 Truth Social posts "
            "the week of August 16, 2026?"
        ),
        rules_primary="",
        rules_secondary="",
        yes_ask_cents=70,
        no_ask_cents=40,
        contract_terms_url="https://kalshi.com/contracts/truth-social-weekly",
        settlement_sources=(SimpleNamespace(url=_HOME),),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _chat(text: str) -> _Response:
    return _Response(200, {"choices": [{"message": {"content": text}}]})


def _run(text: str, market=None, **kwargs):
    calls = []

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _chat(text)

    evidence = event_news_grok_source_evidence(
        market=market or _market(),
        evidence=kwargs.pop("evidence", []),
        bad_urls=kwargs.pop("bad_urls", (_HOME, "https://assets.kalshi.com/empty.pdf")),
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
        **kwargs,
    )
    return evidence, calls


def test_homepage_without_named_fact_is_not_official_p(politics_cohort):
    market = _market()
    evidence, calls = _run('{"kind":"none","value":"","url":""}', market)
    assert evidence == ()
    assert len(calls) == 1
    assert event_news_has_structured_official_metric(list(evidence)) is False
    assert (
        event_news_official_p_ready(
            estimated_probability=0.9,
            force_side="yes",
            market=market,
        )
        and event_news_google_counter_not_required(
            market=market,
            evidence=[],
            estimated_probability=0.9,
            force_side="yes",
        )
    ) is False
    assert (
        event_news_google_counter_not_required(
            market=market,
            evidence=[],
            estimated_probability=0.9,
            force_side="yes",
        )
        is False
    )


def test_probability_only_reply_does_not_set_p(politics_cohort, caplog):
    market = _market()
    with caplog.at_level(logging.INFO):
        evidence, calls = _run('{"estimated_probability_yes":0.9}', market)
    assert evidence == ()
    assert len(calls) == 1
    assert any("reason=parse_fail" in record.message for record in caplog.records)
    assert _SECRET not in " ".join(record.message for record in caplog.records)
    assert _deterministic_decision_signal([], market, allow_evidence_signal=True) is None


def test_named_count_uses_calculator_not_grok_probability(politics_cohort):
    evidence, calls = _run(
        '{"kind":"count","value":"150","url":"%s","estimated_probability":0.1}'
        % _FACT_URL
    )
    assert len(calls) == 1
    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC
    assert item.metric_value == pytest.approx(0.98)
    assert item.metric_value != pytest.approx(0.1)
    assert item.supports_direction == "yes"
    assert item.source_url == _FACT_URL
    signal = _deterministic_decision_signal(
        list(evidence),
        _market(),
        allow_evidence_signal=True,
    )
    assert signal is not None
    assert signal["estimated_probability_yes"] == pytest.approx(0.98)
    assert signal["direction"] == "yes"
    assert event_news_google_counter_not_required(
        market=_market(),
        evidence=list(evidence),
        estimated_probability=signal["estimated_probability_yes"],
        force_side=signal["direction"],
    )


def test_structured_count_does_not_call_source_reader(politics_cohort):
    existing = [
        SimpleNamespace(
            metric_name=TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
            metric_value=0.64,
        )
    ]
    calls = []

    def _post(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("structured count must not call Grok")

    evidence = event_news_grok_source_evidence(
        market=_market(),
        evidence=existing,
        bad_urls=(_HOME,),
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
    )
    assert evidence == ()
    assert calls == []
    assert existing[0].metric_value == 0.64


@pytest.mark.asyncio
async def test_gate_does_not_invoke_reader_for_structured_count(politics_cohort):
    called = []

    async def _reader(**_kwargs):
        called.append(1)
        return ()

    evidence = [
        SimpleNamespace(
            metric_name="white_house_action_range_probability",
            metric_value=0.98,
        )
    ]
    await _append_grok_source_evidence(
        grok_source_reader=_reader,
        market=_market(ticker="KXTRUMPACT-26OCT11-T5"),
        evidence=evidence,
        fresh_evidence=[],
        existing=set(),
        bad_urls=(_HOME,),
        yes_ask=0.70,
        no_ask=0.40,
        cache_only=False,
        contract_fingerprint="fp",
        ticker="KXTRUMPACT-26OCT11-T5",
        collection_budget_exhausted=False,
    )
    assert called == []


@pytest.mark.asyncio
async def test_gate_invokes_reader_for_homepage_without_structured_count(politics_cohort):
    called = []

    async def _reader(**kwargs):
        called.append(kwargs)
        return ()

    await _append_grok_source_evidence(
        grok_source_reader=_reader,
        market=_market(),
        evidence=[],
        fresh_evidence=[],
        existing=set(),
        bad_urls=(_HOME,),
        yes_ask=0.70,
        no_ask=0.40,
        cache_only=False,
        contract_fingerprint="fp",
        ticker="KXTRUTHSOCIAL-26AUG22-B149",
        collection_budget_exhausted=False,
    )
    assert len(called) == 1
    assert called[0]["bad_urls"] == (_HOME,)


def test_excluded_band_and_no_edge_do_not_call(politics_cohort):
    calls = []

    def _post(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("must not call")

    longshot = _market(ticker="KXTRUMPACT-26OCT11-T5", yes_ask_cents=30, no_ask_cents=20)
    event_news_grok_source_evidence(
        market=longshot,
        evidence=[],
        bad_urls=(_HOME,),
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
    )
    event_news_grok_source_evidence(
        market=_market(yes_ask_cents=10, no_ask_cents=10),
        evidence=[],
        bad_urls=(_HOME,),
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
    )
    event_news_grok_source_evidence(
        market=_market(),
        evidence=[],
        bad_urls=(_HOME,),
        skip_reason="no_edge",
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
    )
    event_news_grok_source_evidence(
        market=_market(),
        evidence=[],
        bad_urls=(_HOME,),
        cache_only=True,
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        now=_NOW,
    )
    assert calls == []


def test_missing_key_timeout_and_parse_fail_add_no_evidence(politics_cohort, caplog):
    def _boom(*_args, **_kwargs):
        raise requests.Timeout("slow")

    with caplog.at_level(logging.INFO):
        missing = event_news_grok_source_evidence(
            market=_market(),
            evidence=[],
            bad_urls=(_HOME,),
            environ={},
            post=_boom,
            now=_NOW,
        )
        timed_out = event_news_grok_source_evidence(
            market=_market(),
            evidence=[],
            bad_urls=(_HOME,),
            environ={"XAI_API_KEY": _SECRET},
            post=_boom,
            now=_NOW,
        )
        failed = event_news_grok_source_evidence(
            market=_market(),
            evidence=[],
            bad_urls=(_HOME,),
            environ={"XAI_API_KEY": _SECRET},
            post=lambda *_args, **_kwargs: _chat("not json"),
            now=_NOW,
        )
    assert missing == timed_out == failed == ()
    text = " ".join(record.message for record in caplog.records)
    assert "reason=missing_key" in text
    assert "reason=timeout" in text
    assert "reason=parse_fail" in text
    assert _SECRET not in text


def test_packet_asks_for_count_not_a_probability(politics_cohort):
    _evidence, calls = _run('{"kind":"none","value":"","url":""}')
    payload = calls[0]["json"]
    assert payload["model"] == GROK_MODEL
    assert payload["max_tokens"] == GROK_MAX_TOKENS
    assert calls[0]["timeout"] == GROK_TIMEOUT_SECONDS
    user = payload["messages"][1]["content"]
    assert "What is the count, vote, or status, and the URL that states it?" in user
    assert "KXTRUTHSOCIAL-26AUG22-B149" in user
    assert _HOME in user
    assert "https://kalshi.com/contracts/truth-social-weekly" in user
    assert "probability" not in payload["messages"][0]["content"].lower() or (
        "Do not include a probability" in payload["messages"][0]["content"]
    )
    assert _SECRET not in user
    assert calls[0]["headers"]["Authorization"] == f"Bearer {_SECRET}"


def test_white_house_count_uses_remaining_threshold(politics_cohort):
    market = _market(
        ticker="KXTRUMPACT-26OCT04-T6",
        title="Will Trump take at least 6 presidential actions",
        rules_primary=(
            "from October 1, 2026 through October 4, 2026 at 11:59 PM ET on "
            "October 4, 2026 using whitehouse.gov"
        ),
        yes_ask_cents=70,
        no_ask_cents=40,
    )
    fact = "https://www.whitehouse.gov/presidential-actions/example/"
    evidence, _calls = _run(
        '{"kind":"count","value":"2","url":"%s","estimated_probability_yes":0.77}'
        % fact,
        market,
    )
    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == "white_house_action_range_probability"
    assert item.metric_value == pytest.approx(0.02)
    assert item.metric_value != pytest.approx(0.77)
    assert item.supports_direction == "no"
    priced = remaining_threshold_probability(
        count=2,
        threshold=6,
        elapsed_days=30.0,
        remaining_days=0.0,
    )
    assert priced is not None
    assert item.metric_value == pytest.approx(priced[0])


def test_vote_and_homepage_fact_url_do_not_set_p(politics_cohort):
    vote, _calls = _run(
        '{"kind":"status","value":"passed","url":"%s"}' % _FACT_URL
    )
    homepage, _calls = _run(
        '{"kind":"count","value":"150","url":"%s"}' % _HOME
    )
    assert vote == ()
    assert homepage == ()


def test_empty_pdf_is_still_none_and_can_be_noted(monkeypatch):
    class _Resp:
        def read(self, _n: int = 0) -> bytes:
            return b"%PDF-1.4 binary"

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(urllib_request, "urlopen", lambda *_args, **_kwargs: _Resp())
    monkeypatch.setattr(
        "analysis.research_gate._extract_pdf_text",
        lambda _raw: ("contract_terms_pdf", ""),
    )
    url = "https://assets.kalshi.com/contract_terms/BILL.pdf"
    assert (
        _fetch_direct_source(
            url,
            "rules_source",
            "contract_terms",
            allow_official_pdf_and_homepage=True,
        )
        is None
    )
    bucket: list[str] = []
    token = _BAD_OFFICIAL_FETCHES.set(bucket)
    try:
        assert (
            _fetch_direct_source(
                url,
                "rules_source",
                "contract_terms",
                allow_official_pdf_and_homepage=True,
            )
            is None
        )
        _note_skipped_direct_source(_HOME, "settlement_source")
    finally:
        _BAD_OFFICIAL_FETCHES.reset(token)
    assert url in bucket
    assert _HOME in bucket


def test_reader_module_does_not_place_orders():
    from pathlib import Path

    text = Path("utils/event_news_grok_source.py").read_text(encoding="utf-8")
    assert "place_limit_order" not in text
    assert "trading.executor" not in text


def test_source_call_logs_usage_and_returns_the_same_text(tmp_path, monkeypatch):
    from utils.event_news_grok_source import _post_source

    call_path = tmp_path / "calls.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)

    def _post(*_args, **_kwargs):
        return _Response(
            200,
            {
                "choices": [{"message": {"content": '{"kind":"none","value":"","url":""}'}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 2,
                    "cost_in_usd_ticks": 3000000000,
                },
            },
        )

    missing, why = _post_source(
        {"ticker": "KXTEST-SOURCE"},
        ticker="KXTEST-SOURCE",
        environ={},
        post=_post,
        started=0.0,
    )
    assert missing is None and why == "missing_key"
    assert not call_path.exists()
    text, reason = _post_source(
        {"ticker": "KXTEST-SOURCE"},
        ticker="KXTEST-SOURCE",
        environ={"XAI_API_KEY": _SECRET},
        post=_post,
        started=0.0,
    )
    assert reason is None
    assert text == '{"kind":"none","value":"","url":""}'
    row = json.loads(call_path.read_text(encoding="utf-8"))
    assert row["kind"] == "source"
    assert row["input_tokens"] == 11
    assert row["output_tokens"] == 2
    assert row["total_tokens"] == 13
    assert row["cost_usd"] == "0.3"
    assert row["cost_source"] == "provider"
    assert row["grounded"] is False
    assert row["rationale"] == ""
    assert row["prompt_hash"] == ""
    assert _SECRET not in call_path.read_text(encoding="utf-8")
