from __future__ import annotations

import json
from datetime import UTC, datetime

from scripts import governance_monitor


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _decision(decision_id, target, reasoning, action="disable_source"):
    return {
        "type": "GOVERNANCE_DECISION",
        "decision_id": decision_id,
        "decided_at": "2026-05-03T12:00:00+00:00",
        "action": action,
        "target": target,
        "reasoning": reasoning,
        "applied": False,
    }


def test_happy_path_summarizes_cycles_actions_and_targets(tmp_path):
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        {"type": "GOVERNANCE_CYCLE_START", "cycle_id": "gc_2026-05-03_010000", "cadence": "deep"},
        _decision("gd_2026-05-03_0001", "r/a", "alpha beta gamma delta epsilon zeta"),
        _decision("gd_2026-05-03_0002", "r/b", "beta gamma delta epsilon zeta eta", "no_action"),
        {"type": "GOVERNANCE_CYCLE_END", "cycle_id": "gc_2026-05-03_010000", "cadence": "deep"},
    ])

    report = governance_monitor.analyze(
        log,
        overrides=tmp_path / "missing.yaml",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )

    assert report["per_day"]["2026-05-03"]["cycles_started"] == 1
    assert report["per_day"]["2026-05-03"]["cycles_ended"] == 1
    assert report["actions"] == {"disable_source": 1, "no_action": 1}
    assert report["targets"] == {"r/a": 1, "r/b": 1}


def test_since_filters_older_records(tmp_path):
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        _decision("gd_2026-05-02_0001", "r/old", "old old old old old old"),
        _decision("gd_2026-05-03_0001", "r/new", "new new new new new new"),
    ])

    report = governance_monitor.analyze(log, since="2026-05-03", overrides=tmp_path / "missing.yaml")

    assert "2026-05-02" not in report["per_day"]
    assert report["raw_decision_count"] == 1
    assert report["targets"] == {"r/new": 1}


def test_effective_sample_size_deduplicates_target_reasoning_pairs(tmp_path):
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        _decision("gd_2026-05-03_0001", "r/a", "same same same same same same"),
        _decision("gd_2026-05-03_0002", "r/a", "same same same same same same"),
        _decision("gd_2026-05-03_0003", "r/a", "different different different different different"),
    ])

    report = governance_monitor.analyze(log, overrides=tmp_path / "missing.yaml")

    assert report["raw_decision_count"] == 3
    assert report["effective_sample_size"] == 2
    assert report["duplication_ratio"] == 1.5


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        _decision("gd_2026-05-03_0001", "r/a", "alpha beta gamma delta epsilon zeta"),
    ])

    assert governance_monitor.main(["--logfile", str(log), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["raw_decision_count"] == 1
    assert payload["effective_sample_size"] == 1


# ---------------------------------------------------------------------------
# governance_monitor.py fix harness — KALSHI_HOME path bug + type-set membership.
#
# Spec: docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md
# Status: pre-loaded during PROFIT-PHASE2-001 soak; intended landing companion
# for OBS-005 (both LOW-risk warm-ups for the post-soak deploy pipeline).
#
# Until the fix lands these tests xfail strictly:
#   - default-path tests fail because KALSHI_HOME currently overrides the
#     repo-root resolution.
#   - type-set tests fail because the aggregator looks for "PARSE_ERROR" /
#     "VALIDATION_ERROR" rather than "GOVERNANCE_DECISION_PARSE_ERROR" /
#     "GOVERNANCE_DECISION_VALIDATION_ERROR".
# ---------------------------------------------------------------------------

import os
from pathlib import Path

import pytest

_GOV_MONITOR_XFAIL_REASON = (
    "governance_monitor.py KALSHI_HOME / type-set bugs not yet fixed. "
    "Lands post-soak per docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md."
)


def _expected_repo_root() -> Path:
    """Repo root derived from this test file's location (not from env)."""
    return Path(__file__).resolve().parent.parent


@pytest.mark.xfail(reason=_GOV_MONITOR_XFAIL_REASON, strict=True)
def test_default_logfile_resolves_to_repo_root_regardless_of_kalshi_home(monkeypatch):
    """`_DEFAULT_LOG` must point at <repo>/logs/governance/decisions.jsonl
    even when KALSHI_HOME is set to an unrelated path."""
    monkeypatch.setenv("KALSHI_HOME", "/tmp/nonexistent/kalshi_home_override")
    # Re-import to force the module-level constants to re-evaluate against the
    # patched env. importlib.reload preserves type identity for downstream use.
    import importlib
    from scripts import governance_monitor as _gm
    importlib.reload(_gm)

    expected = _expected_repo_root() / "logs" / "governance" / "decisions.jsonl"
    assert _gm._DEFAULT_LOG == expected, (
        f"_DEFAULT_LOG must ignore KALSHI_HOME and resolve to {expected}; "
        f"got {_gm._DEFAULT_LOG}"
    )


@pytest.mark.xfail(reason=_GOV_MONITOR_XFAIL_REASON, strict=True)
def test_default_overrides_resolves_to_repo_root_regardless_of_kalshi_home(monkeypatch):
    """`_DEFAULT_OVERRIDES` must point at <repo>/data/runtime_overrides.yaml
    even when KALSHI_HOME is set to an unrelated path."""
    monkeypatch.setenv("KALSHI_HOME", "/tmp/nonexistent/kalshi_home_override")
    import importlib
    from scripts import governance_monitor as _gm
    importlib.reload(_gm)

    expected = _expected_repo_root() / "data" / "runtime_overrides.yaml"
    assert _gm._DEFAULT_OVERRIDES == expected, (
        f"_DEFAULT_OVERRIDES must ignore KALSHI_HOME and resolve to {expected}; "
        f"got {_gm._DEFAULT_OVERRIDES}"
    )


@pytest.mark.xfail(reason=_GOV_MONITOR_XFAIL_REASON, strict=True)
def test_aggregator_counts_governance_decision_parse_error(tmp_path):
    """Live JSONL emits `GOVERNANCE_DECISION_PARSE_ERROR`; the aggregator
    must count it under `per_day[<date>]["parse_error"]`."""
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        {"type": "GOVERNANCE_CYCLE_START", "cycle_id": "gc_2026-05-03_010000", "cadence": "fast"},
        {
            "type": "GOVERNANCE_DECISION_PARSE_ERROR",
            "cycle_id": "gc_2026-05-03_010000",
            "candidate_action": "disable_source",
            "candidate_target": "r/test",
            "error": "missing fields",
        },
        {"type": "GOVERNANCE_CYCLE_END", "cycle_id": "gc_2026-05-03_010000"},
    ])

    report = governance_monitor.analyze(
        log,
        overrides=tmp_path / "missing.yaml",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )

    day = report["per_day"]["2026-05-03"]
    assert day.get("parse_error") == 1, (
        f"GOVERNANCE_DECISION_PARSE_ERROR must increment parse_error; got per_day={day}"
    )


