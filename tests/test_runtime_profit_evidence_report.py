import json
import sqlite3
from pathlib import Path

import pytest

from scripts import runtime_profit_evidence_report as runtime_profit_report


def _create_paper_db(path: Path, trade_ids: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT PRIMARY KEY,
                edge REAL NOT NULL,
                resolved INTEGER NOT NULL,
                pnl_dollars REAL,
                notional_bankroll_before REAL,
                notional_bankroll_after REAL,
                venue TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO paper_trades (
                trade_id, edge, resolved, pnl_dollars,
                notional_bankroll_before, notional_bankroll_after, venue
            ) VALUES (?, 0.07, 0, NULL, 100.0, 100.0, 'kalshi')
            """,
            [(trade_id,) for trade_id in trade_ids],
        )


def _attested_binding(database_path: Path) -> dict[str, object]:
    return {
        "status": "attested",
        "detail": "runtime binding attested",
        "pid": 12345,
        "cohort_id": "active-20260801",
        "cohort_kind": "active",
        "database_path": database_path,
    }


def test_uses_attested_runtime_database_not_legacy_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    root_db = data_dir / "paper_trades.db"
    _create_paper_db(root_db, ["legacy-1", "legacy-2", "legacy-3"])
    attested_db = data_dir / "paper_cohorts" / "active-20260801" / "paper_trades.db"
    attested_db.parent.mkdir(parents=True)
    _create_paper_db(attested_db, ["runtime-1", "runtime-2"])
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()

    report = runtime_profit_report.build_runtime_profit_evidence_report(
        _attested_binding(attested_db),
        data_dir=data_dir,
        edge_replay_root=replay_root,
    )

    assert report.runtime_cohort.cohort_id == "active-20260801"
    assert report.runtime_cohort.cohort_kind == "active"
    assert report.runtime_cohort.database_path == attested_db
    assert report.profit_evidence.paper.total_trades == 2


def test_unverified_runtime_binding_fails_closed_without_root_db_fallback(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _create_paper_db(data_dir / "paper_trades.db", ["legacy-1"])
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()

    with pytest.raises(
        runtime_profit_report.RuntimeProfitEvidenceError,
        match="runtime cohort binding is unverified",
    ):
        runtime_profit_report.build_runtime_profit_evidence_report(
            {"status": "unverified", "detail": "stale receipt"},
            data_dir=data_dir,
            edge_replay_root=replay_root,
        )


def test_symlinked_attested_database_fails_closed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside_db = tmp_path / "outside.db"
    _create_paper_db(outside_db, ["outside-1"])
    attested_db = data_dir / "paper_cohorts" / "active-20260801" / "paper_trades.db"
    attested_db.parent.mkdir(parents=True)
    attested_db.symlink_to(outside_db)
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir()

    with pytest.raises(
        runtime_profit_report.RuntimeProfitEvidenceError,
        match="escapes the data directory|not a regular file",
    ):
        runtime_profit_report.build_runtime_profit_evidence_report(
            _attested_binding(attested_db),
            data_dir=data_dir,
            edge_replay_root=replay_root,
        )


def test_replay_inventory_counts_corpus_candidates_without_claiming_current_oos(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    attested_db = data_dir / "paper_cohorts" / "active-20260801" / "paper_trades.db"
    attested_db.parent.mkdir(parents=True)
    _create_paper_db(attested_db, ["runtime-1"])
    replay_root = tmp_path / "edge_replay"
    replay_root.mkdir(parents=True)
    (replay_root / "corpus_candidate.jsonl").write_text('{"event":"candidate"}\n')

    report = runtime_profit_report.build_runtime_profit_evidence_report(
        _attested_binding(attested_db),
        data_dir=data_dir,
        edge_replay_root=replay_root,
    )
    payload = json.loads(runtime_profit_report.render_json(report))

    assert report.replay_inventory.top_level_corpus_candidate_count == 1
    assert report.replay_inventory.current_oos_replay_available is False
    assert payload["runtime_cohort"]["status"] == "attested"
    assert payload["profit_evidence"]["paper_expectancy"]["total_trades"] == 1
    assert payload["replay_inventory"]["current_oos_replay_available"] is False


def test_main_returns_unverified_json_without_profit_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        runtime_profit_report,
        "collect_live_runtime_binding",
        lambda *_args, **_kwargs: {"status": "unverified", "detail": "stale receipt"},
    )

    result = runtime_profit_report.main(["--home", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload == {
        "runtime_cohort": {"status": "unverified", "detail": "runtime cohort binding is unverified: stale receipt"}
    }


def test_main_reports_corrupt_attested_database_as_unverified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    attested_db = data_dir / "paper_cohorts" / "active-20260801" / "paper_trades.db"
    attested_db.parent.mkdir(parents=True)
    attested_db.write_text("not a sqlite database")
    monkeypatch.setattr(
        runtime_profit_report,
        "collect_live_runtime_binding",
        lambda *_args, **_kwargs: _attested_binding(attested_db),
    )

    result = runtime_profit_report.main(["--home", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["runtime_cohort"]["status"] == "unverified"
    assert "profit_evidence" not in payload
