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
    assert hasattr(mod, "CachePoisonedError")
    assert hasattr(mod, "CoverageReport")
    assert hasattr(mod, "ScanReport")
    assert hasattr(mod, "compute_cache_coverage")
    assert hasattr(mod, "migrate_jsonl_to_sqlite")
    assert hasattr(mod, "scan_integrity")


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


# ---------------------------------------------------------------------------
# I-12 helpers
# ---------------------------------------------------------------------------


def _force_poison(db_path: Path, row_id: str, reason: str = "manual") -> None:
    """Mark a row poisoned via a direct writer connection (test-only).

    The reader connection is read-only by design; flipping the poison bit
    in production happens via ``scan_integrity`` or
    ``migrate_jsonl_to_sqlite``. Tests use this helper to set up a
    quarantined state without depending on the scan path.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE llm_responses SET poisoned = 1, poisoned_reason = ? "
            "WHERE row_id = ?",
            (reason, row_id),
        )
        conn.commit()


def _tamper_response_json(db_path: Path, row_id: str, new_payload) -> None:
    """Overwrite ``response_json`` while leaving ``response_hash`` intact.

    Simulates the corruption mode ``scan_integrity`` is built to detect.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE llm_responses SET response_json = ? WHERE row_id = ?",
            (json.dumps(new_payload), row_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# I-12: poison-row mark — is_poisoned, has, get behavior
# ---------------------------------------------------------------------------


def test_is_poisoned_returns_false_on_fresh_row(tmp_path):
    """A row written through the normal migration path is not poisoned."""
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.is_poisoned(key) is False
        # And the row is also gate-eligible.
        assert cache.has(key) is True


def test_is_poisoned_returns_true_after_mark(tmp_path):
    """After ``_force_poison`` flips the bit, ``is_poisoned`` reports True."""
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])

    _force_poison(db_path, record["row_id"], reason="integrity_mismatch")

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.is_poisoned(key) is True


def test_get_raises_cache_poisoned_error_on_poisoned_row(tmp_path):
    """``get`` raises ``CachePoisonedError`` (not corruption, not miss)."""
    from scripts.edge_replay.llm_cache import (
        CacheCorruptionError,
        CacheMissError,
        CachePoisonedError,
        LLMReplayCache,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])
    _force_poison(db_path, record["row_id"], reason="integrity_mismatch")

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        with pytest.raises(CachePoisonedError) as excinfo:
            cache.get(key)
        # Exception type discrimination is load-bearing: operator runbooks
        # branch on which error fired, so the test pins the *type*, not
        # just "any exception".
        assert not isinstance(excinfo.value, CacheCorruptionError)
        assert not isinstance(excinfo.value, CacheMissError)
        # Reason flows through to the exception for forensic clarity.
        assert "integrity_mismatch" in str(excinfo.value)


def test_has_returns_false_on_poisoned_row(tmp_path):
    """``has`` excludes poisoned rows so the gate cannot inadvertently pass.

    This is the structural plug behind ``compute_cache_coverage``: a
    quarantined row must not contribute to coverage. Without this, the
    operator would silently re-promote a known-bad row by re-running the
    gate.
    """
    from scripts.edge_replay.llm_cache import LLMReplayCache

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])
    _force_poison(db_path, record["row_id"], reason="repeat_nondeterministic")

    key = _record_to_cache_key(record)
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.has(key) is False
        # ``count`` still includes the row — it's quarantined, not deleted.
        assert cache.count() == 1


