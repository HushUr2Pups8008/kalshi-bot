from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from analysis.research_gate import ResearchStatus, ResearchVerdict
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    apply_event_news_live_research,
    event_news_crossed_asks,
    event_news_exposure_prefix,
    event_news_favorite_side,
    event_news_horizon_days,
    event_news_illiquid,
    event_news_in_allowed_ask_band,
    event_news_min_edge,
    event_news_missing_snapshot_ask,
    event_news_non_politics_series,
    event_news_pin_matcher_series,
    event_news_forecast_refresh_series,
    event_news_official_research_kwargs,
    event_news_omit_idle_runtime_tasks,
    event_news_open_prefix_cap,
    event_news_bypass_quote_skip_cooldown,
    event_news_prewarm_allows,
    event_news_prewarm_seed_markets,
    event_news_prewarm_skip_reason,
    event_news_quote_dependent_skip_reason,
    event_news_settle_statuses,
    event_news_spread_disagreement,
    event_news_wide_spread,
    is_event_news_paper_cohort,
    routing_yes_probability,
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
    boundary = SimpleNamespace(yes_ask_cents=1, no_ask_cents=100, yes_ask=None, no_ask=None)
    assert snapshot_ask_cents(boundary) == (1, None)


def test_event_news_missing_snapshot_ask_only_on_politics_cohort():
    market = SimpleNamespace(yes_ask_cents=None, no_ask_cents=None, yes_ask=None, no_ask=None)
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    assert event_news_missing_snapshot_ask(market, config=freeze) is None
    assert event_news_missing_snapshot_ask(market, config=politics) == "missing_snapshot_ask"
    complete = SimpleNamespace(yes_ask_cents=80, no_ask_cents=22, yes_ask=None, no_ask=None)
    assert event_news_missing_snapshot_ask(complete, config=politics) is None
    lottery = SimpleNamespace(yes_ask_cents=1, no_ask_cents=100, yes_ask=None, no_ask=None)
    assert event_news_missing_snapshot_ask(lottery, config=politics) is None


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
    assert gate.await_args.kwargs["allow_official_pdf_and_homepage"] is True
    assert gate.await_args.kwargs["prefer_official_sources"] is True
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


def test_routing_prefers_ask_on_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    market = SimpleNamespace(
        yes_prob=0.36,
        yes_price=36,
        yes_ask_cents=70,
        yes_ask=70,
    )
    mid, source = routing_yes_probability(market, config=freeze)
    assert source == "mid"
    assert mid == pytest.approx(0.36)
    ask, source = routing_yes_probability(market, config=politics)
    assert source == "yes_ask"
    assert ask == pytest.approx(0.70)


def test_spread_disagreement_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    wide = SimpleNamespace(
        ticker="KXCANUSDEAL-26-26AUG24",
        last_price_cents=36,
        yes_ask_cents=70,
    )
    tight = SimpleNamespace(last_price_cents=69, yes_ask_cents=70)
    assert event_news_spread_disagreement(wide, config=freeze) is None
    assert event_news_spread_disagreement(wide, config=politics) == "last_ask_divergence"
    assert event_news_spread_disagreement(tight, config=politics) is None


def test_illiquid_skip_requires_both_oi_and_volume():
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    empty = SimpleNamespace(
        ticker="KXTHIN-1",
        yes_ask_size=1.0,
        no_ask_size=0.0,
        open_interest_fp=3.0,
        volume_24h_fp=1.0,
    )
    assert event_news_illiquid(empty, config=freeze) is None
    assert event_news_illiquid(empty, config=politics) == "illiquid_top_size"
    missing_size = SimpleNamespace(
        ticker="KXTHIN-2",
        yes_ask_size=None,
        no_ask_size=None,
        open_interest_fp=3.0,
        volume_24h_fp=1.0,
    )
    assert event_news_illiquid(missing_size, config=politics) == "illiquid_open_interest"
    # Missing volume must not skip — fail open on incomplete quotes.
    oi_only = SimpleNamespace(
        ticker="KXTHIN-3",
        yes_ask_size=None,
        no_ask_size=None,
        open_interest_fp=3.0,
        volume_24h_fp=None,
    )
    assert event_news_illiquid(oi_only, config=politics) is None


def test_early_close_compresses_horizon_on_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    market = SimpleNamespace(
        ticker="KXDEAL-1",
        can_close_early=True,
        early_close_condition="Closes when the deal is announced.",
    )
    assert event_news_horizon_days(market, 14.0, config=freeze) == pytest.approx(14.0)
    assert event_news_horizon_days(market, 14.0, config=politics) == pytest.approx(2.0)
    already_short = SimpleNamespace(
        ticker="KXDEAL-2",
        can_close_early=False,
        early_close_condition="announced",
    )
    assert event_news_horizon_days(already_short, 0.5, config=politics) == pytest.approx(0.5)


