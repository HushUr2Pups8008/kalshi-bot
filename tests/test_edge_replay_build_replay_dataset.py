import json
import sqlite3
import sys
from pathlib import Path

import pytest

import scripts.edge_replay.build_replay_dataset as replay_builder
from scripts.edge_replay.build_replay_dataset import build_replay_dataset, filter_post_p0_rows
from tests._helpers import write_jsonl


def _paper_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ts TEXT,
                ticker TEXT,
                market_title TEXT,
                side TEXT,
                contracts INTEGER,
                price_cents INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                edge REAL,
                signal_headline TEXT,
                signal_source TEXT,
                resolved INTEGER,
                resolved_yes INTEGER,
                pnl_dollars REAL,
                series_ticker TEXT,
                signal_type TEXT,
                fast_lane_p REAL,
                accumulation_p REAL,
                structural_p REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                't1', '2026-05-01T00:00:00+00:00', 'KXTEST-26MAY01',
                'Will the test pass?', 'yes', 2, 40, 0.61, 40.0, 0.21,
                'Headline', 'Reuters', 1, 1, 1.20, 'KXTEST', 'llm',
                0.58, 0.59, 0.57
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _paper_db_with_sentinel(
    path: Path,
    *,
    sentinel: str,
    trades: list[tuple[str, str, int, float]],
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ts TEXT,
                ticker TEXT,
                market_title TEXT,
                side TEXT,
                contracts INTEGER,
                price_cents INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                edge REAL,
                signal_headline TEXT,
                signal_source TEXT,
                resolved INTEGER,
                resolved_yes INTEGER,
                pnl_dollars REAL,
                series_ticker TEXT,
                signal_type TEXT,
                fast_lane_p REAL,
                accumulation_p REAL,
                structural_p REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?)",
            ("p0_price_fix_deployed_ts", sentinel),
        )
        for trade_id, ts, price_cents, estimated_prob in trades:
            conn.execute(
                """
                INSERT INTO paper_trades VALUES (
                    ?, ?, 'KXTEST-26MAY01', 'Will the test pass?', 'yes',
                    1, ?, ?, ?, ?,
                    'Headline', 'Reuters', 1, 1, 0.00, 'KXTEST', 'llm',
                    0.58, 0.59, 0.57
                )
                """,
                (
                    trade_id,
                    ts,
                    price_cents,
                    estimated_prob,
                    float(price_cents),
                    estimated_prob - (price_cents / 100.0),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _markets_file(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "ticker": "KXTEST-26MAY01",
                    "title": "Will the test pass?",
                    "series_ticker": "KXTEST",
                    "resolved_yes": True,
                    "result": "yes",
                }
            ]
        ),
        encoding="utf-8",
    )


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    markets_path: Path,
    paper_trades_db: Path,
    output_path: Path,
) -> int:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_replay_dataset.py",
            "--markets",
            str(markets_path),
            "--paper-trades-db",
            str(paper_trades_db),
            "--evidence-store-db",
            str(tmp_path / "missing_evidence.db"),
            "--trade-log",
            str(tmp_path / "missing_trade_log.jsonl"),
            "--output",
            str(output_path),
        ],
    )
    return replay_builder.main()


def test_build_replay_dataset_joins_paper_trades_to_resolved_markets(tmp_path):
    db_path = tmp_path / "paper_trades.db"
    _paper_db(db_path)
    markets_path = tmp_path / "markets.json"
    markets_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "KXTEST-26MAY01",
                    "title": "Will the test pass?",
                    "series_ticker": "KXTEST",
                    "resolved_yes": True,
                    "result": "yes",
                }
            ]
        ),
        encoding="utf-8",
    )

    rows = build_replay_dataset(markets_path=markets_path, paper_trades_db=db_path, trade_logs=[])

    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXTEST-26MAY01"
    assert rows[0]["signal_source"] == "Reuters"
    assert rows[0]["decision_kind"] == "paper_trade"
    assert rows[0]["resolved_yes"] is True
    assert rows[0]["model_prob"] == 0.61
    assert rows[0]["market_yes_price"] == 40.0


def test_main_filters_pre_p0_rows_when_bot_state_sentinel_exists(tmp_path, monkeypatch):
    sentinel = "2026-05-13T00:00:00+00:00"
    db_path = tmp_path / "paper_trades.db"
    _paper_db_with_sentinel(
        db_path,
        sentinel=sentinel,
        trades=[
            ("pre", "2026-05-12T23:59:59+00:00", 41, 0.61),
            ("post", "2026-05-13T00:00:00+00:00", 43, 0.63),
        ],
    )
    markets_path = tmp_path / "markets.json"
    _markets_file(markets_path)
    output_path = tmp_path / "out" / "replay.jsonl"

    exit_code = _run_main(
        monkeypatch,
        tmp_path,
        markets_path=markets_path,
        paper_trades_db=db_path,
        output_path=output_path,
    )

    assert exit_code == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["decision_ts"] for row in rows] == ["2026-05-13T00:00:00+00:00"]
    assert rows[0]["market_yes_price"] == 43.0


