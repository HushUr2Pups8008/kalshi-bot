from __future__ import annotations

import json

from scripts.simulations import lever_a1_plus_candidate_feed_sizing as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_feed_class_mapping():
    assert audit.feed_class("News - The White House") == "government_bulletin"
    assert audit.feed_class("Breaking Defense") == "specialist_analyst"
    assert audit.feed_class("price_fade") == "market_microstructure"
    assert audit.feed_class("Reuters") == "mainstream_news"


def test_candidate_feed_sizing_counts_and_latency(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            {"type": "MATCH_DIAGNOSTIC", "source": "Breaking Defense", "ticker": "KX1"},
            {"type": "OPPORTUNITY", "source": "Breaking Defense", "ticker": "KX1"},
            {"type": "PAPER_TRADE", "signal_source": "Breaking Defense", "ticker": "KX1"},
            {"type": "ANALYSIS_REJECTED", "source": "Breaking Defense", "age_seconds": 10},
            {"type": "ANALYSIS_REJECTED", "source": "Breaking Defense", "age_seconds": 30},
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])
    specialist = next(row for row in report["class_rows"] if row["feed_class"] == "specialist_analyst")

    assert specialist["match_diagnostic"] == 1
    assert specialist["opportunity"] == 1
    assert specialist["paper_trade"] == 1
    assert specialist["latency"]["median_sec"] == 20
