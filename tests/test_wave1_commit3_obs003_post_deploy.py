from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.xfail(
    strict=True,
    reason="PROFIT-OBS-003 deploy has not landed; BlendTask SKIPPED xfail markers still exist.",
)
def test_obs003_post_deploy_skipped_emission_marker_cleanup():
    tests = (ROOT / "tests/test_blend_task.py").read_text()
    source = (ROOT / "tasks/blend_task.py").read_text()
    start = tests.index("PROFIT-OBS-003 harness")
    end = tests.index("PROFIT-EXEC-002 harness")
    block = tests[start:end]

    assert "pytest.mark.xfail" not in block
    assert "def _emit_skipped" in source
    assert "SKIPPED" in source