def test_build_replay_dataset_can_apply_explicit_post_p0_sentinel(tmp_path):
    sentinel = "2026-05-13T00:00:00+00:00"
    db_path = tmp_path / "paper_trades.db"
    _paper_db_with_sentinel(
        db_path,
        sentinel=sentinel,
        trades=[
            ("pre", "2026-05-12T23:59:59+00:00", 41, 0.61),
            ("post", "2026-05-13T00:00:00+00:00", 43, 0.63),
        ],
    )
    markets_path = tmp_path / "markets.json"
    _markets_file(markets_path)

    rows = build_replay_dataset(
        markets_path=markets_path,
        paper_trades_db=db_path,
        trade_logs=[],
        post_p0_sentinel=sentinel,
        validate_post_p0_quality=True,
    )

    assert [row["decision_ts"] for row in rows] == ["2026-05-13T00:00:00+00:00"]


def test_main_rejects_missing_p0_sentinel_before_writing_output(tmp_path, monkeypatch):
    db_path = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE bot_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE paper_trades (
                trade_id TEXT,
                ts TEXT,
                ticker TEXT,
                market_title TEXT,
                side TEXT,
                contracts INTEGER,
                price_cents INTEGER,
                estimated_prob REAL,
                market_yes_price REAL,
                edge REAL,
                signal_headline TEXT,
                signal_source TEXT,
                resolved INTEGER,
                resolved_yes INTEGER,
                pnl_dollars REAL,
                series_ticker TEXT,
                signal_type TEXT,
                fast_lane_p REAL,
                accumulation_p REAL,
                structural_p REAL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades VALUES (
                'pre', '2026-05-12T23:59:59+00:00', 'KXTEST-26MAY01',
                'Will the test pass?', 'yes', 1, 41, 0.61, 41.0, 0.20,
                'Headline', 'Reuters', 1, 1, 0.00, 'KXTEST', 'llm',
                0.58, 0.59, 0.57
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    markets_path = tmp_path / "markets.json"
    _markets_file(markets_path)
    output_path = tmp_path / "out" / "replay.jsonl"

    with pytest.raises(AssertionError, match="p0_price_fix_deployed_ts sentinel missing"):
        _run_main(
            monkeypatch,
            tmp_path,
            markets_path=markets_path,
            paper_trades_db=db_path,
            output_path=output_path,
        )

    assert not output_path.exists()


def test_filter_post_p0_rows_treats_naive_decision_ts_as_utc():
    kept = list(
        filter_post_p0_rows(
            [
                {"decision_ts": "2026-05-12T23:59:59", "ticker": "PRE"},
                {"decision_ts": "2026-05-13T00:00:01", "ticker": "POST"},
            ],
            "2026-05-13T00:00:00+00:00",
        )
    )

    assert [row["ticker"] for row in kept] == ["POST"]


def test_main_rejects_all_50_post_sentinel_corpus_before_writing_output(
    tmp_path,
    monkeypatch,
):
    sentinel = "2026-05-13T00:00:00+00:00"
    db_path = tmp_path / "paper_trades.db"
    _paper_db_with_sentinel(
        db_path,
        sentinel=sentinel,
        trades=[
            ("post-1", "2026-05-13T00:00:00+00:00", 50, 0.50),
            ("post-2", "2026-05-13T00:01:00+00:00", 50, 0.50),
        ],
    )
    markets_path = tmp_path / "markets.json"
    _markets_file(markets_path)
    output_path = tmp_path / "out" / "replay.jsonl"

    with pytest.raises(AssertionError, match="P0-REG-022"):
        _run_main(
            monkeypatch,
            tmp_path,
            markets_path=markets_path,
            paper_trades_db=db_path,
            output_path=output_path,
        )

    assert not output_path.exists()


def test_build_replay_dataset_includes_skipped_trade_log_rows(tmp_path):
    markets_path = tmp_path / "markets.json"
    markets_path.write_text(
        json.dumps([{"ticker": "KXTEST-26MAY02", "series_ticker": "KXTEST", "resolved_yes": False}]),
        encoding="utf-8",
    )
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "SKIPPED",
                "ts": "2026-05-01T00:05:00+00:00",
                "ticker": "KXTEST-26MAY02",
                "source": "NYT > World News",
                "edge": 0.0,
                "estimated_prob": 0.50,
                "market_yes_price": 50,
                "reason": "edge +0.0000 below min_edge 0.02",
            }
        ],
    )

    rows = build_replay_dataset(markets_path=markets_path, paper_trades_db=None, trade_logs=[log_path])

    assert len(rows) == 1
    assert rows[0]["decision_kind"] == "skipped"
    assert rows[0]["signal_source"] == "NYT > World News"
    assert rows[0]["trade_gate_reason"] == "edge +0.0000 below min_edge 0.02"


