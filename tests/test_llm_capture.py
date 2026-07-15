"""Tests for ``scripts/edge_replay/llm_capture.py`` (I-3).

Reference spec:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §3 row I-3.

The capture hook is ADDITIVE — production behavior must be unchanged on any
capture failure. Each test pins one slice of the contract:

* All 13 cache-key fields land in the JSONL record.
* ``response_hash`` is sha256 of the canonical-JSON serialization.
* Repeat-verification flags ``verified`` and ``nondeterministic`` correctly.
* Fail-soft: capture exceptions never escape.
* Endpoint distinction (``openai_compat`` vs ``native``) is recorded.
* Module-level caches (model_digest, ollama_version, hardware_backend_class)
  are computed once per process.
* Hash computation uses canonical (sorted-key) JSON.
* Caller-supplied ``row_id`` flows through.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Reload the capture module so module-level caches start empty per test."""
    import scripts.edge_replay.llm_capture as mod

    monkeypatch.setattr(mod, "_DEFAULT_CAPTURE_PATH", tmp_path / "llm_capture.jsonl")
    # Clear the lazy caches so each test exercises the cold-cache path.
    mod._reset_caches_for_tests()
    return mod


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _canonical_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Module import test (drives initial creation of the file).
# ---------------------------------------------------------------------------


def test_module_importable_and_exposes_capture_function():
    mod = importlib.import_module("scripts.edge_replay.llm_capture")
    assert callable(getattr(mod, "capture_llm_response"))
    assert callable(getattr(mod, "_reset_caches_for_tests"))


# ---------------------------------------------------------------------------
# All 13 fields present in the JSONL record.
# ---------------------------------------------------------------------------


def test_capture_writes_all_thirteen_cache_key_fields(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    # Inject deterministic static-field values to avoid touching real Ollama.
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "sha256:deadbeef")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "0.1.42")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "arm64-Darwin")

    response = {"prob": 0.42, "direction": "yes"}
    mod.capture_llm_response(
        row_id="signal-1",
        prompt_template="TEMPLATE",
        prompt_filled="TEMPLATE filled with X",
        model_id="qwen3:14b",
        endpoint_type="openai_compat",
        request_payload={"model": "qwen3:14b", "temperature": 0, "seed": 7, "options": {"num_ctx": 8192}},
        response=response,
        repeat_call=None,
    )

    rows = _read_jsonl(tmp_path / "llm_capture.jsonl")
    assert len(rows) == 1
    rec = rows[0]
    required = {
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
    }
    assert required.issubset(rec.keys()), f"missing: {required - rec.keys()}"
    # Plus raw response + repeat_verification + capture timestamp.
    assert "response" in rec
    assert "repeat_verification" in rec
    assert "captured_at_utc" in rec


# ---------------------------------------------------------------------------
# response_hash canonicalization.
# ---------------------------------------------------------------------------


def test_response_hash_is_sha256_of_canonical_json(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "sha256:abc")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "0.0.0")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "x")

    response = {"b": 2, "a": 1, "nested": {"y": 2, "x": 1}}
    mod.capture_llm_response(
        row_id="r1",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response=response,
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["response_hash"] == _canonical_sha256(response)


def test_response_hash_is_order_independent(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    common_kwargs = dict(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
    )
    mod.capture_llm_response(response={"a": 1, "b": 2}, **common_kwargs)
    mod.capture_llm_response(response={"b": 2, "a": 1}, **common_kwargs)
    rows = _read_jsonl(tmp_path / "llm_capture.jsonl")
    assert rows[0]["response_hash"] == rows[1]["response_hash"]


# ---------------------------------------------------------------------------
# Repeat verification.
# ---------------------------------------------------------------------------


def test_repeat_verification_verified_on_identical_responses(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    response = {"prob": 0.5}
    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response=response,
        repeat_call=lambda: response,
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["repeat_verification"] == "verified"


def test_repeat_verification_nondeterministic_on_divergent_responses(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"prob": 0.5},
        repeat_call=lambda: {"prob": 0.6},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["repeat_verification"] == "nondeterministic"
    # Both responses preserved for forensic analysis.
    assert "repeat_response" in rec
    assert rec["repeat_response"] == {"prob": 0.6}
    assert "repeat_response_hash" in rec
    assert rec["repeat_response_hash"] == _canonical_sha256({"prob": 0.6})


def test_repeat_verification_skipped_when_no_callable_provided(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"prob": 0.5},
        repeat_call=None,
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["repeat_verification"] == "skipped"


# ---------------------------------------------------------------------------
# Fail-soft contract.
# ---------------------------------------------------------------------------


def test_capture_failure_does_not_raise(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    # Force a write failure: point capture at a path under a non-writable parent.
    bad_path = tmp_path / "nonexistent" / "subdir" / "file.jsonl"
    monkeypatch.setattr(mod, "_DEFAULT_CAPTURE_PATH", bad_path)
    # Make the directory creation itself fail.
    monkeypatch.setattr(mod, "_ensure_parent_dir", lambda p: (_ for _ in ()).throw(OSError("nope")))

    # Must NOT raise.
    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"prob": 0.5},
    )


def test_capture_failure_logs_via_supplied_logger(monkeypatch, tmp_path, caplog):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: (_ for _ in ()).throw(RuntimeError("boom")))
    logger = logging.getLogger("llm_capture_test_logger")
    logger.setLevel(logging.WARNING)

    with caplog.at_level(logging.WARNING, logger="llm_capture_test_logger"):
        mod.capture_llm_response(
            row_id="r",
            prompt_template="t",
            prompt_filled="t",
            model_id="m",
            endpoint_type="native",
            request_payload={},
            response={"prob": 0.5},
            logger=logger,
        )
    # Some warning was emitted on the supplied logger.
    msgs = [r.getMessage() for r in caplog.records if r.name == "llm_capture_test_logger"]
    assert any("capture" in m.lower() for m in msgs)


def test_capture_failure_does_not_create_partial_record(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: (_ for _ in ()).throw(RuntimeError("boom")))
    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"prob": 0.5},
    )
    # Either the JSONL file does not exist at all (preferred), or it has zero non-empty lines.
    path = tmp_path / "llm_capture.jsonl"
    if path.exists():
        assert _read_jsonl(path) == []


