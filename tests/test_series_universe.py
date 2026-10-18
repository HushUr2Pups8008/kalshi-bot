"""Universe file, exact series match, fail-closed load, and paper no-POST."""

from __future__ import annotations

import inspect
import logging
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts.universe_discovery import (
    PROMOTION_RULE,
    SCORE_ENV,
    format_money,
    is_denied,
    is_politics_econ_policy,
    market_qualifies,
    recommend_paper_series,
    render_report,
    settlement_rows,
)
from tests.test_executor import _make_analysis
from trading.executor import TradeExecutor
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    event_news_favorite_side,
    event_news_is_reserve_series,
    event_news_matcher_reserve_series,
    event_news_paper_score_decision,
    event_news_prewarm_skip_reason,
)
from utils.series_universe import (
    event_news_live_post_block_reason,
    live_series_tickers,
    reset_series_universe,
    series_ticker_matches,
    universe_failed,
)

EXPECTED_PAPER = (
    "KXHORMUZWEEKLY",
    "KXSUEZWEEKLY",
    "KXBABELMANDEBWEEKLY",
    "KXTRUMPUFC",
    "KXHORMUZMAX",
    "KXTRUMPAPPROVE",
    "KXTRUMPNOMNUM",
    "KXHORMUZPEAK",
    "KXPANAMAWEEKLY",
    "KXUSAIRANAGREEMENT",
)
EXPECTED_LIVE = (
    "KXTRUTHSOCIAL",
    "KXCANUSDEAL",
    "KXMOCTRUMP25",
    "KXTRUMPACT",
    "KXTRUMPSAY",
    "KXTRUMPMENTION",
    "KXAPRPOTUS",
    "KXPOLLPOTUS",
    "KXEFFTARIFF",
    "KXVOTESAVEAMERICA",
    "KXSBUDGETRES",
    "KXLEAVECONGRESS",
    "KXFISAEXTEND",
)
GLUED_TRUMP_SAY = (
    "KXTRUMPSAYCOUNTRY",
    "KXTRUMPSAYCOMPANY",
    "KXTRUMPSAYMONTH",
    "KXTRUMPSAYNICKNAME",
)


def _politics(**extra):
    values = {
        "paper_cohort_id": EVENT_NEWS_COHORT_ID,
        "llm_allowed_price_bands": [(0.55, 0.99)],
        "llm_excluded_price_bands": [(0.00, 0.35)],
        "research_prewarm_sourceable_series_fallback": (),
    }
    values.update(extra)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _reset_universe():
    reset_series_universe()
    yield
    reset_series_universe()


def test_live_series_snapshot_matches_the_shipped_thirteen():
    assert live_series_tickers() == EXPECTED_LIVE
    politics = _politics()
    assert event_news_matcher_reserve_series(config=politics) == EXPECTED_LIVE


def test_shipped_paper_series_are_not_live_and_cannot_post():
    from utils.series_universe import get_universe

    politics = _politics()
    entries = {entry.series: entry for entry in get_universe().entries}
    assert tuple(series for series in entries if entries[series].mode == "paper") == (
        ("KXTRUMPMENTION",) + EXPECTED_PAPER
    )
    assert live_series_tickers() == EXPECTED_LIVE
    for series in EXPECTED_PAPER:
        entry = entries[series]
        assert entry.mode == "paper"
        assert entry.favorite_band == (0.55, 0.99)
        assert entry.transcript_skip is False
        assert entry.paper_score is False
        market = SimpleNamespace(ticker=f"{series}-26OCT-A", series_ticker=series)
        assert series not in event_news_matcher_reserve_series(config=politics)
        assert event_news_is_reserve_series(market, config=politics) is False
        assert event_news_live_post_block_reason(market, config=politics) == (
            "paper_series_no_post"
        )
    mention = entries["KXTRUMPMENTION"]
    assert mention.mode == "paper"
    assert mention.transcript_skip is True
    assert mention.paper_score is True
    mention_market = SimpleNamespace(
        ticker="KXTRUMPMENTION-26AUG30-MAGA",
        series_ticker="KXTRUMPMENTION",
    )
    assert "KXTRUMPMENTION" in live_series_tickers()
    assert event_news_live_post_block_reason(mention_market, config=politics) == (
        "transcript_skip"
    )


