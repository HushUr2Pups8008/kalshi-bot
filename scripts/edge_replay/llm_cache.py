"""I-2 + I-12 SQLite-backed LLM response cache (rapid-learning v3).

Reference spec:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §3 row I-2 + I-12 + §4 §16.7.

Purpose
-------

Replay must run WITHOUT any live LLM call. I-3 (``llm_capture.py``) records
every production LLM response into ``logs/edge_replay/llm_capture.jsonl``
with a 13-field cache-identity tuple. This module migrates that JSONL into
SQLite and exposes a read-only exact-match lookup. ``LLMReplayCache.get``
returns the cached response or raises ``CacheMissError`` — replay code
must NEVER fall back to a live LLM call when the cache is cold.

Public API
----------

``LLMReplayCache``                 — read-only SQLite reader; context-manager
``CacheKey``                       — 13-field dataclass
``CacheHit``                       — wrapper for a successful lookup
``CacheMissError``                 — no row matched the 13-field key
``CacheCorruptionError``           — single-read hash-vs-payload mismatch
``CachePoisonedError``             — durable poisoned-row mark hit (I-12)
``CoverageReport``                 — output of ``compute_cache_coverage``
``ScanReport``                     — output of ``scan_integrity`` (I-12)
``compute_cache_coverage``         — pre-gate ratio check
``migrate_jsonl_to_sqlite``        — one-shot JSONL → SQLite migration
``scan_integrity``                 — one-shot DB-wide integrity scan (I-12)

Design constraints
------------------

* **Pure stdlib.** No third-party deps.
* **Read-only by default.** The reader opens via ``file:?mode=ro`` so the
  reader connection can NEVER write — a stray dev never corrupts the
  capture. The migration utility opens its own writer connection.
* **WAL on the writer.** Per security-reviewer recommendation for I-3,
  multi-writer-safe storage eliminates the single-writer-JSONL caveat.
* **Untrusted response payload.** The ``response`` column may contain
  LLM-generated text that was itself shaped by adversarial news copy
  (prompt-injection vector). The reader treats it as opaque data —
  ``json.loads`` only; never ``eval``/``exec``/``pickle.load``.
* **No live-LLM fallback.** ``get`` raises on miss; callers must
  surface the miss to the operator. Falling back silently to a live
  call would re-introduce the non-determinism the cache exists to
  defeat.

Integrity check (I-12)
----------------------

Two layers, distinct on purpose:

1. **Single-read recompute (I-2 seed).** ``LLMReplayCache.get`` recomputes
   ``sha256(canonical_json(response))`` and compares to the stored
   ``response_hash``. Mismatch → ``CacheCorruptionError`` (in-flight
   corruption a scan has not yet seen).
2. **Durable poison mark (I-12).** Two columns — ``poisoned INTEGER`` and
   ``poisoned_reason TEXT`` — quarantine a row from the gate-eligible
   pool. Poison is set by either:
   * ``scan_integrity`` finding an integrity mismatch, or
   * ``migrate_jsonl_to_sqlite`` seeing ``repeat_verification ==
     'nondeterministic'`` at migration time (per Codex blocker D —
     Ollama+qwen3 multi-slot drift is durable evidence the row cannot
     be trusted to gate paper-mode deploys).
   Poisoned rows raise ``CachePoisonedError`` from ``get`` BEFORE the
   live recompute and report ``False`` from ``has`` so
   ``compute_cache_coverage`` excludes them from gate-eligible hits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Iterator, Literal

__all__ = [
    "CacheCorruptionError",
    "CacheHit",
    "CacheKey",
    "CacheMissError",
    "CachePoisonedError",
    "CoverageReport",
    "LLMReplayCache",
    "ScanReport",
    "compute_cache_coverage",
    "migrate_jsonl_to_sqlite",
    "scan_integrity",
]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DEFAULT_JSONL_PATH: Path = _REPO_ROOT / "logs" / "edge_replay" / "llm_capture.jsonl"
_DEFAULT_DB_PATH: Path = _REPO_ROOT / "logs" / "edge_replay" / "llm_cache.sqlite"

_ALLOWED_ENDPOINT_TYPES: Final[frozenset[str]] = frozenset({"openai_compat", "native"})
_ALLOWED_REPEAT_VERIFICATIONS: Final[frozenset[str]] = frozenset(
    {"verified", "nondeterministic", "skipped"}
)

# Columns making up the 13-field cache key (in fixed order).
_CACHE_KEY_COLUMNS: Final[tuple[str, ...]] = (
    "row_id",
    "prompt_template_hash",
    "prompt_filled_hash",
    "model_id",
    "model_digest",
    "ollama_version",
    "endpoint_type",
    "seed",
    "temperature",
    "num_ctx",
    "sampler_options_hash",
    "hardware_backend_class",
    "response_hash",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CacheMissError(Exception):
    """Raised when ``LLMReplayCache.get`` cannot find a row for the requested key.

    Replay code MUST NOT fall back to a live LLM call. Surface the miss
    to the operator: the cache being cold for a corpus row is itself a
    signal that the corpus contains rows that were never produced by the
    capture pipeline.
    """


class CacheCorruptionError(Exception):
    """Raised when an integrity hash mismatch is detected on read.

    This is the *single-read* recompute failure: we just hashed the stored
    payload and it does not match the stored hash. It is NOT a durable
    mark — the next read will recompute and may raise again, or may
    succeed if the underlying storage was repaired. Pair with
    ``scan_integrity`` + ``CachePoisonedError`` for the durable form.
    """


class CachePoisonedError(Exception):
    """Raised when ``LLMReplayCache.get`` hits a row marked ``poisoned=1``.

    Distinct from ``CacheCorruptionError`` (single-read recompute failure).
    Poison is a *durable* mark set by ``scan_integrity`` or by
    ``migrate_jsonl_to_sqlite`` when ``repeat_verification ==
    'nondeterministic'``. Poisoned rows are structurally excluded from
    gate-eligible hits: ``has`` returns False on them so
    ``compute_cache_coverage`` counts them as misses, and ``get`` raises
    this exception BEFORE running the on-the-fly recompute.

    The two-error split lets the operator tell a quarantined row
    (recorded fact: this row failed scan at time T) from a freshly-failed
    one (this read just disagreed with storage).
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheKey:
    """All 13 fields that uniquely identify a cached LLM response."""

    row_id: str
    prompt_template_hash: str
    prompt_filled_hash: str
    model_id: str
    model_digest: str | None
    ollama_version: str | None
    endpoint_type: Literal["openai_compat", "native"]
    seed: int | None
    temperature: float
    num_ctx: int | None
    sampler_options_hash: str
    hardware_backend_class: str | None
    response_hash: str


