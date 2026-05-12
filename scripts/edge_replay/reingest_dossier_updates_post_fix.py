#!/usr/bin/env python3
"""Cycle-15B C9: rebuild dossier updates through the post-fix extraction path.

Writes a separate database. The source evidence store is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.dossier_builder import classify_update, update_dossier
from analysis.evidence_scorer import score_evidence
from analysis.evidence_types import Dossier, Evidence
from analysis.signal_analyzer import keyword_estimate
from feeds import NewsItem
from kalshi import KalshiMarket


SCHEMA_PATH = Path("docs/evidence_store_schema.sql")
DEFAULT_SOURCE_DB = Path("data/evidence_store.db")
DEFAULT_OUTPUT_DB = Path("data/dossier_updates_post_fix.db")
DEFAULT_AUDIT = Path("logs/edge_replay/cycle15b/reingestion_audit.json")
DEFAULT_MARKETS = Path("logs/edge_replay/cycle13_live/resolved_markets_full.json")
BASE_PROBABILITY = 0.50


def _load_markets(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.values()
    return {str(row.get("ticker")): row for row in rows if row.get("ticker")}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_output_db(path: Path) -> sqlite3.Connection:
    conn = _connect(path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("CREATE TABLE pre_fix_dossier_updates AS SELECT * FROM dossier_updates WHERE 0")
    conn.execute(
        """
        CREATE TABLE reingestion_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return conn


def _copy_pre_fix_updates(source: sqlite3.Connection, dest: sqlite3.Connection) -> int:
    rows = source.execute("SELECT * FROM dossier_updates ORDER BY market_ticker, dossier_version").fetchall()
    if not rows:
        return 0
    columns = rows[0].keys()
    placeholders = ", ".join("?" for _ in columns)
    dest.executemany(
        f"INSERT INTO pre_fix_dossier_updates ({', '.join(columns)}) VALUES ({placeholders})",
        [[row[column] for column in columns] for row in rows],
    )
    return len(rows)


def _source_evidence_rows(source: sqlite3.Connection) -> list[sqlite3.Row]:
    return source.execute(
        """
        SELECT *
        FROM evidence
        ORDER BY market_ticker ASC, ingested_ts ASC, evidence_id ASC
        """
    ).fetchall()


def _market(row: sqlite3.Row, market_meta: dict[str, Any] | None) -> KalshiMarket:
    ticker = str(row["market_ticker"])
    title = str((market_meta or {}).get("title") or ticker)
    yes_price = float((market_meta or {}).get("yes_price") or 50)
    yes_int = max(1, min(99, int(round(yes_price))))
    return KalshiMarket(
        ticker=ticker,
        title=title,
        yes_bid=yes_price,
        yes_ask=yes_price,
        yes_price=yes_price,
        volume=1,
        open_interest=1,
        close_time=str((market_meta or {}).get("close_time") or "2026-05-01T00:00:00Z"),
        status=str((market_meta or {}).get("status") or "open"),
        series_ticker=str((market_meta or {}).get("series_ticker") or ticker.split("-", 1)[0]),
        # P-5 CR-C: post-P0 fields required for guarded legacy reads.
        yes_bid_cents=yes_int,
        yes_ask_cents=yes_int,
        no_bid_cents=100 - yes_int,
        no_ask_cents=100 - yes_int,
        price_available=True,
        price_source="rest_list",
        price_method="dollars_fixed_point",
    )


def _news(row: sqlite3.Row) -> NewsItem:
    return NewsItem(
        headline=str(row["headline"] or ""),
        body="",
        source=str(row["source"] or "unknown"),
        url=str(row["url"] or f"evidence://{row['evidence_id']}"),
    )