def test_event_prefix_and_cap_isolated_from_freeze():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    market = SimpleNamespace(
        ticker="KXCANUSDEAL-26-26AUG24",
        event_ticker="KXCANUSDEAL-26",
    )
    assert event_news_exposure_prefix(market, "KXCANUSDEAL", config=freeze) == "KXCANUSDEAL"
    assert (
        event_news_exposure_prefix(market, "KXCANUSDEAL", config=politics)
        == "KXCANUSDEAL-26"
    )
    assert event_news_open_prefix_cap(2, config=freeze) == 2
    assert event_news_open_prefix_cap(2, config=politics) == 1


def test_determined_status_only_on_politics():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = SimpleNamespace(paper_cohort_id=EVENT_NEWS_COHORT_ID)
    assert event_news_settle_statuses(config=freeze) == ("finalized", "settled")
    assert event_news_settle_statuses(config=politics) == (
        "finalized",
        "settled",
        "determined",
    )


def _politics_config(**extra):
    values = {
        "paper_cohort_id": EVENT_NEWS_COHORT_ID,
        "llm_allowed_price_bands": [(0.55, 0.99)],
        "llm_excluded_price_bands": [(0.00, 0.35)],
    }
    values.update(extra)
    return SimpleNamespace(**values)


def test_favorite_ask_band_and_prewarm_filter_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    favorite = SimpleNamespace(
        ticker="KXSENATE-1",
        yes_prob=0.40,
        yes_price=40,
        yes_ask_cents=72,
        yes_ask=72,
        no_ask_cents=30,
        no_ask=30,
        yes_ask_size=8.0,
        no_ask_size=6.0,
        open_interest_fp=40.0,
        volume_24h_fp=20.0,
    )
    longshot = SimpleNamespace(
        ticker="KXLONG-1",
        yes_prob=0.12,
        yes_price=12,
        yes_ask_cents=12,
        yes_ask=12,
        no_ask_cents=100,
        no_ask=100,
        yes_ask_size=8.0,
        no_ask_size=6.0,
        open_interest_fp=40.0,
        volume_24h_fp=20.0,
    )
    assert event_news_in_allowed_ask_band(longshot, config=freeze) is True
    assert event_news_prewarm_allows(longshot, config=freeze) is True
    assert event_news_prewarm_skip_reason(longshot, config=freeze) is None
    assert event_news_in_allowed_ask_band(favorite, config=politics) is True
    assert event_news_prewarm_allows(favorite, config=politics) is True
    assert event_news_in_allowed_ask_band(longshot, config=politics) is False
    assert event_news_prewarm_skip_reason(longshot, config=politics) == (
        "ask_outside_favorite_band"
    )


def test_near_certain_ask_is_not_politics_money_path():
    politics = _politics_config()
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    ninety_eight_no = SimpleNamespace(
        ticker="KXLEAVECONGRESS-26AUG",
        yes_ask_cents=7,
        no_ask_cents=98,
        yes_ask=7,
        no_ask=98,
        yes_ask_size=180.0,
        no_ask_size=180.0,
        open_interest_fp=1200.0,
        volume_24h_fp=100.0,
    )
    ninety_yes = SimpleNamespace(
        ticker="KXFAV-90",
        yes_ask_cents=90,
        no_ask_cents=12,
        yes_ask=90,
        no_ask=12,
        yes_ask_size=20.0,
        no_ask_size=20.0,
        open_interest_fp=100.0,
        volume_24h_fp=50.0,
    )
    assert event_news_prewarm_skip_reason(ninety_eight_no, config=freeze) is None
    assert event_news_prewarm_skip_reason(ninety_eight_no, config=politics) == (
        "near_certain_ask"
    )
    assert event_news_favorite_side(ninety_eight_no, config=politics) == (None, None)
    assert event_news_prewarm_allows(ninety_yes, config=politics) is True
    assert event_news_bypass_quote_skip_cooldown(
        "neutral_only_evidence", ninety_eight_no, config=politics
    ) is False


