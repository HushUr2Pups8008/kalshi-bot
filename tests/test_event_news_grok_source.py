"""Politics source reader: a homepage is not official-p without a free count."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib import request as urllib_request

import pytest

from analysis.research_gate import (
    ResearchEvidence,
    _BAD_OFFICIAL_FETCHES,
    _append_grok_source_evidence,
    _deterministic_decision_signal,
    _fetch_direct_source,
    _note_skipped_direct_source,
)
from analysis.truth_social_forecast import (
    TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
    TruthSocialCountObservation,
    parse_truth_social_count_contract,
)
from utils.event_news_research import (
    event_news_google_counter_not_required,
    event_news_has_structured_official_metric,
    event_news_official_p_ready,
)

_NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)
_FACT_URL = "https://rollcall.com/factbase/trump/topic/social/"
_HOME = "https://www.whitehouse.gov/"
_ACTIONS = "https://www.whitehouse.gov/presidential-actions/"


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


def _observation(market, count: int, *, source_url: str = _FACT_URL, complete: bool = True):
    contract = parse_truth_social_count_contract(
        " ".join(
            str(part)
            for part in (market.ticker, market.title, market.rules_primary)
            if part
        )
    )
    assert contract is not None
    return TruthSocialCountObservation(
        count=count,
        deleted_count=0,
        window_start=contract.week_start,
        window_end=contract.week_start + timedelta(days=6),
        lower=contract.lower,
        upper=contract.upper,
        complete=complete,
        pages=1,
        source_url=source_url,
        observed_at=_NOW.isoformat(),
    )


def _evidence(market=None, **kwargs):
    from utils.event_news_grok_source import event_news_grok_source_evidence

    return event_news_grok_source_evidence(
        market=market or _market(),
        evidence=kwargs.pop("evidence", []),
        bad_urls=kwargs.pop("bad_urls", (_HOME, "https://assets.kalshi.com/empty.pdf")),
        post=kwargs.pop("post", None),
        now=_NOW,
        **kwargs,
    )


def test_homepage_without_a_free_count_is_not_official_p(politics_cohort, monkeypatch):
    market = _market()
    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        lambda *_args, **_kwargs: None,
    )
    called = []

    def _post(*_args, **_kwargs):
        called.append(1)
        raise AssertionError("source reader must not call xAI")

    evidence = _evidence(market, post=_post)
    assert evidence == ()
    assert called == []
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


def test_failed_free_fetch_does_not_invent_p(politics_cohort, monkeypatch, caplog):
    market = _market()

    def _boom(*_args, **_kwargs):
        raise TimeoutError("factbase down")

    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        _boom,
    )
    with caplog.at_level(logging.INFO):
        evidence = _evidence(market)
    assert evidence == ()
    assert any("reason=fetch_error" in record.message for record in caplog.records)
    assert _deterministic_decision_signal([], market, allow_evidence_signal=True) is None


def test_named_count_uses_the_calculator(politics_cohort, monkeypatch):
    market = _market()
    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        lambda *_args, **_kwargs: _observation(market, 150),
    )
    evidence = _evidence(market)
    assert len(evidence) == 1
    item = evidence[0]
    assert item.metric_name == TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC
    assert item.metric_value == pytest.approx(0.98)
    assert item.supports_direction == "yes"
    assert item.source_url == _FACT_URL
    signal = _deterministic_decision_signal(
        list(evidence),
        market,
        allow_evidence_signal=True,
    )
    assert signal is not None
    assert signal["estimated_probability_yes"] == pytest.approx(0.98)
    assert signal["direction"] == "yes"
    assert event_news_google_counter_not_required(
        market=market,
        evidence=list(evidence),
        estimated_probability=signal["estimated_probability_yes"],
        force_side=signal["direction"],
    )


def test_structured_count_does_not_fetch(politics_cohort, monkeypatch):
    existing = [
        SimpleNamespace(
            metric_name=TRUTH_SOCIAL_RANGE_PROBABILITY_METRIC,
            metric_value=0.64,
        )
    ]

    def _fetch(*_args, **_kwargs):
        raise AssertionError("structured count must not fetch")

    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        _fetch,
    )
    evidence = _evidence(evidence=existing, bad_urls=(_HOME,))
    assert evidence == ()
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


def test_excluded_band_and_no_edge_do_not_fetch(politics_cohort, monkeypatch):
    def _fetch(*_args, **_kwargs):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        _fetch,
    )
    longshot = _market(ticker="KXTRUMPACT-26OCT11-T5", yes_ask_cents=30, no_ask_cents=20)
    _evidence(longshot, bad_urls=(_HOME,))
    _evidence(_market(yes_ask_cents=10, no_ask_cents=10), bad_urls=(_HOME,))
    _evidence(skip_reason="no_edge", bad_urls=(_HOME,))
    _evidence(cache_only=True, bad_urls=(_HOME,))


def test_incomplete_count_and_homepage_url_add_no_evidence(politics_cohort, monkeypatch, caplog):
    market = _market()
    seen = {"n": 0}

    def _fetch(*_args, **_kwargs):
        seen["n"] += 1
        if seen["n"] == 1:
            return None
        return _observation(market, 150, source_url=_HOME)

    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        _fetch,
    )
    with caplog.at_level(logging.INFO):
        missing = _evidence(market)
        homepage = _evidence(market)
    assert missing == homepage == ()
    text = " ".join(record.message for record in caplog.records)
    assert "reason=no_free_count" in text


def test_white_house_keeps_only_a_priced_archive_probability(politics_cohort, monkeypatch):
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
    calls = []

    def _search(*_args, **_kwargs):
        calls.append(1)
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="White House Presidential Actions",
                source_url=_ACTIONS,
                title="count only",
                snippet="2 actions",
                claim_type="official_resolution",
                metric_name="white_house_presidential_actions_count",
                metric_value=2.0,
                supports_confidence=0.98,
            ),
            ResearchEvidence(
                source_class="official_primary",
                source_name="White House Presidential Actions",
                source_url=_ACTIONS,
                title="priced",
                snippet="calculator",
                claim_type="official_resolution",
                supports_direction="no",
                supports_confidence=0.9,
                metric_name="white_house_action_range_probability",
                metric_value=0.02,
                metric_unit="probability",
            ),
        ]

    monkeypatch.setattr(
        "analysis.research_gate._white_house_presidential_actions_search",
        _search,
    )
    evidence = _evidence(market)
    assert calls == [1]
    assert len(evidence) == 1
    assert evidence[0].metric_name == "white_house_action_range_probability"
    assert evidence[0].metric_value == pytest.approx(0.02)
    assert evidence[0].supports_direction == "no"


def test_white_house_count_without_a_probability_stays_empty(politics_cohort, monkeypatch):
    market = _market(
        ticker="KXTRUMPACT-26OCT04-T6",
        title="Will Trump take at least 6 presidential actions",
        rules_primary=(
            "from October 1, 2026 through October 4, 2026 at 11:59 PM ET on "
            "October 4, 2026 using whitehouse.gov"
        ),
    )

    def _search(*_args, **_kwargs):
        return [
            ResearchEvidence(
                source_class="official_primary",
                source_name="White House Presidential Actions",
                source_url=_ACTIONS,
                title="count only",
                snippet="2 actions",
                claim_type="official_resolution",
                metric_name="white_house_presidential_actions_count",
                metric_value=2.0,
                supports_confidence=0.98,
            )
        ]

    monkeypatch.setattr(
        "analysis.research_gate._white_house_presidential_actions_search",
        _search,
    )
    assert _evidence(market) == ()


def test_unsupported_contract_does_not_fetch(politics_cohort, monkeypatch):
    market = _market(
        ticker="KXOTHER-26OCT-A",
        title="Will it rain tomorrow",
        rules_primary="",
    )

    def _fetch(*_args, **_kwargs):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        _fetch,
    )
    monkeypatch.setattr(
        "analysis.research_gate._white_house_presidential_actions_search",
        _fetch,
    )
    assert _evidence(market) == ()


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


def test_reader_module_does_not_call_xai_or_place_orders():
    from pathlib import Path

    text = Path("utils/event_news_grok_source.py").read_text(encoding="utf-8")
    assert "place_limit_order" not in text
    assert "trading.executor" not in text
    assert "api.x.ai" not in text
    assert "requests" not in text


def test_free_fetch_writes_no_source_call_row(tmp_path, monkeypatch):
    market = _market()
    call_path = tmp_path / "calls.jsonl"
    monkeypatch.setattr("utils.event_news_grok_veto.GROK_CALL_LOG_PATH", call_path)
    monkeypatch.setattr(
        "analysis.truth_social_forecast.fetch_truth_social_count",
        lambda *_args, **_kwargs: _observation(market, 150),
    )
    evidence = _evidence(market)
    assert len(evidence) == 1
    assert not call_path.exists()