def test_fallback_still_dedupes_in_front_of_the_yaml_live_rows():
    politics = _politics(research_prewarm_sourceable_series_fallback=("KXTRUTHSOCIAL",))
    assert event_news_matcher_reserve_series(config=politics) == EXPECTED_LIVE


def test_shipped_caps_families_and_x_search_match_todays_desk():
    """Caps and edge stay at today's values. Category is a label, not a gate."""
    from utils.series_universe import get_universe

    entries = {entry.series: entry for entry in get_universe().entries}
    assert all(entry.max_contracts == 8 for entry in entries.values())
    assert all(entry.edge_min == "0.04" for entry in entries.values())
    assert all(entry.category == "politics" for entry in entries.values())
    assert tuple(
        series for series, entry in entries.items() if entry.occupancy_family
    ) == ("KXTRUTHSOCIAL", "KXTRUMPACT", "KXTRUMPSAY")
    assert tuple(
        series for series, entry in entries.items() if entry.open_family_lock
    ) == ("KXTRUTHSOCIAL",)
    assert tuple(series for series, entry in entries.items() if entry.x_search) == (
        "KXTRUTHSOCIAL",
        "KXMOCTRUMP25",
        "KXTRUMPACT",
        "KXTRUMPSAY",
        "KXTRUMPMENTION",
        "KXAPRPOTUS",
        "KXPOLLPOTUS",
        "KXVOTESAVEAMERICA",
        "KXTRUMPUFC",
        "KXTRUMPAPPROVE",
        "KXTRUMPNOMNUM",
    )


def test_yaml_exact_series_survive_the_prefix_block_and_glued_siblings_do_not(
    monkeypatch,
):
    from main import _research_prewarm_ticker_blocked
    from utils.series_universe import event_news_prefix_blocked

    monkeypatch.setattr("config.cfg.paper_cohort_id", EVENT_NEWS_COHORT_ID)
    prefixes = ("KXTRUMPSAY", "KXAPRPOTUS", "KXPOLLPOTUS", "KXNBA", "KXMVE")
    for series in ("KXTRUMPSAY", "KXAPRPOTUS", "KXPOLLPOTUS"):
        assert event_news_prefix_blocked(series, prefixes) is False
        assert _research_prewarm_ticker_blocked(f"{series}-26OCT19-A") is False
    assert event_news_prefix_blocked("KXTRUMPSAYMONTH", prefixes) is True
    assert _research_prewarm_ticker_blocked("KXTRUMPSAYMONTH-26OCT-X") is True
    assert event_news_prefix_blocked("KXNBA", prefixes) is True
    assert _research_prewarm_ticker_blocked("KXNBA-26OCT-G") is True
    assert _research_prewarm_ticker_blocked("KXMVE-SPORT") is True


def test_prefix_block_stays_on_a_non_politics_cohort(monkeypatch):
    from utils.series_universe import event_news_prefix_blocked

    monkeypatch.setattr("config.cfg.paper_cohort_id", "legacy")
    assert event_news_prefix_blocked("KXTRUMPSAY", ("KXTRUMPSAY",)) is True
    assert event_news_prefix_blocked("KXTRUMPSAYMONTH", ("KXTRUMPSAY",)) is True


def test_macro_fallback_does_not_enter_the_politics_reserve():
    politics = _politics(
        research_prewarm_sourceable_series_fallback=(
            "KXGDP",
            "KXCPI",
            "KXFED",
            "KXNASDAQ100",
            "KXBTC",
            "KXETH",
            "KXHIGHNY",
            "KXMLB",
            "KXNBA",
        )
    )
    assert event_news_matcher_reserve_series(config=politics) == EXPECTED_LIVE


def test_favorite_bands_match_the_previous_hardcoded_pairs():
    from utils.series_universe import get_universe

    bands = {entry.series: entry.favorite_band for entry in get_universe().entries}
    assert bands["KXTRUTHSOCIAL"] == (0.36, 0.90)
    assert bands["KXTRUMPACT"] == (0.12, 0.90)
    for series in EXPECTED_LIVE:
        if series in {"KXTRUTHSOCIAL", "KXTRUMPACT"}:
            continue
        assert bands[series] == (0.55, 0.99)