@dataclass(frozen=True)
class CacheHit:
    """Successful cache lookup. ``response`` is opaque UNTRUSTED data.

    The ``response`` field carries whatever the LLM returned, including
    potentially prompt-injected content from a hostile news headline.
    Callers must treat it as plain JSON-decoded data — ``dict``, ``list``,
    ``str``, ``int``, ``float``, ``bool``, or ``None`` — and MUST NOT
    ``eval``/``exec``/``pickle.load`` or attribute-access on it. The
    ``response_hash`` is an integrity anchor; do not re-derive trust
    from a hash match alone.

    The annotation is ``Any`` because JSON's value space is genuinely
    heterogeneous, not because anything goes.
    """

    key: CacheKey
    response: Any
    captured_at_utc: str
    repeat_verification: str


@dataclass(frozen=True)
class CoverageReport:
    """Output of ``compute_cache_coverage``."""

    total_rows: int
    cache_hits: int
    cache_misses: int
    coverage_ratio: float
    threshold: float
    passes_threshold: bool
    missing_row_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScanReport:
    """Output of ``scan_integrity`` (I-12).

    Fields
    ------
    total_rows:
        Every row visited (poisoned and clean).
    integrity_mismatches:
        Rows whose ``sha256(canonical_json(response_json))`` did not equal
        the stored ``response_hash`` at scan time.
    already_poisoned:
        Rows that were already ``poisoned=1`` before the scan started.
        Included so a re-run reports the steady-state quarantine size.
    newly_poisoned:
        Rows the scan promoted from clean to poisoned. Always 0 when
        ``mark_poisoned=False``.
    repeat_verification_nondeterministic:
        Rows whose ``repeat_verification`` column is ``'nondeterministic'``.
        Reported separately so the operator can confirm the
        migration-time auto-poison covered them.
    scan_duration_ms:
        Wall-clock duration of the scan in milliseconds. Helpful for
        capacity planning if the cache grows.
    """

    total_rows: int
    integrity_mismatches: int
    already_poisoned: int
    newly_poisoned: int
    repeat_verification_nondeterministic: int
    scan_duration_ms: float


# ---------------------------------------------------------------------------
# Canonical-JSON helpers (must match I-3's _canonical_json exactly)
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Order-stable, separator-stable JSON. Matches I-3 ``_canonical_json``.

    ``allow_nan=False`` ensures NaN / Infinity in a captured response would
    raise at hashing time rather than producing non-standard tokens
    downstream readers cannot round-trip.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_canonical(obj: Any) -> str:
    return _sha256_hex(_canonical_json(obj))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


# Note: the composite PRIMARY KEY uses the FULL extended cache key.
# Including ``response_hash`` in the key means a deterministic re-run with
# identical inputs produces a duplicate that INSERT OR REPLACE collapses
# safely. A non-deterministic re-run produces a different ``response_hash``
# and lands as a distinct row — which is the correct behavior for
# I-12 / non-determinism diagnostics.
#
# I-12 columns (``poisoned``, ``poisoned_reason``) are present in the fresh
# CREATE TABLE for new DBs. For existing DBs that pre-date I-12, the
# ``_ensure_i12_columns`` migration step issues ``ALTER TABLE ADD COLUMN``
# defensively — SQLite has no ``ADD COLUMN IF NOT EXISTS``, so we inspect
# PRAGMA table_info first to keep the call idempotent.
_SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS llm_responses (
    row_id                   TEXT NOT NULL,
    prompt_template_hash     TEXT NOT NULL,
    prompt_filled_hash       TEXT NOT NULL,
    model_id                 TEXT NOT NULL,
    model_digest             TEXT,
    ollama_version           TEXT,
    endpoint_type            TEXT NOT NULL CHECK (endpoint_type IN ('openai_compat', 'native')),
    seed                     INTEGER,
    temperature              REAL NOT NULL,
    num_ctx                  INTEGER,
    sampler_options_hash     TEXT NOT NULL,
    hardware_backend_class   TEXT,
    response_hash            TEXT NOT NULL,
    response_json            TEXT NOT NULL,
    repeat_verification      TEXT NOT NULL CHECK (repeat_verification IN ('verified', 'nondeterministic', 'skipped')),
    captured_at_utc          TEXT NOT NULL,
    migrated_at_utc          TEXT NOT NULL,
    poisoned                 INTEGER NOT NULL DEFAULT 0,
    poisoned_reason          TEXT,
    PRIMARY KEY (row_id, prompt_template_hash, prompt_filled_hash, model_id,
                 endpoint_type, sampler_options_hash, response_hash)
);

