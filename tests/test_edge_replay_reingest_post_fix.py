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
    assert first["skipped_no_price_rows"] == 0
    conn = sqlite3.connect(out1)
    try:
        pre_count = conn.execute("SELECT count(*) FROM pre_fix_dossier_updates").fetchone()[0]
        post = conn.execute("SELECT new_estimate, update_delta FROM dossier_updates ORDER BY dossier_version").fetchall()
    finally:
        conn.close()
    assert pre_count == 2
    assert post[0][0] > 0.5
    assert post[1][1] == 0.0


def test_rebuild_post_fix_db_skips_rows_with_no_price_metadata(tmp_path):
    """v0.30.1 follow-up — P-4 LD-2: no silent-50.

    When the markets fixture is missing entirely or carries no `yes_price`
    field for a ticker, the post-fix rebuild must fail closed for that row
    (skip + audit-counter), never silently fabricate a 50¢ midpoint.
    """
    source = tmp_path / "source.db"
    out = tmp_path / "post.db"
    audit_path = tmp_path / "audit.json"
    markets = tmp_path / "markets.json"
    _init_source_db(source)
    # Two evidence rows on `KXFISAEXTEND-26APR-MAY01`; markets fixture
    # carries the ticker but NO `yes_price` field — must NOT fall back
    # to 50.
    markets.write_text(
        json.dumps(
            [
                {
                    "ticker": "KXFISAEXTEND-26APR-MAY01",
                    "title": "Will legislation that reauthorizes FISA Section 702 authority become law before May 1, 2026?",
                    # NOTE: yes_price intentionally absent.
                    "close_time": "2026-05-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = rebuild_post_fix_db(source, out, audit_path, markets)

    assert result["source_evidence_rows"] == 2
    assert result["skipped_no_price_rows"] == 2
    assert result["post_fix_dossier_updates_rows"] == 0
    assert result["post_fix_dossiers_rows"] == 0

    # Audit JSON on disk reflects the skip — operators reading the audit
    # never see a silently-fabricated 50¢ row in the output corpus.
    audit_blob = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_blob["skipped_no_price_rows"] == 2
    assert audit_blob["post_fix_dossier_updates_rows"] == 0


def test_rebuild_post_fix_db_skips_rows_with_no_markets_entry(tmp_path):
    """v0.30.1 follow-up — P-4 LD-2: no silent-50.

    When the markets fixture is empty (no entry for the ticker at all),
    the row must also be skipped — `market_meta is None` is the same
    fail-closed path as `market_meta.get("yes_price") is None`.
    """
    source = tmp_path / "source.db"
    out = tmp_path / "post.db"
    audit_path = tmp_path / "audit.json"
    markets = tmp_path / "markets.json"
    _init_source_db(source)
    # Empty markets list — no entry for any ticker.
    markets.write_text(json.dumps([]), encoding="utf-8")

    result = rebuild_post_fix_db(source, out, audit_path, markets)

    assert result["source_evidence_rows"] == 2
    assert result["skipped_no_price_rows"] == 2
    assert result["post_fix_dossier_updates_rows"] == 0


def test_market_helper_returns_none_when_yes_price_missing():
    """Direct unit-level lock on `_market` — explicit `yes_price` values
    (including 0 and 50) are honored; only the absent/None path is rejected.
    """
    from scripts.edge_replay.reingest_dossier_updates_post_fix import _market

    class _FakeRow:
        def __init__(self, ticker: str) -> None:
            self._data = {"market_ticker": ticker}

        def __getitem__(self, key: str) -> str:
            return self._data[key]

    row = _FakeRow("KXTEST-1")

    # No markets fixture row at all → reject.
    assert _market(row, None) is None

    # Markets fixture entry exists but no yes_price field → reject.
    assert _market(row, {"title": "Test"}) is None

    # Markets fixture entry exists with yes_price=None → reject.
    assert _market(row, {"title": "Test", "yes_price": None}) is None

    # Markets fixture entry exists with explicit yes_price=0 → accepted.
    m_zero = _market(row, {"title": "Test", "yes_price": 0})
    assert m_zero is not None
    assert m_zero.yes_price == 0.0

    # Markets fixture entry exists with explicit yes_price=50 → accepted.
    m_fifty = _market(row, {"title": "Test", "yes_price": 50})
    assert m_fifty is not None
    assert m_fifty.yes_price == 50.0

    # Markets fixture entry with realistic dollars value → accepted.
    m_real = _market(row, {"title": "Test", "yes_price": 37.0})
    assert m_real is not None
    assert m_real.yes_price == 37.0
