from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest

from analysis.research_gate import ResearchStatus
from config import cfg
from scripts.research_ticker import (
    apply_pinned_asks,
    both_sides_edges,
    build_argparser,
    evaluate,
    resolve_dossier_store,
    routing_snapshot,
)
from tasks.research_dossier import DEFAULT_RESEARCH_DOSSIER_DB_PATH
from utils.event_news_research import EVENT_NEWS_COHORT_ID


def _politics():
    return SimpleNamespace(
        paper_cohort_id=EVENT_NEWS_COHORT_ID,
        llm_allowed_price_bands=[(0.55, 0.99)],
        llm_excluded_price_bands=[(0.00, 0.35)],
    )


def _b230_market(**overrides):
    values = dict(
        ticker="KXTRUTHSOCIAL-26AUG29-B230",
        title=(
            "Will Donald Trump make between 220 and 240 Truth Social posts "
            "the week of Aug 23, 2026?"
        ),
        subtitle="",
        status="active",
        series_ticker="KXTRUTHSOCIAL",
        event_ticker="KXTRUTHSOCIAL-26AUG29",
        yes_ask=0.38,
        no_ask=0.63,
        yes_ask_cents=38,
        no_ask_cents=63,
        yes_ask_size=12.0,
        no_ask_size=10.0,
        open_interest=13118,
        volume=17767,
        close_time="2026-08-30T13:59:00Z",
        rules_primary=(
            "If the number of Truth Social posts by Donald Trump from "
            "Aug 23, 2026 to Aug 29, 2026 is between 220 and 240, then "
            "the market resolves to Yes."
        ),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_parser_pins_the_example_book():
    args = build_argparser().parse_args(
        [
            "KXTRUTHSOCIAL-26AUG29-B230",
            "--yes-ask",
            "0.38",
            "--no-ask",
            "0.63",
            "--llm",
            "--json",
        ]
    )
    assert args.ticker == "KXTRUTHSOCIAL-26AUG29-B230"
    assert args.yes_ask == 0.38
    assert args.no_ask == 0.63
    assert args.llm is True
    assert args.json is True
    assert args.write_dossier is False
    assert args.no_gate is False


def test_38_63_favorite_is_no_not_a_yes_only_research_path():
    market = _b230_market()
    routing = routing_snapshot(market, config=_politics())
    assert routing["favorite_side"] == "no"
    assert routing["favorite_cents"] == 63
    assert routing["prewarm_skip_reason"] is None
    assert routing["score_both_sides"] is True


def test_both_sides_takes_cheap_yes_at_p_49():
    edges = both_sides_edges(0.49, 0.38, 0.63, live_mode=False)
    assert edges["selected_side"] == "yes"
    assert edges["yes"]["net_edge"] == pytest.approx(0.10)
    assert edges["no"]["net_edge"] == pytest.approx(-0.13)


def test_both_sides_takes_no_when_no_is_the_value():
    edges = both_sides_edges(0.20, 0.38, 0.63, live_mode=False)
    assert edges["selected_side"] == "no"
    assert edges["no"]["net_edge"] == pytest.approx(0.16)


def test_pinned_asks_override_live_cents():
    market = _b230_market(yes_ask=0.42, no_ask=0.62, yes_ask_cents=42, no_ask_cents=62)
    apply_pinned_asks(market, 0.38, 0.63)
    assert market.yes_ask_cents == 38
    assert market.no_ask_cents == 63
    assert market.yes_ask == pytest.approx(0.38)
    assert market.no_ask == pytest.approx(0.63)
    assert market.yes_price == pytest.approx(38)
    assert market.last_price_cents == 38
    assert market.price_available is True


def test_both_sides_ignores_empty_book_asks():
    edges = both_sides_edges(0.49, None, 0.0, live_mode=False)
    assert edges["yes"]["net_edge"] is None
    assert edges["no"]["net_edge"] is None
    assert edges["selected_side"] is None


def test_write_dossier_refuses_live_evidence_store(tmp_path):
    with pytest.raises(ValueError, match="requires --db-path"):
        resolve_dossier_store(Namespace(write_dossier=True, db_path=None))
    with pytest.raises(ValueError, match="live evidence_store"):
        resolve_dossier_store(
            Namespace(write_dossier=True, db_path=DEFAULT_RESEARCH_DOSSIER_DB_PATH)
        )
    with pytest.raises(ValueError, match="live evidence_store"):
        resolve_dossier_store(
            Namespace(write_dossier=True, db_path=tmp_path / "evidence_store.db")
        )
    store, tmp_dir = resolve_dossier_store(
        Namespace(write_dossier=True, db_path=tmp_path / "probe.db")
    )
    assert tmp_dir is None
    assert store.db_path == (tmp_path / "probe.db").resolve()
    store, tmp_dir = resolve_dossier_store(Namespace(write_dossier=False, db_path=None))
    assert tmp_dir is not None
    assert "evidence_store.db" not in str(store.db_path)
    tmp_dir.cleanup()


class _Client:
    def __init__(self, market):
        self.market = market
        self.calls: list[str] = []

    def get_market(self, ticker: str):
        self.calls.append(ticker)
        return self.market


async def _gate(*_args, **_kwargs):
    return SimpleNamespace(
        status=ResearchStatus.TRADE_CANDIDATE,
        skip_reason=None,
        force_side="yes",
        estimated_probability=0.49,
        estimated_edge=0.10,
        confidence=0.85,
        summary="structured both-sides YES",
        decision_grade_reasons=(),
        evidence=(),
        queries=(),
    )


async def _llm(*_args, **_kwargs):
    return (0.55, 0.4, ["truth"], "llm guessed yes", "yes", "moderate", 0.4)


@pytest.mark.asyncio
async def test_evaluate_replays_38_63_without_writing_live_dossier_or_orders():
    prior_cohort = cfg.paper_cohort_id
    market = _b230_market()
    args = Namespace(
        ticker="KXTRUTHSOCIAL-26AUG29-B230",
        yes_ask=0.38,
        no_ask=0.63,
        cohort=EVENT_NEWS_COHORT_ID,
        live_mode=False,
        llm=True,
        no_gate=False,
        timeout_seconds=18.0,
        max_queries=10,
        db_path=None,
        write_dossier=False,
    )
    report = await evaluate(
        args,
        client=_Client(market),
        research_gate=_gate,
        llm_fn=_llm,
        structured_fn=lambda _market: {
            "query": "B230",
            "observation": {"count": 175, "lower": 220, "upper": 240},
            "p_yes": 0.49,
            "state": "open_run_rate",
            "confidence": 0.85,
        },
    )
    assert report["placed_orders"] == 0
    assert report["dossier"]["persisted"] is False
    assert "evidence_store.db" not in report["dossier"]["path"]
    assert report["routing"]["favorite_side"] == "no"
    assert report["routing"]["favorite_cents"] == 63
    assert report["both_sides"]["selected_side"] == "yes"
    assert report["both_sides"]["yes"]["net_edge"] == pytest.approx(0.10)
    assert report["gate"]["force_side"] == "yes"
    assert report["llm"]["direction"] == "yes"
    assert report["conclusion"]["scoring_side"] == "yes"
    assert report["conclusion"]["admitted"] is True
    assert report["conclusion"]["trade_side"] == "yes"
    assert report["conclusion"]["favorite_side_is_not_trade_side"] is True
    assert report["conclusion"]["halted_on_neutral"] is False
    assert report["conclusion"]["placed_orders"] == 0
    assert cfg.paper_cohort_id == prior_cohort


@pytest.mark.asyncio
async def test_evaluate_restores_cohort_after_politics_probe():
    market = _b230_market()
    args = Namespace(
        ticker="KXTRUTHSOCIAL-26AUG29-B230",
        yes_ask=0.38,
        no_ask=0.63,
        cohort=EVENT_NEWS_COHORT_ID,
        live_mode=False,
        llm=False,
        no_gate=True,
        timeout_seconds=18.0,
        max_queries=10,
        db_path=None,
        write_dossier=False,
    )
    prior = cfg.paper_cohort_id
    await evaluate(
        args,
        client=_Client(market),
        structured_fn=lambda _market: {
            "query": "B230",
            "observation": None,
            "p_yes": 0.49,
            "state": "open_run_rate",
            "confidence": 0.85,
        },
    )
    assert cfg.paper_cohort_id == prior
