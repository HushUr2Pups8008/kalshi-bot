import json
import sqlite3
from pathlib import Path

from scripts.edge_replay.reingest_dossier_updates_post_fix import rebuild_post_fix_db


def _init_source_db(path: Path) -> None:
    schema = Path("docs/evidence_store_schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        conn.execute(
            """
            INSERT INTO dossiers (
                market_ticker, dossier_version, current_estimate, confidence,
                prior_estimate, created_ts, updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("KXFISAEXTEND-26APR-MAY01", 2, 0.5, 0.2, 0.5, "2026-04-30T00:00:00+00:00", "2026-04-30T02:00:00+00:00"),
        )
        rows = [
            ("e1", "FISA Section 702 reauthorization signed into law on April 30, 2026", "2026-04-30T01:00:00+00:00"),
            ("e2", "FISA Section 702 reauthorization signed into law on April 30, 2026", "2026-04-30T02:00:00+00:00"),
        ]
        for idx, (evidence_id, headline, ingested_ts) in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO evidence (
                    evidence_id, market_ticker, source, source_class, headline,
                    url, published_ts, ingested_ts, raw_payload_json, content_hash,
                    update_type, quality_score, original_weight,
                    dossier_version_before, dossier_version_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    "KXFISAEXTEND-26APR-MAY01",
                    "AP News",
                    "news",
                    headline,
                    None,
                    ingested_ts,
                    ingested_ts,
                    "{}",
                    evidence_id,
                    "state",
                    0.7,
                    0.7 / idx,
                    idx - 1,
                    idx,
                ),
            )
            conn.execute(
                """
                INSERT INTO dossier_updates (
                    market_ticker, dossier_version, created_ts, trigger_evidence_id,
                    prior_estimate, new_estimate, update_delta, confidence_before,
                    confidence_after, update_type, llm_called, drift_suspect, in_recovery
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "KXFISAEXTEND-26APR-MAY01",
                    idx,
                    ingested_ts,
                    evidence_id,
                    0.5,
                    0.5,
                    0.0,
                    0.1,
                    0.1,
                    "state",
                    0,
                    0,
                    0,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def test_rebuild_post_fix_db_preserves_pre_fix_rows_and_is_idempotent(tmp_path):
    source = tmp_path / "source.db"
    out1 = tmp_path / "post1.db"
    out2 = tmp_path / "post2.db"
    audit1 = tmp_path / "audit1.json"
    audit2 = tmp_path / "audit2.json"
    markets = tmp_path / "markets.json"
    _init_source_db(source)
    markets.write_text(
        json.dumps(
            [
                {
                    "ticker": "KXFISAEXTEND-26APR-MAY01",
                    "title": "Will legislation that reauthorizes FISA Section 702 authority become law before May 1, 2026?",
                    "yes_price": 50,
                    "close_time": "2026-05-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    first = rebuild_post_fix_db(source, out1, audit1, markets)
    second = rebuild_post_fix_db(source, out2, audit2, markets)

    assert first["idempotence_sha256"] == second["idempotence_sha256"]
    assert first["pre_fix_dossier_updates_rows"] == 2
    assert first["post_fix_dossier_updates_rows"] == 2
    conn = sqlite3.connect(out1)
    try:
        pre_count = conn.execute("SELECT count(*) FROM pre_fix_dossier_updates").fetchone()[0]
        post = conn.execute("SELECT new_estimate, update_delta FROM dossier_updates ORDER BY dossier_version").fetchall()
    finally:
        conn.close()
    assert pre_count == 2
    assert post[0][0] > 0.5
    assert post[1][1] == 0.0
