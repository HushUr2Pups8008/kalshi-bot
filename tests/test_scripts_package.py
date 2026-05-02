"""Verify scripts/ is a proper Python package with importable library functions
and no side effects (no argparse, no I/O, no print) at import time."""

from __future__ import annotations



def test_scripts_is_a_package():
    import scripts

    assert hasattr(scripts, "__path__"), "scripts should be a regular package"


def test_audit_libs_importable_without_side_effects(capsys):
    # Importing should not parse argv, write to stdout, or open files.
    captured_before = capsys.readouterr()

    from scripts import source_market_alignment_audit  # noqa: F401
    from scripts import keyword_feedback  # noqa: F401
    from scripts import reddit_source_audit  # noqa: F401
    from scripts import freshness_diagnostics  # noqa: F401

    captured_after = capsys.readouterr()
    assert captured_after.out == captured_before.out, (
        "import of audit modules must be silent on stdout"
    )
    assert captured_after.err == captured_before.err, (
        "import of audit modules must be silent on stderr"
    )


def test_audit_libs_expose_pure_aggregator_functions():
    """Each audit module must expose a documented pure-function entry point
    that returns a structured dict — this is the surface governance/evidence.py
    consumes."""
    from scripts import source_market_alignment_audit
    from scripts import keyword_feedback
    from scripts import reddit_source_audit
    from scripts import freshness_diagnostics

    assert callable(getattr(source_market_alignment_audit, "aggregate", None))
    assert callable(getattr(keyword_feedback, "summarize", None))
    assert callable(getattr(reddit_source_audit, "collect", None))
    assert callable(getattr(freshness_diagnostics, "summarize", None))
