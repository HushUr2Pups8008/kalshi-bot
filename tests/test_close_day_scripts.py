from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from scripts import distribution_analysis_v2 as dist


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