def test_compute_cache_coverage_counts_poisoned_as_miss_with_label(tmp_path):
    """Poisoned rows are misses AND show up labelled in ``missing_row_ids``.

    The label distinguishes "absent capture" from "quarantined capture"
    so the operator can react: a poisoned row is fixable by understanding
    the underlying nondeterminism; an absent row needs the capture
    pipeline re-run.
    """
    from scripts.edge_replay.llm_cache import (
        LLMReplayCache,
        compute_cache_coverage,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    healthy = _sample_record(row_id="signal::KX::healthy")
    poisoned = _sample_record(row_id="signal::KX::poisoned")
    _populate_db(jsonl_path, db_path, [healthy, poisoned])
    _force_poison(db_path, poisoned["row_id"], reason="integrity_mismatch")

    corpus = [healthy, poisoned]
    with LLMReplayCache(db_path=db_path) as cache:
        report = compute_cache_coverage(corpus, cache, threshold=0.95)
    assert report.total_rows == 2
    assert report.cache_hits == 1
    assert report.cache_misses == 1
    assert report.passes_threshold is False
    assert len(report.missing_row_ids) == 1
    assert report.missing_row_ids[0] == "signal::KX::poisoned [poisoned]"


# ---------------------------------------------------------------------------
# I-12: scan_integrity
# ---------------------------------------------------------------------------


def test_scan_integrity_empty_db_reports_zeros(tmp_path):
    """A scan against an empty DB returns the zero-state report."""
    from scripts.edge_replay.llm_cache import (
        ScanReport,
        migrate_jsonl_to_sqlite,
        scan_integrity,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "empty.jsonl"
    _write_jsonl(jsonl_path, [])
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)

    report = scan_integrity(db_path=db_path)
    assert isinstance(report, ScanReport)
    assert report.total_rows == 0
    assert report.integrity_mismatches == 0
    assert report.already_poisoned == 0
    assert report.newly_poisoned == 0
    assert report.repeat_verification_nondeterministic == 0
    assert report.scan_duration_ms >= 0.0


def test_scan_integrity_healthy_db_reports_no_mismatches(tmp_path):
    """A clean DB scan finds nothing to poison."""
    from scripts.edge_replay.llm_cache import scan_integrity

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    records = [
        _sample_record(row_id=f"signal::KX::news_{i}") for i in range(5)
    ]
    _populate_db(jsonl_path, db_path, records)

    report = scan_integrity(db_path=db_path)
    assert report.total_rows == 5
    assert report.integrity_mismatches == 0
    assert report.newly_poisoned == 0
    assert report.already_poisoned == 0
    assert report.repeat_verification_nondeterministic == 0


def test_scan_integrity_marks_tampered_row_poisoned(tmp_path):
    """A tampered row is detected AND durably promoted to poisoned.

    After the scan, ``get`` must raise ``CachePoisonedError`` (not
    ``CacheCorruptionError``) — the durable mark replaces the on-the-fly
    detection on subsequent reads.
    """
    from scripts.edge_replay.llm_cache import (
        CachePoisonedError,
        LLMReplayCache,
        scan_integrity,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    healthy = _sample_record(row_id="signal::KX::healthy")
    tampered = _sample_record(row_id="signal::KX::tampered")
    _populate_db(jsonl_path, db_path, [healthy, tampered])

    _tamper_response_json(db_path, tampered["row_id"], {"prob": 0.99})

    report = scan_integrity(db_path=db_path, mark_poisoned=True)
    assert report.total_rows == 2
    assert report.integrity_mismatches == 1
    assert report.newly_poisoned == 1
    assert report.already_poisoned == 0

    key = _record_to_cache_key(tampered)
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.is_poisoned(key) is True
        with pytest.raises(CachePoisonedError):
            cache.get(key)


def test_scan_integrity_report_only_does_not_write(tmp_path):
    """``mark_poisoned=False`` reports but leaves the DB unchanged."""
    from scripts.edge_replay.llm_cache import (
        CacheCorruptionError,
        LLMReplayCache,
        scan_integrity,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    tampered = _sample_record(row_id="signal::KX::tampered")
    _populate_db(jsonl_path, db_path, [tampered])
    _tamper_response_json(db_path, tampered["row_id"], {"prob": 0.99})

    report = scan_integrity(db_path=db_path, mark_poisoned=False)
    assert report.integrity_mismatches == 1
    assert report.newly_poisoned == 0  # we did NOT write
    assert report.already_poisoned == 0

    # Since nothing was written, ``get`` still raises
    # ``CacheCorruptionError`` (single-read recompute), not poisoned.
    # This pins that ``mark_poisoned=False`` is genuinely a no-op write.
    key = _record_to_cache_key(tampered)
    with LLMReplayCache(db_path=db_path) as cache:
        assert cache.is_poisoned(key) is False
        with pytest.raises(CacheCorruptionError):
            cache.get(key)


def test_scan_integrity_counts_already_poisoned_separately(tmp_path):
    """A pre-poisoned row is reported under ``already_poisoned``, not ``newly_poisoned``."""
    from scripts.edge_replay.llm_cache import scan_integrity

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])
    _force_poison(db_path, record["row_id"], reason="manual")

    report = scan_integrity(db_path=db_path)
    assert report.total_rows == 1
    assert report.already_poisoned == 1
    assert report.newly_poisoned == 0
    assert report.integrity_mismatches == 0


def test_scan_integrity_raises_on_missing_db(tmp_path):
    """Scanning a non-existent DB raises rather than silently creating one."""
    from scripts.edge_replay.llm_cache import scan_integrity

    db_path = tmp_path / "does_not_exist.sqlite"
    with pytest.raises(FileNotFoundError):
        scan_integrity(db_path=db_path)


# ---------------------------------------------------------------------------
# I-12: migrate_jsonl_to_sqlite auto-poison rule
# ---------------------------------------------------------------------------


def test_migrate_jsonl_auto_poisons_repeat_nondeterministic_rows(tmp_path):
    """Per Codex blocker D: nondeterministic rows must be auto-quarantined.

    Migration time is the right place because (a) the operator can't
    forget to scan, and (b) the rows otherwise look healthy to ``has``,
    which would silently promote them past T1 gates.
    """
    from scripts.edge_replay.llm_cache import (
        CachePoisonedError,
        LLMReplayCache,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    clean = _sample_record(
        row_id="signal::KX::clean",
        repeat_verification="verified",
    )
    flaky = _sample_record(
        row_id="signal::KX::flaky",
        repeat_verification="nondeterministic",
    )
    _populate_db(jsonl_path, db_path, [clean, flaky])

    with LLMReplayCache(db_path=db_path) as cache:
        clean_key = _record_to_cache_key(clean)
        flaky_key = _record_to_cache_key(flaky)
        assert cache.is_poisoned(clean_key) is False
        assert cache.is_poisoned(flaky_key) is True
        # The poisoned row does NOT satisfy a gate.
        assert cache.has(flaky_key) is False
        with pytest.raises(CachePoisonedError) as excinfo:
            cache.get(flaky_key)
        assert "repeat_nondeterministic" in str(excinfo.value)


# ---------------------------------------------------------------------------
# I-12: schema-migration idempotency
# ---------------------------------------------------------------------------


def test_alter_table_migration_is_idempotent(tmp_path):
    """Re-opening an already-migrated DB does not raise on the ALTER step.

    SQLite has no ``ADD COLUMN IF NOT EXISTS``, so this would fail if
    ``_ensure_i12_columns`` didn't inspect the table first.
    """
    from scripts.edge_replay.llm_cache import (
        _open_writer,
        migrate_jsonl_to_sqlite,
    )

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    _populate_db(jsonl_path, db_path, [_sample_record()])

    # Re-running _open_writer must not raise on the duplicate ALTER.
    # We do this twice to also cover the cached-state path.
    conn1 = _open_writer(db_path)
    conn1.close()
    conn2 = _open_writer(db_path)
    conn2.close()

    # And the standard migrate path also re-opens — no error.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM llm_responses")
        conn.commit()
    migrate_jsonl_to_sqlite(jsonl_path=jsonl_path, db_path=db_path)


def test_alter_table_migration_promotes_pre_i12_db(tmp_path):
    """A pre-I-12 DB (no poisoned column) is upgraded transparently.

    Simulates the live production cache shipped before I-12: a writer
    re-open must add the columns and leave existing rows clean.
    """
    from scripts.edge_replay.llm_cache import _open_writer

    db_path = tmp_path / "cache.sqlite"
    # Hand-build a pre-I-12 schema: same as production minus the I-12
    # columns and index.
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE llm_responses (
                row_id TEXT NOT NULL,
                prompt_template_hash TEXT NOT NULL,
                prompt_filled_hash TEXT NOT NULL,
                model_id TEXT NOT NULL,
                model_digest TEXT,
                ollama_version TEXT,
                endpoint_type TEXT NOT NULL CHECK (endpoint_type IN ('openai_compat', 'native')),
                seed INTEGER,
                temperature REAL NOT NULL,
                num_ctx INTEGER,
                sampler_options_hash TEXT NOT NULL,
                hardware_backend_class TEXT,
                response_hash TEXT NOT NULL,
                response_json TEXT NOT NULL,
                repeat_verification TEXT NOT NULL CHECK (repeat_verification IN ('verified', 'nondeterministic', 'skipped')),
                captured_at_utc TEXT NOT NULL,
                migrated_at_utc TEXT NOT NULL,
                PRIMARY KEY (row_id, prompt_template_hash, prompt_filled_hash, model_id,
                             endpoint_type, sampler_options_hash, response_hash)
            );
        """)
        conn.execute(
            """
            INSERT INTO llm_responses VALUES (
                'rid', 'pth', 'pfh', 'qwen3:14b', NULL, '0.1', 'openai_compat',
                NULL, 0.0, 8192, 'soh', 'arm64-Darwin', 'rh', '{}',
                'verified', '2026-05-23T22:01:23Z', '2026-05-23T22:01:24Z'
            )
            """
        )
        conn.commit()

    # Pre-I-12 sanity: poisoned column does not exist yet.
    with sqlite3.connect(db_path) as conn:
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(llm_responses)")}
    assert "poisoned" not in cols_before
    assert "poisoned_reason" not in cols_before

    # Trigger the writer-open migration.
    conn = _open_writer(db_path)
    conn.close()

    with sqlite3.connect(db_path) as conn:
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(llm_responses)")}
        # The existing row's poisoned defaulted to 0 (clean) per the
        # ALTER TABLE DEFAULT. This is the load-bearing invariant: a
        # pre-I-12 row is presumed clean until a scan says otherwise.
        poisoned_row = conn.execute(
            "SELECT poisoned, poisoned_reason FROM llm_responses"
        ).fetchone()
    assert "poisoned" in cols_after
    assert "poisoned_reason" in cols_after
    assert poisoned_row == (0, None)


# ---------------------------------------------------------------------------
# I-12: CLI scan exit codes
# ---------------------------------------------------------------------------


def test_cli_scan_exits_zero_on_clean_db(tmp_path, capsys):
    """CLI ``scan`` exits 0 when no integrity mismatches are found."""
    from scripts.edge_replay.llm_cache import main

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    _populate_db(jsonl_path, db_path, [_sample_record()])

    rc = main(["scan", "--db", str(db_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["integrity_mismatches"] == 0
    assert out["newly_poisoned"] == 0
    assert out["mark_poisoned"] is True


def test_cli_scan_exits_one_on_mismatch_regardless_of_flag(tmp_path, capsys):
    """CLI ``scan`` exits 1 on any mismatch, even with ``--report-only``.

    The exit code is the CI signal; writing is the side effect. Operators
    must always know "did this scan see damage?" without parsing JSON.
    """
    from scripts.edge_replay.llm_cache import main

    db_path = tmp_path / "cache.sqlite"
    jsonl_path = tmp_path / "cap.jsonl"
    record = _sample_record()
    _populate_db(jsonl_path, db_path, [record])
    _tamper_response_json(db_path, record["row_id"], {"prob": 0.99})

    # --report-only path: exit 1, nothing written.
    rc = main(["scan", "--db", str(db_path), "--report-only"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["integrity_mismatches"] == 1
    assert out["newly_poisoned"] == 0
    assert out["mark_poisoned"] is False

    # Default (mark-poisoned) path: still exit 1, but now wrote.
    rc = main(["scan", "--db", str(db_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["integrity_mismatches"] == 1
    assert out["newly_poisoned"] == 1
    assert out["mark_poisoned"] is True

    # After the write, the row is durably poisoned — a third run
    # finds 0 integrity_mismatches (we skip the recompute on poisoned
    # rows in the scan) and reports it under already_poisoned, exit 0.
    rc = main(["scan", "--db", str(db_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["integrity_mismatches"] == 0
    assert out["already_poisoned"] == 1
