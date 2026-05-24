"""Tests for ``scripts/edge_replay/llm_cache.py`` (I-2).

Reference spec:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §3 row I-2 + §4 §16.7.

The cache reader provides exact-match lookup over the 13-field cache key
shipped by I-3. The contract under test here:

* ``LLMReplayCache.get`` raises ``CacheMissError`` on lookup miss and
  ``CacheCorruptionError`` on hash-vs-payload mismatch.
* ``LLMReplayCache.has`` is a non-raising boolean equivalent.
* ``compute_cache_coverage`` returns ``CoverageReport`` with the correct
  ratio, threshold result, and missing_row_ids list (capped at 20).
* ``migrate_jsonl_to_sqlite`` is idempotent (INSERT OR REPLACE), refuses
  to overwrite without ``overwrite=True``, skips malformed JSONL lines
  with a warning, and supports zero-row JSONL.
* SQLite is opened with WAL mode on the writer and ``mode=ro`` on the
  reader; attempting a write through the reader connection raises.
* ``endpoint_type`` CHECK constraint rejects values outside the allowed
  set.
* NULL/None field equivalence: ``model_digest=None`` in the key matches
  ``model_digest`` NULL in the DB.
* Untrusted response payload is loaded via ``json.loads`` only — never
  ``eval``/``exec``/``pickle`` (security caveat carried from I-3).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
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


def _sample_response() -> dict[str, Any]:
    return {"prob": 0.42, "direction": "yes", "magnitude": "medium"}


def _sample_record(
    *,
    row_id: str = "signal::KXFOO::news_123",
    model_digest: str | None = "sha256:deadbeef",
    seed: int | None = None,
    response: Any | None = None,
    repeat_verification: str = "verified",
) -> dict[str, Any]:
    """Build a JSONL record matching I-3's on-disk schema."""
    if response is None:
        response = _sample_response()
    return {
        "row_id": row_id,
        "prompt_template_hash": _sha256_hex("template-text"),
        "prompt_filled_hash": _sha256_hex("filled-text-" + row_id),
        "model_id": "qwen3:14b",
        "model_digest": model_digest,
        "ollama_version": "0.11.2",
        "endpoint_type": "openai_compat",
        "seed": seed,
        "temperature": 0,
        "num_ctx": 8192,
        "sampler_options_hash": _hash_canonical({}),
        "hardware_backend_class": "arm64-Darwin",
        "response_hash": _hash_canonical(response),
        "response": response,
        "request_payload": {"model": "qwen3:14b", "messages": []},
        "repeat_verification": repeat_verification,
        "captured_at_utc": "2026-05-23T22:01:23Z",
    }


def _record_to_cache_key(record: dict[str, Any]):
    """Build a CacheKey from a JSONL record (test-only convenience)."""
    from scripts.edge_replay.llm_cache import CacheKey

    return CacheKey(
        row_id=record["row_id"],
        prompt_template_hash=record["prompt_template_hash"],
        prompt_filled_hash=record["prompt_filled_hash"],
        model_id=record["model_id"],
        model_digest=record["model_digest"],
        ollama_version=record["ollama_version"],
        endpoint_type=record["endpoint_type"],
        seed=record["seed"],
        temperature=record["temperature"],
        num_ctx=record["num_ctx"],
        sampler_options_hash=record["sampler_options_hash"],
        hardware_backend_class=record["hardware_backend_class"],
        response_hash=record["response_hash"],
    )


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _populate_db(jsonl_path: Path, db_path: Path, records: list[dict[str, Any]]) -> int:
    from scripts.edge_replay.llm_cache import migrate_jsonl_to_sqlite

    _write_jsonl(jsonl_path, records)
    return migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_exposes_public_api():
    import scripts.edge_replay.llm_cache as mod

    assert hasattr(mod, "LLMReplayCache")
    assert hasattr(mod, "CacheKey")
    assert hasattr(mod, "CacheHit")
    assert hasattr(mod, "CacheMissError")
    assert hasattr(mod, "CacheCorruptionError")
    assert hasattr(mod, "CoverageReport")
    assert hasattr(mod, "compute_cache_coverage")
    assert hasattr(mod, "migrate_jsonl_to_sqlite")


