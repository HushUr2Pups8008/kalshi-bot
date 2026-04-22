"""Tests for the CALIBRATION_CHECK log schema (S1.6)."""

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
