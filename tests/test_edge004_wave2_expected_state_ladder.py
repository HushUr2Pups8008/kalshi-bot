from __future__ import annotations

from scripts.simulations import edge004_wave2_expected_state_ladder as audit


def test_ladder_contains_expected_wave2_steps(monkeypatch):
    monkeypatch.setattr(
        audit.post_soak_landing_simulation,
        "analyze",
        lambda paths=None: {
            "steps": [
                {"step": "baseline"},
                {
                    "step": "EXEC-002",
                    "opportunity": 87,
                    "skipped": 89,
                    "paper_trade": 1,
                    "trade_rate": 1 / 87,
                },
            ]
        },
    )
    monkeypatch.setattr(
        audit.lever_a1_source_classifier_counterfactual,
        "analyze",
        lambda paths=None: {
            "opportunity_source_label_replay": {
                "post_a1_distribution": {"official": 4}
            }
        },
    )
    monkeypatch.setattr(
        audit.g1_admittance_counterfactual,
        "analyze",
        lambda paths=None: {
            "floor_rows": [
                {"g1_floor": 0.04, "g1_kills_admitted": 32, "edge_ge_0_02": 1},
                {"g1_floor": 0.03, "g1_kills_admitted": 65, "edge_ge_0_02": 2},
            ]
        },
    )

    report = audit.analyze([])

    assert [row["step"] for row in report["ladder"]] == [
        "Wave1_post_EXEC002",
        "Lever_A1_classifier_fix",
        "Lever_A1_plus_new_feed",
        "Lever_B_G1_0.04",
        "Lever_C_cross_series_hash",
    ]
    assert report["ladder"][0]["opportunity"] == 87
