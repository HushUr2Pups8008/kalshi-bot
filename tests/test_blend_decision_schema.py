import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.logger import BLEND_DECISION_REQUIRED_FIELDS, TradeLogger


def _valid_blend_decision_kwargs() -> dict:
    return {
        "market_ticker": "KXTEST-26DEC31",
        "fast_lane_p": 0.612345,
        "fast_lane_confidence": 0.712345,
        "accumulation_p": 0.582345,
        "accumulation_confidence": 0.662345,
        "structural_p": 0.522345,
        "structural_confidence": 0.442345,
        "regime_weights": {
            "fast": 1.0,
            "accumulation": 0.8,
            "structural": 0.6,
        },
        "regime_confidence": 0.812345,
        "blended_p": 0.592345,
        "blended_confidence": 0.702345,
        "disagreement_score": 0.122345,
        "blend_mode": "weighted_blend",
        "trade_considered": True,
        "trade_blocked_reason": None,
        "evidence_ids_contributing": ["ev-1", "ev-2"],
    }


def test_blend_decision_required_fields_match_contract_exactly():
    assert BLEND_DECISION_REQUIRED_FIELDS == (
        "market_ticker",
        "fast_lane_p",
        "fast_lane_confidence",
        "accumulation_p",
        "accumulation_confidence",
        "structural_p",
        "structural_confidence",
        "regime_weights",
        "regime_confidence",
        "blended_p",
        "blended_confidence",
        "disagreement_score",
        "blend_mode",
        "trade_considered",
        "trade_blocked_reason",
        "evidence_ids_contributing",
    )


def test_log_blend_decision_emits_contract_schema_exactly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    with patch.object(logger, "_write") as write_mock:
        logger.log_blend_decision(**_valid_blend_decision_kwargs())

    write_mock.assert_called_once()
    record = write_mock.call_args.args[0]
    assert record["type"] == "BLEND_DECISION"
    assert tuple(key for key in record if key != "type") == BLEND_DECISION_REQUIRED_FIELDS
    assert set(record) == {"type", *BLEND_DECISION_REQUIRED_FIELDS}
    assert record["market_ticker"] == "KXTEST-26DEC31"
    assert record["fast_lane_p"] == pytest.approx(0.6123)
    assert record["fast_lane_confidence"] == pytest.approx(0.7123)
    assert record["accumulation_p"] == pytest.approx(0.5823)
    assert record["accumulation_confidence"] == pytest.approx(0.6623)
    assert record["structural_p"] == pytest.approx(0.5223)
    assert record["structural_confidence"] == pytest.approx(0.4423)
    assert record["regime_weights"] == {"fast": 1.0, "accumulation": 0.8, "structural": 0.6}
    assert record["regime_confidence"] == pytest.approx(0.8123)
    assert record["blended_p"] == pytest.approx(0.5923)
    assert record["blended_confidence"] == pytest.approx(0.7023)
    assert record["disagreement_score"] == pytest.approx(0.1223)
    assert record["blend_mode"] == "weighted_blend"
    assert record["trade_considered"] is True
    assert record["trade_blocked_reason"] is None
    assert record["evidence_ids_contributing"] == ["ev-1", "ev-2"]


def test_log_blend_decision_missing_required_field_fails_clearly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_blend_decision_kwargs()
    kwargs.pop("evidence_ids_contributing")
    with pytest.raises(TypeError, match="evidence_ids_contributing"):
        logger.log_blend_decision(**kwargs)


def test_log_blend_decision_fast_lane_only_uses_empty_evidence_list(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_blend_decision_kwargs()
    kwargs.update(
        {
            "accumulation_p": None,
            "accumulation_confidence": None,
            "structural_p": None,
            "structural_confidence": None,
            "evidence_ids_contributing": [],
        }
    )

    with patch.object(logger, "_write") as write_mock:
        logger.log_blend_decision(**kwargs)

    record = write_mock.call_args.args[0]
    assert record["accumulation_p"] is None
    assert record["accumulation_confidence"] is None
    assert record["structural_p"] is None
    assert record["structural_confidence"] is None
    assert record["evidence_ids_contributing"] == []


def test_log_blend_decision_emits_optional_venue_when_present(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_blend_decision_kwargs()
    kwargs["venue"] = "polymarket_us"

    with patch.object(logger, "_write") as write_mock:
        logger.log_blend_decision(**kwargs)

    record = write_mock.call_args.args[0]
    assert record["venue"] == "polymarket_us"


def test_log_blend_decision_emits_g7_mark_snapshot(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    snapshot = {
        "drawdown_pct": 0.21,
        "threshold_pct": 0.20,
        "valuation_basis": "legacy_marked_value_pre_exit_fees",
        "provider": "scripts.mark_open_positions",
    }
    kwargs = _valid_blend_decision_kwargs()
    kwargs["lifecycle_id"] = "lc-g7-mark"
    kwargs["g7_mark_snapshot"] = snapshot

    logger.log_blend_decision(**kwargs)

    record = json.loads((tmp_path / "trades.jsonl").read_text(encoding="utf-8"))
    assert record["lifecycle_id"] == "lc-g7-mark"
    assert record["g7_mark_snapshot"] == snapshot