def _initial_dossier(evidence: Evidence) -> Dossier:
    return Dossier(
        market_ticker=evidence.market_ticker,
        dossier_version=0,
        confidence=0.0,
        drift_suspect=False,
        in_recovery=False,
        created_ts=evidence.ingested_ts,
        updated_ts=evidence.ingested_ts,
    )


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _evidence_from_row(row: sqlite3.Row, implied_probability: float) -> Evidence:
    return Evidence(
        evidence_id=str(row["evidence_id"]),
        market_ticker=str(row["market_ticker"]),
        source=str(row["source"]),
        source_class=str(row["source_class"]),
        headline=str(row["headline"]),
        ingested_ts=str(row["ingested_ts"]),
        implied_probability=implied_probability,
        content_hash=str(row["content_hash"]),
        url=row["url"],
        published_ts=row["published_ts"],
    )


def _insert_dossier(conn: sqlite3.Connection, dossier: Dossier) -> None:
    conn.execute(
        """
        INSERT INTO dossiers (
            market_ticker, dossier_version, current_estimate, confidence,
            prior_estimate, drift_suspect, in_recovery, freeze_started_ts,
            recovery_started_ts, recovery_until_ts, last_cross_class_state_update_ts,
            created_ts, updated_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_ticker) DO UPDATE SET
            dossier_version = excluded.dossier_version,
            current_estimate = excluded.current_estimate,
            confidence = excluded.confidence,
            prior_estimate = excluded.prior_estimate,
            drift_suspect = excluded.drift_suspect,
            in_recovery = excluded.in_recovery,
            freeze_started_ts = excluded.freeze_started_ts,
            recovery_started_ts = excluded.recovery_started_ts,
            recovery_until_ts = excluded.recovery_until_ts,
            last_cross_class_state_update_ts = excluded.last_cross_class_state_update_ts,
            updated_ts = excluded.updated_ts
        """,
        (
            dossier.market_ticker,
            dossier.dossier_version,
            dossier.current_estimate,
            dossier.confidence,
            dossier.prior_estimate,
            int(dossier.drift_suspect),
            int(dossier.in_recovery),
            dossier.freeze_started_ts,
            dossier.recovery_started_ts,
            dossier.recovery_until_ts,
            dossier.last_cross_class_state_update_ts,
            dossier.created_ts,
            dossier.updated_ts,
        ),
    )


def _insert_evidence(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    evidence: Evidence,
    *,
    update_type: str,
    quality_score: float,
    original_weight: float,
    is_duplicate: bool,
    correlation_discount_applied: bool,
    dossier_version_before: int,
    dossier_version_after: int,
) -> None:
    raw_payload = json.dumps(
        {
            "cycle": "15b_post_fix_reingestion",
            "implied_probability": evidence.implied_probability,
            "source_evidence_id": evidence.evidence_id,
        },
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO evidence (
            evidence_id, market_ticker, source, source_class, headline, url,
            published_ts, ingested_ts, raw_payload_json, content_hash,
            headline_ngram_fingerprint, correlation_cluster_id, is_duplicate,
            correlation_discount_applied, update_type, quality_score,
            original_weight, dossier_version_before, dossier_version_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence.evidence_id,
            evidence.market_ticker,
            evidence.source,
            evidence.source_class,
            evidence.headline,
            evidence.url,
            evidence.published_ts,
            evidence.ingested_ts,
            raw_payload,
            evidence.content_hash,
            row["headline_ngram_fingerprint"],
            row["correlation_cluster_id"],
            int(is_duplicate),
            int(correlation_discount_applied),
            update_type,
            quality_score,
            original_weight,
            dossier_version_before,
            dossier_version_after,
        ),
    )