def test_reserve_gate_source_has_no_hardcoded_series_tickers():
    from utils import event_news_research as research

    source = "\n".join(
        inspect.getsource(fn)
        for fn in (
            research.event_news_matcher_reserve_series,
            research.event_news_is_reserve_series,
            research.event_news_favorite_side,
            research.event_news_prewarm_skip_reason,
        )
    )
    for series in EXPECTED_LIVE:
        assert series not in source


def test_glued_trump_say_siblings_are_not_the_live_series():
    politics = _politics()
    for series in GLUED_TRUMP_SAY:
        market = SimpleNamespace(
            ticker=f"{series}-26OCT-X",
            series_ticker=series,
        )
        assert series_ticker_matches(series, "KXTRUMPSAY") is False
        assert event_news_is_reserve_series(market, config=politics) is False
    exact = SimpleNamespace(
        ticker="KXTRUMPSAY-26OCT19-ZACH",
        series_ticker="KXTRUMPSAY",
    )
    assert event_news_is_reserve_series(exact, config=politics) is True
    blank_series = SimpleNamespace(ticker="KXTRUMPSAY-26OCT19-ZACH", series_ticker="")
    assert event_news_is_reserve_series(blank_series, config=politics) is True


def test_truth_and_act_bands_stay_on_the_old_prices():
    politics = _politics()
    truth = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG29-B230",
        series_ticker="KXTRUTHSOCIAL",
        yes_ask_cents=38,
        no_ask_cents=63,
    )
    act = SimpleNamespace(
        ticker="KXTRUMPACT-26AUG23-T4",
        series_ticker="KXTRUMPACT",
        yes_ask_cents=38,
        no_ask_cents=100,
    )
    assert event_news_favorite_side(truth, config=politics) == ("no", 63)
    assert event_news_favorite_side(act, config=politics) == ("yes", 38)


def test_mention_live_skip_stays_and_paper_score_does_not_call_veto():
    politics = _politics()
    market = SimpleNamespace(
        ticker="KXTRUMPMENTION-26AUG30-MAGA",
        series_ticker="KXTRUMPMENTION",
        yes_ask_cents=22,
        no_ask_cents=81,
        yes_ask_size=10.0,
        no_ask_size=10.0,
        open_interest_fp=2000.0,
        volume_24h_fp=2000.0,
    )
    assert event_news_prewarm_skip_reason(market, config=politics) == (
        "mention_requires_transcript"
    )
    decision = event_news_paper_score_decision(
        market,
        probability=None,
        config=politics,
        environ=SCORE_ENV,
    )
    assert decision["would_post"] is False
    assert decision["veto"] == "veto_not_called"
    assert "transcript_skip_bypassed_for_paper_score" in decision["reasons"]
    assert "mention_requires_transcript" not in decision["reasons"]
    assert "edge_not_evaluated" in decision["reasons"]
    assert decision["decision"] == "incomplete_no_probability"


def _act_yes_38():
    return SimpleNamespace(
        ticker="KXTRUMPACT-26AUG23-T4",
        series_ticker="KXTRUMPACT",
        yes_ask_cents=38,
        no_ask_cents=100,
        yes_ask_size=12.0,
        no_ask_size=12.0,
        open_interest_fp=200.0,
        volume_24h_fp=100.0,
    )


def _truth_no_63():
    return SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26AUG29-B230",
        series_ticker="KXTRUTHSOCIAL",
        yes_ask_cents=38,
        no_ask_cents=63,
        yes_ask_size=12.0,
        no_ask_size=10.0,
        open_interest_fp=200.0,
        volume_24h_fp=100.0,
    )


def test_flag_off_still_researches_and_shadows_the_drop(monkeypatch, caplog):
    # YES at 38c is inside the act band and below the 55c live YES floor.
    monkeypatch.delenv("EVENT_NEWS_LIVE_TRADABLE_RESEARCH", raising=False)
    politics = _politics()
    mention = SimpleNamespace(
        ticker="KXTRUMPMENTION-26AUG30-MAGA",
        series_ticker="KXTRUMPMENTION",
        yes_ask_cents=22,
        no_ask_cents=81,
        yes_ask_size=10.0,
        no_ask_size=10.0,
        open_interest_fp=2000.0,
        volume_24h_fp=2000.0,
    )
    with caplog.at_level(logging.INFO):
        assert event_news_prewarm_skip_reason(_act_yes_38(), config=politics) is None
        assert event_news_prewarm_skip_reason(mention, config=politics) == (
            "mention_requires_transcript"
        )
    assert "ticker=KXTRUMPACT-26AUG23-T4 old=research new=not_live_tradable" in caplog.text
    assert "ticker=KXTRUMPMENTION-26AUG30-MAGA old=research new=not_researched" in caplog.text
    assert event_news_prewarm_skip_reason(_truth_no_63(), config=politics) is None
    assert "KXTRUMPMENTION" in live_series_tickers()


