from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import distribution_analysis_v2 as dist
from scripts import governance_decision_review as review
from scripts import llm_throughput_audit as llm_audit
from scripts import parse_error_retroactive_audit as parse_audit
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


def test_bothealth_post_deploy_mode_references_wave1_smoke():
    script = (REPO_ROOT / "scripts/bothealth.sh").read_text(encoding="utf-8")

    assert "--post-deploy" in script
    assert "wave1_post_deploy_smoke.sh" in script
    assert "Wave-1 post-deploy smoke" in script


def test_pre_soak_close_branch_backup_dry_run_skips_dirty_tree_abort():
    script = (REPO_ROOT / "scripts/pre_soak_close_branch_backup.sh").read_text(encoding="utf-8")

    assert "DRY-RUN WARNING: working tree has uncommitted changes" in script
    assert 'if [[ "$DRY_RUN" == "0" ]]' in script
