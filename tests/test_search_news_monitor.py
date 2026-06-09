"""Unit tests for feeds/search_news_monitor query construction.

Covers `_markets_to_queries`, specifically the news-edge prioritization
(option A, 2026-05-30): markets on series with demonstrated news-edge must
always be turned into search queries, even when high-open-interest macro /
"mention" markets would otherwise fill all SEARCH_MAX_QUERIES slots first.
The 158 lifetime opportunities clustered entirely on these event-driven
political/geopolitical series; letting liquid-but-no-edge markets crowd them
out of the query budget starves the only markets the bot actually trades.
"""

from types import SimpleNamespace

from feeds.search_news_monitor import _markets_to_queries, SEARCH_MAX_QUERIES


def _mkt(series, title, oi, price=50):
    return SimpleNamespace(
        title=title,
        open_interest=oi,
        yes_price=price,
        series_ticker=series,
        ticker=f"{series}-26JUN05-X",
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
