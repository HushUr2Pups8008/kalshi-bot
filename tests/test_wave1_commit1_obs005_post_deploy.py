from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_obs005_post_deploy_cooldown_sentinel_cleanup():
    executor_tests = (ROOT / "tests/test_executor.py").read_text()
    conftest = (ROOT / "tests/conftest.py").read_text()
    start = executor_tests.index("class TestCooldownSentinelOBS005")
    block = executor_tests[start:]

    assert "pytest.mark.xfail" not in block
    assert "PAPER_TICKER_COOLDOWN" not in conftest
    assert "LIVE_TICKER_COOLDOWN" not in conftest
    assert ".get(analysis.market.ticker, float(\"-inf\"))" in (ROOT / "trading/executor.py").read_text()

