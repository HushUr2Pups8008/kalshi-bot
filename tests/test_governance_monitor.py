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
