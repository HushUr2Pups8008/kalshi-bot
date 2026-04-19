from pathlib import Path
from unittest.mock import patch

import pytest

from utils.logger import STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS, TradeLogger


def _valid_structural_prior_recompute_kwargs() -> dict:
    return {
        "market_ticker": "KXTEST-26DEC31",
        "prior_estimate": 0.412345,
        "new_estimate": 0.482345,
        "input_sources": ["base_rate", "market_context"],
        "llm_called": True,
        "token_count": 1234,
    }


def test_structural_prior_recompute_required_fields_match_contract_exactly():
    assert STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS == (
        "market_ticker",
        "prior_estimate",
        "new_estimate",
        "input_sources",
        "llm_called",
        "token_count",
    )


def test_log_structural_prior_recompute_emits_contract_schema_exactly(tmp_path: Path):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    with patch.object(logger, "_write") as write_mock:
        logger.log_structural_prior_recompute(
            **_valid_structural_prior_recompute_kwargs()
        )

    write_mock.assert_called_once()
    record = write_mock.call_args.args[0]
    assert record["type"] == "STRUCTURAL_PRIOR_RECOMPUTE"
    assert tuple(key for key in record if key != "type") == STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS
    assert set(record) == {"type", *STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS}
    assert record["market_ticker"] == "KXTEST-26DEC31"
    assert record["prior_estimate"] == pytest.approx(0.4123)
    assert record["new_estimate"] == pytest.approx(0.4823)
    assert record["input_sources"] == ["base_rate", "market_context"]
    assert record["llm_called"] is True
    assert record["token_count"] == 1234


def test_log_structural_prior_recompute_missing_required_field_fails_clearly(
    tmp_path: Path,
):
    logger = TradeLogger(path=tmp_path / "trades.jsonl")
    kwargs = _valid_structural_prior_recompute_kwargs()
    kwargs.pop("input_sources")
    with pytest.raises(TypeError, match="input_sources"):
        logger.log_structural_prior_recompute(**kwargs)