def test_build_replay_dataset_excludes_trade_log_paper_trade_duplicates(tmp_path):
    markets_path = tmp_path / "markets.json"
    markets_path.write_text(
        json.dumps([{"ticker": "KXTEST-26MAY02", "series_ticker": "KXTEST", "resolved_yes": False}]),
        encoding="utf-8",
    )
    log_path = tmp_path / "trades.jsonl"
    write_jsonl(
        log_path,
        [
            {
                "type": "PAPER_TRADE",
                "ts": "2026-05-01T00:05:00+00:00",
                "ticker": "KXTEST-26MAY02",
                "source": "paper-trade-roundtrip",
                "edge": 0.20,
                "market_yes_price": 50,
            }
        ],
    )

    rows = build_replay_dataset(markets_path=markets_path, paper_trades_db=None, trade_logs=[log_path])

    assert rows == []


def test_build_replay_dataset_includes_evidence_store_dossier_updates(tmp_path):
    markets_path = tmp_path / "markets.json"
    markets_path.write_text(
        json.dumps([{"ticker": "KXTEST-26MAY03", "series_ticker": "KXTEST", "resolved_yes": True}]),
        encoding="utf-8",
    )
    evidence_db = tmp_path / "evidence_store.db"
    conn = sqlite3.connect(evidence_db)
    try:
        conn.executescript(
            """
            CREATE TABLE evidence (
                evidence_id TEXT,
                market_ticker TEXT,
                source TEXT,
                source_class TEXT,
                headline TEXT,
                ingested_ts TEXT,
                update_type TEXT
            );
            CREATE TABLE dossier_updates (
                market_ticker TEXT,
                dossier_version INTEGER,
                created_ts TEXT,
                trigger_evidence_id TEXT,
                prior_estimate REAL,
                new_estimate REAL,
                update_delta REAL,
                confidence_before REAL,
                confidence_after REAL,
                update_type TEXT,
                llm_called INTEGER
            );
            """
        )
        conn.execute(
            """
            INSERT INTO evidence VALUES (
                'ev1', 'KXTEST-26MAY03', 'Reuters', 'publisher_rss',
                'Headline', '2026-05-01T00:00:00+00:00', 'state'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dossier_updates VALUES (
                'KXTEST-26MAY03', 1, '2026-05-01T00:01:00+00:00',
                'ev1', 0.50, 0.62, 0.12, 0.20, 0.40, 'state', 1
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    rows = build_replay_dataset(
        markets_path=markets_path,
        paper_trades_db=None,
        trade_logs=[],
        evidence_store_db=evidence_db,
    )

    assert len(rows) == 1
    assert rows[0]["decision_kind"] == "dossier_update"
    assert rows[0]["signal_source"] == "Reuters"
    assert rows[0]["news_class"] == "publisher_rss"
    assert rows[0]["model_prob"] == 0.62


def test_build_replay_dataset_applies_decision_time_price_map(tmp_path):
    markets_path = tmp_path / "markets.json"
    markets_path.write_text(
        json.dumps([{"ticker": "KXTEST-26MAY04", "series_ticker": "KXTEST", "resolved_yes": True}]),
        encoding="utf-8",
    )
    evidence_db = tmp_path / "evidence_store.db"
    conn = sqlite3.connect(evidence_db)
    try:
        conn.executescript(
            """
            CREATE TABLE evidence (
                evidence_id TEXT,
                market_ticker TEXT,
                source TEXT,
                source_class TEXT,
                headline TEXT,
                ingested_ts TEXT,
                update_type TEXT
            );
            CREATE TABLE dossier_updates (
                market_ticker TEXT,
                dossier_version INTEGER,
                created_ts TEXT,
                trigger_evidence_id TEXT,
                prior_estimate REAL,
                new_estimate REAL,
                update_delta REAL,
                confidence_before REAL,
                confidence_after REAL,
                update_type TEXT,
                llm_called INTEGER
            );
            """
        )
        conn.execute("INSERT INTO evidence VALUES ('ev1', 'KXTEST-26MAY04', 'Reuters', 'publisher_rss', 'Headline', '2026-05-01T00:00:00+00:00', 'state')")
        conn.execute("INSERT INTO dossier_updates VALUES ('KXTEST-26MAY04', 1, '2026-05-01T00:01:00+00:00', 'ev1', 0.50, 0.70, 0.20, 0.20, 0.90, 'state', 1)")
        conn.commit()
    finally:
        conn.close()
    price_path = tmp_path / "prices.json"
    price_path.write_text(
        json.dumps({"KXTEST-26MAY04": [{"ts": "2026-05-01T00:00:30+00:00", "yes_price": 44.0}]}),
        encoding="utf-8",
    )

    rows = build_replay_dataset(
        markets_path=markets_path,
        paper_trades_db=None,
        trade_logs=[],
        evidence_store_db=evidence_db,
        historical_prices_path=price_path,
    )

    assert rows[0]["market_yes_price"] == 44.0
    assert rows[0]["edge"] == pytest.approx(0.26)