CREATE INDEX IF NOT EXISTS idx_row_id ON llm_responses(row_id);
CREATE INDEX IF NOT EXISTS idx_response_hash ON llm_responses(response_hash);
"""
# Note: the ``idx_poisoned`` index lives in ``_ensure_i12_columns`` rather
# than ``_SCHEMA_SQL`` so it runs AFTER the ``ALTER TABLE`` step. SQLite
# evaluates ``executescript`` top-to-bottom against the live schema, and a
# pre-I-12 DB does not yet have the column at script-evaluation time.


# I-12 poison-reason taxonomy. ``manual`` is reserved for future operator-set
# poison; today only the first two are written by this module.
POISON_REASON_INTEGRITY_MISMATCH: Final[str] = "integrity_mismatch"
POISON_REASON_REPEAT_NONDETERMINISTIC: Final[str] = "repeat_nondeterministic"
POISON_REASON_MANUAL: Final[str] = "manual"


def _ensure_i12_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add the I-12 ``poisoned`` / ``poisoned_reason`` columns.

    SQLite has no ``ALTER TABLE ADD COLUMN IF NOT EXISTS``, so we inspect
    ``PRAGMA table_info`` and only issue the ``ADD COLUMN`` when the column
    is absent. This makes the migration safe to run against:
      * a freshly created DB (CREATE TABLE already includes the columns),
      * a pre-I-12 DB (ADD COLUMN runs once, then is a no-op forever).
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(llm_responses)")}
    if "poisoned" not in existing:
        conn.execute(
            "ALTER TABLE llm_responses ADD COLUMN poisoned INTEGER NOT NULL DEFAULT 0"
        )
    if "poisoned_reason" not in existing:
        conn.execute(
            "ALTER TABLE llm_responses ADD COLUMN poisoned_reason TEXT"
        )
    # The index is in _SCHEMA_SQL, but a pre-I-12 DB never executed that
    # statement against the column. Re-emit it idempotently.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poisoned ON llm_responses(poisoned)")


# ---------------------------------------------------------------------------
# Writer (used by migrate_jsonl_to_sqlite only)
# ---------------------------------------------------------------------------


def _open_writer(db_path: Path) -> sqlite3.Connection:
    """Open a writer connection with WAL mode enabled.

    WAL mode persists in the database file header, so subsequent reader
    connections will also see WAL-mode semantics (concurrent reads while
    writes happen, no `database is locked` on read).

    Also runs the I-12 ``ALTER TABLE`` step to add the ``poisoned`` /
    ``poisoned_reason`` columns on pre-I-12 DBs. Idempotent — re-opening
    an already-migrated DB does nothing.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    _ensure_i12_columns(conn)
    return conn


# ---------------------------------------------------------------------------
# JSONL row → SQLite param tuple
# ---------------------------------------------------------------------------


def _validate_record(record: dict[str, Any]) -> None:
    """Raise ValueError if a record violates the cache-key contract.

    Called from migrate_jsonl_to_sqlite; failures here are caught and
    converted to a warning + skip (fail-soft on per-row basis).
    """
    missing = [
        col
        for col in _CACHE_KEY_COLUMNS
        if col not in record
    ]
    # The hash/payload fields are required too.
    for required in ("response", "captured_at_utc"):
        if required not in record:
            missing.append(required)
    if missing:
        raise ValueError(f"missing required fields: {missing}")

    endpoint_type = record["endpoint_type"]
    if endpoint_type not in _ALLOWED_ENDPOINT_TYPES:
        raise ValueError(
            f"invalid endpoint_type {endpoint_type!r} "
            f"(allowed: {sorted(_ALLOWED_ENDPOINT_TYPES)})"
        )

    repeat_verification = record.get("repeat_verification", "skipped")
    if repeat_verification not in _ALLOWED_REPEAT_VERIFICATIONS:
        raise ValueError(
            f"invalid repeat_verification {repeat_verification!r} "
            f"(allowed: {sorted(_ALLOWED_REPEAT_VERIFICATIONS)})"
        )


