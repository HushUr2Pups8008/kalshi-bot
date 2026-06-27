"""Durable per-market research dossier storage.

The research gate uses this store to avoid relearning the same ticker from
scratch every time the live bot sees a neutral/no-keyword event.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from analysis.research_gate import ResearchEvidence
from config import DATA_DIR

_T = TypeVar("_T")

DEFAULT_RESEARCH_DOSSIER_DB_PATH = DATA_DIR / "evidence_store.db"


@dataclass(frozen=True)
class ResearchDossierSnapshot:
    market_ticker: str
    last_research_run_id: str | None
    last_researched_ts: str
    last_verdict_status: str
    last_skip_reason: str | None
    last_force_side: str | None
    last_estimated_probability: float | None
    last_confidence: float | None


class ResearchDossierStore:
    def __init__(self, db_path: Path = DEFAULT_RESEARCH_DOSSIER_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def add_evidence(
        self,
        market_ticker: str,
        research_run_id: str,
        evidence: ResearchEvidence,
    ) -> None:
        await self._run_market_write(
            market_ticker,
            lambda: self._add_evidence_sync(market_ticker, research_run_id, evidence),
        )

    async def record_research_run(
        self,
        market_ticker: str,
        research_run_id: str,
        *,
        trigger_headline: str,
        trigger_source: str,
        attempted: bool,
        summary: str,
        verdict_status: str,
        skip_reason: str | None = None,
        force_side: str | None = None,
        estimated_probability: float | None = None,
        confidence: float | None = None,
        queries: list[object] | None = None,
        evidence: list[ResearchEvidence] | None = None,
    ) -> None:
        await self._run_market_write(
            market_ticker,
            lambda: self._record_research_run_sync(
                market_ticker,
                research_run_id,
                trigger_headline=trigger_headline,
                trigger_source=trigger_source,
                attempted=attempted,
                summary=summary,
                verdict_status=verdict_status,
                skip_reason=skip_reason,
                force_side=force_side,
                estimated_probability=estimated_probability,
                confidence=confidence,
                queries=queries or [],
                evidence=evidence or [],
            ),
        )

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 50,
    ) -> list[ResearchEvidence]:
        return await asyncio.to_thread(self._get_recent_evidence_sync, market_ticker, limit)

    async def get_dossier_snapshot(self, market_ticker: str) -> ResearchDossierSnapshot | None:
        return await asyncio.to_thread(self._get_dossier_snapshot_sync, market_ticker)

    async def _run_market_write(self, market_ticker: str, operation: Callable[[], _T]) -> _T:
        lock = self._locks.setdefault(market_ticker, asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(operation)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_dossiers (
                    market_ticker TEXT PRIMARY KEY,
                    last_research_run_id TEXT,
                    last_researched_ts TEXT NOT NULL,
                    last_verdict_status TEXT NOT NULL,
                    last_skip_reason TEXT,
                    last_force_side TEXT,
                    last_estimated_probability REAL,
                    last_confidence REAL,
                    created_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    research_run_id TEXT PRIMARY KEY,
                    market_ticker TEXT NOT NULL,
                    trigger_headline TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    attempted INTEGER NOT NULL CHECK (attempted IN (0, 1)),
                    summary TEXT NOT NULL,
                    verdict_status TEXT NOT NULL,
                    skip_reason TEXT,
                    force_side TEXT,
                    estimated_probability REAL,
                    confidence REAL,
                    created_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    FOREIGN KEY (market_ticker) REFERENCES research_dossiers(market_ticker)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_run_queries (
                    research_run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    query_intent TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    PRIMARY KEY (research_run_id, ordinal),
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(research_run_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    market_ticker TEXT NOT NULL,
                    research_run_id TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    supports_direction TEXT NOT NULL,
                    supports_confidence REAL NOT NULL,
                    published_at TEXT,
                    retrieved_at TEXT,
                    metric_name TEXT,
                    metric_value REAL,
                    metric_unit TEXT,
                    extraction_confidence REAL,
                    contract_fingerprint TEXT,
                    raw_payload_json TEXT NOT NULL,
                    inserted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    FOREIGN KEY (market_ticker) REFERENCES research_dossiers(market_ticker),
                    FOREIGN KEY (research_run_id) REFERENCES research_runs(research_run_id)
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_evidence)").fetchall()
            }
            if "contract_fingerprint" not in columns:
                conn.execute("ALTER TABLE research_evidence ADD COLUMN contract_fingerprint TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_research_evidence_market_inserted
                ON research_evidence (market_ticker, inserted_at DESC)
                """
            )

    def _add_evidence_sync(
        self,
        market_ticker: str,
        research_run_id: str,
        evidence: ResearchEvidence,
    ) -> None:
        self._initialize_sync()
        self._ensure_dossier_and_run(
            market_ticker,
            research_run_id,
            trigger_headline="",
            trigger_source="",
            attempted=True,
            summary="standalone evidence insert",
            verdict_status="evidence_cached",
        )
        self._insert_evidence_sync(market_ticker, research_run_id, evidence)

    def _record_research_run_sync(
        self,
        market_ticker: str,
        research_run_id: str,
        *,
        trigger_headline: str,
        trigger_source: str,
        attempted: bool,
        summary: str,
        verdict_status: str,
        skip_reason: str | None,
        force_side: str | None,
        estimated_probability: float | None,
        confidence: float | None,
        queries: list[object],
        evidence: list[ResearchEvidence],
    ) -> None:
        self._initialize_sync()
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            self._ensure_dossier_and_run(
                market_ticker,
                research_run_id,
                trigger_headline=trigger_headline,
                trigger_source=trigger_source,
                attempted=attempted,
                summary=summary,
                verdict_status=verdict_status,
                skip_reason=skip_reason,
                force_side=force_side,
                estimated_probability=estimated_probability,
                confidence=confidence,
                conn=conn,
            )
            for index, query in enumerate(queries):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO research_run_queries (
                        research_run_id, ordinal, query, query_intent, source_class
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        research_run_id,
                        index,
                        str(getattr(query, "query", "")),
                        str(getattr(query, "query_intent", "")),
                        str(getattr(query, "source_class", "")),
                    ),
                )
            for item in evidence:
                self._insert_evidence_sync(market_ticker, research_run_id, item, conn=conn)
            conn.commit()

    def _ensure_dossier_and_run(
        self,
        market_ticker: str,
        research_run_id: str,
        *,
        trigger_headline: str,
        trigger_source: str,
        attempted: bool,
        summary: str,
        verdict_status: str,
        skip_reason: str | None = None,
        force_side: str | None = None,
        estimated_probability: float | None = None,
        confidence: float | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO research_dossiers (
                    market_ticker, last_research_run_id, last_researched_ts,
                    last_verdict_status, last_skip_reason, last_force_side,
                    last_estimated_probability, last_confidence
                ) VALUES (
                    ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?
                )
                ON CONFLICT(market_ticker) DO UPDATE SET
                    last_research_run_id=excluded.last_research_run_id,
                    last_researched_ts=excluded.last_researched_ts,
                    last_verdict_status=excluded.last_verdict_status,
                    last_skip_reason=excluded.last_skip_reason,
                    last_force_side=excluded.last_force_side,
                    last_estimated_probability=excluded.last_estimated_probability,
                    last_confidence=excluded.last_confidence,
                    updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (
                    market_ticker,
                    research_run_id,
                    verdict_status,
                    skip_reason,
                    force_side,
                    estimated_probability,
                    confidence,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO research_runs (
                    research_run_id, market_ticker, trigger_headline, trigger_source,
                    attempted, summary, verdict_status, skip_reason, force_side,
                    estimated_probability, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id,
                    market_ticker,
                    trigger_headline,
                    trigger_source,
                    int(attempted),
                    summary,
                    verdict_status,
                    skip_reason,
                    force_side,
                    estimated_probability,
                    confidence,
                ),
            )
            if close_conn:
                conn.commit()
        finally:
            if close_conn:
                conn.close()

    def _insert_evidence_sync(
        self,
        market_ticker: str,
        research_run_id: str,
        evidence: ResearchEvidence,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        evidence_id = _evidence_id(market_ticker, evidence)
        raw_payload = json.dumps(evidence.__dict__, sort_keys=True)
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT OR IGNORE INTO research_evidence (
                    evidence_id, market_ticker, research_run_id, source_class,
                    source_name, source_url, title, snippet, claim_type,
                    supports_direction, supports_confidence, published_at,
                    retrieved_at, metric_name, metric_value, metric_unit,
                    extraction_confidence, contract_fingerprint, raw_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    market_ticker,
                    research_run_id,
                    evidence.source_class,
                    evidence.source_name,
                    evidence.source_url,
                    evidence.title,
                    evidence.snippet,
                    evidence.claim_type,
                    evidence.supports_direction,
                    float(evidence.supports_confidence),
                    evidence.published_at,
                    evidence.retrieved_at,
                    evidence.metric_name,
                    evidence.metric_value,
                    evidence.metric_unit,
                    evidence.extraction_confidence,
                    evidence.contract_fingerprint,
                    raw_payload,
                ),
            )
            if close_conn:
                conn.commit()
        finally:
            if close_conn:
                conn.close()

    def _get_recent_evidence_sync(self, market_ticker: str, limit: int) -> list[ResearchEvidence]:
        self._initialize_sync()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_evidence
                WHERE market_ticker = ?
                ORDER BY inserted_at DESC, evidence_id DESC
                LIMIT ?
                """,
                (market_ticker, int(limit)),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def _get_dossier_snapshot_sync(self, market_ticker: str) -> ResearchDossierSnapshot | None:
        self._initialize_sync()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM research_dossiers
                WHERE market_ticker = ?
                """,
                (market_ticker,),
            ).fetchone()
        if row is None:
            return None
        return ResearchDossierSnapshot(
            market_ticker=row["market_ticker"],
            last_research_run_id=row["last_research_run_id"],
            last_researched_ts=row["last_researched_ts"],
            last_verdict_status=row["last_verdict_status"],
            last_skip_reason=row["last_skip_reason"],
            last_force_side=row["last_force_side"],
            last_estimated_probability=(
                float(row["last_estimated_probability"])
                if row["last_estimated_probability"] is not None
                else None
            ),
            last_confidence=(
                float(row["last_confidence"])
                if row["last_confidence"] is not None
                else None
            ),
        )


