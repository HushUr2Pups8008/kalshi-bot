from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason="OBS-005 deploy not landed; 24h cooldown validation window has no post-deploy evidence yet.",
)
def test_obs005_post_deploy_24h_validation_window_evidence_exists():
    from pathlib import Path

    evidence = Path("docs/_archive/governance/wave-1-post-deploy-observation-plan.md").read_text()
    assert "OBS-005" in evidence
    assert "OBS-005 24h validation: PASS" in evidence


@pytest.mark.xfail(
    strict=True,
    reason="MATCH-001 deploy not landed; 72h token-guard validation window has no post-deploy evidence yet.",
)
def test_match001_post_deploy_72h_validation_window_evidence_exists():
    from pathlib import Path

    evidence = Path("docs/_archive/governance/wave-1-post-deploy-observation-plan.md").read_text()
    assert "MATCH-001" in evidence
    assert "MATCH-001 72h validation: PASS" in evidence


@pytest.mark.xfail(
    strict=True,
    reason="OBS-003 deploy not landed; 48h SKIPPED-emission validation window has no post-deploy evidence yet.",
)
def test_obs003_post_deploy_48h_validation_window_evidence_exists():
    from pathlib import Path

    evidence = Path("docs/_archive/governance/wave-1-post-deploy-observation-plan.md").read_text()
    assert "OBS-003" in evidence
    assert "OBS-003 48h validation: PASS" in evidence


@pytest.mark.xfail(
    strict=True,
    reason="EXEC-002 deploy not landed; 7d series-correlation validation window has no post-deploy evidence yet.",
)
def test_exec002_post_deploy_7d_validation_window_evidence_exists():
    from pathlib import Path

    evidence = Path("docs/_archive/governance/wave-1-post-deploy-observation-plan.md").read_text()
    assert "EXEC-002" in evidence
    assert "EXEC-002 7d validation: PASS" in evidence
