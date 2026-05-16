from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "governance_cadence_audit.py"


def _write_log(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def _cycle(cycle_id: str, cadence: str, started_at: str, run_source: str | None = None):
    record = {
        "type": "GOVERNANCE_CYCLE_START",
        "cycle_id": cycle_id,
        "cadence": cadence,
        "started_at": started_at,
    }
    if run_source is not None:
        record["run_source"] = run_source
    return record


def test_cadence_audit_fails_extra_legacy_fast_cycle(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    _write_log(
        log_path,
        [
            _cycle("fast-1", "fast", "2026-05-02T03:00:00+00:00"),
            _cycle("manual-extra", "fast", "2026-05-02T04:00:00+00:00"),
            _cycle("fast-2", "fast", "2026-05-02T05:00:00+00:00"),
            _cycle("deep-1", "deep", "2026-05-02T15:00:00+00:00"),
            _cycle("deep-2", "deep", "2026-05-03T15:00:00+00:00"),
        ],
    )

    result = subprocess.run(
        [str(REPO_ROOT / ".venv/bin/python"), str(SCRIPT), "--log", str(log_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert payload["cadence"]["fast"]["violation_count"] == 2


def test_cadence_audit_excludes_documented_manual_cycles(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    _write_log(
        log_path,
        [
            _cycle("fast-1", "fast", "2026-05-02T03:00:00+00:00", "launchd"),
            _cycle("manual-extra", "fast", "2026-05-02T04:00:00+00:00", "manual"),
            _cycle("fast-2", "fast", "2026-05-02T05:00:00+00:00", "launchd"),
        ],
    )

    result = subprocess.run(
        [str(REPO_ROOT / ".venv/bin/python"), str(SCRIPT), "--log", str(log_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["excluded_manual_cycle_ids"] == ["manual-extra"]


def test_cadence_audit_excludes_documented_kickstart_cycle(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    _write_log(
        log_path,
        [
            _cycle("fast-1", "fast", "2026-05-02T03:00:00+00:00", "launchd"),
            _cycle("kickstart-extra", "fast", "2026-05-02T04:00:00+00:00", "launchd"),
            _cycle("fast-2", "fast", "2026-05-02T05:00:00+00:00", "launchd"),
        ],
    )

    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "--log",
            str(log_path),
            "--manual-cycle-id",
            "kickstart-extra",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["excluded_manual_cycle_ids"] == ["kickstart-extra"]


def test_cadence_audit_accepts_documented_legacy_phase_reset(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    _write_log(
        log_path,
        [
            _cycle("fast-1", "fast", "2026-05-03T13:06:44+00:00"),
            _cycle("deep-1", "deep", "2026-05-03T15:00:00+00:00"),
            _cycle("fast-reset", "fast", "2026-05-03T15:28:36+00:00"),
            _cycle("fast-2", "fast", "2026-05-03T17:28:56+00:00"),
        ],
    )

    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "--log",
            str(log_path),
            "--phase-reset-cycle-id",
            "fast-reset",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["cadence"]["fast"]["ignored_transition_count"] == 1
