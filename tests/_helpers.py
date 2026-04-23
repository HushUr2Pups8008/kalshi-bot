"""
Shared test helpers.

Intentionally narrow: only symbols used by multiple test modules live here.
Module-specific fixtures stay local to their test file. A generic
`_make_market` helper is not provided because the three callers
(`test_signal_analyzer`, `test_market_matcher`, `test_main_pipeline`) each
construct markets with different shapes — a MagicMock, a configurable
KalshiMarket factory, and a fixed KalshiMarket — that are not interchangeable.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from feeds import NewsItem


def make_news(headline: str, body: str = "") -> NewsItem:
    """Construct a canonical NewsItem for tests that don't care about fields
    other than headline and body."""
    return NewsItem(
        headline=headline,
        url="https://example.com/story",
        source="Reuters",
        published=datetime.now(timezone.utc),
        body=body,
        item_id="news-1",
    )


def make_tmp_dir(prefix: str) -> Path:
    """Create a unique directory under tests/_tmp_<prefix>/<uuid>. Caller is
    responsible for cleanup via cleanup_tmp_dir()."""
    root = Path(__file__).resolve().parent / f"_tmp_{prefix}"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_tmp_dir(path: Path) -> None:
    """Remove a directory tree created by make_tmp_dir(); missing is ignored."""
    shutil.rmtree(path, ignore_errors=True)


def write_jsonl(path: Path, records, *, ensure_dir: bool = True) -> None:
    """Write ``records`` as JSONL at ``path``.

    Strings pass through verbatim (for pre-formatted lines / malformed-input
    tests); other objects are ``json.dumps``'d. Parent directories are
    created by default.
    """
    if ensure_dir:
        path.parent.mkdir(parents=True, exist_ok=True)
    lines = [record if isinstance(record, str) else json.dumps(record)
             for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
