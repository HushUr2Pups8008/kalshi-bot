from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.xfail(
    strict=True,
    reason="PROFIT-EXEC-002 deploy has not landed; series-correlation xfail markers still exist.",
)
def test_exec002_post_deploy_series_correlation_marker_cleanup():
    tests = (ROOT / "tests/test_blend_task.py").read_text()
    source = (ROOT / "tasks/blend_task.py").read_text()
    config = (ROOT / "config.py").read_text()
    start = tests.index("PROFIT-EXEC-002 harness")
    block = tests[start:]

    assert "pytest.mark.xfail" not in block
    assert "_recent_series_enqueues" in source
    assert "series_correlation_window_seconds" in config

