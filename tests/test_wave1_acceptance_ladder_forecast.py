from __future__ import annotations

from scripts.simulations import wave1_acceptance_ladder_forecast as audit


def test_wave1_ladder_uses_post_soak_simulation(monkeypatch):
    monkeypatch.setattr(
        audit.post_soak_landing_simulation,
        "analyze",
        lambda paths=None: {
            "steps": [
                {"step": "OBS-005", "opportunity": 260, "skipped": 17, "paper_trade": 3},
                {"step": "MATCH-001_B_prime", "opportunity": 87, "skipped": 9, "paper_trade": 3},
                {"step": "OBS-003", "opportunity": 87, "skipped": 87, "paper_trade": 3},
                {"step": "EXEC-002", "opportunity": 87, "skipped": 89, "paper_trade": 1},
            ]
        },
    )

    report = audit.analyze([])

    assert [row["item"] for row in report["ladder"]] == [
        "OBS-005",
        "MATCH-001_B_prime",
        "OBS-003",
        "EXEC-002",
    ]
    assert report["ladder"][-1]["archive_anchor"]["paper_trade"] == 1
