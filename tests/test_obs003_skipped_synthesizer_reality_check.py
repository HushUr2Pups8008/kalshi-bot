from __future__ import annotations

from pathlib import Path

import pytest

from scripts.simulations import obs003_skipped_synthesizer_reality_check as audit
from scripts.simulations import post_soak_landing_simulation


_MAC_ARCHIVE_TRADES = post_soak_landing_simulation._DEFAULT_PATHS[0]


@pytest.mark.skipif(
    not _MAC_ARCHIVE_TRADES.exists(),
    reason=(
        "mac_archive/macbook_2026-05-01_import/logs/trades is gitignored and "
        "absent in CI; reality-check requires the post-cutover trade-log archive "
        "to populate simulation reason counts. Test runs locally where the "
        "archive is present (operator machine post-cutover)."
    ),
)
def test_reality_check_passes_when_synthesizer_matches_simulation():
    report = audit.analyze([])

    assert report["verdict"] == "PASS"
    assert report["simulation_reason_counts"]["G1_blended_confidence"] == 59
    assert report["synthesizer_reason_counts"]["G1_blended_confidence"] == 59
    assert report["synthesizer_total"] == report["expected_total_with_executor"] == 87
