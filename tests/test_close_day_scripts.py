from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import calibration_drift_wiring_audit as calibration_audit
from scripts import governance_decision_review as review


REPO_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
           "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)


def _minimal_runtime_tree(repo: Path) -> None:
    for directory in ("analysis", "tasks", "feeds", "governance", "kalshi", "trading"):
        (repo / directory).mkdir(parents=True, exist_ok=True)
        (repo / directory / ".keep").write_text("", encoding="utf-8")
    (repo / "main.py").write_text("", encoding="utf-8")
    (repo / "config.py").write_text("", encoding="utf-8")


def test_check_soak_invariant_json_passes_on_clean_window(tmp_path: Path):
    _git(tmp_path, "init")
    _minimal_runtime_tree(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial runtime tree")

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/check_soak_invariant.sh"),
            "--since",
            "2026-05-01T19:01Z",
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["commit_count"] == 0
    assert "trading/" in payload["behavioural_paths"]
    assert "executor/" not in payload["behavioural_paths"]


def test_check_soak_invariant_path_sanity_reports_missing_runtime_path(tmp_path: Path):
    _git(tmp_path, "init")
    _minimal_runtime_tree(tmp_path)
    (tmp_path / "trading" / ".keep").unlink()
    (tmp_path / "trading").rmdir()
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "missing trading dir")

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts/check_soak_invariant.sh"), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "trading/" in payload["missing_paths"]


def test_wave1_smoke_script_wraps_five_named_feature_checks():
    script = (REPO_ROOT / "scripts/wave1_post_deploy_smoke.sh").read_text(encoding="utf-8")

    expected = (
        "OBS-005",
        "MATCH-001",
        "OBS-003",
        "EXEC-002",
        "GOV-002",
    )
    for label in expected:
        assert label in script
    assert script.count("run_check ") == 5


def test_governance_decision_review_bulk_candidates_select_uniform_dead_disable():
    decisions = [
        {
            "decision_id": "gd-1",
            "action": "disable_source",
            "target": "r/deadsub",
            "evidence_summary": {"fresh_pass_count": 0, "match_count": 0, "active_market_count": 0},
            "safety_checks_passed": {"kill_switch": True},
        },
        {
            "decision_id": "gd-2",
            "action": "disable_source",
            "target": "r/activesub",
            "evidence_summary": {"fresh_pass_count": 1, "match_count": 0, "active_market_count": 0},
            "safety_checks_passed": {"kill_switch": True},
        },
        {
            "decision_id": "gd-3",
            "action": "disable_source",
            "target": "r/reviewed",
            "evidence_summary": {"fresh_pass_count": 0, "match_count": 0, "active_market_count": 0},
            "safety_checks_passed": {"kill_switch": True},
        },
    ]

    selected = review._bulk_candidates(decisions, resume_seen={"gd-3"})

    assert [row["decision_id"] for row in selected] == ["gd-1"]


def test_calibration_drift_wiring_audit_flags_required_paths(tmp_path: Path):
    repo = tmp_path
    (repo / "main.py").write_text(
        "from tasks.calibration_task import CalibrationTask\n"
        "self._calibration_task = CalibrationTask()\n"
        "PaperTrader(calibration_task=self._calibration_task)\n"
        "BlendTask(calibration=self._calibration_task)\n",
        encoding="utf-8",
    )
    (repo / "tasks").mkdir()
    (repo / "tasks/calibration_task.py").write_text("class CalibrationTask: pass\nrecord_calibration_check\n", encoding="utf-8")
    (repo / "tasks/blend_task.py").write_text("calibration: CalibrationLike | None = None\nget_scaling_factor\n", encoding="utf-8")

    report = calibration_audit.analyze(repo)

    assert report["status"] == "pass"
    assert all(check["passed"] for check in report["checks"])


def test_bothealth_post_deploy_mode_references_wave1_smoke():
    script = (REPO_ROOT / "scripts/bothealth.sh").read_text(encoding="utf-8")

    assert "--post-deploy" in script
    assert "wave1_post_deploy_smoke.sh" in script
    assert "Wave-1 post-deploy smoke" in script
    assert "VALIDATION_ERROR" in script
    assert "batch_aborted" in script


def test_pre_soak_close_branch_backup_dry_run_skips_dirty_tree_abort():
    script = (REPO_ROOT / "scripts/pre_soak_close_branch_backup.sh").read_text(encoding="utf-8")

    assert "DRY-RUN WARNING: working tree has uncommitted changes" in script
    assert 'if [[ "$DRY_RUN" == "0" ]]' in script