# ---------------------------------------------------------------------------
# Endpoint type recording.
# ---------------------------------------------------------------------------


def test_endpoint_type_openai_compat_recorded(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="openai_compat",
        request_payload={},
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["endpoint_type"] == "openai_compat"


def test_endpoint_type_native_recorded(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={"think": False},
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["endpoint_type"] == "native"


def test_native_payload_think_false_preserved_in_capture(monkeypatch, tmp_path):
    """The capture must NOT mutate the request payload, and when the payload
    is for the native endpoint it must contain ``think: False`` per CLAUDE.md
    PROFIT-GOV-001 / governance/prompts.py:27-31 anchor_rate gotcha."""
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    payload = {"model": "qwen3:14b", "system": "s", "prompt": "p", "format": "json", "think": False,
               "options": {"temperature": 0.0}}
    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="qwen3:14b",
        endpoint_type="native",
        request_payload=payload,
        response={"x": 1},
    )
    # Capture echoes back the exact payload snapshot in `request_payload`.
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["request_payload"]["think"] is False
    # Source payload is unmutated.
    assert payload["think"] is False


# ---------------------------------------------------------------------------
# Per-process caching of static fields.
# ---------------------------------------------------------------------------


def test_static_field_lookups_run_at_most_once_per_process(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    counters = {"digest": 0, "version": 0, "hw": 0}

    def _digest(model_id):
        counters["digest"] += 1
        return "sha256:once"

    def _version():
        counters["version"] += 1
        return "0.1.0"

    def _hw():
        counters["hw"] += 1
        return "arm64-Darwin"

    monkeypatch.setattr(mod, "_lookup_model_digest", _digest)
    monkeypatch.setattr(mod, "_lookup_ollama_version", _version)
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", _hw)

    common = dict(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="qwen3:14b",
        endpoint_type="native",
        request_payload={},
        response={"x": 1},
    )
    mod.capture_llm_response(**common)
    mod.capture_llm_response(**common)
    mod.capture_llm_response(**common)

    # Each static lookup runs exactly once per (model_id) for digest, and at most
    # once per process for the global lookups.
    assert counters["digest"] == 1
    assert counters["version"] == 1
    assert counters["hw"] == 1


def test_model_digest_cache_keyed_per_model(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    counter = {"n": 0}

    def _digest(model_id):
        counter["n"] += 1
        return f"sha256:{model_id}"

    monkeypatch.setattr(mod, "_lookup_model_digest", _digest)
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    for model_id in ("qwen3:14b", "qwen3:14b", "qwen3:8b", "qwen3:8b"):
        mod.capture_llm_response(
            row_id="r",
            prompt_template="t",
            prompt_filled="t",
            model_id=model_id,
            endpoint_type="native",
            request_payload={},
            response={"x": 1},
        )
    # Two distinct models → exactly two digest lookups.
    assert counter["n"] == 2


# ---------------------------------------------------------------------------
# row_id flow-through and prompt-hash semantics.
# ---------------------------------------------------------------------------


def test_row_id_flows_through(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="signal-2026-05-23-KX-001",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["row_id"] == "signal-2026-05-23-KX-001"


def test_prompt_template_and_filled_hashes_differ(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="You are an analyst. {placeholder}",
        prompt_filled="You are an analyst. NEWS_HEADLINE: foo",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    expected_template_hash = hashlib.sha256(
        "You are an analyst. {placeholder}".encode("utf-8")
    ).hexdigest()
    expected_filled_hash = hashlib.sha256(
        "You are an analyst. NEWS_HEADLINE: foo".encode("utf-8")
    ).hexdigest()
    assert rec["prompt_template_hash"] == expected_template_hash
    assert rec["prompt_filled_hash"] == expected_filled_hash
    assert rec["prompt_template_hash"] != rec["prompt_filled_hash"]


# ---------------------------------------------------------------------------
# Sampler options + seed/temperature/num_ctx pulled from request_payload.
# ---------------------------------------------------------------------------


def test_seed_temperature_num_ctx_pulled_from_request_payload(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    payload = {
        "model": "qwen3:14b",
        "temperature": 0.0,
        "seed": 42,
        "options": {"num_ctx": 4096, "top_p": 0.9, "top_k": 40},
    }
    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="qwen3:14b",
        endpoint_type="native",
        request_payload=payload,
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["seed"] == 42
    assert rec["temperature"] == 0.0
    assert rec["num_ctx"] == 4096
    # Sampler options hash covers the rest of the sampler dict (excluding the
    # three fields we already broke out).
    assert isinstance(rec["sampler_options_hash"], str)
    assert len(rec["sampler_options_hash"]) == 64  # sha256 hex


def test_missing_seed_recorded_as_null(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="r",
        prompt_template="t",
        prompt_filled="t",
        model_id="m",
        endpoint_type="openai_compat",
        request_payload={"temperature": 0, "max_tokens": 256},
        response={"x": 1},
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["seed"] is None
    assert rec["temperature"] == 0
    # OpenAI-compat payloads may not have num_ctx; should be null.
    assert rec["num_ctx"] is None


# ---------------------------------------------------------------------------
# Append-only semantics.
# ---------------------------------------------------------------------------


def test_capture_appends_one_line_per_call(monkeypatch, tmp_path):
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    for i in range(5):
        mod.capture_llm_response(
            row_id=f"r{i}",
            prompt_template="t",
            prompt_filled=f"t{i}",
            model_id="m",
            endpoint_type="native",
            request_payload={},
            response={"i": i},
        )
    rows = _read_jsonl(tmp_path / "llm_capture.jsonl")
    assert [r["row_id"] for r in rows] == ["r0", "r1", "r2", "r3", "r4"]


# ---------------------------------------------------------------------------
# Response-type coverage (python-reviewer LOW: bare-string response path).
# ---------------------------------------------------------------------------


def test_capture_handles_string_response(monkeypatch, tmp_path):
    """signal_analyzer passes ``response_text`` (extracted string) as
    ``response``. The capture record must preserve the string verbatim
    and compute ``response_hash`` over its canonical-JSON serialization
    (a quoted JSON string literal). This locks the I-2 reader contract
    for the string-response path that production exercises today."""
    import hashlib
    import json as _json

    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    payload_text = "plain text from LLM"
    mod.capture_llm_response(
        row_id="str-resp",
        prompt_template="t",
        prompt_filled="p",
        model_id="m",
        endpoint_type="openai_compat",
        request_payload={},
        response=payload_text,
    )
    rec = _read_jsonl(tmp_path / "llm_capture.jsonl")[0]
    assert rec["response"] == payload_text
    expected_hash = hashlib.sha256(
        _json.dumps(payload_text, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert rec["response_hash"] == expected_hash


def test_capture_rejects_nan_in_response(monkeypatch, tmp_path):
    """Per security-reviewer Q9: NaN / Infinity in a response must
    fail-fast inside the capture path rather than produce non-standard
    JSON that the I-2 reader (or external JSON.parse) cannot consume.
    The fail-soft outer handler swallows the resulting ValueError so
    production behavior is unchanged."""
    mod = _fresh_module(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_lookup_model_digest", lambda model_id: "d")
    monkeypatch.setattr(mod, "_lookup_ollama_version", lambda: "v")
    monkeypatch.setattr(mod, "_lookup_hardware_backend_class", lambda: "h")

    mod.capture_llm_response(
        row_id="nan-resp",
        prompt_template="t",
        prompt_filled="p",
        model_id="m",
        endpoint_type="native",
        request_payload={},
        response={"bad": float("nan")},
    )
    # The capture should have swallowed the ValueError raised by
    # allow_nan=False; no JSONL line should be written.
    assert not (tmp_path / "llm_capture.jsonl").exists()


# ---------------------------------------------------------------------------
# PROFIT-PHASE3 I-1: shared join key + offline read index
# ---------------------------------------------------------------------------


def test_signal_capture_row_id_format():
    """The signal join key is signal::<ticker>::<news_item_id>. This is the
    single source of truth shared by the capture site and record_trade — its
    format is load-bearing for the capture->trade join."""
    from scripts.edge_replay.llm_capture import signal_capture_row_id

    assert signal_capture_row_id("KXFOO-1", "news-abc") == "signal::KXFOO-1::news-abc"
    # Empty news item id (non-news signals) still yields a stable, parseable key.
    assert signal_capture_row_id("KXFOO-1", "") == "signal::KXFOO-1::"


def test_capture_selector_uses_nearest_pretrade_capture_and_drops_payload(tmp_path: Path):
    """Duplicate row ids require a time-bounded as-of join, never last-write-wins."""
    from scripts.edge_replay.llm_capture import (
        CAPTURE_CACHE_KEY_FIELDS,
        index_captures_by_row_id,
        select_capture_for_trade,
    )

    p = tmp_path / "cap.jsonl"
    lines = [
        json.dumps({"row_id": "k1", "response_hash": "OLD", "model_id": "m",
                    "response": {"x": 1}, "request_payload": {"y": 2},
                    "captured_at_utc": "2026-07-15T04:50:00Z"}),
        "this is not json",  # unparseable -> skipped, must not lose the index
        json.dumps({"row_id": "k1", "response_hash": "NEAREST", "model_id": "m",
                    "captured_at_utc": "2026-07-15T04:59:00Z"}),
        json.dumps({"row_id": "k1", "response_hash": "POST_TRADE", "model_id": "m",
                    "captured_at_utc": "2026-07-15T05:01:00Z"}),
        json.dumps({"response_hash": "noid"}),  # missing row_id -> skipped
        json.dumps({"row_id": "k2", "prompt_template_hash": "tpl",
                    "captured_at_utc": "2026-07-15T04:58:00Z"}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    idx = index_captures_by_row_id(p)
    selected = select_capture_for_trade(
        idx,
        row_id="k1",
        trade_ts=datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc),
        max_age=timedelta(minutes=15),
    )

    assert set(idx.keys()) == {"k1", "k2"}
    assert len(idx["k1"]) == 3
    assert selected is not None
    assert selected["response_hash"] == "NEAREST"
    # Only the 13 cache-key fields are retained (missing ones present as None).
    assert set(selected.keys()) == set(CAPTURE_CACHE_KEY_FIELDS)
    assert "response" not in selected
    assert "request_payload" not in selected


def test_capture_selector_leaves_posttrade_stale_and_ambiguous_uncovered(tmp_path: Path):
    from scripts.edge_replay.llm_capture import (
        index_captures_by_row_id,
        select_capture_for_trade,
    )

    p = tmp_path / "cap.jsonl"
    records = [
        {"row_id": "post", "response_hash": "p", "captured_at_utc": "2026-07-15T05:01:00Z"},
        {"row_id": "stale", "response_hash": "s", "captured_at_utc": "2026-07-15T04:00:00Z"},
        {"row_id": "amb", "response_hash": "a1", "captured_at_utc": "2026-07-15T04:59:00Z"},
        {"row_id": "amb", "response_hash": "a2", "captured_at_utc": "2026-07-15T04:59:00Z"},
    ]
    p.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    idx = index_captures_by_row_id(p)
    trade_ts = datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)

    for row_id in ("post", "stale", "amb"):
        assert select_capture_for_trade(
            idx,
            row_id=row_id,
            trade_ts=trade_ts,
            max_age=timedelta(minutes=15),
        ) is None


def test_index_captures_by_row_id_missing_file_returns_empty(tmp_path: Path):
    """Honest absence: no capture file -> empty index (gate counts those rows as
    cache-uncovered rather than fabricating coverage)."""
    from scripts.edge_replay.llm_capture import index_captures_by_row_id

    assert index_captures_by_row_id(tmp_path / "does-not-exist.jsonl") == {}


def test_capture_cache_key_fields_match_gate_cache_columns():
    """Drift guard: the 13-field key the corpus builder stamps
    (CAPTURE_CACHE_KEY_FIELDS) MUST equal the key the replay gate looks up
    (llm_cache._CACHE_KEY_COLUMNS), in the same order. If they drift, the gate's
    cache-coverage check silently misses every stamped row -> a real corpus
    would falsely read as 0% covered. These are hand-maintained duplicates in
    two modules; this test is the only thing pinning them together."""
    from scripts.edge_replay.llm_capture import CAPTURE_CACHE_KEY_FIELDS
    from scripts.edge_replay.llm_cache import _CACHE_KEY_COLUMNS

    assert tuple(CAPTURE_CACHE_KEY_FIELDS) == tuple(_CACHE_KEY_COLUMNS)
