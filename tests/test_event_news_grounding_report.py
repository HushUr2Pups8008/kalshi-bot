"""Grounding report recommends live only after 20 resolved vetoes."""

from __future__ import annotations

import json
from decimal import Decimal

from scripts.event_news_grounding_report import (
    format_grounding_report,
    grounding_report,
)


def test_empty_log_does_not_recommend_live(tmp_path) -> None:
    report = grounding_report(log_path=tmp_path / "missing.jsonl")
    assert report["n"] == 0
    assert report["precision"] == "not_measured"
    assert report["vetoed_fee_net"] == "not_measured"
    assert report["recommend_live"] is False
    text = format_grounding_report(report)
    assert "recommend live: no" in text
    assert "at least 20" in text


def test_precision_counts_vetoes_that_would_have_lost(tmp_path) -> None:
    path = tmp_path / "grounding.jsonl"
    rows = [
        {
            "kind": "veto",
            "ticker": "KXTRUTHSOCIAL-26OCT03-B129",
            "verdict": "no",
            "response_id": "resp-1",
            "sources": ["https://example.test/a"],
            "outcome": None,
        },
        {
            "kind": "veto",
            "ticker": "KXTRUMPSAY-26SEP14-UKRA",
            "verdict": "no",
            "response_id": "resp-2",
            "sources": ["https://example.test/b"],
            "outcome": None,
        },
        {
            "kind": "watch",
            "ticker": "KXTRUMPSAY-26OCT19-GOLF",
            "verdict": "ok",
            "response_id": "resp-3",
            "sources": [],
            "outcome": None,
        },
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    report = grounding_report(
        log_path=path,
        outcomes={
            "KXTRUTHSOCIAL-26OCT03-B129": Decimal("-7.5226"),
            "KXTRUMPSAY-26SEP14-UKRA": Decimal("3.832"),
        },
    )
    assert report["n_vetoed"] == 2
    assert report["n"] == 2
    assert report["precision"] == 0.5
    assert report["vetoed_fee_net"] == Decimal("-3.6906")
    assert report["recommend_live"] is False
