import asyncio
import json
from pathlib import Path

from feeds import NewsItem
from feeds.search_news_monitor import SEARCH_MAX_QUERIES, _markets_to_queries
from kalshi import KalshiMarket
from kalshi.series_metadata import KalshiSeriesMetadata, SettlementSource
from kalshi.source_hints import (
    DEFAULT_SOURCE_REGISTRY,
    MarketContractContext,
    build_market_contract_context,
    build_market_first_queries,
)
from analysis.candidate_assignment_shadow import build_shadow_assignment
from scripts.market_first_assignment_audit import summarize, validate


REPO_ROOT = Path(__file__).resolve().parents[1]


def _market() -> KalshiMarket:
    return KalshiMarket(
        ticker="KXTRUMPIRAN-26",
        title="Will Trump visit Iran?",
        yes_bid=49.0,
        yes_ask=51.0,
        yes_price=50.0,
        volume=100,
        open_interest=200,
        close_time="2026-12-31T23:59:59Z",
        status="active",
        series_ticker="KXTRUMPIRAN",
        price_available=True,
        rules_primary="Resolution source: The Associated Press.",
        rules_secondary="Secondary rule",
        market_metadata={
            "rules_primary": "Resolution source: The Associated Press.",
            "custom_source": "Reuters",
        },
    )


def test_default_source_registry_is_importable_and_lookup_works():
    assert DEFAULT_SOURCE_REGISTRY.lookup("The Associated Press").domain == "apnews.com"


def test_contract_context_uses_newline_join_and_excludes_duplicate_rules_metadata():
    context = build_market_contract_context(_market(), None)

    assert isinstance(context, MarketContractContext)
    assert "Secondary rule" in context.rules_text
    assert "\\n" not in context.rules_text
    assert context.rules_text.count("Resolution source: The Associated Press.") == 1


def test_market_first_queries_use_topic_terms_not_exact_full_title_or_kalshi_placeholder():
    context = build_market_contract_context(
        _market(),
        KalshiSeriesMetadata(
            series_ticker="KXTRUMPIRAN",
            tags=("Trump", "Iran"),
            settlement_sources=(
                SettlementSource("The Associated Press", "https://apnews.com/politics", "apnews.com"),
                SettlementSource("Kalshi", "https://kalshi.com/markets/example", "kalshi.com"),
            ),
        ),
    )

    queries = build_market_first_queries(context, max_queries=SEARCH_MAX_QUERIES)

    assert queries == ("site:apnews.com trump iran",)
    assert "Will Trump visit Iran?" not in queries[0]
    assert not any("kalshi.com" in query for query in queries)


def test_markets_to_queries_keeps_shadow_queries_default_off_and_budgeted():
    market = _market()
    meta = KalshiSeriesMetadata(
        series_ticker="KXTRUMPIRAN",
        tags=("Trump", "Iran"),
        settlement_sources=(SettlementSource("The Associated Press", domain="apnews.com"),),
    )

    default_queries = _markets_to_queries([market], series_metadata_by_ticker={"KXTRUMPIRAN": meta})
    shadow_queries = _markets_to_queries(
        [market],
        series_metadata_by_ticker={"KXTRUMPIRAN": meta},
        market_first_query_shadow=True,
    )

    assert "site:apnews.com trump iran" not in default_queries
    assert "site:apnews.com trump iran" in shadow_queries
    assert len(shadow_queries) <= SEARCH_MAX_QUERIES


class _Matcher:
    def __init__(self, candidates):
        self._candidates = candidates

    async def find_candidates(self, news):
        return self._candidates


def test_shadow_assignment_unpacks_candidate_tuple_without_getattr_defaults():
    row = asyncio.run(build_shadow_assignment(
        _Matcher([(_market(), 0.42, {"basis": "test"})]),
        NewsItem(headline="Trump Iran update", url="https://example.com", source="test"),
    ))

    assert row.assigned is True
    assert row.top_ticker == "KXTRUMPIRAN-26"
    assert row.top_score == 0.42
    assert row.malformed is False


def test_shadow_assignment_emits_malformed_row_instead_of_raising():
    row = asyncio.run(build_shadow_assignment(
        _Matcher([("not-a-market", 0.42, {})]),
        NewsItem(headline="Trump Iran update", url="https://example.com", source="test"),
    ))

    assert row.assigned is False
    assert row.malformed is True
    assert row.top_ticker == ""


def test_assignment_audit_flags_false_clean_rows(tmp_path):
    shadow_file = tmp_path / "fresh_pass_assignment_shadow.jsonl"
    shadow_file.write_text(
        json.dumps({
            "type": "FRESH_PASS_ASSIGNMENT_SHADOW",
            "assigned": True,
            "top_ticker": "",
            "top_score": None,
            "malformed": False,
        }) + "\n",
        encoding="utf-8",
    )

    summary = summarize(tmp_path)

    assert summary.rows == 1
    assert summary.rows_assigned_without_ticker == 1
    assert summary.rows_assigned_without_score == 1
    assert validate(summary) == [
        "assigned rows without top_ticker",
        "assigned rows without top_score",
    ]


def test_fresh_pass_assignment_shadow_is_scheduled_out_of_band():
    source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    enqueue_body = source.split("async def _enqueue_news", 1)[1].split(
        "def _schedule_fresh_pass_assignment_shadow", 1
    )[0]

    assert "self._schedule_fresh_pass_assignment_shadow(news)" in enqueue_body
    assert "await build_shadow_assignment" not in enqueue_body
    assert "has_market_snapshot()" in source
    assert "asyncio.wait_for(" in source
