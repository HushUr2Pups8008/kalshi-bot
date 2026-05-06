from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pre_day7_smoke_uses_launchd_equivalence_gate():
    body = (REPO_ROOT / "scripts/pre_day7_smoke.sh").read_text()

    assert "launchd_template_equivalence_audit.py --installed --json" in body
    assert "launchd_plist_drift_audit.sh --json" not in body


def test_db_snapshot_retention_audit_reports_old_snapshot(tmp_path: Path):
    archive = tmp_path / "mac_archive/db_snapshots"
    old = archive / "2026-04-01T0000Z"
    old.mkdir(parents=True)

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/db_snapshot_retention_audit.sh"),
            "--archive-root",
            str(archive),
            "--now",
            "2026-04-10T00:00:00Z",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert '"status":"fail"' in result.stdout
    assert "2026-04-01T0000Z" in result.stdout


def test_wave2_branch_a_invariant_detects_operator_trade(tmp_path: Path):
    trades = tmp_path / "trades.jsonl"
    trades.write_text(
        '{"type":"PAPER_TRADE","ts":"2026-05-20T00:00:00Z","operator_initiated":true}\n'
    )

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/wave2_branch_a_invariant_check.sh"),
            "--trades",
            str(trades),
            "--start",
            "2026-05-09T00:00:00Z",
            "--end",
            "2026-05-23T00:00:00Z",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert '"operator_initiated_trades":1' in result.stdout


def test_doc_xref_audit_checks_versions_inside_skip_blocks(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "prestage.md").write_text(
        "<!-- audit-skip-block: prestaged changelog -->\n"
        "## [0.30.0]\n"
        "```bash\n"
        'echo "0.30.1" > VERSION\n'
        'git tag -a v0.30.0 -m "Wave-1"\n'
        "```\n"
        "<!-- /audit-skip-block -->\n"
    )

    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "scripts/doc_xref_audit.py"),
            "--repo-root",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "version drift in audit-skip-block" in result.stdout
