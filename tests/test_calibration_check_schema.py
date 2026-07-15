"""Tests for the CALIBRATION_CHECK log schema (S1.6)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from utils.logger import CALIBRATION_CHECK_REQUIRED_FIELDS, TradeLogger, TradeLogStore


def _valid_kwargs() -> dict:
    return {
        "market_ticker": "KXTEST-CALIB-1",
        "lane": "fast",
        "lane_estimate": 0.65,
        "final_resolution": 1.0,
        "error": 0.35,
    }


def _make_logger(tmp_path: Path) -> TradeLogger:
    log_file = tmp_path / "trades.jsonl"
    log_file.touch()
    logger = TradeLogger.__new__(TradeLogger)
    logger._store = TradeLogStore(live_path=log_file)
    return logger


# ── Required-field tuple matches contract Section 8 ──────────────────────────

def test_required_fields_match_contract():
    assert set(CALIBRATION_CHECK_REQUIRED_FIELDS) == {
        "market_ticker",
        "lane",
        "lane_estimate",
        "final_resolution",
        "error",
    }


def test_required_fields_count():
    assert len(CALIBRATION_CHECK_REQUIRED_FIELDS) == 5


# ── Emitted payload shape ─────────────────────────────────────────────────────

def test_payload_contains_all_required_fields(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    logger.log_calibration_check(**_valid_kwargs())
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    for field in CALIBRATION_CHECK_REQUIRED_FIELDS:
        assert field in record, f"Missing required field: {field}"


def test_payload_type_field(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    logger.log_calibration_check(**_valid_kwargs())
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    assert record["type"] == "CALIBRATION_CHECK"


def test_payload_exact_fields(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    logger.log_calibration_check(**_valid_kwargs())
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    expected_keys = set(CALIBRATION_CHECK_REQUIRED_FIELDS) | {"type", "ts"}
    assert set(record.keys()) == expected_keys


def test_payload_includes_optional_venue_when_present(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    kwargs = _valid_kwargs()
    kwargs["venue"] = "polymarket_us"
    logger.log_calibration_check(**kwargs)
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    assert record["venue"] == "polymarket_us"


# ── Numeric rounding ──────────────────────────────────────────────────────────

def test_numeric_fields_rounded_to_4dp(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    kwargs = _valid_kwargs()
    kwargs["lane_estimate"] = 0.123456789
    kwargs["final_resolution"] = 0.999999
    kwargs["error"] = 0.876543211
    logger.log_calibration_check(**kwargs)
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    assert record["lane_estimate"] == round(0.123456789, 4)
    assert record["final_resolution"] == round(0.999999, 4)
    assert record["error"] == round(0.876543211, 4)


# ── Field values are preserved correctly ─────────────────────────────────────

def test_lane_values_accepted(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    for lane in ("fast", "accumulation", "structural"):
        kwargs = _valid_kwargs()
        kwargs["lane"] = lane
        logger.log_calibration_check(**kwargs)
    lines = (tmp_path / "trades.jsonl").read_text().strip().splitlines()
    lanes = [json.loads(l)["lane"] for l in lines]
    assert lanes == ["fast", "accumulation", "structural"]


def test_market_ticker_preserved(tmp_path):
    import json
    logger = _make_logger(tmp_path)
    logger.log_calibration_check(**_valid_kwargs())
    record = json.loads((tmp_path / "trades.jsonl").read_text().strip())
    assert record["market_ticker"] == "KXTEST-CALIB-1"


# ── Missing required fields raise TypeError ───────────────────────────────────

@pytest.mark.parametrize("drop_field", list(CALIBRATION_CHECK_REQUIRED_FIELDS))
def test_missing_required_field_raises(tmp_path, drop_field):
    logger = _make_logger(tmp_path)
    kwargs = _valid_kwargs()
    del kwargs[drop_field]
    with pytest.raises(TypeError):
        logger.log_calibration_check(**kwargs)


def test_settlement_lineage_is_optional_on_calibration_rows(tmp_path):
    logger = _make_logger(tmp_path)
    settled_at = datetime.now(timezone.utc).isoformat()
    outbox_id = "a" * 64
    logger.log_calibration_check(
        **_valid_kwargs(),
        outbox_id=outbox_id,
        ts=settled_at,
    )
    logger.log_calibration_observation(
        trade_id="trade-1",
        ticker="KXTEST-CALIB-1",
        market_prefix="KXTEST",
        side="yes",
        estimated_probability=0.65,
        realized_outcome=1,
        entry_price_cents=40.0,
        pnl_dollars=15.0,
        cost_dollars=10.0,
        llm_magnitude="moderate",
        llm_confidence=0.81,
        signal_source="wire:test",
        ts_entry="2026-07-14T21:00:00+00:00",
        ts_resolved=settled_at,
        outbox_id=outbox_id,
        ts=settled_at,
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "trades.jsonl").read_text().splitlines()
    ]
    assert [record["outbox_id"] for record in records] == [outbox_id, outbox_id]
    assert [record["ts"] for record in records] == [settled_at, settled_at]


def test_paper_resolution_accepts_void_lineage_and_preserves_legacy_callers(tmp_path):
    logger = _make_logger(tmp_path)
    logger.log_paper_resolution(
        trade_id="legacy-trade",
        ticker="KXLEGACY",
        resolved_yes=True,
        pnl_dollars=5.0,
    )
    settled_at = datetime.now(timezone.utc).isoformat()
    outbox_id = "b" * 64
    logger.log_paper_resolution(
        trade_id="void-trade",
        ticker="KXVOID",
        resolved_yes=None,
        terminal_state="void",
        pnl_dollars=2.5,
        outbox_id=outbox_id,
        ts=settled_at,
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "trades.jsonl").read_text().splitlines()
    ]
    assert records[0]["resolved_yes"] is True
    assert "outbox_id" not in records[0]
    assert "terminal_state" not in records[0]
    assert records[1]["resolved_yes"] is None
    assert records[1]["terminal_state"] == "void"
    assert records[1]["outbox_id"] == outbox_id
    assert records[1]["ts"] == settled_at
