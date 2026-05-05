from __future__ import annotations

import json
from pathlib import Path

from scripts import parse_error_retroactive_audit as parse_audit


def test_missing_required_fields_parse_error_regression_counts_all_seven_events(tmp_path: Path):
    log = tmp_path / "decisions.jsonl"
    rows = [
        {
            "type": "GOVERNANCE_DECISION_PARSE_ERROR",
            "cycle_id": f"cycle-{idx}",
            "candidate_action": "disable_source",
            "candidate_target": f"r/source{idx}",
            "error": "LLM output missing required fields: ['action', 'target']",
        }
        for idx in range(7)
    ]
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    report = parse_audit.analyze(log)

    assert report["total_parse_errors"] == 7
    assert report["root_causes"] == {"missing_required_fields": 7}
    assert report["candidate_actions"] == {"disable_source": 7}
