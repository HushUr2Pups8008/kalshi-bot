"""Politics history corpus stays out of replay-ci discovery."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.edge_replay.export_politics_history_corpus import (
    export_politics_history_corpus,
)
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
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "corpus_politics_desk_history" not in workflow
    assert "tests/fixtures/edge_replay" not in workflow

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


def test_exporter_writes_keyless_rows_and_drops_headlines(tmp_path: Path) -> None:
    db_path = tmp_path / "paper.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                resolved INTEGER,
                signal_headline TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE paper_trade_accounting (
                trade_id TEXT PRIMARY KEY,
                fee_net_pnl_dollars TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades
                (trade_id, ts, ticker, side, resolved, signal_headline)
            VALUES
                ('t1', '2026-08-29T00:00:00Z', 'KXTRUTHSOCIAL-26AUG29-T240',
                 'yes', 1, 'SECRET HEADLINE SHOULD NOT EXPORT'),
                ('t2', '2026-09-06T00:00:00Z', 'KXTRUMPACT-26SEP06-T4',
                 'no', 1, 'ANOTHER HEADLINE')
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trade_accounting (trade_id, fee_net_pnl_dollars)
            VALUES ('t1', '1.1286'), ('t2', '-7.6005')
            """
        )
    report = tmp_path / "live-fill-log.md"
    report.write_text(
        "\n".join(
            [
                "# do not log keys",
                "2026-10-04T16:02:48Z, KXTRUTHSOCIAL-26OCT10-B149, no, 84, 8, "
                "ed372fa8fa374a6592f5c7040a11f284, 201",
                "2026-10-12T19:11:24Z, KXTRUMPSAY-26OCT19-GOLF, yes, 89, 8, "
                "21c0d75937a64081b8c70a4f1baf0029, 201",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "logs" / "edge_replay" / "corpus_politics_history.jsonl"
    count = export_politics_history_corpus(
        paper_db=db_path,
        fill_report=report,
        output=output,
    )
    text = output.read_text(encoding="utf-8")
    assert count == 4
    assert "SECRET HEADLINE" not in text
    assert "ed372fa8" not in text
    assert "21c0d759" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    families = {row["market_families"][0] for row in rows}
    assert families == {"KXTRUTHSOCIAL", "KXTRUMPACT", "KXTRUMPSAY"}
    assert all(row["evidence_class"] == "desk_history" for row in rows)
