from pathlib import Path
from unittest.mock import patch

import pytest

from utils.logger import (
    DOSSIER_UPDATE_REQUIRED_FIELDS,
    EVIDENCE_INGESTION_REQUIRED_FIELDS,
    TradeLogger,
)


def _valid_evidence_ingestion_kwargs() -> dict:
    return {
        "market_ticker": "KXTEST-26DEC31",
        "evidence_id": "ev-1",
        "source_class": "rss",
        "is_duplicate": False,
        "correlation_discount_applied": True,
        "update_type": "state",
        "dossier_version_before": 3,
        "dossier_version_after": 4,
    }


def _valid_dossier_update_kwargs() -> dict:
    return {
        "market_ticker": "KXTEST-26DEC31",
        "dossier_version": 4,
        "prior_estimate": 0.512345,
        "new_estimate": 0.572345,
        "update_delta": 0.062345,
        "confidence_before": 0.412345,
        "confidence_after": 0.532345,
        "evidence_ids_contributing": ["ev-1", "ev-2"],
        "llm_called": True,
        "drift_suspect": False,
        "in_recovery": False,
    }


def test_evidence_ingestion_required_fields_match_contract_exactly():
    assert EVIDENCE_INGESTION_REQUIRED_FIELDS == (
        "market_ticker",
        "evidence_id",
        "source_class",
        "is_duplicate",
        "correlation_discount_applied",
        "update_type",
        "dossier_version_before",
        "dossier_version_after",
    )


def test_dossier_update_required_fields_match_contract_exactly():
    assert DOSSIER_UPDATE_REQUIRED_FIELDS == (
        "market_ticker",
        "dossier_version",
        "prior_estimate",
        "new_estimate",
        "update_delta",
        "confidence_before",
        "confidence_after",
        "evidence_ids_contributing",
        "llm_called",
        "drift_suspect",
        "in_recovery",
    )


def test_log_evidence_ingestion_emits_contract_schema_exactly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    with patch.object(logger, "_write") as write_mock:
        logger.log_evidence_ingestion(**_valid_evidence_ingestion_kwargs())

    write_mock.assert_called_once()
    record = write_mock.call_args.args[0]
    assert record["type"] == "EVIDENCE_INGESTION"
    assert tuple(key for key in record if key != "type") == EVIDENCE_INGESTION_REQUIRED_FIELDS
    assert set(record) == {"type", *EVIDENCE_INGESTION_REQUIRED_FIELDS}
    assert record == {
        "type": "EVIDENCE_INGESTION",
        "market_ticker": "KXTEST-26DEC31",
        "evidence_id": "ev-1",
        "source_class": "rss",
        "is_duplicate": False,
        "correlation_discount_applied": True,
        "update_type": "state",
        "dossier_version_before": 3,
        "dossier_version_after": 4,
    }


def test_log_dossier_update_emits_contract_schema_exactly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    with patch.object(logger, "_write") as write_mock:
        logger.log_dossier_update(**_valid_dossier_update_kwargs())

    write_mock.assert_called_once()
    record = write_mock.call_args.args[0]
    assert record["type"] == "DOSSIER_UPDATE"
    assert tuple(key for key in record if key != "type") == DOSSIER_UPDATE_REQUIRED_FIELDS
    assert set(record) == {"type", *DOSSIER_UPDATE_REQUIRED_FIELDS}
    assert record["market_ticker"] == "KXTEST-26DEC31"
    assert record["dossier_version"] == 4
    assert record["prior_estimate"] == pytest.approx(0.5123)
    assert record["new_estimate"] == pytest.approx(0.5723)
    assert record["update_delta"] == pytest.approx(0.0623)
    assert record["confidence_before"] == pytest.approx(0.4123)
    assert record["confidence_after"] == pytest.approx(0.5323)
    assert record["evidence_ids_contributing"] == ["ev-1", "ev-2"]
    assert record["llm_called"] is True
    assert record["drift_suspect"] is False
    assert record["in_recovery"] is False


def test_log_evidence_ingestion_missing_required_field_fails_clearly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_evidence_ingestion_kwargs()
    kwargs.pop("evidence_id")
    with pytest.raises(TypeError, match="evidence_id"):
        logger.log_evidence_ingestion(**kwargs)


def test_log_dossier_update_missing_required_field_fails_clearly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_dossier_update_kwargs()
    kwargs.pop("evidence_ids_contributing")
    with pytest.raises(TypeError, match="evidence_ids_contributing"):
        logger.log_dossier_update(**kwargs)