def _evidence_id(market_ticker: str, evidence: ResearchEvidence) -> str:
    key = "|".join(
        (
            market_ticker,
            evidence.source_class,
            evidence.source_name,
            evidence.source_url,
            evidence.title,
            evidence.snippet,
            evidence.contract_fingerprint or "",
        )
    )
    return "rev-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _evidence_from_row(row: sqlite3.Row) -> ResearchEvidence:
    return ResearchEvidence(
        source_class=row["source_class"],
        source_name=row["source_name"],
        source_url=row["source_url"],
        title=row["title"],
        snippet=row["snippet"],
        claim_type=row["claim_type"],
        supports_direction=row["supports_direction"],
        supports_confidence=float(row["supports_confidence"]),
        published_at=row["published_at"],
        retrieved_at=row["retrieved_at"],
        metric_name=row["metric_name"],
        metric_value=row["metric_value"],
        metric_unit=row["metric_unit"],
        extraction_confidence=row["extraction_confidence"],
        inserted_at=row["inserted_at"],
        contract_fingerprint=row["contract_fingerprint"],
    )


_default_store: ResearchDossierStore | None = None


def default_store() -> ResearchDossierStore:
    global _default_store
    if _default_store is None:
        _default_store = ResearchDossierStore()
    return _default_store