def test_flag_on_researches_only_live_tradable_books(monkeypatch):
    monkeypatch.setenv("EVENT_NEWS_LIVE_TRADABLE_RESEARCH", "true")
    politics = _politics()
    mention = SimpleNamespace(
        ticker="KXTRUMPMENTION-26AUG30-MAGA",
        series_ticker="KXTRUMPMENTION",
    )
    assert event_news_prewarm_skip_reason(_act_yes_38(), config=politics) == (
        "not_live_tradable"
    )
    assert event_news_prewarm_skip_reason(_truth_no_63(), config=politics) is None
    assert "KXTRUMPMENTION" not in live_series_tickers()
    assert event_news_is_reserve_series(mention, config=politics) is False
    assert event_news_prewarm_skip_reason(mention, config=politics) == "not_reserve_series"
    assert event_news_live_post_block_reason(mention, config=politics) == (
        "paper_series_no_post"
    )
    no_36 = SimpleNamespace(
        ticker="KXTRUMPACT-26AUG23-N36",
        series_ticker="KXTRUMPACT",
        yes_ask_cents=100,
        no_ask_cents=36,
        yes_ask_size=12.0,
        no_ask_size=12.0,
        open_interest_fp=200.0,
        volume_24h_fp=100.0,
    )
    no_35 = SimpleNamespace(
        ticker="KXTRUMPACT-26AUG23-N35",
        series_ticker="KXTRUMPACT",
        yes_ask_cents=100,
        no_ask_cents=35,
        yes_ask_size=12.0,
        no_ask_size=12.0,
        open_interest_fp=200.0,
        volume_24h_fp=100.0,
    )
    assert event_news_prewarm_skip_reason(no_36, config=politics) is None
    assert event_news_prewarm_skip_reason(no_35, config=politics) == "not_live_tradable"


def test_shadow_log_failure_does_not_change_the_skip(monkeypatch):
    monkeypatch.delenv("EVENT_NEWS_LIVE_TRADABLE_RESEARCH", raising=False)
    politics = _politics()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("log down")

    monkeypatch.setattr("utils.event_news_research.log.info", _boom)
    assert event_news_prewarm_skip_reason(_act_yes_38(), config=politics) is None
    mention = SimpleNamespace(
        ticker="KXTRUMPMENTION-26AUG30-MAGA",
        series_ticker="KXTRUMPMENTION",
        yes_ask_cents=22,
        no_ask_cents=81,
        yes_ask_size=10.0,
        no_ask_size=10.0,
        open_interest_fp=2000.0,
        volume_24h_fp=2000.0,
    )
    assert event_news_prewarm_skip_reason(mention, config=politics) == (
        "mention_requires_transcript"
    )


def test_research_flag_zero_stays_off(monkeypatch):
    monkeypatch.setenv("EVENT_NEWS_LIVE_TRADABLE_RESEARCH", "0")
    politics = _politics()
    assert event_news_prewarm_skip_reason(_act_yes_38(), config=politics) is None
    assert "KXTRUMPMENTION" in live_series_tickers()


def test_missing_universe_admits_nothing_and_logs_once(tmp_path, caplog):
    reset_series_universe(tmp_path / "missing.yaml")
    politics = _politics()
    market = SimpleNamespace(
        ticker="KXTRUTHSOCIAL-26OCT-A",
        series_ticker="KXTRUTHSOCIAL",
    )
    with caplog.at_level(logging.ERROR):
        assert universe_failed() is True
        assert live_series_tickers() == ()
        assert event_news_is_reserve_series(market, config=politics) is False
        assert event_news_matcher_reserve_series(config=politics) == ()
        assert event_news_prewarm_skip_reason(market, config=politics) == (
            "universe_unavailable"
        )
        assert event_news_live_post_block_reason(market, config=politics) == (
            "universe_unavailable"
        )
    lines = [rec.message for rec in caplog.records if "fail_closed" in rec.message]
    assert lines == [
        f"[EVENT_NEWS_UNIVERSE] fail_closed path={tmp_path / 'missing.yaml'} reason=missing"
    ]