def _record_to_params(record: dict[str, Any], migrated_at: str) -> tuple[Any, ...]:
    """Convert a JSONL record into a parameter tuple for INSERT OR REPLACE.

    I-12 auto-poison rule (per spec §3 row I-12 v3 extension and Codex
    blocker D): when ``repeat_verification == 'nondeterministic'``, the
    row is durably marked ``poisoned=1`` with reason
    ``repeat_nondeterministic``. Ollama+qwen3 multi-slot GPU drift produced
    these rows in I-3; they MUST NOT be allowed to satisfy a paper-mode
    deploy gate, so we encode the quarantine at migration time rather than
    relying on operator memo discipline at read time.
    """
    response_json = _canonical_json(record["response"])
    repeat_verification = record.get("repeat_verification", "skipped")
    if repeat_verification == "nondeterministic":
        poisoned = 1
        poisoned_reason: str | None = POISON_REASON_REPEAT_NONDETERMINISTIC
    else:
        poisoned = 0
        poisoned_reason = None
    return (
        record["row_id"],
        record["prompt_template_hash"],
        record["prompt_filled_hash"],
        record["model_id"],
        record.get("model_digest"),
        record.get("ollama_version"),
        record["endpoint_type"],
        record.get("seed"),
        record["temperature"],
        record.get("num_ctx"),
        record["sampler_options_hash"],
        record.get("hardware_backend_class"),
        record["response_hash"],
        response_json,
        repeat_verification,
        record["captured_at_utc"],
        migrated_at,
        poisoned,
        poisoned_reason,
    )


_INSERT_SQL: Final[str] = """
INSERT OR REPLACE INTO llm_responses (
    row_id, prompt_template_hash, prompt_filled_hash, model_id,
    model_digest, ollama_version, endpoint_type, seed, temperature, num_ctx,
    sampler_options_hash, hardware_backend_class, response_hash, response_json,
    repeat_verification, captured_at_utc, migrated_at_utc,
    poisoned, poisoned_reason
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# Migration utility (writer)
# ---------------------------------------------------------------------------


def _iter_jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, line)`` pairs, skipping blank lines silently."""
    with open(path, "r", encoding="utf-8") as f:
        for idx, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            yield idx, stripped


def migrate_jsonl_to_sqlite(
    *,
    jsonl_path: Path | None = None,
    db_path: Path | None = None,
    overwrite: bool = False,
    logger: logging.Logger | None = None,
) -> int:
    """Migrate I-3's JSONL capture into the SQLite cache.

    Returns the number of rows MIGRATED (i.e. INSERT OR REPLACE attempts
    that succeeded). Malformed JSONL lines and rows violating the schema
    are logged at WARNING level and skipped, not raised. The migration
    is idempotent — re-running with the same JSONL produces the same DB
    state.

    **I-12 auto-poison.** Rows with ``repeat_verification ==
    'nondeterministic'`` are written with ``poisoned=1``,
    ``poisoned_reason='repeat_nondeterministic'`` so they are structurally
    excluded from gate-eligible reads. See ``_record_to_params``.

    Parameters
    ----------
    jsonl_path:
        Source JSONL. Defaults to ``logs/edge_replay/llm_capture.jsonl``
        under the repo root.
    db_path:
        Target SQLite file. Defaults to ``logs/edge_replay/llm_cache.sqlite``.
    overwrite:
        If False (default) and the target DB already contains rows,
        raise ``RuntimeError``. Set True to clear and re-migrate.
    logger:
        Optional logger. Defaults to the module logger.
    """
    eff_logger = logger or logging.getLogger(__name__)
    src = Path(jsonl_path) if jsonl_path is not None else _DEFAULT_JSONL_PATH
    dst = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH

    if not src.exists():
        # Treat a missing JSONL as 0 rows. The DB is still created with
        # the schema so downstream readers don't trip on a missing file.
        with _open_writer(dst) as conn:
            conn.commit()
        eff_logger.info(
            "migrate_jsonl_to_sqlite: source %s does not exist; 0 rows migrated", src
        )
        return 0

    conn = _open_writer(dst)
    try:
        existing = conn.execute("SELECT COUNT(*) FROM llm_responses").fetchone()[0]
        if existing > 0:
            if not overwrite:
                raise RuntimeError(
                    "cache already populated; pass overwrite=True to replace"
                )
            conn.execute("DELETE FROM llm_responses")
            conn.commit()

        migrated_at = _now_utc_iso()
        migrated = 0

        for line_no, line in _iter_jsonl_lines(src):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                eff_logger.warning(
                    "migrate_jsonl_to_sqlite: malformed JSON on line %d: %s",
                    line_no,
                    exc,
                )
                continue

            try:
                _validate_record(record)
            except ValueError as exc:
                eff_logger.warning(
                    "migrate_jsonl_to_sqlite: invalid record on line %d (skipped): %s",
                    line_no,
                    exc,
                )
                continue

            try:
                params = _record_to_params(record, migrated_at)
                conn.execute(_INSERT_SQL, params)
                migrated += 1
            except sqlite3.IntegrityError as exc:
                # CHECK constraints in the schema mirror _validate_record,
                # so a hit here implies a record _validate_record missed.
                # Skip + warn rather than raise — same fail-soft policy.
                eff_logger.warning(
                    "migrate_jsonl_to_sqlite: integrity error on line %d (skipped): %s",
                    line_no,
                    exc,
                )
                continue
            except (TypeError, ValueError) as exc:
                eff_logger.warning(
                    "migrate_jsonl_to_sqlite: serialization error on line %d (skipped): %s",
                    line_no,
                    exc,
                )
                continue

        conn.commit()
        return migrated
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reader (read-only)
# ---------------------------------------------------------------------------


