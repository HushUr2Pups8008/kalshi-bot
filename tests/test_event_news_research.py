from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from analysis.research_gate import ResearchStatus, ResearchVerdict
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    apply_event_news_live_research,
    event_news_missing_snapshot_ask,
    is_event_news_paper_cohort,
    snapshot_ask_cents,
)


def test_is_event_news_paper_cohort_isolated():
    assert is_event_news_paper_cohort(SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID))
    assert not is_event_news_paper_cohort(
        SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    )


def test_snapshot_ask_requires_both_sides():
    yes_only = SimpleNamespace(yes_ask_cents=72, no_ask_cents=None, yes_ask=None, no_ask=None)
    assert snapshot_ask_cents(yes_only) == (72, None)
    both = SimpleNamespace(yes_ask_cents=72, no_ask_cents=31, yes_ask=None, no_ask=None)
    assert snapshot_ask_cents(both) == (72, 31)


def test_event_news_missing_snapshot_ask_only_on_politics_cohort():
    market = SimpleNamespace(yes_ask_cents=None, no_ask_cents=None, yes_ask=None, no_ask=None)
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    assert event_news_missing_snapshot_ask(market, config=freeze) is None
    assert event_news_missing_snapshot_ask(market, config=politics) == "missing_snapshot_ask"
    complete = SimpleNamespace(yes_ask_cents=80, no_ask_cents=22, yes_ask=None, no_ask=None)
    assert event_news_missing_snapshot_ask(complete, config=politics) is None


@pytest.mark.asyncio
async def test_live_research_prefers_gate_probability(monkeypatch):
    news = SimpleNamespace(source="Reuters", headline="Senate confirms nominee")
    market = SimpleNamespace(
        ticker="KXSENATECONFIRM-26AUG-YES",
        yes_ask_cents=80,
        no_ask_cents=22,
        yes_ask=0.80,
        no_ask=0.22,
        yes_price=0.79,
        no_price=0.21,
    )
    config = SimpleNamespace(
        paper_cohort_id=EVENT_NEWS_COHORT_ID,
        is_paper_trading=True,
        real_web_research_max_queries=6,
        real_web_research_timeout_seconds=12.0,
    )
    verdict = ResearchVerdict(
        status=ResearchStatus.DECISION_GRADE_CANDIDATE,
        attempted=True,
        force_side="yes",
        estimated_probability=0.81,
        confidence=0.7,
        summary="Official vote record supports YES.",
    )
    gate = AsyncMock(return_value=verdict)
    monkeypatch.setattr("utils.event_news_research.run_research_gate", gate)
    monkeypatch.setattr(
        "utils.event_news_research.default_research_dossier_store",
        lambda: object(),
    )

    p, conf, keywords, reasoning, side, mag, llm_conf = await apply_event_news_live_research(
        news=news,
        market=market,
        estimated_prob=0.72,
        confidence=0.4,
        keywords=["senate"],
        reasoning="llm shift",
        llm_dir="yes",
        llm_mag="small",
        llm_conf=0.4,
        config=config,
    )

    assert gate.await_args.kwargs["cache_only"] is False
    assert gate.await_args.kwargs["require_decision_grade"] is True
    assert p == pytest.approx(0.81)
    assert side == "yes"
    assert "research_evidence" in keywords
    assert reasoning == "Official vote record supports YES."
    assert conf == pytest.approx(0.7)
    assert mag == "moderate"
    assert llm_conf == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_live_research_keeps_llm_when_gate_has_no_p(monkeypatch):
    news = SimpleNamespace(source="Reuters", headline="Unclear")
    market = SimpleNamespace(
        ticker="KXTEST-YES",
        yes_ask_cents=70,
        no_ask_cents=32,
        yes_ask=0.70,
        no_ask=0.32,
        yes_price=0.69,
        no_price=0.31,
    )
    config = SimpleNamespace(
        paper_cohort_id=EVENT_NEWS_COHORT_ID,
        is_paper_trading=True,
        real_web_research_max_queries=6,
        real_web_research_timeout_seconds=12.0,
    )
    verdict = ResearchVerdict(
        status=ResearchStatus.NEEDS_COUNTER_EVIDENCE,
        attempted=True,
        skip_reason="missing_resolution_source",
    )
    monkeypatch.setattr(
        "utils.event_news_research.run_research_gate",
        AsyncMock(return_value=verdict),
    )
    monkeypatch.setattr(
        "utils.event_news_research.default_research_dossier_store",
        lambda: object(),
    )
    p, _conf, keywords, reasoning, side, mag, _llm_conf = await apply_event_news_live_research(
        news=news,
        market=market,
        estimated_prob=0.70,
        confidence=0.5,
        keywords=["trump"],
        reasoning="llm shift",
        llm_dir="yes",
        llm_mag="small",
        llm_conf=0.5,
        config=config,
    )
    assert p == pytest.approx(0.70)
    assert reasoning == "llm shift"
    assert side == "yes"
    assert mag == "small"
    assert keywords == ["trump"]


@pytest.mark.asyncio
async def test_live_research_noop_on_macro_cohort(monkeypatch):
    gate = AsyncMock()
    monkeypatch.setattr("utils.event_news_research.run_research_gate", gate)
    p, *_rest = await apply_event_news_live_research(
        news=SimpleNamespace(),
        market=SimpleNamespace(ticker="KXDJI-26AUG2416-53200.00"),
        estimated_prob=0.55,
        confidence=0.5,
        keywords=[],
        reasoning="dji",
        llm_dir=None,
        llm_mag=None,
        llm_conf=None,
        config=SimpleNamespace(paper_cohort_id="kalshi-macro-20260820"),
    )
    assert p == pytest.approx(0.55)
    gate.assert_not_awaited()
