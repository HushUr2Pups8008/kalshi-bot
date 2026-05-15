"""
Tests for field-mapping bugs in build_replay_dataset.py.

Covers the two bugs found in the 2026-05-10 POST_FIX_NEW corpus-readiness audit:
  Bug 1 — _paper_trade_rows() did not map paper_trades.llm_confidence -> corpus.confidence
  Bug 2 — _log_rows() had key-name mismatches for BLEND_DECISION, OPPORTUNITY, SKIPPED events

Authorized scorer-tooling-fix per Cycle-17C charter § "Sole exception to single variable".
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.edge_replay.build_replay_dataset import _log_rows, _paper_trade_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TICKER = "KXMOCK-26MAY10"
MARKETS = {
    TICKER: {
        "ticker": TICKER,
        "title": "Mock market",
        "series_ticker": "KXMOCK",
        "resolved_yes": True,
    }
}


def _make_paper_db(path: Path, *, llm_confidence: float | None = 0.85) -> None:
    """Create a minimal paper_trades DB with the llm_confidence column populated."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ts TEXT,
                ticker TEXT,
                side TEXT,
                contracts INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                edge REAL,
                signal_source TEXT,
                signal_type TEXT,
                news_class TEXT,
                signal_headline TEXT,
                pnl_dollars REAL,
                resolved INTEGER,
                fast_lane_p REAL,
                accumulation_p REAL,
                structural_p REAL,
                llm_confidence REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                't1', '2026-05-10T00:00:00+00:00', ?, 'yes', 1,
                0.70, 45.0, 0.25, 'Reuters', 'llm', 'news',
                'Headline', 0.50, 1, 0.65, 0.66, 0.67, ?
            )
            """,
            (TICKER, llm_confidence),
        )
        conn.commit()
    finally:
        conn.close()


def _make_log_file(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


# ---------------------------------------------------------------------------
# Bug 1 — llm_confidence -> confidence
# ---------------------------------------------------------------------------


def test_paper_trade_row_maps_llm_confidence_to_confidence(tmp_path):
    """_paper_trade_rows() must expose llm_confidence from DB as corpus.confidence."""
    db = tmp_path / "paper_trades.db"
    _make_paper_db(db, llm_confidence=0.85)

    rows = _paper_trade_rows(db, MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] == pytest.approx(0.85)


def test_paper_trade_row_confidence_is_none_when_db_column_is_null(tmp_path):
    """confidence must be None (not KeyError) when llm_confidence is NULL in DB."""
    db = tmp_path / "paper_trades.db"
    _make_paper_db(db, llm_confidence=None)

    rows = _paper_trade_rows(db, MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] is None


def test_paper_trade_row_dual_emits_market_yes_price_and_entry_price_cents(tmp_path):
    """F-11 P1-C: ``_paper_trade_rows`` reads from the live SQLite
    ``paper_trades.market_yes_price`` column (rename pending P1-B) and must
    emit BOTH the legacy ``market_yes_price`` key and the canonical
    ``entry_price_cents`` key on the generated replay row, with identical
    values. Downstream consumers that switched to the canonical key during
    P1-C must find the value without backward-fallback overhead.
    """
    db = tmp_path / "paper_trades.db"
    _make_paper_db(db, llm_confidence=0.75)

    rows = _paper_trade_rows(db, MARKETS)

    assert len(rows) == 1
    row = rows[0]
    assert "market_yes_price" in row, "legacy key must be emitted (P1-B not yet landed)"
    assert "entry_price_cents" in row, "canonical key must be dual-emitted (F-11 P1-C)"
    assert row["market_yes_price"] == row["entry_price_cents"], (
        "dual-emit values must be byte-identical -- they share one _as_float() resolution"
    )
    assert row["market_yes_price"] is not None, (
        "fixture seeds a non-null price; both keys must carry the value"
    )


# ---------------------------------------------------------------------------
# Bug 2a — BLEND_DECISION: blended_p -> model_prob, market_ticker -> ticker
# ---------------------------------------------------------------------------


def test_log_row_maps_blend_decision_keys(tmp_path):
    """BLEND_DECISION rows emit blended_p and market_ticker; builder must normalise both."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "BLEND_DECISION",
                "ts": "2026-05-10T00:01:00+00:00",
                "market_ticker": TICKER,
                "blended_p": 0.60,
                "market_price": 50.0,
                "source": "nyt",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.60)
    assert rows[0]["ticker"] == TICKER


# ---------------------------------------------------------------------------
# Bug 2b — OPPORTUNITY: model_probability -> model_prob
# ---------------------------------------------------------------------------


def test_log_row_maps_opportunity_model_probability(tmp_path):
    """OPPORTUNITY rows emit model_probability; builder must normalise to model_prob."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-05-10T00:02:00+00:00",
                "ticker": TICKER,
                "model_probability": 0.72,
                "market_yes_price": 48.0,
                "source": "bbc",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.72)


# ---------------------------------------------------------------------------
# Bug 2c — SKIPPED: market_price -> market_yes_price
# ---------------------------------------------------------------------------


def test_log_row_maps_skipped_market_price_to_market_yes_price(tmp_path):
    """SKIPPED rows emit market_price; builder must normalise to market_yes_price."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "SKIPPED",
                "ts": "2026-05-10T00:03:00+00:00",
                "ticker": TICKER,
                "estimated_prob": 0.55,
                "market_price": 55.0,
                "source": "ap",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["market_yes_price"] == pytest.approx(55.0)


# ---------------------------------------------------------------------------
# Bug 2d — estimated_probability alt variant
# ---------------------------------------------------------------------------


