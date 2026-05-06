from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts import governance_monitor
from tests.test_governance_monitor import _write_jsonl


@pytest.mark.xfail(
    reason=(
        "PROFIT-GOV-003 not deployed: governance_monitor.py does not yet count "
        "GOVERNANCE_DECISION_PARSE_ERROR missing_required_fields rows."
    ),
    strict=True,
)
def test_missing_required_fields_parse_error_counts_against_day_budget(tmp_path):
    log = tmp_path / "decisions.jsonl"
    _write_jsonl(
        log,
        [
            {
                "type": "GOVERNANCE_DECISION_PARSE_ERROR",
                "cycle_id": "gc_2026-05-03_020000",
                "error": "missing_required_fields",
                "missing_required_fields": ["candidate_target", "reasoning"],
                "raw_response": "{}",
            },
        ],
    )

    report = governance_monitor.analyze(
        log,
        overrides=tmp_path / "missing.yaml",
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )

    day = report["per_day"]["2026-05-03"]
    assert day["parse_error"] == 1

