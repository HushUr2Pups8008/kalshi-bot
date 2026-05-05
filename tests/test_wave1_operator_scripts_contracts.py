from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_wave1_commit_chain_acceptance_audit_lists_locked_order():
    script = REPO_ROOT / "scripts/wave1_commit_chain_acceptance_audit.sh"

    result = subprocess.run(
        ["bash", str(script), "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    expected = [
        "1|PROFIT-OBS-005",
        "2|PROFIT-MATCH-001",
        "3|PROFIT-OBS-003",
        "4|PROFIT-EXEC-002",
        "5|PROFIT-GOV-003",
        "6|PROFIT-EDGE-004 Lever A.1",
    ]
    for row in expected:
        assert row in result.stdout


def test_release_tag_inventory_snapshot_projects_wave_tags():
    script = REPO_ROOT / "scripts/release_tag_inventory_snapshot.py"

    result = subprocess.run(
        ["python3", str(script), "--projected-only"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "v0.30.0|Wave-1 base-stack post-soak deploy" in result.stdout
    assert "v0.31.0|Wave-2 EDGE-004 branch decision deploy" in result.stdout
    assert "v0.32.0|Wave-3 escalation deploy" in result.stdout


def test_macbook_import_archive_migration_dry_run_is_non_destructive(tmp_path):
    root = tmp_path / "repo"
    source = root / "mac_archive/macbook_2026-05-01_import"
    source.mkdir(parents=True)
    (source / "README.txt").write_text("archive fixture\n")
    script = REPO_ROOT / "scripts/macbook_import_archive_migration.sh"

    result = subprocess.run(
        ["bash", str(script), "--repo-root", str(root), "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "DRY-RUN" in result.stdout
    assert source.exists()