# ---------------------------------------------------------------------------
# get / has — miss & hit
# ---------------------------------------------------------------------------


def test_empty_db_get_raises_cache_miss_error(tmp_path):
    from scripts.edge_replay.llm_cache import (
        CacheMissError,
        LLMReplayCache,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "empty.jsonl"
    _write_jsonl(jsonl_path, [])
    migrated = migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)
    assert migrated == 0

    key = _record_to_cache_key(_sample_record())
    with LLMReplayCache(db_path=db_path) as cache:
        with pytest.raises(CacheMissError):
            cache.get(key)


def test_get_returns_cache_hit_for_present_key(tmp_path):
    from scripts.edge_replay.llm_cache import CacheHit, LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        hit = cache.get(key)
    assert isinstance(hit, CacheHit)
    assert hit.key == key
    assert hit.response == record["response"]
    assert hit.captured_at_utc == record["captured_at_utc"]
    assert hit.repeat_verification == record["repeat_verification"]


def test_has_returns_false_on_miss_true_on_hit(tmp_path):
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])

    present = _record_to_cache_key(record)
    other_record = _sample_record(row_id="signal::KXBAR::news_999")
    missing = _record_to_cache_key(other_record)

    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.has(present) is True
        assert cache.has(missing) is False


# ---------------------------------------------------------------------------
# Integrity check
# ---------------------------------------------------------------------------


def test_tampered_response_json_raises_cache_corruption_error(tmp_path):
    from scripts.edge_replay.llm_cache import (
        CacheCorruptionError,
        LLMReplayCache,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])

    # Tamper directly with the SQLite payload while keeping the response_hash
    # column intact. This simulates a corruption event that the integrity
    # check is the seed of I-12's broader integrity-check contract.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE llm_responses SET response_json = ? WHERE row_id = ?",
            (json.dumps({"prob": 0.99, "direction": "no"}), record["row_id"]),
        )
        conn.commit()

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        with pytest.raises(CacheCorruptionError):
            cache.get(key)


def test_count_returns_row_count(tmp_path):
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    records = [
        _sample_record(row_id=f"signal::KX::news_{i}") for i in range(3)
    ]
    _populate_db(jsonl_path, db_path, records)

    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.count() == 3


# ---------------------------------------------------------------------------
# compute_cache_coverage
# ---------------------------------------------------------------------------