@pytest.mark.xfail(reason=_GOV_MONITOR_XFAIL_REASON, strict=True)
def test_aggregator_counts_governance_decision_validation_error(tmp_path):
    """`GOVERNANCE_DECISION_VALIDATION_ERROR` events must increment the
    per-day `validation_error` counter."""
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        {"type": "GOVERNANCE_CYCLE_START", "cycle_id": "gc_2026-05-03_010000", "cadence": "fast"},
        {
            "type": "GOVERNANCE_DECISION_VALIDATION_ERROR",
            "cycle_id": "gc_2026-05-03_010000",
            "error": "schema mismatch",
        },
        {"type": "GOVERNANCE_CYCLE_END", "cycle_id": "gc_2026-05-03_010000"},
    ])

    report = governance_monitor.analyze(
        log,
        overrides=tmp_path / "missing.yaml",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )

    day = report["per_day"]["2026-05-03"]
    assert day.get("validation_error") == 1, (
        f"GOVERNANCE_DECISION_VALIDATION_ERROR must increment validation_error; got per_day={day}"
    )


@pytest.mark.xfail(reason=_GOV_MONITOR_XFAIL_REASON, strict=True)
def test_aggregator_counts_batch_aborted_flag_on_cycle_end(tmp_path):
    """`batch_aborted=True` on a `GOVERNANCE_CYCLE_END` record must
    increment the per-day `batch_aborted` counter."""
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(log, [
        {"type": "GOVERNANCE_CYCLE_START", "cycle_id": "gc_2026-05-03_010000", "cadence": "fast"},
        {
            "type": "GOVERNANCE_CYCLE_END",
            "cycle_id": "gc_2026-05-03_010000",
            "duration_sec": 1.0,
            "batch_aborted": True,
            "decisions_proposed": 0,
            "decisions_made": 0,
            "decisions_applied": 0,
        },
    ])

    report = governance_monitor.analyze(
        log,
        overrides=tmp_path / "missing.yaml",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )

    day = report["per_day"]["2026-05-03"]
    assert day.get("batch_aborted") == 1, (
        f"batch_aborted=True on CYCLE_END must increment batch_aborted; got per_day={day}"
    )
