from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_levera1_post_deploy_source_class_classifier_marker_cleanup():
    tests = (ROOT / "tests/test_main_pipeline.py").read_text()
    source = (ROOT / "main.py").read_text()
    start = tests.index("class TestSourceClassClassifierLeverA1")
    end = tests.index("class TestSourceClassClassifierLeverA1PlusAnalysisBranch")
    block = tests[start:end]

    assert "pytest.mark.xfail" not in block
    assert "department of war" in source
    assert "iaea" in source.lower()
    assert "defense news" in source.lower()