def test_compute_cache_coverage_zero_rows_passes_threshold(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        compute_cache_coverage,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "empty.jsonl"
    _write_jsonl(jsonl_path, [])
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    with LLMReplayCache(db_path=db_path) as cache:
        report = compute_cache_coverage([], cache, threshold=0.95)
    assert report.total_rows == 0
    assert report.cache_hits == 0
    assert report.cache_misses == 0
    assert report.coverage_ratio == 1.0
    assert report.passes_threshold is True
    assert report.missing_row_ids == []


def test_compute_cache_coverage_full_coverage(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        compute_cache_coverage,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    records = [
        _sample_record(row_id=f"signal::KX::news_{i}") for i in range(5)
    ]
    _populate_db(jsonl_path, db_path, records)

    with LLMReplayCache(db_path=db_path) as cache:
        report = compute_cache_coverage(records, cache, threshold=0.95)
    assert report.total_rows == 5
    assert report.cache_hits == 5
    assert report.cache_misses == 0
    assert report.coverage_ratio == 1.0
    assert report.passes_threshold is True
    assert report.missing_row_ids == []


def test_compute_cache_coverage_half_missing_fails_default_threshold(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        compute_cache_coverage,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    present_records = [
        _sample_record(row_id=f"signal::KX::present_{i}") for i in range(5)
    ]
    _populate_db(jsonl_path, db_path, present_records)

    missing_records = [
        _sample_record(row_id=f"signal::KX::missing_{i}") for i in range(5)
    ]
    corpus = present_records + missing_records

    with LLMReplayCache(db_path=db_path) as cache:
        report = compute_cache_coverage(corpus, cache, threshold=0.95)
    assert report.total_rows == 10
    assert report.cache_hits == 5
    assert report.cache_misses == 5
    assert report.coverage_ratio == 0.5
    assert report.passes_threshold is False
    assert len(report.missing_row_ids) == 5
    assert all(r.startswith("signal::KX::missing_") for r in report.missing_row_ids)


def test_compute_cache_coverage_missing_row_ids_capped_at_twenty(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        compute_cache_coverage,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "empty.jsonl"
    _write_jsonl(jsonl_path, [])
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    corpus = [
        _sample_record(row_id=f"signal::KX::missing_{i:03d}") for i in range(50)
    ]
    with LLMReplayCache(db_path=db_path) as cache:
        report = compute_cache_coverage(corpus, cache, threshold=0.95)
    assert report.total_rows == 50
    assert report.cache_hits == 0
    assert report.cache_misses == 50
    assert report.coverage_ratio == 0.0
    assert report.passes_threshold is False
    assert len(report.missing_row_ids) == 20


# ---------------------------------------------------------------------------
# migrate_jsonl_to_sqlite
# ---------------------------------------------------------------------------


def test_migrate_jsonl_populates_db(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    records = [
        _sample_record(row_id=f"signal::KX::news_{i}") for i in range(7)
    ]
    _write_jsonl(jsonl_path, records)

    migrated = migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)
    assert migrated == 7
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.count() == 7


def test_migrate_jsonl_refuses_overwrite_without_flag(tmp_path):
    from scripts.edge_replay.llm_cache import migrate_jsonl_to_sqlite

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    records = [_sample_record()]
    _write_jsonl(jsonl_path, records)
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    with pytest.raises(RuntimeError, match="cache already populated"):
        migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)


def test_migrate_jsonl_overwrite_replaces_existing_rows(tmp_path):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"

    first_records = [_sample_record(row_id="signal::KX::first")]
    _write_jsonl(jsonl_path, first_records)
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    second_records = [
        _sample_record(row_id=f"signal::KX::second_{i}") for i in range(3)
    ]
    _write_jsonl(jsonl_path, second_records)
    migrated = migrate_jsonl_to_sqlite(
        jsonl_path=jsonl_path, db_path=db_path, overwrite=True
    )
    assert migrated == 3

    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.count() == 3


def test_migrate_jsonl_skips_malformed_lines_with_warning(tmp_path, caplog):
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    good_record = _sample_record()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(good_record, sort_keys=True) + "\n")
        f.write("{not a json line\n")
        f.write("\n")  # blank line — must be silently skipped
        f.write(json.dumps(good_record, sort_keys=True) + "\n")  # duplicate

    import logging

    with caplog.at_level(logging.WARNING):
        migrated = migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)
    # The two good lines collapse via INSERT OR REPLACE into one row.
    assert migrated == 2  # we count attempted writes, not unique rows
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.count() == 1
    assert any("malformed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# NULL/None field equivalence
# ---------------------------------------------------------------------------


def test_null_model_digest_in_key_matches_null_in_db(tmp_path):
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record(model_digest=None)
    _populate_db(jsonl_path, db_path, [record])

    key = _record_to_cache_key(record)
    assert key.model_digest is None
    with LLMReplayCache(db_path=db_path) as cache:
        hit = cache.get(key)
    assert hit.key.model_digest is None


# ---------------------------------------------------------------------------
# Connection mode invariants
# ---------------------------------------------------------------------------


def test_wal_mode_enabled_on_writer_connection(tmp_path):
    from scripts.edge_replay.llm_cache import migrate_jsonl_to_sqlite

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    _write_jsonl(jsonl_path, [_sample_record()])
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    # After the writer connection closes, the WAL setting persists in the
    # file header. Open a fresh connection and confirm.
    with sqlite3.connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_reader_connection_is_read_only(tmp_path):
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    _populate_db(jsonl_path, db_path, [_sample_record()])

    with LLMReplayCache(db_path=db_path) as cache:
        # Reach into the internal connection to attempt a write. The
        # connection MUST refuse — read-only is a load-bearing invariant
        # so a stray dev never corrupts the capture.
        with pytest.raises(sqlite3.OperationalError):
            cache._conn.execute("DELETE FROM llm_responses")  # noqa: SLF001


# ---------------------------------------------------------------------------
# endpoint_type CHECK constraint
# ---------------------------------------------------------------------------


def test_endpoint_type_check_rejects_invalid_value(tmp_path):
    from scripts.edge_replay.llm_cache import migrate_jsonl_to_sqlite

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    bad_record = _sample_record()
    bad_record["endpoint_type"] = "chat_completions"  # invalid
    _write_jsonl(jsonl_path, [bad_record])

    # The bad row is logged and skipped, not raised — same fail-soft policy
    # as malformed JSON. The migration completes with zero good rows
    # written and returns 0.
    migrated = migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)
    assert migrated == 0

    # Confirm the constraint really is in the schema: a direct INSERT with
    # an invalid endpoint_type raises.
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO llm_responses (
                    row_id, prompt_template_hash, prompt_filled_hash, model_id,
                    model_digest, ollama_version, endpoint_type, seed,
                    temperature, num_ctx, sampler_options_hash,
                    hardware_backend_class, response_hash, response_json,
                    repeat_verification, captured_at_utc, migrated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rid",
                    "pth",
                    "pfh",
                    "qwen3:14b",
                    None,
                    "0.1",
                    "chat_completions",  # invalid
                    None,
                    0.0,
                    8192,
                    "soh",
                    "arm64-Darwin",
                    "rh",
                    "{}",
                    "verified",
                    "2026-05-23T22:01:23Z",
                    "2026-05-23T22:01:24Z",
                ),
            )


