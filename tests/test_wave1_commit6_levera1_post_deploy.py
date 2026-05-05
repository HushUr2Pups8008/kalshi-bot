from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.xfail(
    strict=True,
    reason="PROFIT-EDGE-004 Lever A.1 deploy has not landed; classifier xfail markers still exist.",
)
def test_levera1_post_deploy_source_class_classifier_marker_cleanup():
    tests = (ROOT / "tests/test_main_pipeline.py").read_text()
    source = (ROOT / "main.py").read_text()
    start = tests.index("class TestSourceClassClassifierLeverA1")
    end = tests.index("class TestSourceClassClassifierLeverA1PlusAnalysisBranch")
    block = tests[start:end]

    assert "pytest.mark.xfail" not in block
    assert "court" in source
    assert "legal" in source
    assert "politico" in source.lower()

