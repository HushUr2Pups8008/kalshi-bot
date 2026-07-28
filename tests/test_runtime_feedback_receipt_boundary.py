"""Receipt-attested boundaries for runtime feedback multipliers."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from config import CREDIBILITY_MAX_MULT, CREDIBILITY_MIN_MULT, CREDIBILITY_MIN_SAMPLE
from tasks.stats.keyword_stats import KeywordStats, MIN_SAMPLES
from tasks.stats.source_credibility import SourceCredibility
from trading.settlement_store import CanonicalDeliveryCompleteOutbox


def _canonical_store(
    events: tuple[CanonicalDeliveryCompleteOutbox, ...] = (),
    failure: Exception | None = None,
):
    calls: list[tuple[Path, bool, datetime]] = []

    class Store:
        def __init__(self, db_path: Path, *, read_only: bool = False) -> None:
            calls.append((Path(db_path), read_only, datetime.now(timezone.utc)))

        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def canonical_delivery_complete_outbox_payloads(self, *, now: datetime):
            assert now.tzinfo is not None
            if failure is not None:
                raise failure
            return events

    Store.calls = calls
    return Store


def _event(
    trade_id: str,
    *,
    source: str = "Reuters",
    series_ticker: str = "KXTEST",
    won: bool = True,
    keyword: str = "ceasefire",
    keyword_correct: bool = True,
    settled_at: str | None = None,
) -> CanonicalDeliveryCompleteOutbox:
    return CanonicalDeliveryCompleteOutbox(
        outbox_id=f"outbox-{trade_id}",
        trade_id=trade_id,
        payload_json=json.dumps(
            {
                "trade_id": trade_id,
                "signal_source": source,
                "series_ticker": series_ticker,
                "won": won,
                "settled_at": settled_at or datetime.now(timezone.utc).isoformat(),
                "keyword_outcomes": [
                    {
                        "keyword": keyword,
                        "direction": "yes",
                        "correct": keyword_correct,
                    }
                ],
            }
        ),
    )


def _source_snapshot(tracker: SourceCredibility) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in tracker._conn.execute(
            """
            SELECT source, wins, losses, total, accuracy, multiplier, last_updated
            FROM source_credibility
            ORDER BY source
            """
        ).fetchall()
    )


def _seed_keyword_telemetry(
    db_path: Path,
    *,
    keyword: str,
    series_ticker: str,
    correct: bool,
    count: int = MIN_SAMPLES,
) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE keyword_outcomes (
                keyword TEXT NOT NULL,
                series_ticker TEXT NOT NULL,
                correct INTEGER NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO keyword_outcomes VALUES (?, ?, ?)",
            [(keyword, series_ticker, int(correct))] * count,
        )
        return tuple(
            conn.execute(
                "SELECT keyword, series_ticker, correct FROM keyword_outcomes ORDER BY rowid"
            ).fetchall()
        )


def _keyword_snapshot(db_path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(db_path) as conn:
        return tuple(
            conn.execute(
                "SELECT keyword, series_ticker, correct FROM keyword_outcomes ORDER BY rowid"
            ).fetchall()
        )


def test_source_telemetry_cache_cannot_activate_multiplier_without_canonical_delivery(
    tmp_path: Path,
):
    tracker = SourceCredibility(tmp_path / "paper.db")
    tracker._conn.execute(
        """
        INSERT INTO source_credibility
            (source, wins, losses, total, accuracy, multiplier, last_updated)
        VALUES ('Reuters', 10, 0, 10, 1.0, 1.5, '2026-07-28T00:00:00+00:00')
        """
    )
    tracker._conn.commit()
    before = _source_snapshot(tracker)
    store = _canonical_store()

    with patch("tasks.stats.source_credibility.SettlementStore", store, create=True):
        assert tracker.get_multiplier("Reuters") == 1.0

    assert store.calls and store.calls[0][1] is True
    assert _source_snapshot(tracker) == before
    tracker._conn.close()


def test_source_multiplier_uses_canonical_delivery_without_blending_or_mutation(
    tmp_path: Path,
):
    tracker = SourceCredibility(tmp_path / "paper.db")
    tracker._conn.execute(
        """
        INSERT INTO source_credibility
            (source, wins, losses, total, accuracy, multiplier, last_updated)
        VALUES ('Reuters', 0, 10, 10, 0.0, 0.5, '2026-07-28T00:00:00+00:00')
        """
    )
    tracker._conn.commit()
    before = _source_snapshot(tracker)
    events = tuple(
        _event(str(index), won=index < 8)
        for index in range(CREDIBILITY_MIN_SAMPLE)
    )
    store = _canonical_store(events)
    expected = CREDIBILITY_MIN_MULT + 0.8 * (
        CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT
    )

    with patch("tasks.stats.source_credibility.SettlementStore", store, create=True):
        assert tracker.get_multiplier("Reuters") == pytest.approx(expected)

    assert _source_snapshot(tracker) == before
    tracker._conn.close()


def test_source_multiplier_fails_neutral_when_canonical_state_is_unreadable(
    tmp_path: Path,
):
    tracker = SourceCredibility(tmp_path / "paper.db")
    tracker._conn.execute(
        """
        INSERT INTO source_credibility
            (source, wins, losses, total, accuracy, multiplier, last_updated)
        VALUES ('Reuters', 10, 0, 10, 1.0, 1.5, '2026-07-28T00:00:00+00:00')
        """
    )
    tracker._conn.commit()
    before = _source_snapshot(tracker)
    store = _canonical_store(failure=RuntimeError("canonical state invalid"))

    with patch("tasks.stats.source_credibility.SettlementStore", store, create=True):
        assert tracker.get_multiplier("Reuters") == 1.0

    assert _source_snapshot(tracker) == before
    tracker._conn.close()


def test_keyword_telemetry_cache_cannot_activate_multiplier_without_canonical_delivery(
    tmp_path: Path,
):
    db_path = tmp_path / "paper.db"
    before = _seed_keyword_telemetry(
        db_path,
        keyword="ceasefire",
        series_ticker="KXTEST",
        correct=True,
    )
    store = _canonical_store()

    with patch("tasks.stats.keyword_stats.SettlementStore", store, create=True):
        stats = KeywordStats(db_path)

    assert stats.get_multiplier("ceasefire", "KXTEST") == 1.0
    assert store.calls and store.calls[0][1] is True
    assert _keyword_snapshot(db_path) == before


def test_keyword_multiplier_uses_canonical_delivery_without_blending_or_mutation(
    tmp_path: Path,
):
    db_path = tmp_path / "paper.db"
    before = _seed_keyword_telemetry(
        db_path,
        keyword="ceasefire",
        series_ticker="KXTEST",
        correct=False,
    )
    events = tuple(
        _event(str(index), keyword_correct=index < 8)
        for index in range(MIN_SAMPLES)
    )
    store = _canonical_store(events)

    with patch("tasks.stats.keyword_stats.SettlementStore", store, create=True):
        stats = KeywordStats(db_path)

    assert stats.get_multiplier("ceasefire", "KXTEST") == pytest.approx(1.3)
    assert _keyword_snapshot(db_path) == before


def test_keyword_multiplier_fails_neutral_when_canonical_state_is_unreadable(
    tmp_path: Path,
):
    db_path = tmp_path / "paper.db"
    before = _seed_keyword_telemetry(
        db_path,
        keyword="ceasefire",
        series_ticker="KXTEST",
        correct=True,
    )
    store = _canonical_store(failure=RuntimeError("canonical state invalid"))

    with patch("tasks.stats.keyword_stats.SettlementStore", store, create=True):
        stats = KeywordStats(db_path)

    assert stats.get_multiplier("ceasefire", "KXTEST") == 1.0
    assert _keyword_snapshot(db_path) == before


def test_feedback_multipliers_fail_neutral_for_tampered_canonical_payload(
    tmp_path: Path,
):
    tampered = CanonicalDeliveryCompleteOutbox(
        outbox_id="outbox-tampered",
        trade_id="t1",
        payload_json='{"trade_id":"other"}',
    )

    source_tracker = SourceCredibility(tmp_path / "source.db")
    source_tracker._conn.execute(
        """
        INSERT INTO source_credibility
            (source, wins, losses, total, accuracy, multiplier, last_updated)
        VALUES ('Reuters', 10, 0, 10, 1.0, 1.5, '2026-07-28T00:00:00+00:00')
        """
    )
    source_tracker._conn.commit()
    source_store = _canonical_store((tampered,))
    with patch(
        "tasks.stats.source_credibility.SettlementStore", source_store, create=True
    ):
        assert source_tracker.get_multiplier("Reuters") == 1.0
    source_tracker._conn.close()

    keyword_db = tmp_path / "keyword.db"
    _seed_keyword_telemetry(
        keyword_db,
        keyword="ceasefire",
        series_ticker="KXTEST",
        correct=True,
    )
    keyword_store = _canonical_store((tampered,))
    with patch("tasks.stats.keyword_stats.SettlementStore", keyword_store, create=True):
        keyword_stats = KeywordStats(keyword_db)

    assert keyword_stats.get_multiplier("ceasefire", "KXTEST") == 1.0


def test_source_multiplier_receipt_binds_the_canonical_snapshot(tmp_path: Path):
    tracker = SourceCredibility(tmp_path / "paper.db")
    events = tuple(
        _event(str(index), won=index < 8)
        for index in range(CREDIBILITY_MIN_SAMPLE)
    )
    store = _canonical_store(events)

    with patch(
        "tasks.stats.source_credibility.SettlementStore", store, create=True
    ):
        multiplier, receipt = tracker.get_multiplier_with_receipt("Reuters")

    expected = CREDIBILITY_MIN_MULT + 0.8 * (
        CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT
    )
    assert multiplier == pytest.approx(expected)
    assert receipt.status == "canonical"
    assert receipt.applied_multiplier == pytest.approx(expected)
    assert receipt.effective_sample_count == CREDIBILITY_MIN_SAMPLE
    assert receipt.delivered_event_count == CREDIBILITY_MIN_SAMPLE
    assert len(receipt.canonical_basis_sha256 or "") == 64
    tracker._conn.close()


def test_keyword_multiplier_receipt_binds_the_canonical_snapshot(tmp_path: Path):
    db_path = tmp_path / "paper.db"
    events = tuple(
        _event(str(index), keyword_correct=index < 8)
        for index in range(MIN_SAMPLES)
    )
    store = _canonical_store(events)

    with patch("tasks.stats.keyword_stats.SettlementStore", store, create=True):
        stats = KeywordStats(db_path)
        multiplier, receipt = stats.get_multiplier_with_receipt("ceasefire", "KXTEST")

    assert multiplier == pytest.approx(1.3)
    assert receipt.status == "canonical"
    assert receipt.applied_multiplier == pytest.approx(1.3)
    assert receipt.effective_sample_count == MIN_SAMPLES
    assert receipt.delivered_event_count == MIN_SAMPLES
    assert len(receipt.canonical_basis_sha256 or "") == 64
