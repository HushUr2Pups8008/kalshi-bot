"""The maker shadow records a bid. It does not grade the tape and it does not order."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from utils import event_news_maker_shadow as maker_shadow
from utils.event_news_grok_edge import _store_decision
from utils.event_news_maker_shadow import resting_bid_cents

WHEN = datetime(2026, 10, 22, 13, 50, tzinfo=timezone.utc)


def test_resting_bid_is_one_cent_under_the_ask_and_stops_at_the_band() -> None:
    assert resting_bid_cents(61) == 60
    assert resting_bid_cents(36) is None
    assert resting_bid_cents(True) is None
    assert resting_bid_cents(None) is None


def test_candidate_log_records_the_bid_and_the_database_ignores_it(tmp_path: Path) -> None:
    """The bid is a log field. Queue-ahead size is not stored, so no tape fill is graded."""
    log_path = tmp_path / "candidates.jsonl"
    db_path = tmp_path / "paper.db"
    _store_decision(
        {"series": "KXTRUTHSOCIAL", "ticker": "KXTRUTHSOCIAL-26OCT24-T240", "yes_ask": 61},
        reason="edge",
        candidate_log_path=log_path,
        db_path=db_path,
        now=WHEN,
        side="yes",
        ask=61,
    )
    row = json.loads(log_path.read_text(encoding="utf-8"))
    assert row["maker_limit_cents"] == 60
    text = Path(maker_shadow.__file__).read_text(encoding="utf-8")
    assert "place_limit_order" not in text
    assert "api.x.ai" not in text
    assert "first_fill_cents" not in text
    with sqlite3.connect(db_path) as conn:
        columns = [info[1] for info in conn.execute("PRAGMA table_info(paper_candidates)")]
    assert "maker_limit_cents" not in columns
