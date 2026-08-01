"""Tests for feeds/search_news_monitor query construction and state recovery.

Covers `_markets_to_queries`, specifically the news-edge prioritization
(option A, 2026-05-30): markets on series with demonstrated news-edge must
always be turned into search queries, even when high-open-interest macro /
"mention" markets would otherwise fill all SEARCH_MAX_QUERIES slots first.
The 158 lifetime opportunities clustered entirely on these event-driven
political/geopolitical series; letting liquid-but-no-edge markets crowd them
out of the query budget starves the only markets the bot actually trades.
"""

from types import SimpleNamespace

import pytest

from feeds import NewsItem
from feeds.search_news_monitor import _markets_to_queries, _tag_source_hint_item, SEARCH_MAX_QUERIES
from kalshi.series_metadata import SettlementSource


def _mkt(series, title, oi, price=50, *, settlement_sources=()):
    return SimpleNamespace(
        title=title,
        open_interest=oi,
        yes_price=price,
        series_ticker=series,
        ticker=f"{series}-26JUN05-X",
        settlement_sources=tuple(settlement_sources),
    )


class StopAfterOneSearchCycle(Exception):
    pass


def _parsed(*entries):
    return SimpleNamespace(feed=SimpleNamespace(title="Example"), entries=list(entries))


