from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def test_makefile_exposes_botcheck_signal_flow_operator_target():
    out = _make_dry_run("botcheck")

    assert ".venv/bin/python scripts/botcheck.py" in out


def test_makefile_uses_module_invocation_for_package_style_diagnostics():
    decision_funnel = _make_dry_run("decision-funnel")
    freshness = _make_dry_run("freshness")

    assert ".venv/bin/python -m scripts.decision_funnel_summary" in decision_funnel
    assert ".venv/bin/python -m scripts.freshness_diagnostics" in freshness


def test_makefile_exposes_phase2_and_governance_operator_targets():
    assert "scripts/governance_monitor.py" in _make_dry_run("governance-monitor")
    assert "scripts/governance_decision_review.py" in _make_dry_run("governance-review")
    assert "bash scripts/check_soak_invariant.sh" in _make_dry_run("soak-invariant")
    assert "bash scripts/precommit_hook_health_audit.sh" in _make_dry_run("hook-health")


def test_makefile_exposes_market_source_hints_diagnostics_target():
    out = _make_dry_run("msh-diagnostics")

    assert ".venv/bin/python scripts/market_source_hints_diagnostics.py" in out
    assert "--exclude-test" in out