@pytest.mark.parametrize(
    "text,reason",
    [
        ("{series: KXTRUTHSOCIAL}\n", "not_a_list"),
        (
            "- series: KXTRUTHSOCIAL\n"
            "  mode: live\n"
            "  favorite_lo_cents: 90\n"
            "  favorite_hi_cents: 36\n"
            "  notes: inverted\n",
            "bad_band",
        ),
        (
            "- series: KXTRUTHSOCIAL\n"
            "  mode: sometimes\n"
            "  favorite_lo_cents: 36\n"
            "  favorite_hi_cents: 90\n"
            "  notes: bad mode\n",
            "bad_mode",
        ),
        (
            "- series: KXTRUTHSOCIAL\n"
            "  mode: live\n"
            "  favorite_lo_cents: 36\n"
            "  favorite_hi_cents: 90\n"
            "  notes: one\n"
            "- series: KXTRUTHSOCIAL\n"
            "  mode: paper\n"
            "  favorite_lo_cents: 55\n"
            "  favorite_hi_cents: 99\n"
            "  notes: two\n",
            "duplicate",
        ),
        (
            "- series: KXTRUTHSOCIAL\n"
            "  mode: live\n"
            "  favorite_lo_cents: 36\n"
            "  favorite_hi_cents: 90\n"
            "  max_contracts: 9\n"
            "  notes: over the hard cap\n",
            "bad_max_contracts",
        ),
        (
            "- series: KXTRUTHSOCIAL\n"
            "  mode: live\n"
            "  favorite_lo_cents: 36\n"
            "  favorite_hi_cents: 90\n"
            "  edge_min: \"1.5\"\n"
            "  notes: edge above 1\n",
            "bad_edge_min",
        ),
    ],
)
def test_invalid_universe_admits_nothing(tmp_path, text, reason):
    path = tmp_path / "universe.yaml"
    path.write_text(text, encoding="utf-8")
    reset_series_universe(path)
    politics = _politics()
    market = SimpleNamespace(ticker="KXTRUTHSOCIAL-1", series_ticker="KXTRUTHSOCIAL")
    assert universe_failed() is True
    assert live_series_tickers() == ()
    assert event_news_is_reserve_series(market, config=politics) is False
    from utils.series_universe import get_universe

    assert get_universe().reason == reason


