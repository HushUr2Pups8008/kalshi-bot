from scripts.edge_replay.llm_split_audit import audit_llm_split


def test_llm_split_reports_movement_by_branch():
    rows = [
        {"decision_kind": "dossier_update", "edge": 0.20, "model_prob": 0.70, "resolved_yes": True, "llm_called": True, "signal_source": "Reuters"},
        {"decision_kind": "dossier_update", "edge": 0.00, "model_prob": 0.50, "resolved_yes": True, "llm_called": False, "signal_source": "Reuters"},
    ]

    result = audit_llm_split(rows)

    assert result["branches"]["llm_called"]["movement_rate"] == 1.0
    assert result["branches"]["non_llm_called"]["movement_rate"] == 0.0
    assert result["llm_movement_rate_ratio"] == "inf"
    assert result["per_source"][0]["source"] == "Reuters"
