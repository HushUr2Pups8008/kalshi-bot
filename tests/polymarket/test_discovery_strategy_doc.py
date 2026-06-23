from __future__ import annotations

from pathlib import Path


def test_polymarket_discovery_strategy_documents_non_politics_filters():
    text = Path("docs/governance/polymarket_discovery_strategy.md").read_text(encoding="utf-8")

    required = [
        "replaces politics-only discovery",
        "active/open binary market",
        "volume or open interest",
        "public tags",
        "event title",
        "series title",
        "resolution source",
        "Public comments",
        "non-politics market without a resolution source",
    ]
    for phrase in required:
        assert phrase in text
