from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "docs/_archive/governance/2026-05-05-branch-d-fire-procedure-runbook.md"


def test_branch_d_runbook_pins_fire_tag_pattern():
    body = RUNBOOK.read_text(encoding="utf-8")

    assert "edge-004-branch-d-fired-$(date -u +%Y-%m-%d)" in body
    assert "git tag -a edge-004-branch-d-fired-" in body
    assert "git push origin edge-004-branch-d-fired-" in body


def test_branch_d_runbook_pins_debt_log_structure():
    body = RUNBOOK.read_text(encoding="utf-8")

    assert "##### Branch D fire log" in body
    assert "**Trigger (per Lever-D escalation criteria spec §2):**" in body
    assert "**Window data:**" in body
    assert "**Operator attestation:**" in body
    assert "**Anti-trigger check:**" in body
