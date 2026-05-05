from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import distribution_analysis_v2 as dist
from scripts import calibration_drift_wiring_audit as calibration_audit
from scripts import governance_decision_review as review
from scripts import llm_throughput_audit as llm_audit
from scripts import parse_error_retroactive_audit as parse_audit
from scripts import pre_wave1_llm_call_budget_projection as llm_projection
from scripts import pre_wave1_cooldown_distribution_audit as cooldown_audit
from scripts import pre_wave1_opportunity_age_audit as opportunity_age_audit
from scripts import pre_wave1_skipped_rate_baseline as skipped_rate
from scripts import pre_wave1_trade_rate_per_ticker_baseline as ticker_rate
from scripts import test_coverage_audit as coverage_audit
from scripts import source_class_evolution_audit as source_audit


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


def test_wave1_smoke_script_wraps_six_named_feature_checks():
    script = (REPO_ROOT / "scripts/wave1_post_deploy_smoke.sh").read_text(encoding="utf-8")

    expected = (
        "OBS-005",
        "MATCH-001",
        "OBS-003",
        "EXEC-002",
        "GOV-002",
        "wave1-ladder",
    )
    for label in expected:
        assert label in script
    assert script.count("run_check ") == 6


def test_distribution_analysis_v2_groups_uniform_disable_sources(tmp_path: Path):
    log = tmp_path / "decisions.jsonl"
    records = [
        {
            "type": "GOVERNANCE_DECISION",
            "decision_id": "gd-1",
            "decided_at": "2026-05-05T00:00:00+00:00",
            "action": "disable_source",
            "target": "r/deadsub",
            "confidence": 0.85,
            "evidence_summary": {
                "window_hours": 168,
                "ingestion_events": 5,
                "fresh_pass_count": 0,
                "match_count": 0,
                "active_market_count": 0,
            },
            "safety_checks_passed": {"kill_switch": True},
        },
        {
            "type": "GOVERNANCE_DECISION",
            "decision_id": "gd-2",
            "decided_at": "2026-05-05T01:00:00+00:00",
            "action": "disable_source",
            "target": "r/activesub",
            "confidence": 0.85,
            "evidence_summary": {
                "window_hours": 168,
                "ingestion_events": 5,
                "fresh_pass_count": 1,
                "match_count": 0,
                "active_market_count": 0,
            },
            "safety_checks_passed": {"kill_switch": True},
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = dist.analyze(log, since="2026-05-05T00:00:00Z")

    assert report["total_decisions"] == 2
    assert report["bulk_review"]["uniform_disable_source_dead_count"] == 1
    assert report["anomalies"][0]["decision_id"] == "gd-2"
    assert "Close-Day Decision Distribution v2" in dist.render_markdown(report)


def test_distribution_analysis_v2_daily_trend(tmp_path: Path):
    log = tmp_path / "decisions.jsonl"
    records = [
        {
            "type": "GOVERNANCE_DECISION",
            "decision_id": "gd-1",
            "decided_at": "2026-05-04T00:00:00+00:00",
            "action": "disable_source",
            "target": "r/a",
            "confidence": 0.9,
            "evidence_summary": {"fresh_pass_count": 0, "match_count": 0, "active_market_count": 0},
            "safety_checks_passed": {"kill_switch": True},
        },
        {
            "type": "GOVERNANCE_DECISION",
            "decision_id": "gd-2",
            "decided_at": "2026-05-05T00:00:00+00:00",
            "action": "disable_source",
            "target": "r/b",
            "confidence": 0.85,
            "evidence_summary": {"fresh_pass_count": 1, "match_count": 0, "active_market_count": 0},
            "safety_checks_passed": {"kill_switch": True},
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = dist.analyze(log, daily_trend=True)

    assert report["daily_trend"]["2026-05-04"]["total_decisions"] == 1
    assert report["daily_trend"]["2026-05-04"]["uniform_disable_source_dead_count"] == 1
    assert report["daily_trend"]["2026-05-05"]["anomaly_count"] == 1
    assert "## Daily Trend" in dist.render_markdown(report)


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


def test_llm_throughput_audit_summarizes_latency(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    records = [
        {"type": "SIGNAL_ANALYSIS_DETAIL", "ts": "2026-05-05T00:00:00+00:00", "cycle_id": "c1", "llm_attempted": True, "llm_latency_ms": 1000},
        {"type": "SIGNAL_ANALYSIS_DETAIL", "ts": "2026-05-05T00:02:00+00:00", "cycle_id": "c1", "llm_attempted": True, "llm_latency_ms": 3000},
        {"type": "SIGNAL_ANALYSIS_DETAIL", "ts": "2026-05-05T01:00:00+00:00", "cycle_id": "c2", "llm_attempted": False},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = llm_audit.analyze(log)

    assert report["attempted_calls"] == 2
    assert report["latency_ms"]["median"] == 2000
    assert report["cycles"]["c1"]["attempted_calls"] == 2


def test_pre_wave1_llm_call_budget_projection_uses_cadence_and_budget():
    throughput = {
        "input_path": "fixture.jsonl",
        "attempted_calls": 6,
        "latency_ms": {"median": 1000, "p90": 3000},
        "cycles": {
            "c1": {"attempted_calls": 2, "latency_ms": {"median": 1000}},
            "c2": {"attempted_calls": 4, "latency_ms": {"median": 3000}},
        },
    }

    report = llm_projection.project(
        throughput,
        cadence_minutes=90,
        llm_call_budget_per_day=96,
    )

    assert report["observed_calls_per_cycle"] == 3
    assert report["projection"]["cycles_per_day"] == 16
    assert report["projection"]["calls_per_day"] == 48
    assert report["projection"]["budget_utilization_pct"] == 50
    assert report["projection"]["median_wall_clock_seconds_per_day"] == 48


def test_source_class_evolution_audit_groups_by_day(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    records = [
        {"type": "EARLY_FRESH_PASS", "ts": "2026-05-04T00:00:00+00:00", "source": "Reuters"},
        {"type": "SIGNAL_ANALYSIS_DETAIL", "ts": "2026-05-04T01:00:00+00:00", "source": "White House"},
        {"type": "MATCH_DIAGNOSTIC", "ts": "2026-05-05T00:00:00+00:00", "source": "price_fade"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = source_audit.analyze(log)

    assert report["per_day"]["2026-05-04"]["news"] == 1
    assert report["per_day"]["2026-05-04"]["official"] == 1
    assert report["per_day"]["2026-05-05"]["market"] == 1


def test_parse_error_retroactive_audit_groups_root_causes(tmp_path: Path):
    log = tmp_path / "decisions.jsonl"
    records = [
        {"type": "GOVERNANCE_DECISION_PARSE_ERROR", "cycle_id": "c1", "candidate_action": "disable_source", "candidate_target": "r/a", "error": "LLM output missing required fields: ['action']"},
        {"type": "GOVERNANCE_DECISION_PARSE_ERROR", "cycle_id": "c2", "candidate_action": "disable_source", "candidate_target": "r/b", "error": "JSON decode failed"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = parse_audit.analyze(log)

    assert report["total_parse_errors"] == 2
    assert report["root_causes"]["missing_required_fields"] == 1
    assert report["root_causes"]["json_decode"] == 1


def test_pre_wave1_skipped_rate_baseline_counts_denominator(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    rows = [
        {"type": "OPPORTUNITY", "ticker": "KX1", "ts": "2026-05-05T00:00:00Z"},
        {"type": "SKIPPED", "ticker": "KX1", "reason": "G1_blended_confidence", "ts": "2026-05-05T00:01:00Z"},
        {"type": "PAPER_TRADE", "ticker": "KX2", "ts": "2026-05-05T00:02:00Z"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = skipped_rate.analyze(log)

    assert report["total"]["opportunity"] == 1
    assert report["total"]["skipped"] == 1
    assert report["total"]["paper_trade"] == 1
    assert report["total"]["skipped_rate_pct"] == 33.3
    assert report["skip_reasons"]["G1_blended_confidence"] == 1


def test_pre_wave1_opportunity_age_audit_uses_available_age_fields(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    rows = [
        {"type": "OPPORTUNITY", "age_seconds": 60, "ts": "2026-05-05T00:00:00Z"},
        {"type": "MATCH_DIAGNOSTIC", "age_at_match_seconds": 3600, "ts": "2026-05-05T00:01:00Z"},
        {"type": "EARLY_FRESH_PASS", "age_seconds": 120, "ts": "2026-05-05T00:02:00Z"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = opportunity_age_audit.analyze(log)

    assert report["event_counts"]["OPPORTUNITY"] == 1
    assert report["age_seconds"]["count"] == 3
    assert report["age_seconds"]["max"] == 3600
    assert "Opportunity Age Audit" in opportunity_age_audit.render_markdown(report)


def test_pre_wave1_cooldown_distribution_audit_extracts_cooldown_reasons(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    rows = [
        {"type": "SKIPPED", "reason": "paper cooldown: last trade 0.5h ago (cooldown=4h)", "ticker": "KX1", "ts": "2026-05-05T00:00:00Z"},
        {"type": "SKIPPED", "reason": "G1_blended_confidence", "ticker": "KX2", "ts": "2026-05-05T00:01:00Z"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = cooldown_audit.analyze(log)

    assert report["cooldown_skips"] == 1
    assert report["by_ticker"]["KX1"] == 1
    assert report["cooldown_reason_counts"]["paper cooldown"] == 1


def test_pre_wave1_trade_rate_per_ticker_baseline_counts_paper_trades(tmp_path: Path):
    log = tmp_path / "trades.jsonl"
    rows = [
        {"type": "PAPER_TRADE", "ticker": "KX1", "ts": "2026-05-05T00:00:00Z"},
        {"type": "PAPER_TRADE", "ticker": "KX1", "ts": "2026-05-05T02:00:00Z"},
        {"type": "PAPER_TRADE", "ticker": "KX2", "ts": "2026-05-06T00:00:00Z"},
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = ticker_rate.analyze(log)

    assert report["total_paper_trades"] == 3
    assert report["tickers"][0]["ticker"] == "KX1"
    assert report["tickers"][0]["trade_count"] == 2
    assert report["tickers"][0]["trades_per_day"] == 2.0


def test_test_coverage_audit_summarizes_coverage_json(tmp_path: Path):
    payload = {
        "totals": {"covered_lines": 75, "num_statements": 100, "percent_covered": 75.0},
        "files": {
            "main.py": {"summary": {"covered_lines": 30, "num_statements": 60, "percent_covered": 50.0}},
            "scripts/example.py": {"summary": {"covered_lines": 45, "num_statements": 40, "percent_covered": 100.0}},
        },
    }
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(json.dumps(payload), encoding="utf-8")

    report = coverage_audit.analyze_coverage_json(coverage_json)

    assert report["totals"]["percent_covered"] == 75.0
    assert report["modules"][0]["path"] == "main.py"
    assert "Coverage Audit" in coverage_audit.render_markdown(report)


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
    assert "com.kalshi.db-backup" in script
    assert "@REPO_ROOT@" in script
    assert "cmp -s" in script
    assert "--install-dir" in script