def test_precommit_hook_health_audit_validates_hooks_path_and_hook():
    script = (REPO_ROOT / "scripts/precommit_hook_health_audit.sh").read_text(encoding="utf-8")

    assert "core.hooksPath" in script
    assert ".githooks/pre-commit" in script
    assert "scripts/sync_readme_version.py" in script
    assert "bash -n" in script


def test_release_tag_inventory_reports_local_and_origin_tags():
    script = (REPO_ROOT / "scripts/release_tag_inventory.sh").read_text(encoding="utf-8")

    assert "git for-each-ref" in script
    assert "refs/tags" in script
    assert "git ls-remote --tags origin" in script
    assert "commit subject" in script


def test_wave1_fire_time_smoke_bundles_day7_and_commit1_checks():
    script = (REPO_ROOT / "scripts/wave1_fire_time_smoke.sh").read_text(encoding="utf-8")

    assert "check_soak_invariant.sh" in script
    assert "wave1_post_deploy_smoke.sh" in script
    assert "TestCooldownSentinelOBS005" in script
    assert "--post-deploy" in script


def test_operator_alert_routing_audit_scans_safety_events_and_surfaces_report():
    script = (REPO_ROOT / "scripts/operator_alert_routing_audit.sh").read_text(encoding="utf-8")

    assert "KILL_SWITCH" in script
    assert "VALIDATION_ERROR" in script
    assert "batch_aborted" in script
    assert "osascript" in script
    assert "operator_alert_routing_" in script


def test_db_backup_health_audit_checks_archive_and_database_paths():
    script = (REPO_ROOT / "scripts/db_backup_health_audit.sh").read_text(encoding="utf-8")

    assert "data/paper_trades.db" in script
    assert "data/evidence_store.db" in script
    assert "mac_archive" in script
    assert "--max-age-hours" in script


def test_wave2_fire_time_smoke_bundles_branch_c_watch_checks():
    script = (REPO_ROOT / "scripts/wave2_fire_time_smoke.sh").read_text(encoding="utf-8")

    assert "tests/test_branch_c_feed_selection_rubric.py" in script
    assert "tests/test_wave2_preload_harnesses.py" in script
    assert "calibration_drift_wiring_audit.py" in script
    assert "operator_alert_routing_audit.sh" in script
    assert "24h watch" in script


def test_wave3_fire_time_smoke_bundles_lever_b_and_c_variants():
    script = (REPO_ROOT / "scripts/wave3_fire_time_smoke.sh").read_text(encoding="utf-8")

    assert "--lever-b" in script
    assert "--lever-c" in script
    assert "tests/test_lever_b_g1_floor_lock.py" in script
    assert "tests/test_lever_c_cross_series_correlation.py" in script
    assert "CALIBRATION_CHECK" in script
    assert "KILL_SWITCH" in script


def test_macbook_import_disposition_audit_reports_keep_archive_inputs():
    script = (REPO_ROOT / "scripts/macbook_import_disposition_audit.sh").read_text(encoding="utf-8")

    assert "mac_archive/macbook_2026-05-01_import" in script
    assert "repo_reference_count" in script
    assert "db_count" in script
    assert "jsonl_count" in script
    assert "keep_through_wave1_close_then_archive" in script


def test_launchd_plist_drift_audit_compares_templates_and_db_backup_plist():
    script = (REPO_ROOT / "scripts/launchd_plist_drift_audit.sh").read_text(encoding="utf-8")

    assert "ops/launchd/*.plist.template" in script
    assert "@REPO_ROOT@" in script
    assert "cmp -s" in script
    assert "--install-dir" in script
    # db-backup is audited via the ops/launchd/*.plist.template glob asserted
    # above. Cycle-8 (commit 193fb9f) refactored the script to template-glob
    # rather than literal labels; assert the template artifact exists instead.
    assert (REPO_ROOT / "ops/launchd/com.kalshi.db-backup.plist.template").exists()


def test_launchd_install_and_precommit_gate_equivalence_audit():
    install = (REPO_ROOT / "ops/launchd/install.sh").read_text(encoding="utf-8")
    hook = (REPO_ROOT / ".githooks/pre-commit").read_text(encoding="utf-8")

    assert "launchd_template_equivalence_audit.py" in install
    assert "--allow-drift-confirm ALLOW-DRIFT" in install
    assert "launchd_template_equivalence_audit.py" in hook
    assert "ops/launchd/.*\\.plist\\.template" in hook
