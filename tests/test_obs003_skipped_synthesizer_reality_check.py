from __future__ import annotations

from scripts.simulations import obs003_skipped_synthesizer_reality_check as audit


def test_reality_check_passes_when_synthesizer_matches_simulation():
    report = audit.analyze([])

    assert report["verdict"] == "PASS"
    assert report["simulation_reason_counts"]["G1_blended_confidence"] == 59
    assert report["synthesizer_reason_counts"]["G1_blended_confidence"] == 59
    assert report["synthesizer_total"] == report["expected_total_with_executor"] == 87
