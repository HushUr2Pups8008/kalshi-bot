"""
Persistence layer for the accumulation-lane evidence store.

This module implements the S2.1 schema in ``data/evidence_store.db``. It is
storage-only: no evidence scoring, dossier update policy, trading logic, or
analysis behavior belongs here.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from analysis.evidence_types import PriorEstimate
from config import BASE_DIR, DATA_DIR

DEFAULT_EVIDENCE_DB_PATH = DATA_DIR / "evidence_store.db"
SCHEMA_SQL_PATH = BASE_DIR / "docs" / "evidence_store_schema.sql"

_T = TypeVar("_T")


class EvidenceStoreError(Exception):
    """Base class for evidence-store persistence errors."""


class DuplicateEvidenceError(EvidenceStoreError):
    """Raised when an immutable evidence_id is inserted more than once."""


class EvidenceStoreIntegrityError(EvidenceStoreError):
    """Raised when schema constraints reject a write."""


@dataclass(frozen=True)
class DossierState:
    market_ticker: str
    dossier_version: int
    current_estimate: float | None
    confidence: float
    prior_estimate: float | None
    drift_suspect: bool = False
    in_recovery: bool = False
    freeze_started_ts: str | None = None
    recovery_started_ts: str | None = None
    recovery_until_ts: str | None = None
    last_cross_class_state_update_ts: str | None = None
    created_ts: str = ""
    updated_ts: str = ""


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    market_ticker: str
    source: str
    source_class: str
    headline: str
    ingested_ts: str
    content_hash: str
    update_type: str
    dossier_version_before: int
    dossier_version_after: int
    url: str | None = None
    published_ts: str | None = None
    raw_payload_json: str | None = None
    headline_ngram_fingerprint: str | None = None
    correlation_cluster_id: str | None = None
    is_duplicate: bool = False
    correlation_discount_applied: bool = False
    quality_score: float | None = None
    original_weight: float | None = None
    p0_contract_version: int = 1


@dataclass(frozen=True)
class DossierUpdateRecord:
    market_ticker: str
    dossier_version: int
    created_ts: str
    trigger_evidence_id: str
    prior_estimate: float | None
    new_estimate: float | None
    update_delta: float
    confidence_before: float
    confidence_after: float
    update_type: str
    llm_called: bool = False
    drift_suspect: bool = False
    in_recovery: bool = False
    p0_contract_version: int = 1


@dataclass(frozen=True)
class StructuralPriorRecord:
    market_ticker: str
    prior_estimate: float | None
    confidence: float
    computed_ts: str
    recompute_trigger: str | None
    input_source_count: int
    llm_called: bool


class EvidenceStore:
    """SQLite-backed evidence store with per-market async write serialization."""

    def __init__(
        self,
        db_path: Path = DEFAULT_EVIDENCE_DB_PATH,
        *,
        schema_path: Path = SCHEMA_SQL_PATH,
        initialize: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.schema_path = Path(schema_path)
        self._market_locks: dict[str, asyncio.Lock] = {}
        self._market_locks_guard = asyncio.Lock()
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        """Create the S2.1 schema and run idempotent additive migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_sql = self.schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema_sql)
            self._migrate_db(conn)

    def _migrate_db(self, conn: sqlite3.Connection) -> None:
        for table in ("evidence", "dossier_updates"):
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "p0_contract_version" not in cols:
                conn.execute(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN p0_contract_version INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (p0_contract_version >= 0)"
                )

    async def get_dossier(self, market_ticker: str) -> DossierState | None:
        """Return current dossier state for ``market_ticker``, or None if absent."""
        return await asyncio.to_thread(self._get_dossier_sync, market_ticker)

    async def get_recent_evidence(
        self,
        market_ticker: str,
        *,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        """Return recent evidence for a market, newest first.

        Window policy belongs to the caller; this storage helper only keeps DB
        access encapsulated for the scorer input path.
        """
        return await asyncio.to_thread(
            self._get_recent_evidence_sync,
            market_ticker,
            limit,
        )

    async def list_dossier_market_tickers(self) -> list[str]:
        """Return dossier market tickers for scheduler-style task iteration."""
        return await asyncio.to_thread(self._list_dossier_market_tickers_sync)

    async def get_structural_prior(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None:
        """Return current structural prior for ``market_ticker``, or None."""
        return await asyncio.to_thread(self._get_structural_prior_sync, market_ticker)

    async def add_evidence(self, evidence: EvidenceRecord) -> None:
        """Insert one immutable evidence event for an existing market dossier."""

        def _write() -> None:
            self._add_evidence_sync(evidence)

        await self._run_market_write(evidence.market_ticker, _write)

    async def update_dossier(
        self,
        state: DossierState,
        *,
        update: DossierUpdateRecord | None = None,
        evidence_ids_contributing: Sequence[str] = (),
    ) -> None:
        """Persist current dossier state and optionally append one update event.

        Passing ``update=None`` creates or replaces only the current dossier row;
        this is the narrow initialization path needed before first evidence
        insert. When ``update`` is provided, append-only update history and its
        contributing evidence links are inserted in the same transaction.
        """
        if update is not None:
            if update.market_ticker != state.market_ticker:
                raise ValueError("update.market_ticker must match state.market_ticker")
            if update.dossier_version != state.dossier_version:
                raise ValueError("update.dossier_version must match state.dossier_version")

        def _write() -> None:
            self._update_dossier_sync(state, update, evidence_ids_contributing)

        await self._run_market_write(state.market_ticker, _write)

    async def update_structural_prior(
        self,
        prior: PriorEstimate,
        *,
        recompute_trigger: str,
    ) -> None:
        """Upsert the current structural prior row for a market."""

        def _write() -> None:
            self._update_structural_prior_sync(prior, recompute_trigger)

        await self._run_market_write(prior.market_ticker, _write)

    async def _run_market_write(self, market_ticker: str, operation: Callable[[], _T]) -> _T:
        lock = await self._lock_for_market(market_ticker)
        async with lock:
            return await asyncio.to_thread(operation)

    async def _lock_for_market(self, market_ticker: str) -> asyncio.Lock:
        async with self._market_locks_guard:
            lock = self._market_locks.get(market_ticker)
            if lock is None:
                lock = asyncio.Lock()
                self._market_locks[market_ticker] = lock
            return lock

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _get_dossier_sync(self, market_ticker: str) -> DossierState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dossiers WHERE market_ticker = ?",
                (market_ticker,),
            ).fetchone()
        return _dossier_from_row(row) if row is not None else None

    def _get_recent_evidence_sync(
        self,
        market_ticker: str,
        limit: int,
    ) -> list[EvidenceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM evidence
                WHERE market_ticker = ?
                ORDER BY ingested_ts DESC, evidence_id DESC
                LIMIT ?
                """,
                (market_ticker, limit),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def _list_dossier_market_tickers_sync(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT market_ticker FROM dossiers ORDER BY market_ticker"
            ).fetchall()
        return [row["market_ticker"] for row in rows]

    def _get_structural_prior_sync(
        self,
        market_ticker: str,
    ) -> StructuralPriorRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM structural_priors WHERE market_ticker = ?",
                (market_ticker,),
            ).fetchone()
        return _structural_prior_from_row(row) if row is not None else None

    def _add_evidence_sync(self, evidence: EvidenceRecord) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO evidence (
                        evidence_id, market_ticker, source, source_class, headline,
                        url, published_ts, ingested_ts, raw_payload_json,
                        content_hash, headline_ngram_fingerprint,
                        correlation_cluster_id, is_duplicate,
                        correlation_discount_applied, update_type, quality_score,
                        original_weight, dossier_version_before,
                        dossier_version_after, p0_contract_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        evidence.raw_payload_json,
                        evidence.content_hash,
                        evidence.headline_ngram_fingerprint,
                        evidence.correlation_cluster_id,
                        int(evidence.is_duplicate),
                        int(evidence.correlation_discount_applied),
                        evidence.update_type,
                        evidence.quality_score,
                        evidence.original_weight,
                        evidence.dossier_version_before,
                        evidence.dossier_version_after,
                        evidence.p0_contract_version,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            _raise_write_integrity_error(exc, evidence.evidence_id)

    def _update_dossier_sync(
        self,
        state: DossierState,
        update: DossierUpdateRecord | None,
        evidence_ids_contributing: Sequence[str],
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dossiers (
                        market_ticker, dossier_version, current_estimate,
                        confidence, prior_estimate, drift_suspect, in_recovery,
                        freeze_started_ts, recovery_started_ts, recovery_until_ts,
                        last_cross_class_state_update_ts, created_ts, updated_ts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        state.market_ticker,
                        state.dossier_version,
                        state.current_estimate,
                        state.confidence,
                        state.prior_estimate,
                        int(state.drift_suspect),
                        int(state.in_recovery),
                        state.freeze_started_ts,
                        state.recovery_started_ts,
                        state.recovery_until_ts,
                        state.last_cross_class_state_update_ts,
                        state.created_ts,
                        state.updated_ts,
                    ),
                )

                if update is not None:
                    conn.execute(
                        """
                        INSERT INTO dossier_updates (
                            market_ticker, dossier_version, created_ts,
                            trigger_evidence_id, prior_estimate, new_estimate,
                            update_delta, confidence_before, confidence_after,
                            update_type, llm_called, drift_suspect, in_recovery,
                            p0_contract_version
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            update.market_ticker,
                            update.dossier_version,
                            update.created_ts,
                            update.trigger_evidence_id,
                            update.prior_estimate,
                            update.new_estimate,
                            update.update_delta,
                            update.confidence_before,
                            update.confidence_after,
                            update.update_type,
                            int(update.llm_called),
                            int(update.drift_suspect),
                            int(update.in_recovery),
                            update.p0_contract_version,
                        ),
                    )
                    conn.executemany(
                        """
                        INSERT INTO dossier_update_evidence (
                            market_ticker, dossier_version, evidence_id
                        )
                        VALUES (?, ?, ?)
                        """,
                        [
                            (update.market_ticker, update.dossier_version, evidence_id)
                            for evidence_id in evidence_ids_contributing
                        ],
                    )
        except sqlite3.IntegrityError as exc:
            raise EvidenceStoreIntegrityError(str(exc)) from exc

    def _update_structural_prior_sync(
        self,
        prior: PriorEstimate,
        recompute_trigger: str,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO structural_priors (
                        market_ticker, prior_estimate, confidence, computed_ts,
                        recompute_trigger, input_source_count, llm_called
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market_ticker) DO UPDATE SET
                        prior_estimate = excluded.prior_estimate,
                        confidence = excluded.confidence,
                        computed_ts = excluded.computed_ts,
                        recompute_trigger = excluded.recompute_trigger,
                        input_source_count = excluded.input_source_count,
                        llm_called = excluded.llm_called
                    """,
                    (
                        prior.market_ticker,
                        prior.estimate,
                        prior.confidence,
                        prior.computed_ts,
                        recompute_trigger,
                        prior.input_source_count,
                        int(prior.llm_called),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise EvidenceStoreIntegrityError(str(exc)) from exc


def _dossier_from_row(row: sqlite3.Row) -> DossierState:
    return DossierState(
        market_ticker=row["market_ticker"],
        dossier_version=int(row["dossier_version"]),
        current_estimate=row["current_estimate"],
        confidence=float(row["confidence"]),
        prior_estimate=row["prior_estimate"],
        drift_suspect=bool(row["drift_suspect"]),
        in_recovery=bool(row["in_recovery"]),
        freeze_started_ts=row["freeze_started_ts"],
        recovery_started_ts=row["recovery_started_ts"],
        recovery_until_ts=row["recovery_until_ts"],
        last_cross_class_state_update_ts=row["last_cross_class_state_update_ts"],
        created_ts=row["created_ts"],
        updated_ts=row["updated_ts"],
    )


def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=row["evidence_id"],
        market_ticker=row["market_ticker"],
        source=row["source"],
        source_class=row["source_class"],
        headline=row["headline"],
        ingested_ts=row["ingested_ts"],
        content_hash=row["content_hash"],
        update_type=row["update_type"],
        dossier_version_before=int(row["dossier_version_before"]),
        dossier_version_after=int(row["dossier_version_after"]),
        url=row["url"],
        published_ts=row["published_ts"],
        raw_payload_json=row["raw_payload_json"],
        headline_ngram_fingerprint=row["headline_ngram_fingerprint"],
        correlation_cluster_id=row["correlation_cluster_id"],
        is_duplicate=bool(row["is_duplicate"]),
        correlation_discount_applied=bool(row["correlation_discount_applied"]),
        quality_score=row["quality_score"],
        original_weight=row["original_weight"],
        p0_contract_version=int(row["p0_contract_version"]),
    )


def _structural_prior_from_row(row: sqlite3.Row) -> StructuralPriorRecord:
    return StructuralPriorRecord(
        market_ticker=row["market_ticker"],
        prior_estimate=row["prior_estimate"],
        confidence=float(row["confidence"]),
        computed_ts=row["computed_ts"],
        recompute_trigger=row["recompute_trigger"],
        input_source_count=int(row["input_source_count"]),
        llm_called=bool(row["llm_called"]),
    )


def _raise_write_integrity_error(exc: sqlite3.IntegrityError, evidence_id: str) -> None:
    message = str(exc)
    if "UNIQUE constraint failed" in message and "evidence" in message:
        raise DuplicateEvidenceError(f"duplicate evidence_id: {evidence_id}") from exc
    raise EvidenceStoreIntegrityError(message) from exc


_default_store: EvidenceStore | None = None


def default_store() -> EvidenceStore:
    global _default_store
    if _default_store is None:
        _default_store = EvidenceStore()
    return _default_store


async def get_dossier(market_ticker: str) -> DossierState | None:
    return await default_store().get_dossier(market_ticker)


async def get_recent_evidence(
    market_ticker: str,
    *,
    limit: int = 100,
) -> list[EvidenceRecord]:
    return await default_store().get_recent_evidence(market_ticker, limit=limit)


async def list_dossier_market_tickers() -> list[str]:
    return await default_store().list_dossier_market_tickers()


async def get_structural_prior(market_ticker: str) -> StructuralPriorRecord | None:
    return await default_store().get_structural_prior(market_ticker)


async def add_evidence(evidence: EvidenceRecord) -> None:
    await default_store().add_evidence(evidence)


async def update_dossier(
    state: DossierState,
    *,
    update: DossierUpdateRecord | None = None,
    evidence_ids_contributing: Sequence[str] = (),
) -> None:
    await default_store().update_dossier(
        state,
        update=update,
        evidence_ids_contributing=evidence_ids_contributing,
    )


async def update_structural_prior(
    prior: PriorEstimate,
    *,
    recompute_trigger: str,
) -> None:
    await default_store().update_structural_prior(
        prior,
        recompute_trigger=recompute_trigger,
    )