def _open_reader(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection via ``file:?mode=ro`` URI.

    The reader connection is structurally incapable of writing — attempts
    to INSERT/UPDATE/DELETE through it raise ``sqlite3.OperationalError``.
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"LLM cache DB does not exist: {db_path}. "
            f"Run migrate_jsonl_to_sqlite() first."
        )
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    return conn


# Lookup uses NULL-aware comparison: ``col IS ?`` collapses NULL=NULL to TRUE
# and NULL=value to FALSE in a single SQL primitive. Doing this with `=` would
# silently fail to match NULL-bearing rows because `NULL = NULL` is NULL.
_LOOKUP_WHERE: Final[str] = " AND ".join(f"{col} IS ?" for col in _CACHE_KEY_COLUMNS)
# I-12: SELECT also pulls ``poisoned`` + ``poisoned_reason`` so ``get`` can
# raise ``CachePoisonedError`` BEFORE running the live integrity recompute.
# This avoids spending CPU on a row the operator has already quarantined.
_LOOKUP_SQL: Final[str] = f"""
SELECT response_json, captured_at_utc, repeat_verification, poisoned, poisoned_reason
FROM llm_responses
WHERE {_LOOKUP_WHERE}
LIMIT 1
"""

# ``has`` excludes poisoned rows by construction so ``compute_cache_coverage``
# counts them as misses (a poisoned row cannot satisfy a gate). The
# additional WHERE clause is added to ``_LOOKUP_WHERE`` rather than checking
# the row in Python so the predicate is evaluated inside SQLite.
_HAS_SQL: Final[str] = f"""
SELECT 1
FROM llm_responses
WHERE {_LOOKUP_WHERE} AND poisoned = 0
LIMIT 1
"""

# ``is_poisoned`` ignores ``poisoned`` filtering — we want to report on a
# row whether it is healthy or poisoned. Returns NULL if the row is absent.
_IS_POISONED_SQL: Final[str] = f"""
SELECT poisoned
FROM llm_responses
WHERE {_LOOKUP_WHERE}
LIMIT 1
"""


def _key_to_params(key: CacheKey) -> tuple[Any, ...]:
    return (
        key.row_id,
        key.prompt_template_hash,
        key.prompt_filled_hash,
        key.model_id,
        key.model_digest,
        key.ollama_version,
        key.endpoint_type,
        key.seed,
        key.temperature,
        key.num_ctx,
        key.sampler_options_hash,
        key.hardware_backend_class,
        key.response_hash,
    )