def test_favorite_no_side_and_wide_spread_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    no_favorite = SimpleNamespace(
        ticker="KXMOCTRUMP25-26-JAN01",
        yes_prob=0.11,
        yes_price=11,
        yes_ask_cents=13,
        yes_ask=13,
        no_ask_cents=87,
        no_ask=87,
        yes_ask_size=100.0,
        no_ask_size=100.0,
        open_interest_fp=4000.0,
        volume_24h_fp=200.0,
    )
    wide = SimpleNamespace(
        ticker="KXCANUSDEAL-26-26SEP01",
        yes_prob=0.50,
        yes_price=50,
        yes_ask_cents=97,
        yes_ask=97,
        no_ask_cents=97,
        no_ask=97,
        yes_ask_size=3000.0,
        no_ask_size=3000.0,
        open_interest_fp=250.0,
        volume_24h_fp=250.0,
    )
    lottery = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG22-B129",
        yes_ask_cents=1,
        no_ask_cents=100,
        yes_ask=1,
        no_ask=100,
        yes_ask_size=9000.0,
        no_ask_size=None,
        open_interest_fp=6000.0,
        volume_24h_fp=200.0,
    )
    both = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG22-B230",
        yes_ask_cents=55,
        no_ask_cents=56,
        yes_ask=55,
        no_ask=56,
        yes_ask_size=40.0,
        no_ask_size=40.0,
        open_interest_fp=23000.0,
        volume_24h_fp=20000.0,
    )
    assert event_news_favorite_side(no_favorite, config=freeze) == (None, None)
    assert event_news_favorite_side(no_favorite, config=politics) == ("no", 87)
    assert event_news_prewarm_allows(no_favorite, config=politics) is True
    assert event_news_wide_spread(wide, config=freeze) is None
    assert event_news_wide_spread(wide, config=politics) == "wide_spread"
    assert event_news_prewarm_skip_reason(wide, config=politics) == "wide_spread"
    assert event_news_prewarm_skip_reason(lottery, config=politics) == (
        "ask_outside_favorite_band"
    )
    assert event_news_favorite_side(both, config=politics) == ("yes", 55)
    near_no = SimpleNamespace(yes_ask_cents=45, no_ask_cents=56, yes_ask=45, no_ask=56)
    assert event_news_min_edge(0.02, near_no, config=politics) == pytest.approx(
        0.07 * 0.56 * 0.44 + 0.005
    )


def test_llm_routing_allows_favorite_no_and_blocks_lottery(monkeypatch):
    from analysis.signal_analyzer import _llm_routing_reason
    from analysis import signal_analyzer

    politics = _politics_config()
    monkeypatch.setattr(signal_analyzer.cfg, "enable_llm_routing_filter", True)
    monkeypatch.setattr(signal_analyzer.cfg, "llm_allowed_price_bands", [(0.55, 0.99)])
    monkeypatch.setattr(signal_analyzer.cfg, "llm_excluded_price_bands", [(0.00, 0.35)])
    monkeypatch.setattr("utils.event_news_research.cfg", politics)
    news = SimpleNamespace(headline="x")
    no_favorite = SimpleNamespace(
        ticker="KXMOCTRUMP25-26-JAN01",
        yes_ask_cents=13,
        no_ask_cents=87,
        yes_ask=13,
        no_ask=87,
        yes_prob=0.11,
    )
    lottery = SimpleNamespace(
        ticker="KXLONG-1",
        yes_ask_cents=12,
        no_ask_cents=100,
        yes_ask=12,
        no_ask=100,
        yes_prob=0.12,
    )
    assert _llm_routing_reason(news, no_favorite) is None
    assert _llm_routing_reason(news, lottery) == "price_band_excluded"


def test_quote_dependent_cooldown_bypass_only_on_live_favorite():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    favorite = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG22-T240",
        yes_ask_cents=58,
        no_ask_cents=50,
        yes_ask=58,
        no_ask=50,
        yes_ask_size=25.0,
        no_ask_size=10.0,
        open_interest_fp=30000.0,
        volume_24h_fp=14000.0,
    )
    longshot = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG22-B129",
        yes_ask_cents=1,
        no_ask_cents=100,
        yes_ask=1,
        no_ask=100,
        yes_ask_size=9000.0,
        no_ask_size=None,
        open_interest_fp=6000.0,
        volume_24h_fp=200.0,
    )
    assert event_news_quote_dependent_skip_reason("illiquid_top_size") is True
    assert event_news_quote_dependent_skip_reason("official_data_pending") is False
    assert (
        event_news_bypass_quote_skip_cooldown(
            "illiquid_top_size", favorite, config=freeze
        )
        is False
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "illiquid_top_size", favorite, config=politics
        )
        is True
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "ask_outside_favorite_band", favorite, config=politics
        )
        is True
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "illiquid_top_size", longshot, config=politics
        )
        is False
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "missing_resolution_source", favorite, config=politics
        )
        is True
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "neutral_only_evidence", favorite, config=politics
        )
        is True
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "missing_resolution_source", longshot, config=politics
        )
        is False
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "official_data_pending", favorite, config=politics
        )
        is False
    )
    assert (
        event_news_bypass_quote_skip_cooldown(
            "no_edge", favorite, config=politics
        )
        is False
    )


