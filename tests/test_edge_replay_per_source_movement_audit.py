from scripts.edge_replay.per_source_movement_audit import audit_per_source


def test_per_source_audit_flags_inert_and_wrong_direction_sources():
    rows = [
        {
            "ticker": "KX1",
            "decision_kind": "dossier_update",
            "signal_source": "Reuters",
            "news_class": "news",
            "series_ticker": "KX",
            "signal_type": "state",
            "model_prob": 0.50,
            "edge": 0.0,
            "resolved_yes": True,
            "llm_called": False,
        },
        {
            "ticker": "KX2",
            "decision_kind": "dossier_update",
            "signal_source": "VitalLaw.com",
            "news_class": "news",
            "series_ticker": "KX",
            "signal_type": "state",
            "model_prob": 0.40,
            "edge": -0.10,
            "resolved_yes": True,
            "llm_called": True,
        },
    ]

    result = audit_per_source(rows)
    by_source = {row["signal_source"]: row for row in result["groups"]}

    assert by_source["Reuters"]["inert_source"] is True
    assert by_source["VitalLaw.com"]["wrong_direction_cluster"] is True
    assert by_source["VitalLaw.com"]["llm_called_fraction"] == 1.0