class LLMReplayCache:
    """Read-only SQLite-backed cache for I-3 LLM captures.

    The connection is opened in read-only mode via ``file:?mode=ro``.
    Attempts to write through this connection raise ``OperationalError``.

    **Not thread-safe.** SQLite connections default to ``check_same_thread=True``;
    create one ``LLMReplayCache`` instance per thread. If I-4 ever parallelises
    corpus rows across a ``ThreadPoolExecutor``, sharing a single instance
    will produce silent corruption or ``ProgrammingError``.

    Use as a context manager:

        with LLMReplayCache() as cache:
            hit = cache.get(key)

    Security note: ``CacheHit.response`` is opaque, possibly adversarial
    data (the original LLM call may have been shaped by prompt-injected
    news content). Callers MUST NOT ``eval``/``exec``/``pickle.load`` it.
    Treat as plain JSON data — read fields and compare values only.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
        self._conn = _open_reader(self._db_path)

    def __enter__(self) -> "LLMReplayCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM llm_responses").fetchone()
        return int(row[0])

    def has(self, key: CacheKey) -> bool:
        """Return True iff a NON-POISONED row matches ``key``.

        Poisoned rows are excluded by construction so that
        ``compute_cache_coverage`` counts them as misses — a poisoned row
        cannot satisfy a gate. Call ``is_poisoned`` if you need to
        distinguish "absent" from "quarantined".
        """
        row = self._conn.execute(_HAS_SQL, _key_to_params(key)).fetchone()
        return row is not None

    def is_poisoned(self, key: CacheKey) -> bool:
        """Return True iff a row matches ``key`` AND is marked poisoned.

        Returns False for both "row absent" and "row present, not poisoned".
        Use ``has`` for the gate-eligible check; this is the diagnostic
        used to explain WHY ``has`` returned False on a row that
        ``LLMReplayCache.count`` would otherwise include.
        """
        row = self._conn.execute(_IS_POISONED_SQL, _key_to_params(key)).fetchone()
        if row is None:
            return False
        return bool(row[0])

    def get(self, key: CacheKey) -> CacheHit:
        """Return the cached response for ``key`` or raise.

        Raises
        ------
        CacheMissError
            No row matches all 13 cache-key fields.
        CachePoisonedError
            A matching row exists but has ``poisoned=1`` (durable mark set
            by ``scan_integrity`` or by ``migrate_jsonl_to_sqlite`` for
            ``repeat_verification='nondeterministic'``). Raised BEFORE the
            live recompute so we never spend CPU re-hashing a row the
            operator has already quarantined.
        CacheCorruptionError
            The stored ``response_json`` does not hash to the stored
            ``response_hash`` on this read. Distinct from
            ``CachePoisonedError`` — this is a single-read recompute
            failure, not a durable mark. ``scan_integrity`` is the
            companion tool that promotes such rows to poisoned.
        """
        row = self._conn.execute(_LOOKUP_SQL, _key_to_params(key)).fetchone()
        if row is None:
            raise CacheMissError(f"no cache row for key row_id={key.row_id!r}")

        response_json, captured_at_utc, repeat_verification, poisoned, poisoned_reason = row

        # I-12: poisoned-row gate runs BEFORE the live recompute. A
        # poisoned row is durable evidence the operator has already
        # quarantined this entry; re-running the hash would be wasted
        # CPU and would surface the wrong error type (CacheCorruption
        # implies in-flight surprise; CachePoisoned implies recorded fact).
        if poisoned:
            raise CachePoisonedError(
                f"poisoned cache row for row_id={key.row_id!r}: "
                f"reason={poisoned_reason!r}"
            )

        # json.loads only — NEVER eval/exec/pickle. The payload is
        # adversarial-by-default (see class docstring).
        try:
            response = json.loads(response_json)
        except json.JSONDecodeError as exc:
            raise CacheCorruptionError(
                f"response_json is not valid JSON for row_id={key.row_id!r}: {exc}"
            ) from exc

        # Integrity check: recompute the canonical-JSON hash and compare.
        # Mismatch is single-read corruption — pair with ``scan_integrity``
        # to durably mark all such rows as poisoned in a single pass.
        computed = _hash_canonical(response)
        if computed != key.response_hash:
            raise CacheCorruptionError(
                f"integrity check failed for row_id={key.row_id!r}: "
                f"stored hash {key.response_hash!r} != computed hash {computed!r}"
            )

        return CacheHit(
            key=key,
            response=response,
            captured_at_utc=captured_at_utc,
            repeat_verification=repeat_verification,
        )


# ---------------------------------------------------------------------------
# Coverage check
# ---------------------------------------------------------------------------


def _row_to_cache_key(row: dict[str, Any]) -> CacheKey:
    """Build a CacheKey from a row dict. Missing optional fields default to None."""
    return CacheKey(
        row_id=row["row_id"],
        prompt_template_hash=row["prompt_template_hash"],
        prompt_filled_hash=row["prompt_filled_hash"],
        model_id=row["model_id"],
        model_digest=row.get("model_digest"),
        ollama_version=row.get("ollama_version"),
        endpoint_type=row["endpoint_type"],
        seed=row.get("seed"),
        temperature=row["temperature"],
        num_ctx=row.get("num_ctx"),
        sampler_options_hash=row["sampler_options_hash"],
        hardware_backend_class=row.get("hardware_backend_class"),
        response_hash=row["response_hash"],
    )


_MAX_MISSING_ROW_IDS: Final[int] = 20


def compute_cache_coverage(
    corpus_rows: Iterable[dict[str, Any]],
    cache: LLMReplayCache,
    *,
    threshold: float = 0.95,
) -> CoverageReport:
    """Compute cache coverage over ``corpus_rows``.

    Each row in ``corpus_rows`` MUST carry the 13 cache-key fields as keys
    (matching I-3's JSONL schema). Optional fields (``model_digest``,
    ``ollama_version``, ``seed``, ``num_ctx``, ``hardware_backend_class``)
    may be ``None`` or absent.

    Returns a ``CoverageReport`` with ``coverage_ratio = hits / total`` and
    ``passes_threshold = coverage_ratio >= threshold``. An empty corpus is
    treated as passing (ratio = 1.0) since there's nothing to fail on. The
    ``missing_row_ids`` list is capped at the first 20 misses for readable
    operator output.

    This function is intended as a pre-gate check: I-4 replay-as-CI must
    refuse to open a T1 observation window if coverage is below the
    threshold.

    Implementation note (per python-reviewer): we materialise ``corpus_rows``
    into a list rather than stream it. The signature accepts ``Iterable``
    for API ergonomics, but materialisation is the simpler implementation
    at current corpus scale (≤1k rows per cycle-17d artifact). I-4 may
    revisit if corpora ever cross 100k rows; today the trade-off favours
    code clarity over a streaming counter + bounded missing-list buffer.
    """
    rows = list(corpus_rows)
    total = len(rows)
    if total == 0:
        return CoverageReport(
            total_rows=0,
            cache_hits=0,
            cache_misses=0,
            coverage_ratio=1.0,
            threshold=threshold,
            passes_threshold=True,
            missing_row_ids=[],
        )

    hits = 0
    misses = 0
    missing_row_ids: list[str] = []
    for row in rows:
        try:
            key = _row_to_cache_key(row)
        except KeyError as exc:
            # A corpus row missing a required cache-key column is treated
            # as a miss, with the row_id (if present) recorded.
            misses += 1
            if len(missing_row_ids) < _MAX_MISSING_ROW_IDS:
                missing_row_ids.append(str(row.get("row_id", f"<missing-{exc}>")))
            continue

        if cache.has(key):
            hits += 1
        else:
            misses += 1
            if len(missing_row_ids) < _MAX_MISSING_ROW_IDS:
                # I-12: distinguish absent rows from poisoned (quarantined)
                # rows in operator output. ``has`` already excludes
                # poisoned rows, but the operator needs to know WHY a
                # gate failed — "poisoned" is fixable by re-capture or
                # by un-poisoning after the underlying nondeterminism
                # is understood; a missing row needs the capture pipeline
                # to be re-run.
                if cache.is_poisoned(key):
                    missing_row_ids.append(f"{key.row_id} [poisoned]")
                else:
                    missing_row_ids.append(key.row_id)

    ratio = hits / total
    return CoverageReport(
        total_rows=total,
        cache_hits=hits,
        cache_misses=misses,
        coverage_ratio=ratio,
        threshold=threshold,
        passes_threshold=ratio >= threshold,
        missing_row_ids=missing_row_ids,
    )


# ---------------------------------------------------------------------------
# scan_integrity (I-12)
# ---------------------------------------------------------------------------


# Composite-PK component columns identify a row for UPDATE. Including the
# full PK in the WHERE clause is the safest way to mutate exactly one row.
_SCAN_SELECT_SQL: Final[str] = """
SELECT
    row_id, prompt_template_hash, prompt_filled_hash, model_id,
    endpoint_type, sampler_options_hash, response_hash,
    response_json, repeat_verification, poisoned
