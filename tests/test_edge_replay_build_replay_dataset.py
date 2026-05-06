import json
import sqlite3
from pathlib import Path

from scripts.edge_replay.build_replay_dataset import build_replay_dataset
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
