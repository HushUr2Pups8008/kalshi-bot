from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.xfail(
    strict=True,
    reason="PROFIT-GOV-003 deploy has not landed; governance monitor xfail markers still exist.",
)
def test_gov003_post_deploy_monitor_marker_cleanup():
    tests = (ROOT / "tests/test_governance_monitor.py").read_text()
    source = (ROOT / "scripts/governance_monitor.py").read_text()

    assert "pytest.mark.xfail" not in tests
    assert 'os.environ.get("KALSHI_HOME"' not in source
    assert "GOVERNANCE_DECISION_PARSE_ERROR" in source
    assert "GOVERNANCE_DECISION_VALIDATION_ERROR" in source

