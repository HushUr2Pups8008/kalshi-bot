"""The maker shadow records a bid and prices a tape fill. It does not order."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from trading.fees import FeeRole
from trading.venue import Venue
from utils.event_news_grok_edge import _store_decision
from utils import event_news_maker_shadow as maker_shadow
from utils.event_news_maker_shadow import (
    fee_saved,
    first_fill_cents,
    quoted_fee,
    resting_bid_cents,
    settled_fee_net,
    yes_price_cents,
)

WHEN = datetime(2026, 10, 22, 13, 50, tzinfo=timezone.utc)
PM_WHEN = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


def test_resting_bid_is_one_cent_under_the_ask_and_stops_at_the_band() -> None:
    assert resting_bid_cents(61) == 60
    assert resting_bid_cents(36) is None
    assert resting_bid_cents(True) is None
    assert resting_bid_cents(None) is None


def test_yes_bid_fills_at_or_below_and_no_bid_fills_on_the_complement() -> None:
    """Optimistic and not queue-aware: the first print through the bid is the fill."""
    assert first_fill_cents(({"yes_price_cents": 61}, {"yes_price_cents": 60}), "yes", 60) == 60
    assert first_fill_cents(({"yes_price_cents": 61},), "yes", 60) is None
    assert first_fill_cents(({"yes_price_cents": 16}, {"yes_price_cents": 17}), "no", 83) == 17
    assert first_fill_cents(({"yes_price_cents": 16},), "no", 83) is None
    assert first_fill_cents(({"yes_price_dollars": "0.6000"},), "yes", 60) == 60
    assert yes_price_cents("0.6100") == 61
    assert yes_price_cents("0.615") is None


def test_fee_saved_is_the_taker_ask_minus_the_maker_bid() -> None:
    """8 contracts at 61c versus a 60c maker bid. Kalshi ceil is a centicent."""
    saved = fee_saved(61, 60, 8, Venue.KALSHI, WHEN)
    taker = quoted_fee(61, 8, FeeRole.TAKER, Venue.KALSHI, WHEN)
    maker = quoted_fee(60, 8, FeeRole.MAKER, Venue.KALSHI, WHEN)
    assert taker == Decimal("0.1333")
    assert maker == Decimal("0.0336")
    assert saved == Decimal("0.0997")
    assert fee_saved(50, 49, 4, Venue.POLYMARKET_US, PM_WHEN) == Decimal("0.07")


def test_settled_fee_net_pays_the_maker_fee_only_when_the_side_wins() -> None:
    won = settled_fee_net("yes", "yes", 60, Decimal("0.0336"), 8)
    lost = settled_fee_net("no", "yes", 60, Decimal("0.0336"), 8)
    assert won == Decimal("8") - Decimal("4.80") - Decimal("0.0336")
    assert lost == Decimal("-4.8336")
    assert settled_fee_net("scalar", "yes", 60, Decimal("0.0336"), 8) is None


def test_candidate_log_records_the_bid_and_the_database_ignores_it(tmp_path: Path) -> None:
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
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        columns = [info[1] for info in conn.execute("PRAGMA table_info(paper_candidates)")]
    assert "maker_limit_cents" not in columns
