import pytest

from scripts.edge_replay.calibration_audit import audit_rows, brier_score, direction_correct


def test_direction_correct_excludes_no_direction_band():
    assert direction_correct(0.50, True) is None
    assert direction_correct(0.52, True) is True
    assert direction_correct(0.48, True) is False


def test_audit_rows_reports_movement_direction_and_sized_subset():
    rows = [
        {"ticker": "KX1", "decision_kind": "dossier_update", "model_prob": 0.60, "edge": 0.10, "resolved_yes": True},
        {"ticker": "KX2", "decision_kind": "dossier_update", "model_prob": 0.40, "edge": -0.10, "resolved_yes": True},
        {"ticker": "KX3", "decision_kind": "dossier_update", "model_prob": 0.50, "edge": 0.0, "resolved_yes": False},
        {
            "ticker": "KX4",
            "decision_kind": "paper_trade",
            "model_prob": 0.70,
            "edge": 0.20,
            "resolved_yes": True,
            "observed_pnl": 2.0,
        },
    ]

    result = audit_rows(rows)

    assert result["movement"]["movement_rate"] == pytest.approx(0.75)
    assert result["direction_correctness"]["correct"] == 2
    assert result["direction_correctness"]["incorrect"] == 1
    assert result["direction_correctness"]["excluded_no_direction"] == 1
    assert result["sized_bet_subset"]["n"] == 1
    assert result["sized_bet_subset"]["direction_correctness"] == pytest.approx(1.0)


def test_brier_score_is_probability_error_against_resolution():
    assert brier_score([{"model_prob": 0.75, "resolved_yes": True}, {"model_prob": 0.25, "resolved_yes": False}]) == pytest.approx(0.0625)
