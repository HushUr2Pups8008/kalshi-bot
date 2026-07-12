"""Durable per-market research dossier storage.

The research gate uses this store to avoid relearning the same ticker from
scratch every time the live bot sees a neutral/no-keyword event.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, TypeVar

from analysis.research_gate import ResearchEvidence
from utils.research_gaps import research_questions_for_skip
from utils.research_priority import (
    extract_pending_event_at,
    official_pending_retry_delay,
)
from utils.research_evidence_quality import (
    MIN_COUNTER_EVIDENCE_CONFIDENCE,
    MIN_DIRECTIONAL_SUPPORT_CONFIDENCE,
    build_contract_relevance_spec,
    effective_research_source_class,
    evidence_is_relevant_to_contract,
    has_reliable_research_source_path,
    research_evidence_temporally_valid,
)
from config import DATA_DIR

_T = TypeVar("_T")
_UNSET = object()

DEFAULT_RESEARCH_DOSSIER_DB_PATH = DATA_DIR / "evidence_store.db"
RESEARCH_TASK_INITIAL_BACKOFF_SECONDS = 300.0
RESEARCH_TASK_MAX_BACKOFF_SECONDS = 21600.0
RESEARCH_TASK_OFFICIAL_PENDING_MAX_BACKOFF_SECONDS = 1800.0
RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON = 2
_SOURCE_PATH_EXHAUSTED_REASONS = {
    "insufficient_corroboration",
    "missing_resolution_source",
    "no_reliable_source_path",
    "no_research_hits",
}
_COUNTER_EVIDENCE_EXHAUSTED_REASONS = {
    "missing_counter_evidence",
    "unresolved_contradiction",
}
_STRUCTURED_SIGNAL_METRICS = {
    "cpi_monthly_change_single_decimal",
    "getty_trump_distinct_photo_days",
    "gdpnow_real_gdp_growth_saar",
    "nws_daily_high_temp_f",
    "white_house_presidential_actions_count",
}
_OFFICIAL_SOURCE_CLASSES = {
    "official",
    "official_primary",
    "official_source",
    "resolution_source",
    "rules_source",
}
_SETTLEMENT_CLAIM_TYPES = {
    "corroboration",
    "contract_terms",
    "official_resolution",
    "resolution",
    "rules",
    "rules_context",
    "settlement",
    "settlement_source",
    "supporting",
}
_COUNTER_CLAIM_TYPES = {"contradiction", "disconfirming", "contradiction_check"}

@dataclass(frozen=True)
class ResearchDossierSnapshot:
    market_ticker: str
    last_research_run_id: str | None
    last_contract_fingerprint: str | None
    contract_question: str | None
    last_researched_ts: str
    last_verdict_status: str
    last_skip_reason: str | None
    last_force_side: str | None
    last_estimated_probability: float | None
    last_confidence: float | None
    last_market_price: float | None = None
    last_estimated_edge: float | None = None
    last_decision_grade_status: str | None = None
    market_status: str | None = None
    market_close_time: str | None = None


@dataclass(frozen=True)
class ResearchTaskSnapshot:
    market_ticker: str
    state: str
    cooldown_until_ts: str | None
    backoff_seconds: float
    terminal_reason: str | None
    open_questions: tuple[str, ...]
    attempt_count: int = 0
    same_reason_count: int = 0
    last_skip_reason: str | None = None
    updated_ts: str | None = None


class ResearchDossierStore:
    def __init__(self, db_path: Path = DEFAULT_RESEARCH_DOSSIER_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._locks: dict[str, asyncio.Lock] = {}
        self._write_lock = asyncio.Lock()
        self._schema_lock = threading.Lock()

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
        contract_question: str | None = None,
        skip_reason: str | None = None,
        force_side: str | None = None,
        estimated_probability: float | None = None,
        confidence: float | None = None,
        contract_fingerprint: str | None = None,
        market_price: float | None = None,
        market_status: object = _UNSET,
        market_close_time: object = _UNSET,
        estimated_edge: float | None = None,
        decision_grade_status: str | None = None,
        decision_grade_reasons: list[str] | None = None,
        open_questions: list[str] | None = None,
        counterclaims: list[str] | None = None,
        queries: list[object] | None = None,
        evidence: list[ResearchEvidence] | None = None,
        update_dossier_snapshot: bool = True,
        update_dossier_run_id: bool = True,
    ) -> None:
        await self._run_market_write(
            market_ticker,
            lambda: self._record_research_run_sync(
                market_ticker,
                research_run_id,
                trigger_headline=trigger_headline,
                trigger_source=trigger_source,
                contract_question=contract_question,
                contract_question_supplied=contract_question is not None,
                attempted=attempted,
                summary=summary,
                verdict_status=verdict_status,
                skip_reason=skip_reason,
                force_side=force_side,
                estimated_probability=estimated_probability,
                confidence=confidence,
                contract_fingerprint=contract_fingerprint,
                market_price=market_price,
                market_price_supplied=market_price is not None,
                market_status=market_status,
                market_close_time=market_close_time,
                estimated_edge=estimated_edge,
                estimated_edge_supplied=estimated_edge is not None,
                decision_grade_status=decision_grade_status,
                decision_grade_status_supplied=decision_grade_status is not None,
                decision_grade_reasons=decision_grade_reasons or [],
                decision_grade_reasons_supplied=decision_grade_reasons is not None,
                open_questions=open_questions or [],
                open_questions_supplied=open_questions is not None,
                counterclaims=counterclaims or [],
                counterclaims_supplied=counterclaims is not None,
                queries=queries or [],
                evidence=evidence or [],
                update_dossier_snapshot=update_dossier_snapshot,
                update_dossier_run_id=update_dossier_run_id,
            ),
        )

    async def claim_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
    ) -> bool:
        return await asyncio.to_thread(
            self._claim_research_paper_admission_sync,
            market_ticker,
            research_run_id,
            contract_fingerprint,
        )

    async def complete_research_paper_admission(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
        *,
        state: Literal["completed", "failed"],
        enqueued: bool | None,
        outcome_reason: str | None,
    ) -> None:
        if state not in {"completed", "failed"}:
            raise ValueError("admission state must be completed or failed")
        await asyncio.to_thread(
            self._complete_research_paper_admission_sync,
            market_ticker,
            research_run_id,
            contract_fingerprint,
            state=state,
            enqueued=enqueued,
            outcome_reason=outcome_reason,
        )

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 50,
    ) -> list[ResearchEvidence]:
        return await asyncio.to_thread(self._get_recent_evidence_sync, market_ticker, limit)

    async def get_research_run_evidence(
        self,
        market_ticker: str,
        research_run_id: str,
    ) -> list[ResearchEvidence]:
        return await asyncio.to_thread(
            self._get_research_run_evidence_sync,
            market_ticker,
            research_run_id,
        )

    async def has_research_run_query_intent(
        self,
        research_run_id: str,
        query_intents: set[str] | frozenset[str],
    ) -> bool:
        return await asyncio.to_thread(
            self._has_research_run_query_intent_sync,
            research_run_id,
            frozenset(query_intents),
        )

    async def get_research_run_query_texts(
        self,
        research_run_id: str,
    ) -> list[str]:
        return await asyncio.to_thread(
            self._get_research_run_query_texts_sync,
            research_run_id,
        )

    async def get_dossier_snapshot(self, market_ticker: str) -> ResearchDossierSnapshot | None:
        return await asyncio.to_thread(self._get_dossier_snapshot_sync, market_ticker)

    async def get_research_task_snapshot(self, market_ticker: str) -> ResearchTaskSnapshot | None:
        return await asyncio.to_thread(self._get_research_task_snapshot_sync, market_ticker)

    async def mark_research_task_researching(self, market_ticker: str) -> None:
        await self._run_market_write(
            market_ticker,
            lambda: self._mark_research_task_researching_sync(market_ticker),
        )

    def get_due_research_task_tickers(
        self,
        *,
        limit: int = 50,
        now: datetime | None = None,
        target_cooldown_seconds: float = 0.0,
    ) -> list[str]:
        return self._get_due_research_task_tickers_sync(
            limit=limit,
            now=now,
            target_cooldown_seconds=target_cooldown_seconds,
        )

    async def _run_market_write(self, market_ticker: str, operation: Callable[[], _T]) -> _T:
        lock = self._locks.setdefault(market_ticker, asyncio.Lock())
        async with self._write_lock:
            async with lock:
                return await asyncio.to_thread(operation)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize_sync(self) -> None:
        with self._schema_lock:
            self._initialize_sync_locked()

    def _initialize_sync_locked(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_dossiers (
                    market_ticker TEXT PRIMARY KEY,
                    last_research_run_id TEXT,
                    last_contract_fingerprint TEXT,
                    contract_question TEXT,
                    market_status TEXT,
                    market_close_time TEXT,
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
            dossier_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_dossiers)").fetchall()
            }
            if "last_contract_fingerprint" not in dossier_columns:
                conn.execute(
                    "ALTER TABLE research_dossiers ADD COLUMN last_contract_fingerprint TEXT"
                )
            for column, definition in (
                ("last_market_price", "REAL"),
                ("last_estimated_edge", "REAL"),
                ("last_decision_grade_status", "TEXT"),
                ("contract_question", "TEXT"),
                ("market_status", "TEXT"),
                ("market_close_time", "TEXT"),
            ):
                if column not in dossier_columns:
                    conn.execute(
                        f"ALTER TABLE research_dossiers ADD COLUMN {column} {definition}"
                    )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    research_run_id TEXT PRIMARY KEY,
                    market_ticker TEXT NOT NULL,
                    trigger_headline TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    contract_question TEXT,
                    market_status TEXT,
                    market_close_time TEXT,
                    attempted INTEGER NOT NULL CHECK (attempted IN (0, 1)),
                    summary TEXT NOT NULL,
                    verdict_status TEXT NOT NULL,
                    skip_reason TEXT,
                    force_side TEXT,
                    estimated_probability REAL,
                    confidence REAL,
                    market_price REAL,
                    estimated_edge REAL,
                    decision_grade_status TEXT,
                    decision_grade_reasons_json TEXT NOT NULL DEFAULT '[]',
                    open_questions_json TEXT NOT NULL DEFAULT '[]',
                    counterclaims_json TEXT NOT NULL DEFAULT '[]',
                    created_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    FOREIGN KEY (market_ticker) REFERENCES research_dossiers(market_ticker)
                )
                """
            )
            run_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
            }
            for column, definition in (
                ("market_price", "REAL"),
                ("estimated_edge", "REAL"),
                ("decision_grade_status", "TEXT"),
                ("contract_question", "TEXT"),
                ("market_status", "TEXT"),
                ("market_close_time", "TEXT"),
                ("decision_grade_reasons_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("open_questions_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("counterclaims_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if column not in run_columns:
                    conn.execute(f"ALTER TABLE research_runs ADD COLUMN {column} {definition}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_paper_admissions (
                    market_ticker TEXT NOT NULL,
                    research_run_id TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('claimed', 'completed', 'failed')),
                    enqueued INTEGER CHECK (enqueued IS NULL OR enqueued IN (0, 1)),
                    outcome_reason TEXT,
                    claimed_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    completed_ts TEXT,
                    updated_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (market_ticker, research_run_id, contract_fingerprint)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_tasks (
                    market_ticker TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    cooldown_until_ts TEXT,
                    backoff_seconds REAL NOT NULL DEFAULT 0,
                    terminal_reason TEXT,
                    open_questions_json TEXT NOT NULL DEFAULT '[]',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    same_reason_count INTEGER NOT NULL DEFAULT 0,
                    last_skip_reason TEXT,
                    created_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
                """
            )
            task_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_tasks)").fetchall()
            }
            for column, definition in (
                ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
                ("same_reason_count", "INTEGER NOT NULL DEFAULT 0"),
                ("last_skip_reason", "TEXT"),
            ):
                if column not in task_columns:
                    conn.execute(f"ALTER TABLE research_tasks ADD COLUMN {column} {definition}")
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

    def _claim_research_paper_admission_sync(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
    ) -> bool:
        self._initialize_sync()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO research_paper_admissions (
                    market_ticker, research_run_id, contract_fingerprint, state
                ) VALUES (?, ?, ?, 'claimed')
                """,
                (market_ticker, research_run_id, contract_fingerprint),
            )
            return cursor.rowcount == 1

    def _complete_research_paper_admission_sync(
        self,
        market_ticker: str,
        research_run_id: str,
        contract_fingerprint: str,
        *,
        state: Literal["completed", "failed"],
        enqueued: bool | None,
        outcome_reason: str | None,
    ) -> None:
        if state not in {"completed", "failed"}:
            raise ValueError("admission state must be completed or failed")
        self._initialize_sync()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE research_paper_admissions
                SET
                    state=?,
                    enqueued=?,
                    outcome_reason=?,
                    completed_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE market_ticker=?
                  AND research_run_id=?
                  AND contract_fingerprint=?
                  AND state='claimed'
                """,
                (
                    state,
                    None if enqueued is None else int(enqueued),
                    outcome_reason,
                    market_ticker,
                    research_run_id,
                    contract_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("claimed research paper admission not found")

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
        contract_question: str | None,
        contract_question_supplied: bool,
        skip_reason: str | None,
        force_side: str | None,
        estimated_probability: float | None,
        confidence: float | None,
        contract_fingerprint: str | None,
        market_price: float | None,
        market_price_supplied: bool,
        market_status: object,
        market_close_time: object,
        estimated_edge: float | None,
        estimated_edge_supplied: bool,
        decision_grade_status: str | None,
        decision_grade_status_supplied: bool,
        decision_grade_reasons: list[str],
        decision_grade_reasons_supplied: bool,
        open_questions: list[str],
        open_questions_supplied: bool,
        counterclaims: list[str],
        counterclaims_supplied: bool,
        queries: list[object],
        evidence: list[ResearchEvidence],
        update_dossier_snapshot: bool = True,
        update_dossier_run_id: bool = True,
    ) -> None:
        self._initialize_sync()
        final_verdict_status, final_decision_grade_status, final_skip_reason = (
            _validated_research_status(
                market_ticker=market_ticker,
                verdict_status=verdict_status,
                decision_grade_status=decision_grade_status,
                skip_reason=skip_reason,
                force_side=force_side,
                queries=queries,
                evidence=evidence,
            )
        )
        final_open_questions = list(
            research_questions_for_skip(final_skip_reason, open_questions)
        )
        final_open_questions_supplied = open_questions_supplied or bool(
            final_open_questions
        )
        final_contract_fingerprint = contract_fingerprint or _run_contract_fingerprint(evidence)
        normalized_market_status = (
            _UNSET if market_status is _UNSET else _normalize_market_status(market_status)
        )
        normalized_market_close_time = (
            _UNSET
            if market_close_time is _UNSET
            else _normalize_market_close_time(market_close_time)
        )
        with self._connection() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN")
            final_update_dossier_snapshot = update_dossier_snapshot
            final_update_dossier_run_id = update_dossier_run_id
            if (
                not update_dossier_snapshot
                and trigger_source == "research_prewarm"
                and not _is_vetted_research_status(final_verdict_status)
                and _has_invalid_decision_grade_snapshot_sync(
                    conn,
                    market_ticker=market_ticker,
                )
            ):
                final_update_dossier_snapshot = True
                final_update_dossier_run_id = True
            if (
                update_dossier_snapshot
                and trigger_source == "research_prewarm"
                and not _is_vetted_research_status(final_verdict_status)
                and _has_existing_vetted_snapshot_sync(
                    conn,
                    market_ticker=market_ticker,
                    contract_fingerprint=None,
                    allow_missing_fingerprint=True,
                )
            ):
                final_update_dossier_snapshot = False
                final_update_dossier_run_id = False
            self._ensure_dossier_and_run(
                market_ticker,
                research_run_id,
                trigger_headline=trigger_headline,
                trigger_source=trigger_source,
                contract_question=contract_question,
                attempted=attempted,
                summary=summary,
                verdict_status=final_verdict_status,
                skip_reason=final_skip_reason,
                force_side=force_side,
                estimated_probability=estimated_probability,
                confidence=confidence,
                contract_fingerprint=final_contract_fingerprint,
                conn=conn,
                update_dossier_snapshot=final_update_dossier_snapshot,
                update_dossier_run_id=final_update_dossier_run_id,
                market_price=market_price,
                market_status=normalized_market_status,
                market_close_time=normalized_market_close_time,
                estimated_edge=estimated_edge,
                decision_grade_status=final_decision_grade_status,
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
            self._upsert_research_task_sync(
                conn,
                market_ticker,
                final_decision_grade_status or final_verdict_status,
                terminal_reason=final_skip_reason,
                open_questions=open_questions,
                pending_event_at=extract_pending_event_at(evidence),
            )
            conn.execute(
                """
                UPDATE research_runs
                SET
                    contract_question=CASE WHEN ? THEN ? ELSE contract_question END,
                    market_status=CASE WHEN ? THEN ? ELSE market_status END,
                    market_close_time=CASE WHEN ? THEN ? ELSE market_close_time END,
                    market_price=CASE WHEN ? THEN ? ELSE market_price END,
                    estimated_edge=CASE WHEN ? THEN ? ELSE estimated_edge END,
                    decision_grade_status=CASE
                        WHEN ? THEN ? ELSE decision_grade_status
                    END,
                    decision_grade_reasons_json=CASE
                        WHEN ? THEN ? ELSE decision_grade_reasons_json
                    END,
                    open_questions_json=CASE
                        WHEN ? THEN ? ELSE open_questions_json
                    END,
                    counterclaims_json=CASE
                        WHEN ? THEN ? ELSE counterclaims_json
                    END
                WHERE research_run_id=?
                """,
                (
                    int(contract_question_supplied),
                    contract_question,
                    int(normalized_market_status is not _UNSET),
                    None if normalized_market_status is _UNSET else normalized_market_status,
                    int(normalized_market_close_time is not _UNSET),
                    (
                        None
                        if normalized_market_close_time is _UNSET
                        else normalized_market_close_time
                    ),
                    int(market_price_supplied),
                    market_price,
                    int(estimated_edge_supplied),
                    estimated_edge,
                    int(decision_grade_status_supplied),
                    final_decision_grade_status,
                    int(decision_grade_reasons_supplied),
                    _json_list(decision_grade_reasons),
                    int(final_open_questions_supplied),
                    _json_list(final_open_questions),
                    int(counterclaims_supplied),
                    _json_list(counterclaims),
                    research_run_id,
                ),
            )
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
        contract_question: str | None = None,
        skip_reason: str | None = None,
        force_side: str | None = None,
        estimated_probability: float | None = None,
        confidence: float | None = None,
        contract_fingerprint: str | None = None,
        market_price: float | None = None,
        market_status: object = _UNSET,
        market_close_time: object = _UNSET,
        estimated_edge: float | None = None,
        decision_grade_status: str | None = None,
        conn: sqlite3.Connection | None = None,
        update_dossier_snapshot: bool = True,
        update_dossier_run_id: bool = True,
    ) -> None:
        close_conn = conn is None
        conn = conn or self._connect()
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if update_dossier_snapshot and update_dossier_run_id:
                conn.execute(
                    """
                    INSERT INTO research_dossiers (
                        market_ticker, last_research_run_id, last_contract_fingerprint,
                        contract_question,
                        last_researched_ts,
                        last_verdict_status, last_skip_reason, last_force_side,
                        last_estimated_probability, last_confidence,
                        last_market_price, last_estimated_edge,
                        last_decision_grade_status
                    ) VALUES (
                        ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(market_ticker) DO UPDATE SET
                        last_research_run_id=excluded.last_research_run_id,
                        last_contract_fingerprint=excluded.last_contract_fingerprint,
                        contract_question=excluded.contract_question,
                        last_researched_ts=excluded.last_researched_ts,
                        last_verdict_status=excluded.last_verdict_status,
                        last_skip_reason=excluded.last_skip_reason,
                        last_force_side=excluded.last_force_side,
                        last_estimated_probability=excluded.last_estimated_probability,
                        last_confidence=excluded.last_confidence,
                        last_market_price=excluded.last_market_price,
                        last_estimated_edge=excluded.last_estimated_edge,
                        last_decision_grade_status=excluded.last_decision_grade_status,
                        updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    """,
                    (
                        market_ticker,
                        research_run_id,
                        contract_fingerprint,
                        contract_question,
                        verdict_status,
                        skip_reason,
                        force_side,
                        estimated_probability,
                        confidence,
                        market_price,
                        estimated_edge,
                        decision_grade_status,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO research_dossiers (
                        market_ticker, last_research_run_id, last_contract_fingerprint,
                        contract_question,
                        last_researched_ts,
                        last_verdict_status, last_skip_reason, last_force_side,
                        last_estimated_probability, last_confidence,
                        last_market_price, last_estimated_edge,
                        last_decision_grade_status
                    ) VALUES (
                        ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        market_ticker,
                        research_run_id,
                        contract_fingerprint,
                        contract_question,
                        verdict_status,
                        skip_reason,
                        force_side,
                        estimated_probability,
                        confidence,
                        market_price,
                        estimated_edge,
                        decision_grade_status,
                    ),
                )
                if update_dossier_snapshot:
                    conn.execute(
                        """
                        UPDATE research_dossiers
                        SET
                            last_contract_fingerprint=?,
                            contract_question=?,
                            last_researched_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                            last_verdict_status=?,
                            last_skip_reason=?,
                            last_force_side=?,
                            last_estimated_probability=?,
                            last_confidence=?,
                            last_market_price=?,
                            last_estimated_edge=?,
                            last_decision_grade_status=?,
                            updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        WHERE market_ticker=?
                        """,
                        (
                            contract_fingerprint,
                            contract_question,
                            verdict_status,
                            skip_reason,
                            force_side,
                            estimated_probability,
                            confidence,
                            market_price,
                            estimated_edge,
                            decision_grade_status,
                            market_ticker,
                        ),
                    )
            conn.execute(
                """
                UPDATE research_dossiers
                SET
                    market_status=CASE WHEN ? THEN ? ELSE market_status END,
                    market_close_time=CASE WHEN ? THEN ? ELSE market_close_time END,
                    updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE market_ticker=?
                """,
                (
                    int(market_status is not _UNSET),
                    None if market_status is _UNSET else market_status,
                    int(market_close_time is not _UNSET),
                    None if market_close_time is _UNSET else market_close_time,
                    market_ticker,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO research_runs (
                    research_run_id, market_ticker, trigger_headline, trigger_source,
                    contract_question, market_status, market_close_time,
                    attempted, summary, verdict_status, skip_reason, force_side,
                    estimated_probability, confidence, market_price, estimated_edge,
                    decision_grade_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    research_run_id,
                    market_ticker,
                    trigger_headline,
                    trigger_source,
                    contract_question,
                    None if market_status is _UNSET else market_status,
                    None if market_close_time is _UNSET else market_close_time,
                    int(attempted),
                    summary,
                    verdict_status,
                    skip_reason,
                    force_side,
                    estimated_probability,
                    confidence,
                    market_price,
                    estimated_edge,
                    decision_grade_status,
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
        evidence_id = _evidence_id(market_ticker, research_run_id, evidence)
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
        with self._connection() as conn:
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

    def _get_research_run_evidence_sync(
        self,
        market_ticker: str,
        research_run_id: str,
    ) -> list[ResearchEvidence]:
        self._initialize_sync()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_evidence
                WHERE market_ticker = ?
                  AND research_run_id = ?
                ORDER BY inserted_at DESC, evidence_id DESC
                """,
                (market_ticker, research_run_id),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def _has_research_run_query_intent_sync(
        self,
        research_run_id: str,
        query_intents: frozenset[str],
    ) -> bool:
        self._initialize_sync()
        normalized = tuple(
            sorted(str(intent or "").strip() for intent in query_intents if intent)
        )
        if not normalized:
            return False
        placeholders = ",".join("?" for _ in normalized)
        with self._connection() as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM research_run_queries
                WHERE research_run_id = ?
                  AND query_intent IN ({placeholders})
                LIMIT 1
                """,
                (research_run_id, *normalized),
            ).fetchone()
        return row is not None

    def _get_research_run_query_texts_sync(
        self,
        research_run_id: str,
    ) -> list[str]:
        self._initialize_sync()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT query
                FROM research_run_queries
                WHERE research_run_id = ?
                ORDER BY ordinal
                """,
                (research_run_id,),
            ).fetchall()
        return [str(row["query"] or "") for row in rows]

    def _get_dossier_snapshot_sync(self, market_ticker: str) -> ResearchDossierSnapshot | None:
        self._initialize_sync()
        with self._connection() as conn:
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
            last_contract_fingerprint=row["last_contract_fingerprint"],
            contract_question=(
                row["contract_question"] if "contract_question" in row.keys() else None
            ),
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
            last_market_price=(
                float(row["last_market_price"])
                if "last_market_price" in row.keys() and row["last_market_price"] is not None
                else None
            ),
            last_estimated_edge=(
                float(row["last_estimated_edge"])
                if "last_estimated_edge" in row.keys() and row["last_estimated_edge"] is not None
                else None
            ),
            last_decision_grade_status=(
                row["last_decision_grade_status"]
                if "last_decision_grade_status" in row.keys()
                else None
            ),
            market_status=(
                row["market_status"] if "market_status" in row.keys() else None
            ),
            market_close_time=(
                row["market_close_time"] if "market_close_time" in row.keys() else None
            ),
        )

    def _get_research_task_snapshot_sync(
        self,
        market_ticker: str,
    ) -> ResearchTaskSnapshot | None:
        self._initialize_sync()
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM research_tasks
                WHERE market_ticker = ?
                """,
                (market_ticker,),
            ).fetchone()
        if row is None:
            return None
        return ResearchTaskSnapshot(
            market_ticker=row["market_ticker"],
            state=row["state"],
            cooldown_until_ts=row["cooldown_until_ts"],
            backoff_seconds=float(row["backoff_seconds"] or 0.0),
            terminal_reason=row["terminal_reason"],
            open_questions=tuple(_parse_json_list(row["open_questions_json"])),
            attempt_count=int(row["attempt_count"] or 0),
            same_reason_count=int(row["same_reason_count"] or 0),
            last_skip_reason=row["last_skip_reason"],
            updated_ts=row["updated_ts"],
        )

    def _mark_research_task_researching_sync(self, market_ticker: str) -> None:
        self._initialize_sync()
        with self._connection() as conn:
            prior = conn.execute(
                """
                SELECT backoff_seconds, attempt_count, same_reason_count, last_skip_reason
                FROM research_tasks
                WHERE market_ticker = ?
                """,
                (market_ticker,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO research_tasks (
                    market_ticker, state, cooldown_until_ts, backoff_seconds,
                    terminal_reason, open_questions_json, attempt_count,
                    same_reason_count, last_skip_reason
                ) VALUES (?, 'researching', NULL, ?, NULL, '[]', ?, ?, ?)
                ON CONFLICT(market_ticker) DO UPDATE SET
                    state='researching',
                    cooldown_until_ts=NULL,
                    terminal_reason=NULL,
                    updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (
                    market_ticker,
                    float(prior["backoff_seconds"] or 0.0) if prior else 0.0,
                    int(prior["attempt_count"] or 0) if prior else 0,
                    int(prior["same_reason_count"] or 0) if prior else 0,
                    prior["last_skip_reason"] if prior else None,
                ),
            )

    def _get_due_research_task_tickers_sync(
        self,
        *,
        limit: int,
        now: datetime | None,
        target_cooldown_seconds: float,
    ) -> list[str]:
        self._initialize_sync()
        limit = max(0, int(limit))
        if limit <= 0:
            return []
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        target_cooldown_seconds = max(0.0, float(target_cooldown_seconds or 0.0))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    market_ticker,
                    cooldown_until_ts,
                    updated_ts,
                    last_skip_reason,
                    backoff_seconds
                FROM research_tasks
                WHERE state != 'untradeable'
                   OR terminal_reason IN (
                        'official_data_pending',
                        'no_reliable_source_path',
                        'research_timeout_exhausted'
                   )
                ORDER BY
                    CASE
                        WHEN state = 'needs_counter_evidence' THEN 0
                        WHEN state = 'needs_price_edge' THEN 1
                        WHEN state = 'needs_research'
                            AND COALESCE(last_skip_reason, terminal_reason, '') IN (
                                'missing_counter_evidence',
                                'missing_price_edge',
                                'missing_probability_estimate',
                                'missing_resolution_source',
                                'missing_source_details',
                                'missing_reasoning'
                            ) THEN 2
                        WHEN state = 'continue_researching' THEN 3
                        WHEN state = 'needs_research' THEN 4
                        WHEN state = 'researching' THEN 5
                        WHEN state = 'decision_grade_candidate' THEN 6
                        WHEN state = 'untradeable' THEN 7
                        ELSE 8
                    END,
                    CASE
                        WHEN state = 'continue_researching' THEN updated_ts
                        ELSE NULL
                    END DESC,
                    CASE COALESCE(last_skip_reason, terminal_reason, '')
                        WHEN 'missing_counter_evidence' THEN 0
                        WHEN 'neutral_only_evidence' THEN 1
                        WHEN 'unresolved_contradiction' THEN 2
                        WHEN 'missing_market_price' THEN 3
                        WHEN 'missing_resolution_source' THEN 4
                        WHEN 'insufficient_corroboration' THEN 5
                        WHEN 'official_data_pending' THEN 6
                        WHEN 'no_research_hits' THEN 7
                        WHEN 'no_reliable_source_path' THEN 8
                        ELSE 9
                    END,
                    updated_ts ASC,
                    market_ticker ASC
                """
            ).fetchall()
        due: list[str] = []
        for row in rows:
            cooldown_until = _parse_utc_ts(row["cooldown_until_ts"])
            updated_at = _parse_utc_ts(row["updated_ts"])
            backoff_seconds = float(row["backoff_seconds"] or 0.0)
            if cooldown_until is not None and cooldown_until > now:
                continue
            if (
                updated_at is not None
                and target_cooldown_seconds > 0
                and not (
                    cooldown_until is None
                    and backoff_seconds <= 0.0
                )
                and updated_at + timedelta(seconds=target_cooldown_seconds) > now
            ):
                continue
            due.append(str(row["market_ticker"]))
            if len(due) >= limit:
                break
        return due

    def _upsert_research_task_sync(
        self,
        conn: sqlite3.Connection,
        market_ticker: str,
        state: str,
        *,
        terminal_reason: str | None,
        open_questions: list[str],
        pending_event_at: datetime | None = None,
    ) -> None:
        prior = conn.execute(
            """
            SELECT attempt_count, same_reason_count, last_skip_reason, backoff_seconds,
                   open_questions_json
            FROM research_tasks
            WHERE market_ticker = ?
            """,
            (market_ticker,),
        ).fetchone()
        reason_key = terminal_reason or ""
        attempt_count = int(prior["attempt_count"] or 0) + 1 if prior else 1
        same_reason_count = (
            int(prior["same_reason_count"] or 0) + 1
            if prior and str(prior["last_skip_reason"] or "") == reason_key
            else 1
        )
        final_state = state
        if final_state not in {"decision_grade_candidate", "untradeable"}:
            if (
                reason_key in _SOURCE_PATH_EXHAUSTED_REASONS
                and same_reason_count >= RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON
            ):
                final_state = "untradeable"
                terminal_reason = "no_reliable_source_path"
            elif (
                reason_key in _COUNTER_EVIDENCE_EXHAUSTED_REASONS
                and same_reason_count >= RESEARCH_TASK_TERMINAL_AFTER_SAME_REASON
            ):
                final_state = "untradeable"
                terminal_reason = (
                    "insufficient_directional_evidence"
                    if reason_key in {"ambiguous_direction", "neutral_only_evidence"}
                    else "contradictory_evidence_unresolved"
                )
        terminal = final_state in {"decision_grade_candidate", "untradeable"}
        prior_questions = (
            _parse_json_list(prior["open_questions_json"]) if prior else []
        )
        if prior and str(prior["last_skip_reason"] or "") == reason_key:
            open_questions = list(
                research_questions_for_skip(
                    reason_key,
                    [*prior_questions, *open_questions],
                )
            )
        else:
            open_questions = list(research_questions_for_skip(reason_key, open_questions))
        if terminal:
            backoff_seconds = 0.0
            cooldown_until_ts = None
        else:
            previous_backoff = float(prior["backoff_seconds"] or 0.0) if prior else 0.0
            if reason_key == "official_data_pending":
                backoff_seconds = official_pending_retry_delay(
                    now=datetime.now(timezone.utc),
                    event_at=pending_event_at,
                )
            else:
                backoff_seconds = min(
                    previous_backoff * 2
                    if previous_backoff
                    else RESEARCH_TASK_INITIAL_BACKOFF_SECONDS,
                    RESEARCH_TASK_MAX_BACKOFF_SECONDS,
                )
            cooldown_until_ts = _utc_cooldown_until(backoff_seconds)
        final_terminal_reason = terminal_reason if final_state == "untradeable" else None
        conn.execute(
            """
            INSERT INTO research_tasks (
                market_ticker, state, cooldown_until_ts, backoff_seconds,
                terminal_reason, open_questions_json, attempt_count,
                same_reason_count, last_skip_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_ticker) DO UPDATE SET
                state=excluded.state,
                cooldown_until_ts=excluded.cooldown_until_ts,
                backoff_seconds=excluded.backoff_seconds,
                terminal_reason=excluded.terminal_reason,
                open_questions_json=excluded.open_questions_json,
                attempt_count=excluded.attempt_count,
                same_reason_count=excluded.same_reason_count,
                last_skip_reason=excluded.last_skip_reason,
                updated_ts=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (
                market_ticker,
                final_state,
                cooldown_until_ts,
                backoff_seconds,
                final_terminal_reason if terminal else None,
                _json_list(open_questions),
                attempt_count,
                same_reason_count,
                reason_key,
            ),
        )


def _normalize_market_status(value: object) -> str | None:
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value or "").strip().lower()
    return normalized or None


def _normalize_market_close_time(value: object) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_contract_fingerprint(evidence: list[ResearchEvidence]) -> str | None:
    fingerprints = {
        item.contract_fingerprint
        for item in evidence
        if item.contract_fingerprint
    }
    if len(fingerprints) != 1:
        return None
    return next(iter(fingerprints))


def _validated_research_status(
    *,
    market_ticker: str,
    verdict_status: str,
    decision_grade_status: str | None,
    skip_reason: str | None,
    force_side: str | None,
    queries: list[object],
    evidence: list[ResearchEvidence],
) -> tuple[str, str | None, str | None]:
    if skip_reason == "official_data_pending":
        return "needs_research", "needs_research", skip_reason
    state = decision_grade_status or verdict_status
    if state != "decision_grade_candidate":
        return verdict_status, decision_grade_status, skip_reason
    side = str(force_side or "").strip().lower()
    quality = _decision_grade_persistence_quality(
        ticker=market_ticker,
        side=side,
        queries=queries,
        evidence=evidence,
    )
    if not quality["has_reliable_source_path"]:
        return "needs_research", "needs_research", "no_reliable_source_path"
    if not quality["has_directional_evidence"]:
        return "needs_counter_evidence", "needs_counter_evidence", "neutral_only_evidence"
    if not quality["has_counter_query"] or not quality["has_counter_evidence"]:
        return (
            "needs_counter_evidence",
            "needs_counter_evidence",
            "missing_counter_evidence",
        )
    return verdict_status, decision_grade_status, skip_reason


def _is_vetted_research_status(status: str | None) -> bool:
    return str(status or "") in {"decision_grade_candidate", "trade_candidate"}


def _has_existing_vetted_snapshot_sync(
    conn: sqlite3.Connection,
    *,
    market_ticker: str,
    contract_fingerprint: str | None,
    allow_missing_fingerprint: bool = False,
) -> bool:
    if not contract_fingerprint and not allow_missing_fingerprint:
        return False
    row = conn.execute(
        """
        SELECT last_verdict_status, last_decision_grade_status,
               last_research_run_id, last_force_side, last_estimated_probability,
               last_confidence, last_market_price, last_estimated_edge,
               last_contract_fingerprint
        FROM research_dossiers
        WHERE market_ticker = ?
        """,
        (market_ticker,),
    ).fetchone()
    if row is None:
        return False
    if not _is_vetted_research_status(row["last_verdict_status"]):
        return False
    if row["last_verdict_status"] == "decision_grade_candidate" and not (
        _stored_decision_grade_snapshot_is_valid_sync(
            conn,
            market_ticker=market_ticker,
            row=row,
        )
    ):
        return False
    if not contract_fingerprint:
        return bool(row["last_contract_fingerprint"])
    return row["last_contract_fingerprint"] == contract_fingerprint


def _has_invalid_decision_grade_snapshot_sync(
    conn: sqlite3.Connection,
    *,
    market_ticker: str,
) -> bool:
    row = conn.execute(
        """
        SELECT last_verdict_status, last_decision_grade_status,
               last_research_run_id, last_force_side, last_estimated_probability,
               last_confidence, last_market_price, last_estimated_edge,
               last_contract_fingerprint
        FROM research_dossiers
        WHERE market_ticker = ?
        """,
        (market_ticker,),
    ).fetchone()
    if row is None or row["last_verdict_status"] != "decision_grade_candidate":
        return False
    return not _stored_decision_grade_snapshot_is_valid_sync(
        conn,
        market_ticker=market_ticker,
        row=row,
    )


def _stored_decision_grade_snapshot_is_valid_sync(
    conn: sqlite3.Connection,
    *,
    market_ticker: str,
    row: sqlite3.Row,
) -> bool:
    if row["last_decision_grade_status"] != "decision_grade_candidate":
        return False
    research_run_id = str(row["last_research_run_id"] or "").strip()
    side = str(row["last_force_side"] or "").strip().lower()
    if not research_run_id or side not in {"yes", "no"}:
        return False
    if (
        row["last_estimated_probability"] is None
        or row["last_confidence"] is None
        or row["last_market_price"] is None
        or row["last_estimated_edge"] is None
    ):
        return False
    if not _decision_grade_edge_recomputes(
        side=side,
        estimated_probability=float(row["last_estimated_probability"]),
        market_price=float(row["last_market_price"]),
        estimated_edge=float(row["last_estimated_edge"]),
    ):
        return False
    queries = [
        SimpleNamespace(
            query=query_row["query"],
            query_intent=query_row["query_intent"],
        )
        for query_row in conn.execute(
            """
            SELECT query, query_intent
            FROM research_run_queries
            WHERE research_run_id = ?
            """,
            (research_run_id,),
        ).fetchall()
    ]
    evidence = [
        _evidence_from_row(evidence_row)
        for evidence_row in conn.execute(
            """
            SELECT *
            FROM research_evidence
            WHERE market_ticker = ?
              AND research_run_id = ?
            """,
            (market_ticker, research_run_id),
        ).fetchall()
    ]
    quality = _decision_grade_persistence_quality(
        ticker=market_ticker,
        side=side,
        queries=queries,
        evidence=evidence,
    )
    return (
        quality["has_reliable_source_path"]
        and quality["has_directional_evidence"]
        and quality["has_counter_query"]
        and quality["has_counter_evidence"]
    )


def _decision_grade_edge_recomputes(
    *,
    side: str,
    estimated_probability: float,
    market_price: float,
    estimated_edge: float,
) -> bool:
    side_probability = (
        estimated_probability if side == "yes" else 1.0 - estimated_probability
    )
    recomputed = side_probability - market_price - 0.01
    return recomputed > 0 and abs(recomputed - estimated_edge) <= 0.005


def _decision_grade_persistence_quality(
    *,
    ticker: str,
    side: str,
    queries: list[object],
    evidence: list[ResearchEvidence],
) -> dict[str, bool]:
    evidence = [
        item for item in evidence if research_evidence_temporally_valid(item)
    ]
    opposite = "no" if side == "yes" else "yes" if side == "no" else ""
    supports_directions: set[str] = set()
    has_counter_evidence = False
    structured_support_metrics: set[str] = set()
    relevance_spec = build_contract_relevance_spec(
        ticker,
        [getattr(query, "query", "") for query in queries],
    )
    for item in evidence:
        direction = str(getattr(item, "supports_direction", "") or "").strip().lower()
        claim_type = str(getattr(item, "claim_type", "") or "").strip().lower()
        confidence = float(getattr(item, "supports_confidence", 0.0) or 0.0)
        is_relevant = evidence_is_relevant_to_contract(
            f"{getattr(item, 'title', '')} {getattr(item, 'snippet', '')}",
            relevance_spec,
        )
        if (
            is_relevant
            and claim_type in _SETTLEMENT_CLAIM_TYPES
            and direction in {"yes", "no"}
            and confidence >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
        ):
            supports_directions.add(direction)
        if (
            is_relevant
            and claim_type in _COUNTER_CLAIM_TYPES
            and (
                direction == "neutral"
                or (
                    direction == opposite
                    and confidence >= MIN_COUNTER_EVIDENCE_CONFIDENCE
                )
            )
        ):
            has_counter_evidence = True
        if (
            is_relevant
            and claim_type in _COUNTER_CLAIM_TYPES
            and direction == side
            and _is_structured_official_metric_countercheck(item)
        ):
            metric_name = str(getattr(item, "metric_name", "") or "").strip()
            if metric_name in structured_support_metrics:
                has_counter_evidence = True
        if (
            is_relevant
            and claim_type in _SETTLEMENT_CLAIM_TYPES
            and direction in {"yes", "no"}
            and confidence >= MIN_DIRECTIONAL_SUPPORT_CONFIDENCE
        ):
            metric_name = str(getattr(item, "metric_name", "") or "").strip()
            extraction_confidence = float(
                getattr(item, "extraction_confidence", 0.0) or 0.0
            )
            if (
                metric_name in _STRUCTURED_SIGNAL_METRICS
                and effective_research_source_class(item) in _OFFICIAL_SOURCE_CLASSES
                and extraction_confidence >= 0.6
            ):
                if direction == side:
                    structured_support_metrics.add(metric_name)
                    if any(
                        str(getattr(counter, "claim_type", "") or "").strip().lower()
                        in _COUNTER_CLAIM_TYPES
                        and str(
                            getattr(counter, "supports_direction", "") or ""
                        ).strip().lower()
                        == side
                        and _is_structured_official_metric_countercheck(counter)
                        and str(getattr(counter, "metric_name", "") or "").strip()
                        == metric_name
                        for counter in evidence
                    ):
                        has_counter_evidence = True
    has_counter_query = any(
        str(getattr(query, "query_intent", "") or "").strip().lower()
        in _COUNTER_CLAIM_TYPES
        for query in queries
    )
    return {
        "has_reliable_source_path": has_reliable_research_source_path(evidence),
        "has_directional_evidence": side in supports_directions,
        "has_counter_query": has_counter_query,
        "has_counter_evidence": has_counter_evidence,
    }


def _is_structured_official_metric_countercheck(item: ResearchEvidence) -> bool:
    metric_name = str(getattr(item, "metric_name", "") or "").strip()
    if metric_name not in _STRUCTURED_SIGNAL_METRICS:
        return False
    if effective_research_source_class(item) not in _OFFICIAL_SOURCE_CLASSES:
        return False
    if float(getattr(item, "supports_confidence", 0.0) or 0.0) < 0.8:
        return False
    extraction_confidence = float(
        getattr(item, "extraction_confidence", 0.0) or 0.0
    )
    return getattr(item, "metric_value", None) is not None or extraction_confidence >= 0.8


def _utc_cooldown_until(backoff_seconds: float) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _evidence_id(market_ticker: str, research_run_id: str, evidence: ResearchEvidence) -> str:
    counter_suffix = ""
    if (
        evidence.claim_type in {"disconfirming", "contradiction_check"}
        and evidence.metric_name in _STRUCTURED_SIGNAL_METRICS
    ):
        counter_suffix = f"|{evidence.claim_type}|{evidence.supports_direction}"
    key = "|".join(
        (
            market_ticker,
            research_run_id,
            evidence.source_class,
            evidence.source_name,
            evidence.source_url,
            evidence.title,
            evidence.snippet,
            evidence.contract_fingerprint or "",
            counter_suffix,
        )
    )
    return "rev-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _evidence_from_row(row: sqlite3.Row) -> ResearchEvidence:
    try:
        raw_payload = json.loads(row["raw_payload_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        raw_payload = {}
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
        available_at=(
            raw_payload.get("available_at") if isinstance(raw_payload, dict) else None
        ),
        retrieved_at=row["retrieved_at"],
        metric_name=row["metric_name"],
        metric_value=row["metric_value"],
        metric_unit=row["metric_unit"],
        extraction_confidence=row["extraction_confidence"],
        inserted_at=row["inserted_at"],
        contract_fingerprint=row["contract_fingerprint"],
        aggregator_url=(
            raw_payload.get("aggregator_url") if isinstance(raw_payload, dict) else None
        ),
    )


def _json_list(values: list[str]) -> str:
    return json.dumps([str(value) for value in values if str(value)], sort_keys=True)


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


_default_store: ResearchDossierStore | None = None


def default_store() -> ResearchDossierStore:
    global _default_store
    if _default_store is None:
        _default_store = ResearchDossierStore()
    return _default_store