FROM llm_responses
"""

_SCAN_POISON_SQL: Final[str] = """
UPDATE llm_responses
SET poisoned = 1, poisoned_reason = ?
WHERE row_id = ?
  AND prompt_template_hash = ?
  AND prompt_filled_hash = ?
  AND model_id = ?
  AND endpoint_type = ?
  AND sampler_options_hash = ?
  AND response_hash = ?
"""


def scan_integrity(
    db_path: Path | None = None,
    *,
    mark_poisoned: bool = True,
    logger: logging.Logger | None = None,
) -> ScanReport:
    """Walk every row in the cache and verify ``response_hash`` integrity.

    For each row, recompute ``sha256(canonical_json(response_json))`` and
    compare to the stored ``response_hash``. Mismatches are integrity
    failures. When ``mark_poisoned`` is True (default), they are
    durably marked ``poisoned=1`` with reason ``integrity_mismatch``.

    Returns a ``ScanReport`` summarising what was found. Always reports
    the count of pre-existing poisoned rows and rows tagged with
    ``repeat_verification='nondeterministic'`` so the operator can
    distinguish:

      * fresh integrity damage (``newly_poisoned > 0``),
      * the steady-state quarantine size (``already_poisoned``), and
      * the migration-time auto-poison coverage
        (``repeat_verification_nondeterministic``).

    Parameters
    ----------
    db_path:
        Target SQLite file. Defaults to ``logs/edge_replay/llm_cache.sqlite``.
    mark_poisoned:
        When True (default), promote integrity-mismatch rows to
        ``poisoned=1``. When False, report only — no DB writes.
    logger:
        Optional logger. Defaults to the module logger.

    Notes
    -----

    Uses the writer connection so it can both SELECT and UPDATE under one
    transaction. ``_open_writer`` runs ``_ensure_i12_columns`` first, so
    a scan against a pre-I-12 DB transparently migrates the schema
    before scanning.
    """
    import time

    eff_logger = logger or logging.getLogger(__name__)
    dst = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH

    if not dst.exists():
        raise FileNotFoundError(
            f"LLM cache DB does not exist: {dst}. "
            f"Run migrate_jsonl_to_sqlite() first."
        )

    started_at = time.perf_counter()
    conn = _open_writer(dst)
    try:
        total = 0
        integrity_mismatches = 0
        already_poisoned = 0
        newly_poisoned = 0
        repeat_nondeterministic = 0

        # Materialise rows first so the UPDATE in the loop body doesn't
        # mutate the iterator (SQLite SELECT cursors are forward-only,
        # and we'd otherwise risk seeing the same row twice or skipping
        # one). The cache is bounded by capture-corpus size (≤1k rows
        # per cycle artifact), so the memory cost is trivial.
        rows = list(conn.execute(_SCAN_SELECT_SQL))
        total = len(rows)

        # Track rows we already marked in this scan so a (degenerate)
        # duplicate-pk situation doesn't double-count newly_poisoned.
        promoted_keys: set[tuple[Any, ...]] = set()

        for row in rows:
            (
                row_id,
                prompt_template_hash,
                prompt_filled_hash,
                model_id,
                endpoint_type,
                sampler_options_hash,
                response_hash,
                response_json,
                repeat_verification,
                poisoned,
            ) = row

            if repeat_verification == "nondeterministic":
                repeat_nondeterministic += 1

            if poisoned:
                already_poisoned += 1
                # An already-poisoned row may also be integrity-damaged;
                # we don't care for the gate-eligibility decision (it
                # was already excluded), but we still report it so the
                # operator can spot a second failure mode on the same row.
                # Skip the recompute to keep the scan O(unpoisoned).
                continue

            try:
                parsed = json.loads(response_json)
            except json.JSONDecodeError as exc:
                integrity_mismatches += 1
                eff_logger.warning(
                    "scan_integrity: row_id=%r has non-JSON response_json (%s); "
                    "poisoning",
                    row_id,
                    exc,
                )
                if mark_poisoned:
                    pk = (
                        row_id,
                        prompt_template_hash,
                        prompt_filled_hash,
                        model_id,
                        endpoint_type,
                        sampler_options_hash,
                        response_hash,
                    )
                    if pk not in promoted_keys:
                        conn.execute(
                            _SCAN_POISON_SQL,
                            (POISON_REASON_INTEGRITY_MISMATCH, *pk),
                        )
                        promoted_keys.add(pk)
                        newly_poisoned += 1
                continue

            computed = _hash_canonical(parsed)
            if computed != response_hash:
                integrity_mismatches += 1
                eff_logger.warning(
                    "scan_integrity: row_id=%r integrity mismatch "
                    "(stored=%s computed=%s)",
                    row_id,
                    response_hash,
                    computed,
                )
                if mark_poisoned:
                    pk = (
                        row_id,
                        prompt_template_hash,
                        prompt_filled_hash,
                        model_id,
                        endpoint_type,
                        sampler_options_hash,
                        response_hash,
                    )
                    if pk not in promoted_keys:
                        conn.execute(
                            _SCAN_POISON_SQL,
                            (POISON_REASON_INTEGRITY_MISMATCH, *pk),
                        )
                        promoted_keys.add(pk)
                        newly_poisoned += 1

        if mark_poisoned:
            conn.commit()

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        return ScanReport(
            total_rows=total,
            integrity_mismatches=integrity_mismatches,
            already_poisoned=already_poisoned,
            newly_poisoned=newly_poisoned,
            repeat_verification_nondeterministic=repeat_nondeterministic,
            scan_duration_ms=elapsed_ms,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm_cache",
        description="I-2 LLM cache reader + JSONL → SQLite migration + I-12 scan",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("migrate", help="Migrate JSONL capture into SQLite")
    m.add_argument("--jsonl", type=Path, default=None)
    m.add_argument("--db", type=Path, default=None)
    m.add_argument("--overwrite", action="store_true")

    c = sub.add_parser("coverage", help="Compute cache coverage over a corpus")
    c.add_argument("--corpus", type=Path, required=True, nargs="+")
    c.add_argument("--db", type=Path, default=None)
    c.add_argument("--threshold", type=float, default=0.95)

    s = sub.add_parser(
        "scan",
        help="I-12: walk every row and verify response_hash integrity",
    )
    s.add_argument("--db", type=Path, default=None)
    # mark-poison vs report-only as a single flag pair so the operator
    # cannot accidentally fall into an unintended write mode.
    poison_group = s.add_mutually_exclusive_group()
    poison_group.add_argument(
        "--mark-poisoned",
        action="store_true",
        default=True,
        help="Promote integrity-mismatch rows to poisoned=1 (default)",
    )
    poison_group.add_argument(
        "--report-only",
        dest="mark_poisoned",
        action="store_false",
        help="Walk the DB and report findings without writing",
    )

    return p


def _load_corpus_rows(paths: list[Path]) -> list[dict[str, Any]]:
    """Read JSONL corpus files into a list. Per python-reviewer MEDIUM:
    malformed lines must surface the (filename, line-number, parser-error)
    context — the migrator's silent-skip pattern is wrong for a one-shot
    operator-invoked coverage check where a malformed corpus is itself
    the signal the operator needs to see."""
    rows: list[dict[str, Any]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    rows.append(json.loads(stripped))
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"malformed JSON in corpus {path}:{lineno}: {exc}"
                    ) from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_argparser().parse_args(argv)

    if args.cmd == "migrate":
        migrated = migrate_jsonl_to_sqlite(
            jsonl_path=args.jsonl,
            db_path=args.db,
            overwrite=args.overwrite,
        )
        print(f"migrated {migrated} rows")
        return 0

    if args.cmd == "coverage":
        rows = _load_corpus_rows(args.corpus)
        db_path = args.db if args.db is not None else _DEFAULT_DB_PATH
        with LLMReplayCache(db_path=db_path) as cache:
            report = compute_cache_coverage(rows, cache, threshold=args.threshold)
        print(json.dumps(
            {
                "total_rows": report.total_rows,
                "cache_hits": report.cache_hits,
                "cache_misses": report.cache_misses,
                "coverage_ratio": report.coverage_ratio,
                "threshold": report.threshold,
                "passes_threshold": report.passes_threshold,
                "missing_row_ids": report.missing_row_ids,
            },
            indent=2,
        ))
        return 0 if report.passes_threshold else 1

    if args.cmd == "scan":
        scan_report = scan_integrity(
            db_path=args.db,
            mark_poisoned=args.mark_poisoned,
        )
        print(json.dumps(
            {
                "total_rows": scan_report.total_rows,
                "integrity_mismatches": scan_report.integrity_mismatches,
                "already_poisoned": scan_report.already_poisoned,
                "newly_poisoned": scan_report.newly_poisoned,
                "repeat_verification_nondeterministic": (
                    scan_report.repeat_verification_nondeterministic
                ),
                "scan_duration_ms": scan_report.scan_duration_ms,
                "mark_poisoned": args.mark_poisoned,
            },
            indent=2,
        ))
        # Exit 1 on any integrity mismatch regardless of --mark-poisoned,
        # so CI / cron observers can detect freshly-failed rows without
        # parsing the JSON. The poison-write happens as a side effect.
        return 0 if scan_report.integrity_mismatches == 0 else 1

    return 2  # pragma: no cover — argparse requires a subcommand


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