def test_prewarm_seed_markets_uses_pinned_series_not_cache():
    politics = _politics_config()
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    cache = [SimpleNamespace(ticker="STALE-CACHE")]
    fetched: list[str] = []

    def get_markets(*, status, series_ticker, limit):
        fetched.append(series_ticker)
        return [SimpleNamespace(ticker=f"{series_ticker}-FRESH", series_ticker=series_ticker)], None

    rest = SimpleNamespace(get_markets=get_markets)
    freeze_markets = event_news_prewarm_seed_markets(
        rest_client=rest, matcher_markets=cache, config=freeze
    )
    assert freeze_markets == cache
    assert fetched == []
    politics_markets = event_news_prewarm_seed_markets(
        rest_client=rest, matcher_markets=cache, config=politics
    )
    assert fetched
    assert all(getattr(market, "ticker", "").endswith("-FRESH") for market in politics_markets)
    assert cache[0] not in politics_markets


def test_crossed_asks_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    crossed = SimpleNamespace(
        ticker="KXLOCK-1",
        yes_ask_cents=48,
        no_ask_cents=48,
        yes_ask=48,
        no_ask=48,
    )
    healthy = SimpleNamespace(
        ticker="KXLOCK-2",
        yes_ask_cents=72,
        no_ask_cents=30,
        yes_ask=72,
        no_ask=30,
    )
    assert event_news_crossed_asks(crossed, config=freeze) is None
    assert event_news_crossed_asks(crossed, config=politics) == "crossed_asks"
    assert event_news_crossed_asks(healthy, config=politics) is None


def test_min_edge_rises_to_taker_fee_on_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    near_even = SimpleNamespace(
        ticker="KXNEAR-1",
        yes_prob=0.56,
        yes_price=56,
        yes_ask_cents=56,
        yes_ask=56,
    )
    deep_favorite = SimpleNamespace(
        ticker="KXFAV-1",
        yes_prob=0.90,
        yes_price=90,
        yes_ask_cents=90,
        yes_ask=90,
    )
    assert event_news_min_edge(0.02, near_even, config=freeze) == pytest.approx(0.02)
    near_even_required = event_news_min_edge(0.02, near_even, config=politics)
    assert near_even_required == pytest.approx(0.07 * 0.56 * 0.44 + 0.005)
    assert near_even_required > 0.02
    assert event_news_min_edge(0.02, deep_favorite, config=politics) == pytest.approx(0.02)


def test_official_research_kwargs_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    assert event_news_official_research_kwargs(config=freeze) == {}
    assert event_news_official_research_kwargs(config=politics) == {
        "allow_official_pdf_and_homepage": True,
        "prefer_official_sources": True,
    }


def test_research_dossier_path_is_cohort_isolated(monkeypatch, tmp_path):
    import config as config_module
    from tasks.research_dossier import _research_dossier_db_path

    monkeypatch.setattr("tasks.research_dossier.DATA_DIR", tmp_path)
    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    path = _research_dossier_db_path()
    assert path == tmp_path / "paper_cohorts" / EVENT_NEWS_COHORT_ID / "research_dossier.db"
    monkeypatch.setattr(config_module.cfg, "paper_cohort_id", "kalshi-macro-20260820")
    path = _research_dossier_db_path()
    assert path == tmp_path / "paper_cohorts" / "kalshi-macro-20260820" / "research_dossier.db"


def test_non_politics_series_denied_on_politics_only():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    gas = SimpleNamespace(ticker="KXAAAGASW-26AUG24-4.096", series_ticker="KXAAAGASW")
    truth = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG22-T240", series_ticker="KXTRUTHSOCIAL"
    )
    assert event_news_non_politics_series(gas, config=freeze) is None
    assert event_news_non_politics_series(gas, config=politics) == "non_politics_series"
    assert event_news_non_politics_series(truth, config=politics) is None
    assert event_news_prewarm_skip_reason(gas, config=politics) == "non_politics_series"


def test_matcher_reserve_pins_politics_series_and_ignores_freeze():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    catalog = [
        {"ticker": "KXRECENTJUNK"},
        {"ticker": "KXTRUTHSOCIAL"},
        {"ticker": "KXMOCTRUMP25"},
    ]
    recency = ["KXRECENTJUNK"]
    assert event_news_pin_matcher_series(recency, catalog, limit=2, config=freeze) == [
        "KXRECENTJUNK"
    ]
    pinned = event_news_pin_matcher_series(recency, catalog, limit=2, config=politics)
    assert pinned[0] == "KXTRUTHSOCIAL"
    assert "KXMOCTRUMP25" in pinned
    assert "KXRECENTJUNK" in pinned or len(pinned) == 2


def test_idle_runtime_tasks_and_forecast_refresh_isolated_from_freeze():
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    politics = _politics_config()
    assert event_news_omit_idle_runtime_tasks(config=freeze) is False
    assert event_news_omit_idle_runtime_tasks(config=politics) is True
    assert event_news_forecast_refresh_series(("KXDJI",), config=freeze) == ("KXDJI",)
    assert event_news_forecast_refresh_series(("KXDJI", "KXCPI"), config=politics) == ()