async def _run_one_search_cycle(delivered, seen_state_path, monkeypatch):
    import feeds.search_news_monitor as search

    async def callback(item):
        delivered.append(item)

    async def stop_after_cycle(_seconds):
        raise StopAfterOneSearchCycle

    monkeypatch.setattr(search.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(StopAfterOneSearchCycle):
        await search.run_search_news_monitor(
            callback,
            get_markets=lambda: [_mkt("KXIRAN", "Iran nuclear response", oi=100)],
            poll_interval=1,
            seen_state_path=seen_state_path,
        )


def test_edge_series_market_queried_despite_low_open_interest():
    # SEARCH_MAX_QUERIES high-OI, no-edge markets that would fill every slot,
    # each with a distinct token set so none are deduped away.
    fillers = [
        _mkt(f"KXDIPLO{i}", f"diplomat zeta{i} treaty accord", oi=1000)
        for i in range(SEARCH_MAX_QUERIES + 5)
    ]
    # One proven news-edge market (KXTRUMPIRAN produced 34 opportunities) with
    # the lowest possible liquidity — under pure OI ranking it never gets queried.
    edge = _mkt("KXTRUMPIRAN", "iran nuclear strike response", oi=1)

    queries = _markets_to_queries(fillers + [edge], edge_series=frozenset({"KXTRUMPIRAN"}))

    assert "iran nuclear strike response" in queries


def test_flag_off_disables_prioritization_by_default(monkeypatch):
    # IC §16 gating: the edge-retrieval prioritization is decision-affecting and
    # must ship INERT (default behaviour == current pure-OI sort) until replay-EV
    # evidence or an operator override turns it on.
    import feeds.search_news_monitor as m
    monkeypatch.setattr(m, "ENABLE_NEWS_EDGE_PRIORITIZATION", False)
    fillers = [
        _mkt(f"KXDIPLO{i}", f"diplomat zeta{i} treaty accord", oi=1000)
        for i in range(SEARCH_MAX_QUERIES + 5)
    ]
    edge = _mkt("KXTRUMPIRAN", "iran nuclear strike response", oi=1)
    # edge_series=None -> goes through the flag path
    queries = _markets_to_queries(fillers + [edge])
    assert "iran nuclear strike response" not in queries


def test_flag_on_uses_active_edge_set(monkeypatch):
    import feeds.search_news_monitor as m
    monkeypatch.setattr(m, "ENABLE_NEWS_EDGE_PRIORITIZATION", True)
    monkeypatch.setattr(m, "active_edge_series", lambda **kw: frozenset({"KXTRUMPIRAN"}))
    fillers = [
        _mkt(f"KXDIPLO{i}", f"diplomat zeta{i} treaty accord", oi=1000)
        for i in range(SEARCH_MAX_QUERIES + 5)
    ]
    edge = _mkt("KXTRUMPIRAN", "iran nuclear strike response", oi=1)
    queries = _markets_to_queries(fillers + [edge])
    assert "iran nuclear strike response" in queries


def test_non_edge_low_oi_market_still_crowded_out():
    # Guards INV-6/7: the edge-priority change must be targeted. A NON-edge
    # low-OI market must still lose its slot to high-OI markets — we are
    # concentrating retrieval on proven-edge series, NOT broadening coverage
    # of every illiquid market.
    fillers = [
        _mkt(f"KXDIPLO{i}", f"diplomat zeta{i} treaty accord", oi=1000)
        for i in range(SEARCH_MAX_QUERIES + 5)
    ]
    non_edge = _mkt("KXRANDOMPOLICY", "obscure ordinance hearing notice", oi=1)

    queries = _markets_to_queries(fillers + [non_edge], edge_series=frozenset({"KXTRUMPIRAN"}))

    assert "obscure ordinance hearing notice" not in queries
    assert len(queries) <= SEARCH_MAX_QUERIES


def test_query_builder_uses_market_question_when_title_is_generic():
    market = SimpleNamespace(
        title="Democratic Party",
        question="U.S House Midterm Winner",
        subtitle="2026 election",
        open_interest=1000,
        yes_price=50,
        series_ticker="polymarket_us",
        ticker="paccc-usho-midterms-2026-11-03-dem",
    )

    queries = _markets_to_queries([market])

    assert queries == ["democratic party house midterm"]


def test_query_builder_skips_fed_rate_markets_as_economic_noise():
    market = SimpleNamespace(
        title="Maintains rate",
        question="Fed Decision in June",
        open_interest=1000,
        yes_price=50,
        series_ticker="polymarket_us",
        ticker="rdc-usfed-fomc-2026-06-17-maintains",
    )

    assert _markets_to_queries([market]) == []


def test_zero_liquidity_markets_still_rank_by_uncertainty():
    decided = SimpleNamespace(
        title="Candidate Decided",
        question="Remote House Primary Winner",
        open_interest=0,
        yes_price=95,
        series_ticker="polymarket_us",
        ticker="remote-primary-decided",
    )
    contested = SimpleNamespace(
        title="Candidate Tossup",
        question="Competitive House Primary Winner",
        open_interest=0,
        yes_price=51,
        series_ticker="polymarket_us",
        ticker="competitive-primary-tossup",
    )

    queries = _markets_to_queries([decided, contested])

    assert queries[0] == "candidate tossup competitive house"


def test_source_hint_query_lane_default_off_excludes_site_scoped_queries():
    market = _mkt(
        "KXTRUMPIRAN",
        "Will Trump visit Iran?",
        oi=1000,
        settlement_sources=(
            SettlementSource(label="Associated Press", url="https://apnews.com/"),
        ),
    )

    queries = _markets_to_queries(
        [market],
        market_source_hint_query_mode="off",
        market_source_hint_query_cap=2,
    )

    assert queries == ["trump visit iran"]


def test_source_hint_query_lane_shadow_excludes_site_scoped_queries():
    market = _mkt(
        "KXTRUMPIRAN",
        "Will Trump visit Iran?",
        oi=1000,
        settlement_sources=(
            SettlementSource(label="Associated Press", url="https://apnews.com/"),
        ),
    )

    queries = _markets_to_queries(
        [market],
        market_source_hint_query_mode="shadow",
        market_source_hint_query_cap=2,
    )

    assert queries == ["trump visit iran"]


def test_source_hint_query_lane_production_adds_capped_site_scoped_queries():
    market = _mkt(
        "KXTRUMPIRAN",
        "Will Trump visit Iran?",
        oi=1000,
        settlement_sources=(
            SettlementSource(label="Associated Press", url="https://apnews.com/"),
            SettlementSource(label="Reuters", url="https://reuters.com/"),
        ),
    )

    queries = _markets_to_queries(
        [market],
        market_source_hint_query_mode="production",
        market_source_hint_query_cap=1,
    )

    assert queries == ["trump visit iran", "site:apnews.com trump visit iran"]


def test_source_hint_query_results_are_tagged_before_callback():
    item = NewsItem(headline="Trump visits Iran", url="https://apnews.com/story", source="AP")

    tagged = _tag_source_hint_item(
        item,
        "site:apnews.com trump visit iran",
        "production",
    )

    assert tagged is item
    assert tagged.retrieval_mode == "source_hint"
    assert tagged.source_hint_query == "site:apnews.com trump visit iran"
    assert tagged.source_hint_domain == "apnews.com"


@pytest.mark.asyncio
async def test_search_monitor_shadow_does_not_poll_source_hint_queries(tmp_path, monkeypatch):
    import feeds.search_news_monitor as search

    queried = []
    metadata_calls = 0

    async def callback(_item):
        pytest.fail("empty poll must not invoke the news callback")

    async def fake_poll(url, _callback, _seen):
        queried.append(url)

    def get_series_metadata():
        nonlocal metadata_calls
        metadata_calls += 1
        return {}

    async def stop_after_cycle(_seconds):
        raise StopAfterOneSearchCycle

    market = _mkt(
        "KXTRUMPIRAN",
        "Will Trump visit Iran?",
        oi=1000,
        settlement_sources=(
            SettlementSource(label="Associated Press", url="https://apnews.com/"),
        ),
    )
    monkeypatch.setattr(search, "MARKET_SOURCE_HINTS_QUERY_MODE", "shadow")
    monkeypatch.setattr(search, "ENABLE_MARKET_FIRST_QUERY_SHADOW", True)
    monkeypatch.setattr(search, "MARKET_SOURCE_HINTS_QUERY_CAP", 2)
    monkeypatch.setattr(search, "_enabled_search_engines", lambda: [("test", lambda query: query)])
    monkeypatch.setattr(search, "poll_feed", fake_poll)
    monkeypatch.setattr(search.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopAfterOneSearchCycle):
        await search.run_search_news_monitor(
            callback,
            get_markets=lambda: [market],
            get_series_metadata=get_series_metadata,
            poll_interval=1,
            seen_state_path=tmp_path / "search_seen_ids.json",
        )

    assert metadata_calls == 0
    assert queried == ["trump visit iran"]


def test_search_seen_state_path_is_distinct_from_rss_path():
    from feeds.rss_monitor import RSS_SEEN_STATE_PATH
    from feeds.search_news_monitor import SEARCH_SEEN_STATE_PATH

    assert SEARCH_SEEN_STATE_PATH != RSS_SEEN_STATE_PATH
    assert SEARCH_SEEN_STATE_PATH.name == "search_seen_ids.json"


@pytest.mark.asyncio
async def test_search_monitor_restart_restores_seen_ids_and_delivers_older_new_item(
    tmp_path, monkeypatch
):
    import feeds.search_news_monitor as search

    first = SimpleNamespace(
        link="https://example.test/first",
        title="first",
        summary="",
        published="2026-07-24T06:00:00Z",
    )
    older_second = SimpleNamespace(
        link="https://example.test/older-second",
        title="older second",
        summary="",
        published="2026-07-24T05:00:00Z",
    )
    entries = [[first], [first, older_second]]
    monkeypatch.setattr(
        "feeds.rss_monitor.feedparser.parse",
        lambda _url: _parsed(*entries.pop(0)),
    )
    monkeypatch.setattr(search, "DISABLED_SOURCE_FAMILIES", frozenset({"bing_news_query"}))
    monkeypatch.setattr(search, "_search_articles_cap", 3)
    assert len(search._enabled_search_engines()) == 1

    delivered = []
    seen_state_path = tmp_path / "search_seen_ids.json"
    await _run_one_search_cycle(delivered, seen_state_path, monkeypatch)
    await _run_one_search_cycle(delivered, seen_state_path, monkeypatch)

    assert [item.headline for item in delivered] == ["first", "older second"]
