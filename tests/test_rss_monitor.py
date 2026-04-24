"""Unit tests for feeds/rss_monitor helpers.

Covers `_entry_source`, which extracts the real publisher when a feed
aggregator (Google News RSS) populates `<source>` at the item level,
and falls back to the feed-level label for regular publisher feeds.
"""

from types import SimpleNamespace

from feeds.rss_monitor import _entry_source


def test_entry_source_prefers_entry_source_title_attribute():
    # feedparser's FeedParserDict exposes nested source via .title
    entry = SimpleNamespace(source=SimpleNamespace(title="Reuters"))
    assert _entry_source(entry, fallback='"query" - Google News') == "Reuters"


def test_entry_source_prefers_entry_source_title_dict_key():
    # Some parsers surface the same data as a plain dict
    entry = SimpleNamespace(source={"title": "AP News"})
    assert _entry_source(entry, fallback='"query" - Google News') == "AP News"


def test_entry_source_falls_back_when_entry_has_no_source():
    entry = SimpleNamespace()
    assert _entry_source(entry, fallback="World news | The Guardian") == "World news | The Guardian"


def test_entry_source_falls_back_when_source_is_none():
    entry = SimpleNamespace(source=None)
    assert _entry_source(entry, fallback="NYT > World News") == "NYT > World News"


def test_entry_source_falls_back_when_title_is_empty_string():
    entry = SimpleNamespace(source=SimpleNamespace(title=""))
    assert _entry_source(entry, fallback="BBC News") == "BBC News"


def test_entry_source_falls_back_when_title_is_whitespace_only():
    entry = SimpleNamespace(source=SimpleNamespace(title="   \n  "))
    assert _entry_source(entry, fallback="BBC News") == "BBC News"


def test_entry_source_strips_whitespace_from_title():
    entry = SimpleNamespace(source=SimpleNamespace(title="  Reuters  "))
    assert _entry_source(entry, fallback="fallback") == "Reuters"


def test_entry_source_handles_non_string_title_defensively():
    # feedparser is usually strict about types but this guards against oddities
    entry = SimpleNamespace(source=SimpleNamespace(title=12345))
    # Coerces to str -> "12345" (non-empty after strip) -> returned
    assert _entry_source(entry, fallback="fallback") == "12345"


def test_entry_source_dict_access_with_missing_title_key_falls_back():
    entry = SimpleNamespace(source={"href": "https://reuters.com"})  # no "title"
    assert _entry_source(entry, fallback="Al Jazeera") == "Al Jazeera"
