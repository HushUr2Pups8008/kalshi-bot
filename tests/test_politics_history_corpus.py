"""Politics history corpus stays out of replay-ci discovery."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.edge_replay.replay_gate import (
    DEFAULT_CORPUS_DIR,
    _discover_all_diverse_corpora,
    run_replay_gate,
)

FIXTURE = Path("tests/fixtures/edge_replay/corpus_politics_desk_history.jsonl")
WORKFLOW = Path(".github/workflows/replay-ci-gate.yml")


def test_fixture_is_diverse_and_outside_discovery(tmp_path: Path) -> None:
    """A committed fixture must not turn the known insufficient-corpus red into a new one."""
    assert FIXTURE.parent != DEFAULT_CORPUS_DIR
    assert FIXTURE.parent.name != "edge_replay" or FIXTURE.parent.parent.name != "logs"
    text = FIXTURE.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    families = {
        family
        for row in rows
        for family in row["market_families"]
    }
    assert len(families) >= 2
    assert {row["evidence_class"] for row in rows} == {"desk_history"}
    for banned in (
        "api_key",
        "BEGIN ",
        "ghp_",
        "github_pat_",
        "Authorization",
        "signal_headline",
        "client_order_id",
        "registered_oos",
    ):
        assert banned not in text
    # The replay workflow is gone. This fixture must not be a CI corpus.
    assert not WORKFLOW.exists()

    corpus_dir = tmp_path / "logs" / "edge_replay"
    verdict = run_replay_gate(
        changed_files=[Path("trading/executor.py")],
        corpus_dir=corpus_dir,
        ci_run_dir=tmp_path / "ci_runs",
        governance_dir=tmp_path / "governance",
        commit_sha="politics-history-fixture",
    )
    assert verdict.pass_ is False
    assert verdict.tier == "T3"
    assert verdict.failure_reason == "insufficient corpus"
    diverse, in_period, _notes = _discover_all_diverse_corpora(corpus_dir)
    assert diverse == []
    assert in_period == []
    from scripts.simulations.politics_desk_replay import format_politics_replay

    table = format_politics_replay()
    assert "failure_reason=insufficient corpus" in table
    assert "desk_history" in table