# ---------------------------------------------------------------------------
# Security: response is opaque untrusted data
# ---------------------------------------------------------------------------


def test_response_is_loaded_via_json_only(tmp_path):
    """The reader must never eval/exec/pickle the response payload.

    We pin this contract by storing a response that LOOKS like Python code
    or pickle data; if the reader were to evaluate it, the test would crash.
    Loading via json.loads only treats it as opaque data.
    """
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    # A response that contains adversarial-looking strings. json.loads
    # preserves them as plain str values and the reader returns them
    # untouched. Eval / exec / pickle would all break on this content.
    response = {
        "magnitude": "__import__('os').system('echo pwned')",
        "direction": "yes",
    }
    record = _sample_record(response=response)
    _populate_db(jsonl_path, db_path, [record])

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        hit = cache.get(key)
    assert hit.response == response
    # The dangerous-looking string must be preserved as a plain str.
    assert isinstance(hit.response["magnitude"], str)


# ---------------------------------------------------------------------------
# Default-path discovery (no required args)
# ---------------------------------------------------------------------------


def test_default_db_path_resolves_under_repo_root(monkeypatch, tmp_path):
    """``LLMReplayCache()`` with no args points at logs/edge_replay/llm_cache.sqlite."""
    import scripts.edge_replay.llm_cache as mod

    fake_db = tmp_path / "cache.sqlite"
    fake_jsonl = tmp_path / "cap.jsonl"
    _populate_db(fake_jsonl, fake_db, [_sample_record()])

    monkeypatch.setattr(mod, "_DEFAULT_DB_PATH", fake_db)
    with mod.LLMReplayCache() as cache:
        assert cache.count() == 1
