from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_match001_post_deploy_token_guard_marker_cleanup():
    tests = (ROOT / "tests/test_market_matcher.py").read_text()
    start = tests.index("class TestSuppressionTokenGuardMATCH001")
    block = tests[start:]

    assert "pytest.mark.xfail" not in block
    assert "non_ticker" in tests or "ticker token" in tests.lower()

