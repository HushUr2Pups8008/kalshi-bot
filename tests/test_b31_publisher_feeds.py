"""B3.1 high-yield publisher-desk RSS additions (Track B / PROFIT-EDGE-004).

The 13 series that actually generate OPPORTUNITY records are all US-political /
geopolitical (KXTRUMPMENTION, KXTXRUNOFFENDORSE, KXTRUMPENDORSE, KXLUTNICKOUT,
KXFISAEXTEND, KXNEWTARIFFS, KXCHINAANNOUNCE, ...). The top opportunity publishers
(NYT #1, Guardian largest-by-desk) were configured only for their World desks, and
The Hill — the canonical Congress / endorsement / cabinet beat — was absent
entirely. This batch adds the missing US-politics desks of already-proven
publishers: pure additive coverage of the highest-converting sources, no
decision-logic change (the matcher/LLM/gate pipeline is untouched).

Two load-bearing invariants this test pins:

  1. **Freshness override is mandatory.** `EARLY_MAX_NEWS_AGE_BY_SOURCE` defaults to
     300s (5 min); at that cutoff "virtually nothing would pass" for publisher RSS
     (which lags minutes-to-hours), so every publisher feed needs a 1800s override
     or it is *dead-on-arrival* — all items stale-dropped at intake. A feed added to
     RSS_FEEDS without its override silently ingests ~nothing. This test fails in
     exactly that case.

  2. **No source-label collision.** `_source_name` in feeds/rss_monitor.py uses the
     feed.title verbatim as the source string. Politico already owns "Politics" and
     WaPo's desks publish generic "Politics"/"National" titles — adding WaPo direct
     would conflate two publishers in source-yield attribution, so WaPo was
     deferred (it still flows via the Google-News relay as "Washington Post"). The
     five added here carry distinct titles confirmed by live probe.
"""
from __future__ import annotations

from config import RSS_FEEDS, EARLY_MAX_NEWS_AGE_BY_SOURCE

# (feed url, exact feed.title source string) -- titles confirmed by live feedparser
# probe on 2026-05-30 (HTTP 200, bozo=False, fresh items).
_B31_ADDITIONS = [
    ("https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "NYT > U.S. > Politics"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/US.xml", "NYT > U.S. News"),
    ("https://thehill.com/news/feed/", "Just In News"),
    ("https://thehill.com/homenews/senate/feed/", "Senate News"),
    ("https://www.theguardian.com/us-news/rss", "US news | The Guardian"),
]


def test_b31_publisher_desks_present_in_rss_feeds():
    feeds = set(RSS_FEEDS)
    for url, _src in _B31_ADDITIONS:
        assert url in feeds, f"{url} missing from RSS_FEEDS (B3.1 high-yield desk)"


def test_b31_additions_have_freshness_override():
    # Without a 1800s override these fall to the 300s default and ingest ~nothing.
    for _url, src in _B31_ADDITIONS:
        assert EARLY_MAX_NEWS_AGE_BY_SOURCE.get(src) == 1800, (
            f"source {src!r} needs a 1800s EARLY_MAX_NEWS_AGE_BY_SOURCE override "
            f"or the feed is dead-on-arrival (300s default drops all publisher RSS)"
        )


def test_b31_no_source_string_collision():
    # New desks must carry distinct labels; 'Politics' (Politico) / 'National' are
    # the WaPo-direct trap we deferred -- they must NOT appear among the new sources.
    from config import DISABLED_NEWS_SOURCES, SOURCE_PRIORITY_TIERS

    new_srcs = [s for _u, s in _B31_ADDITIONS]
    assert len(new_srcs) == len(set(new_srcs)), "duplicate source label in B3.1 batch"
    assert "Politics" not in new_srcs, "collides with Politico source label"
    assert "National" not in new_srcs, "ambiguous WaPo-style generic label"
    # A new desk must not silently shadow another configured source surface.
    for src in new_srcs:
        assert src not in DISABLED_NEWS_SOURCES, f"{src!r} is in DISABLED_NEWS_SOURCES"
        assert src not in SOURCE_PRIORITY_TIERS, f"{src!r} collides with a priority-tier source"