def _write_mixed_universe(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "- series: KXTRUTHSOCIAL",
                "  mode: live",
                "  favorite_lo_cents: 36",
                "  favorite_hi_cents: 90",
                "  notes: live truth",
                "- series: KXPAPERTEST",
                "  mode: paper",
                "  favorite_lo_cents: 55",
                "  favorite_hi_cents: 99",
                "  notes: paper only",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_paper_series_is_not_in_the_live_reserve_tuple(tmp_path):
    path = tmp_path / "universe.yaml"
    _write_mixed_universe(path)
    reset_series_universe(path)
    politics = _politics()
    paper = SimpleNamespace(ticker="KXPAPERTEST-26OCT-A", series_ticker="KXPAPERTEST")
    live = SimpleNamespace(ticker="KXTRUTHSOCIAL-26OCT-A", series_ticker="KXTRUTHSOCIAL")
    freeze = SimpleNamespace(paper_cohort_id="kalshi-macro-20260820")
    assert event_news_matcher_reserve_series(config=politics) == ("KXTRUTHSOCIAL",)
    assert event_news_is_reserve_series(paper, config=politics) is False
    assert event_news_live_post_block_reason(paper, config=politics) == (
        "paper_series_no_post"
    )
    assert event_news_live_post_block_reason(live, config=politics) is None
    assert event_news_live_post_block_reason(paper, config=freeze) is None


@pytest.mark.asyncio
async def test_paper_series_cannot_post(monkeypatch, tmp_path, caplog):
    path = tmp_path / "universe.yaml"
    _write_mixed_universe(path)
    reset_series_universe(path)
    monkeypatch.setattr("config.cfg.paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr("config.cfg.is_paper_trading", False)
    monkeypatch.setattr("config.cfg.bankroll", 500.0)
    for name in (
        "EVENT_NEWS_YES_FLOOR_CENTS",
        "EVENT_NEWS_MAX_RISK_CENTS",
        "EVENT_NEWS_MAX_EXPOSURE_PCT",
        "EVENT_NEWS_ONE_PER_FAMILY",
        "EVENT_NEWS_MAKER_FIRST",
        "EVENT_NEWS_GROK_RULES_CHECK",
        "KALSHI_GROK_PAPER_SIDECAR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
        lambda *args, **kwargs: True,
    )
    rest = MagicMock()
    result = MagicMock()
    result.error = None
    result.order_id = "not-sent"
    result.status = "resting"
    result.filled = 0
    rest.place_limit_order.return_value = result
    executor = TradeExecutor(
        rest,
        MagicMock(),
        live_submission_hold_path=tmp_path / "holds.json",
    )
    analysis = _make_analysis(ticker="KXPAPERTEST-26OCT-A", side="no", yes_price=63)
    analysis.market.series_ticker = "KXPAPERTEST"
    with caplog.at_level(logging.ERROR), patch("trading.executor.trade_log"):
        order_id = await executor._execute_live(analysis)
    assert order_id is None
    rest.place_limit_order.assert_not_called()
    assert any(
        "reason=paper_series_no_post" in rec.message and "no POST" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_live_series_still_reaches_the_mocked_post(monkeypatch, tmp_path):
    path = tmp_path / "universe.yaml"
    _write_mixed_universe(path)
    reset_series_universe(path)
    monkeypatch.setattr("config.cfg.paper_cohort_id", EVENT_NEWS_COHORT_ID)
    monkeypatch.setattr("config.cfg.is_paper_trading", False)
    monkeypatch.setattr("config.cfg.bankroll", 500.0)
    for name in (
        "EVENT_NEWS_YES_FLOOR_CENTS",
        "EVENT_NEWS_MAX_RISK_CENTS",
        "EVENT_NEWS_MAX_EXPOSURE_PCT",
        "EVENT_NEWS_ONE_PER_FAMILY",
        "EVENT_NEWS_MAKER_FIRST",
        "EVENT_NEWS_GROK_RULES_CHECK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "utils.event_news_grok_veto.event_news_grok_veto_allows_live_post",
        lambda *args, **kwargs: True,
    )
    rest = MagicMock()
    result = MagicMock()
    result.error = None
    result.order_id = "mock-order"
    result.status = "resting"
    result.filled = 0
    rest.place_limit_order.return_value = result
    executor = TradeExecutor(
        rest,
        MagicMock(),
        live_submission_hold_path=tmp_path / "holds.json",
    )
    analysis = _make_analysis(ticker="KXTRUTHSOCIAL-26OCT-A", side="no", yes_price=63)
    analysis.market.series_ticker = "KXTRUTHSOCIAL"
    with patch("trading.executor.trade_log"):
        order_id = await executor._execute_live(analysis)
    assert order_id == "mock-order"
    rest.place_limit_order.assert_called_once()


def test_candidate_rules_reject_crossed_books_and_inactive_status():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    close_at = now + timedelta(days=3)
    assert market_qualifies(
        market_state="active",
        close_at=close_at,
        volume=Decimal("500"),
        yes_cents=40,
        no_cents=62,
        now=now,
    )
    assert market_qualifies(
        market_state="active",
        close_at=close_at,
        volume=Decimal("500"),
        yes_cents=40,
        no_cents=74,
        now=now,
    )
    assert not market_qualifies(
        market_state="active",
        close_at=close_at,
        volume=Decimal("500"),
        yes_cents=40,
        no_cents=75,
        now=now,
    )
    assert not market_qualifies(
        market_state="active",
        close_at=close_at,
        volume=Decimal("500"),
        yes_cents=60,
        no_cents=30,
        now=now,
    )
    assert not market_qualifies(
        market_state="open",
        close_at=close_at,
        volume=Decimal("5000"),
        yes_cents=48,
        no_cents=52,
        now=now,
    )
    assert not market_qualifies(
        market_state="active",
        close_at=close_at,
        volume=Decimal("499"),
        yes_cents=48,
        no_cents=52,
        now=now,
    )


def test_politics_recommendation_skips_blocklist_and_configured_series():
    rows = [
        {"series": "KXSPORTS", "category": "Sports", "volume_24h": Decimal("9")},
        {"series": "KXNFLGAME", "category": "Politics", "volume_24h": Decimal("8")},
        {"series": "KXTRUTHSOCIAL", "category": "Politics", "volume_24h": Decimal("7")},
        {"series": "KXPOLICY", "category": "Economics", "volume_24h": Decimal("6")},
        {"series": "KXFEDRATE", "category": "Economics", "volume_24h": Decimal("5")},
    ]
    explicit = {"KXTRUTHSOCIAL"}
    assert is_denied("KXFEDRATE", explicit) is False
    assert is_denied("KXFISAEXTEND", explicit) is False
    assert is_politics_econ_policy(
        "KXPOLICY",
        category="Economics",
        explicit=explicit,
    )
    picked = recommend_paper_series(rows, explicit=explicit, limit=10)
    assert [row["series"] for row in picked] == ["KXPOLICY", "KXFEDRATE"]


def test_settlement_join_uses_fee_net_and_does_not_invent_zero(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE paper_trades (trade_id TEXT, ticker TEXT, series_ticker TEXT, side TEXT, resolved_yes INTEGER)"
    )
    conn.execute(
        "CREATE TABLE paper_trade_accounting (trade_id TEXT, fee_net_pnl_dollars TEXT)"
    )
    conn.executemany(
        "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?)",
        [
            ("t1", "KXPOLICY-1", "KXPOLICY", "yes", 1),
            ("t2", "KXPOLICY-2", "KXPOLICY", "no", 1),
        ],
    )
    conn.executemany(
        "INSERT INTO paper_trade_accounting VALUES (?, ?)",
        [("t1", "1.50"), ("t2", "-0.50")],
    )
    conn.commit()
    conn.close()
    rows = {row["series"]: row for row in settlement_rows(db_path, ["KXPOLICY", "KXNONE"])}
    assert rows["KXPOLICY"]["n"] == 2
    assert rows["KXPOLICY"]["hits"] == 1
    assert rows["KXPOLICY"]["hit_rate"] == Decimal("0.5")
    assert rows["KXPOLICY"]["fee_net_ev"] == Decimal("0.5")
    assert rows["KXNONE"]["n"] == 0
    assert rows["KXNONE"]["fee_net_ev"] is None
    assert "15 settled paper trades" in PROMOTION_RULE
    assert "does not change modes" in PROMOTION_RULE


def test_report_prints_paper_rows_and_rounds_fee_net_half_up():
    from datetime import datetime, timezone

    assert format_money(Decimal("1.315733333333333333333333333")) == "1.3157"
    assert format_money(Decimal("0.5")) == "0.5"
    assert format_money(None) == ""
    text = render_report(
        fetched_at=datetime(2026, 10, 18, 19, 48, 48, tzinfo=timezone.utc),
        pages=1,
        raw_count=1,
        active_count=1,
        series_pages=1,
        candidates=[],
        recommendations=[],
        scores=[],
        settlement=[
            {
                "series": "KXTRUTHSOCIAL",
                "n": 3,
                "hits": 3,
                "hit_rate": Decimal(1),
                "fee_net_ev": Decimal("1.315733333333333333333333333"),
                "note": "",
            }
        ],
        paper_configured=[
            {
                "series": "KXPAPERTEST",
                "markets": 2,
                "volume_24h": Decimal("1000.50"),
                "median_close": "2026-10-24T01:00:00Z",
                "category": "Politics",
                "title": "Example paper series",
            }
        ],
        top=20,
    )
    assert "| KXPAPERTEST | 2 | 1000.5 | 2026-10-24T01:00:00Z | Politics | Example paper series |" in text
    assert "| KXTRUTHSOCIAL | 3 | 3 | 1 | 1.3157 |  |" in text
    assert "half-up to 0.0001" in text
    assert "would_post is false" in text
    assert "does not write the yaml" in text


def test_discovery_script_does_not_place_orders():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "universe_discovery.py"
    ).read_text(encoding="utf-8")
    assert "place_limit_order" not in source
    assert "/portfolio/orders" not in source
    assert "responses.create" not in source
