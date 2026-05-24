"""I-2 SQLite-backed LLM response cache reader (rapid-learning v3).

Reference spec:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §3 row I-2 + §4 §16.7.

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
``CacheCorruptionError``           — integrity-check failure (I-12 seed)
``CoverageReport``                 — output of ``compute_cache_coverage``
``compute_cache_coverage``         — pre-gate ratio check
``migrate_jsonl_to_sqlite``        — one-shot JSONL → SQLite migration

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

Integrity check
---------------

``LLMReplayCache.get`` recomputes ``sha256(canonical_json(response))``
and compares it to the stored ``response_hash``. Any mismatch raises
``CacheCorruptionError``. This is the seed for I-12's broader integrity
contract.
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
    "CoverageReport",
    "LLMReplayCache",
    "compute_cache_coverage",
    "migrate_jsonl_to_sqlite",
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

    Seed for the I-12 broader integrity-check contract.
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
    """Successful cache lookup. ``response`` is opaque untrusted data."""

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
    PRIMARY KEY (row_id, prompt_template_hash, prompt_filled_hash, model_id,
                 endpoint_type, sampler_options_hash, response_hash)
);

CREATE INDEX IF NOT EXISTS idx_row_id ON llm_responses(row_id);
CREATE INDEX IF NOT EXISTS idx_response_hash ON llm_responses(response_hash);
"""


# ---------------------------------------------------------------------------
# Writer (used by migrate_jsonl_to_sqlite only)
# ---------------------------------------------------------------------------


def _open_writer(db_path: Path) -> sqlite3.Connection:
    """Open a writer connection with WAL mode enabled.

    WAL mode persists in the database file header, so subsequent reader
    connections will also see WAL-mode semantics (concurrent reads while
    writes happen, no `database is locked` on read).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
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
    """Convert a JSONL record into a parameter tuple for INSERT OR REPLACE."""
    response_json = _canonical_json(record["response"])
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
        record.get("repeat_verification", "skipped"),
        record["captured_at_utc"],
        migrated_at,
    )


_INSERT_SQL: Final[str] = """
INSERT OR REPLACE INTO llm_responses (
    row_id, prompt_template_hash, prompt_filled_hash, model_id,
    model_digest, ollama_version, endpoint_type, seed, temperature, num_ctx,
    sampler_options_hash, hardware_backend_class, response_hash, response_json,
    repeat_verification, captured_at_utc, migrated_at_utc
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
_LOOKUP_SQL: Final[str] = f"""
SELECT response_json, captured_at_utc, repeat_verification
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
        row = self._conn.execute(_LOOKUP_SQL, _key_to_params(key)).fetchone()
        return row is not None

    def get(self, key: CacheKey) -> CacheHit:
        """Return the cached response for ``key`` or raise.

        Raises
        ------
        CacheMissError
            No row matches all 13 cache-key fields.
        CacheCorruptionError
            The stored ``response_json`` does not hash to the stored
            ``response_hash``. This is the I-12 integrity-check seed.
        """
        row = self._conn.execute(_LOOKUP_SQL, _key_to_params(key)).fetchone()
        if row is None:
            raise CacheMissError(f"no cache row for key row_id={key.row_id!r}")

        response_json, captured_at_utc, repeat_verification = row
        # json.loads only — NEVER eval/exec/pickle. The payload is
        # adversarial-by-default (see class docstring).
        try:
            response = json.loads(response_json)
        except json.JSONDecodeError as exc:
            raise CacheCorruptionError(
                f"response_json is not valid JSON for row_id={key.row_id!r}: {exc}"
            ) from exc

        # Integrity check: recompute the canonical-JSON hash and compare.
        # Mismatch is corruption — seed for I-12 broader integrity contract.
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
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="llm_cache",
        description="I-2 LLM cache reader + JSONL → SQLite migration",
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

    return p


def _load_corpus_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                stripped = raw.strip()
                if not stripped:
                    continue
                rows.append(json.loads(stripped))
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

    return 2  # pragma: no cover — argparse requires a subcommand


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