def _insert_update(
    conn: sqlite3.Connection,
    *,
    current: Dossier,
    updated: Dossier,
    evidence_id: str,
    update_type: str,
) -> None:
    prior = current.current_estimate
    new = updated.current_estimate
    delta = 0.0 if prior is None or new is None else new - prior
    conn.execute(
        """
        INSERT INTO dossier_updates (
            market_ticker, dossier_version, created_ts, trigger_evidence_id,
            prior_estimate, new_estimate, update_delta, confidence_before,
            confidence_after, update_type, llm_called, drift_suspect, in_recovery
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            updated.market_ticker,
            updated.dossier_version,
            updated.updated_ts,
            evidence_id,
            prior,
            new,
            delta,
            current.confidence,
            updated.confidence,
            update_type,
            0,
            int(updated.drift_suspect),
            int(updated.in_recovery),
        ),
    )


def _database_digest(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        lines: list[str] = []
        for table in ("evidence", "dossiers", "dossier_updates", "pre_fix_dossier_updates"):
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            lines.append(table)
            lines.extend(json.dumps(list(row), sort_keys=True, default=str) for row in rows)
        payload = "\n".join(lines).encode("utf-8")
    finally:
        conn.close()
    return hashlib.sha256(payload).hexdigest()


def rebuild_post_fix_db(
    source_db: Path,
    output_db: Path,
    audit_path: Path,
    markets_path: Path | None = DEFAULT_MARKETS,
) -> dict[str, Any]:
    source_db = Path(source_db)
    output_db = Path(output_db)
    audit_path = Path(audit_path)
    markets = _load_markets(markets_path)
    temp_db = output_db.with_suffix(output_db.suffix + ".tmp")
    if temp_db.exists():
        temp_db.unlink()

    source = _connect(source_db)
    dest = _init_output_db(temp_db)
    try:
        pre_fix_count = _copy_pre_fix_updates(source, dest)
        evidence_rows = _source_evidence_rows(source)
        dossiers: dict[str, Dossier] = {}
        recent: dict[str, list[Evidence]] = {}
        extraction_nonzero = 0
        for row in evidence_rows:
            market = _market(row, markets.get(str(row["market_ticker"])))
            prob, *_ = keyword_estimate(_news(row), market, base_probability=BASE_PROBABILITY)
            if abs(float(prob) - BASE_PROBABILITY) > 0.0:
                extraction_nonzero += 1
            evidence = _evidence_from_row(row, float(prob))
            current = dossiers.get(evidence.market_ticker) or _initial_dossier(evidence)
            if evidence.market_ticker not in dossiers:
                _insert_dossier(dest, current)
            score = score_evidence(evidence, recent.get(evidence.market_ticker, []))
            update_type = classify_update(current, score)
            updated = update_dossier(current, score, update_type, now=_parse_ts(evidence.ingested_ts))
            _insert_evidence(
                dest,
                row,
                evidence,
                update_type=update_type,
                quality_score=score.quality_score,
                original_weight=score.original_weight,
                is_duplicate=score.is_duplicate,
                correlation_discount_applied=score.correlation_discount_applied,
                dossier_version_before=current.dossier_version,
                dossier_version_after=updated.dossier_version,
            )
            _insert_update(dest, current=current, updated=updated, evidence_id=evidence.evidence_id, update_type=update_type)
            _insert_dossier(dest, updated)
            dossiers[evidence.market_ticker] = updated
            recent.setdefault(evidence.market_ticker, []).append(evidence)

        dest.execute(
            "INSERT INTO reingestion_metadata (key, value) VALUES (?, ?)",
            ("cycle_15b_c7_deploy_commit", "2222227"),
        )
        dest.execute(
            "INSERT INTO reingestion_metadata (key, value) VALUES (?, ?)",
            ("cycle_15b_c7_deploy_ts", "2026-05-07T00:00:00+00:00"),
        )
        dest.commit()
    finally:
        source.close()
        dest.close()

    os.replace(temp_db, output_db)
    digest = _database_digest(output_db)
    audit = {
        "source_db": str(source_db),
        "output_db": str(output_db),
        "markets_path": str(markets_path) if markets_path else None,
        "source_evidence_rows": len(evidence_rows),
        "pre_fix_dossier_updates_rows": pre_fix_count,
        "post_fix_dossier_updates_rows": len(evidence_rows),
        "post_fix_dossiers_rows": len(dossiers),
        "post_fix_nonzero_extraction_rows": extraction_nonzero,
        "pre_fix_rows_recoverable": True,
        "atomic_write": True,
        "idempotence_sha256": digest,
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    args = parser.parse_args()
    audit = rebuild_post_fix_db(args.source_db, args.output_db, args.audit, args.markets)
    print(json.dumps({k: audit[k] for k in ("output_db", "post_fix_dossier_updates_rows", "idempotence_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