def test_log_row_maps_estimated_probability_alt_variant(tmp_path):
    """Some OPPORTUNITY variants emit estimated_probability; must normalise to model_prob."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-05-10T00:04:00+00:00",
                "ticker": TICKER,
                "estimated_probability": 0.68,
                "market_yes_price": 42.0,
                "source": "guardian",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.68)


# ---------------------------------------------------------------------------
# Canonical keys still win over aliases (regression guard)
# ---------------------------------------------------------------------------


def test_log_row_canonical_model_prob_beats_blended_p(tmp_path):
    """When model_prob is present alongside blended_p, canonical key must win."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-05-10T00:05:00+00:00",
                "ticker": TICKER,
                "model_prob": 0.80,
                "blended_p": 0.50,
                "market_yes_price": 45.0,
                "source": "ft",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.80)


def test_log_row_canonical_market_yes_price_beats_market_price(tmp_path):
    """When market_yes_price is present alongside market_price, canonical key must win."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "SKIPPED",
                "ts": "2026-05-10T00:06:00+00:00",
                "ticker": TICKER,
                "estimated_prob": 0.55,
                "market_yes_price": 44.0,
                "market_price": 99.0,
                "source": "ap",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["market_yes_price"] == pytest.approx(44.0)


# ---------------------------------------------------------------------------
# Reviewer findings (2026-05-10): canonical precedence + None-aware fallthrough
# ---------------------------------------------------------------------------


def test_log_row_canonical_model_prob_beats_estimated_prob(tmp_path):
    """model_prob must win over estimated_prob when both keys are present.

    Pre-fix: estimated_prob is listed first in the or-chain, so it shadows
    model_prob. Expected failure before the fix.
    """
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-05-10T01:00:00+00:00",
                "ticker": TICKER,
                "model_prob": 0.80,
                "estimated_prob": 0.50,
                "market_yes_price": 45.0,
                "source": "ft",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.80)


def test_log_row_model_prob_zero_not_dropped(tmp_path):
    """model_prob=0.0 must be preserved; or-chain falls through falsy values.

    Pre-fix: 0.0 is falsy so the or-chain skips it and picks up blended_p=0.5.
    Expected failure before the fix.
    """
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "OPPORTUNITY",
                "ts": "2026-05-10T01:01:00+00:00",
                "ticker": TICKER,
                "model_prob": 0.0,
                "blended_p": 0.5,
                "market_yes_price": 45.0,
                "source": "ft",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["model_prob"] == pytest.approx(0.0)


def test_log_row_market_yes_price_zero_not_dropped(tmp_path):
    """market_yes_price=0.0 must be preserved; or-chain falls through falsy values.

    Pre-fix: 0.0 is falsy so the or-chain skips it and picks up market_price=50.0.
    Expected failure before the fix.
    """
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "SKIPPED",
                "ts": "2026-05-10T01:02:00+00:00",
                "ticker": TICKER,
                "estimated_prob": 0.55,
                "market_yes_price": 0.0,
                "market_price": 50.0,
                "source": "ap",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["market_yes_price"] == pytest.approx(0.0)


def test_log_row_canonical_ticker_beats_market_ticker(tmp_path):
    """ticker must win over market_ticker when both keys are present (regression guard).

    The current or-chain already has ticker first, so this should pass pre-fix.
    """
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "BLEND_DECISION",
                "ts": "2026-05-10T01:03:00+00:00",
                "ticker": TICKER,
                "market_ticker": "KXALIAS-99",
                "blended_p": 0.60,
                "market_yes_price": 45.0,
                "source": "nyt",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["ticker"] == TICKER


# ---------------------------------------------------------------------------
# Stage 1b — confidence alias chain (2026-05-10)
# ---------------------------------------------------------------------------


def test_log_row_extracts_blend_decision_blended_confidence(tmp_path):
    """BLEND_DECISION emits blended_confidence; builder must expose it as confidence."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "BLEND_DECISION",
                "ts": "2026-05-10T02:00:00+00:00",
                "market_ticker": TICKER,
                "blended_p": 0.60,
                "blended_confidence": 0.75,
                "market_yes_price": 50.0,
                "source": "nyt",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] == pytest.approx(0.75)


def test_log_row_canonical_confidence_beats_aliases(tmp_path):
    """When confidence, blended_confidence, and llm_confidence all present, canonical wins."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "BLEND_DECISION",
                "ts": "2026-05-10T02:01:00+00:00",
                "market_ticker": TICKER,
                "blended_p": 0.60,
                "confidence": 0.80,
                "blended_confidence": 0.55,
                "llm_confidence": 0.30,
                "market_yes_price": 50.0,
                "source": "nyt",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] == pytest.approx(0.80)


def test_log_row_confidence_zero_not_dropped(tmp_path):
    """confidence=0.0 must be preserved; alias must not shadow it with blended_confidence."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "BLEND_DECISION",
                "ts": "2026-05-10T02:02:00+00:00",
                "market_ticker": TICKER,
                "blended_p": 0.60,
                "confidence": 0.0,
                "blended_confidence": 0.5,
                "market_yes_price": 50.0,
                "source": "nyt",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] == pytest.approx(0.0)


def test_log_row_confidence_absent_returns_none(tmp_path):
    """Row with no confidence-bearing field must yield confidence=None (not KeyError)."""
    log = tmp_path / "trades.jsonl"
    _make_log_file(
        log,
        [
            {
                "type": "SKIPPED",
                "ts": "2026-05-10T02:03:00+00:00",
                "ticker": TICKER,
                "estimated_prob": 0.55,
                "market_price": 55.0,
                "source": "ap",
            }
        ],
    )

    rows = _log_rows([log], MARKETS)

    assert len(rows) == 1
    assert rows[0]["confidence"] is None
