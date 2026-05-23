"""I-3 LLM capture hook with extended 13-field cache key (rapid-learning v3).

Reference design:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §3 row I-3 (and Codex blocker D — repeat-verification).

Purpose
-------

Every LLM response observed in production (signal_analyzer's OpenAI-compat
estimator and governance/llm's native qwen3 generator) is captured into an
append-only JSONL store at ``logs/edge_replay/llm_capture.jsonl``. Each row
records the 13-field cache-identity tuple:

    (row_id, prompt_template_hash, prompt_filled_hash, model_id, model_digest,
     ollama_version, endpoint_type, seed, temperature, num_ctx,
     sampler_options_hash, hardware_backend_class, response_hash)

plus the raw response, a snapshot of the request payload, a
``repeat_verification`` flag, and a UTC capture timestamp.

Design constraints
------------------

* **Pure stdlib.** No third-party deps.
* **Additive only.** Capture must never affect production behavior — any
  exception inside capture is logged and swallowed.
* **Read-only with respect to inputs.** The request_payload snapshot passes
  through unchanged; the capture never mutates caller state. In particular,
  the ``think: False`` field on native-endpoint payloads is preserved
  verbatim (PROFIT-GOV-001 / governance/prompts.py:27-31 polarity gotcha).
* **Repeat-verification (Codex blocker D).** If a ``repeat_call`` callable is
  supplied, the capture invokes it once after recording the original
  response, compares canonical-JSON hashes, and tags the row as
  ``verified`` or ``nondeterministic``. The repeat call's exception is
  caught and treated as ``nondeterministic`` (preserves operator visibility
  into intermittent backend failures).

I-2 (next Phase 1 deliverable) will migrate this JSONL store to SQLite and
add the read-path. I-12 will use ``response_hash`` for cache integrity
checks. The 13-field schema is the durable contract; only the storage
backend is provisional.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Literal

__all__ = ["capture_llm_response"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sampler keys we promote to first-class cache-key columns. The remainder of
# the sampler options dict is hashed into ``sampler_options_hash`` so that any
# silent sampler drift still busts the cache without bloating the schema.
_FIRST_CLASS_SAMPLER_KEYS: Final[frozenset[str]] = frozenset(
    {"seed", "temperature", "num_ctx"}
)

# Resolve the default capture path relative to the repo root rather than the
# process working directory. The repo root is the parent of the
# ``scripts`` directory that holds this module.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_DEFAULT_CAPTURE_PATH: Path = _REPO_ROOT / "logs" / "edge_replay" / "llm_capture.jsonl"


# ---------------------------------------------------------------------------
# Module-level caches for static fields
# ---------------------------------------------------------------------------

_cache_lock: Final[threading.Lock] = threading.Lock()
_model_digest_cache: dict[str, str | None] = {}
_ollama_version_cache: dict[str, str | None] = {}  # single key "_": cached value
_hardware_backend_cache: dict[str, str | None] = {}


def _reset_caches_for_tests() -> None:
    """Clear the lazy module-level caches. Test-only entrypoint."""
    with _cache_lock:
        _model_digest_cache.clear()
        _ollama_version_cache.clear()
        _hardware_backend_cache.clear()


# ---------------------------------------------------------------------------
# Static-field lookups (each is overridable from tests via monkeypatch)
# ---------------------------------------------------------------------------


def _lookup_model_digest(model_id: str) -> str | None:
    """Fetch the Ollama-reported model digest. Returns ``None`` on any error.

    Hits ``POST /api/show`` on the Ollama base URL declared via env var
    ``OLLAMA_BASE_URL`` (falls back to ``http://localhost:11434``). The digest
    is part of the cache identity: if the underlying weights change but the
    tag does not, the cache must still treat the rows as distinct.
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    payload = json.dumps({"name": model_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/show",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        digest = data.get("digest") or data.get("details", {}).get("digest")
        return str(digest) if digest else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _lookup_ollama_version() -> str | None:
    """Resolve the running Ollama version. Returns ``None`` on any error.

    Prefers the HTTP ``/api/version`` endpoint over the ``ollama --version``
    CLI because the CLI may be absent on hosts that talk to a remote Ollama.
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/version", timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        version = data.get("version")
        if version:
            return str(version)
    except (urllib.error.URLError, OSError, ValueError):
        pass
    # Fallback: try the CLI.
    try:
        out = subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        text = (out.stdout or out.stderr or "").strip()
        return text or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _lookup_hardware_backend_class() -> str | None:
    """Coarse machine-class fingerprint, e.g. ``"arm64-Darwin"``.

    Captured because Ollama GPU vs CPU vs alternate-Metal-shader backends
    can produce divergent tokens even at temperature=0+seed (the
    multi-slot non-determinism Codex blocker D was filed to defend against).
    """
    try:
        machine = platform.machine() or "unknown"
        system = platform.system() or "unknown"
        return f"{machine}-{system}"
    except Exception:  # pragma: no cover — platform.* should not raise
        return None


def _get_model_digest(model_id: str) -> str | None:
    with _cache_lock:
        if model_id in _model_digest_cache:
            return _model_digest_cache[model_id]
    value = _lookup_model_digest(model_id)
    with _cache_lock:
        _model_digest_cache[model_id] = value
    return value


def _get_ollama_version() -> str | None:
    with _cache_lock:
        if "_" in _ollama_version_cache:
            return _ollama_version_cache["_"]
    value = _lookup_ollama_version()
    with _cache_lock:
        _ollama_version_cache["_"] = value
    return value


def _get_hardware_backend_class() -> str | None:
    with _cache_lock:
        if "_" in _hardware_backend_cache:
            return _hardware_backend_cache["_"]
    value = _lookup_hardware_backend_class()
    with _cache_lock:
        _hardware_backend_cache["_"] = value
    return value


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Order-stable, separator-stable JSON for content-addressing.

    ``allow_nan=False`` ensures NaN / Infinity in a captured response raise
    immediately rather than producing non-standard JSON tokens that strict
    parsers (downstream I-2 reader, JavaScript JSON.parse, Go encoding/json)
    cannot round-trip. Fail-fast is safer than silent corruption per
    Codex / security-reviewer Q9.
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


def _extract_sampler_fields(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Pull seed/temperature/num_ctx + remaining sampler options.

    Ollama's native ``/api/generate`` puts samplers under ``options``; the
    OpenAI-compat ``/v1/chat/completions`` endpoint puts them at the top
    level. We look in both places so a single helper works for both
    endpoints — the capture is endpoint-agnostic at this layer.
    """
    seed: Any = request_payload.get("seed")
    temperature: Any = request_payload.get("temperature")
    num_ctx: Any = request_payload.get("num_ctx")

    options = request_payload.get("options")
    if isinstance(options, dict):
        if seed is None:
            seed = options.get("seed")
        if temperature is None:
            temperature = options.get("temperature")
        if num_ctx is None:
            num_ctx = options.get("num_ctx")
        # Remaining sampler options = everything in `options` except the three
        # we already broke out.
        remaining: dict[str, Any] = {
            k: v for k, v in options.items() if k not in _FIRST_CLASS_SAMPLER_KEYS
        }
    else:
        remaining = {}
    return {
        "seed": seed,
        "temperature": temperature,
        "num_ctx": num_ctx,
        "sampler_options_hash": _hash_canonical(remaining),
    }


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSONL record to ``path``.

    Single-writer assumption: POSIX guarantees atomic writes only for
    payloads under ``PIPE_BUF`` (4096 bytes on Linux) on pipes, NOT on
    regular files. A large captured response may exceed this, so two
    concurrent multi-process writers can interleave partial lines and
    produce corrupt JSON. kalshi-bot is single-process by deployment
    (one launchd job ``com.jake.kalshi-bot``), so this is acceptable
    for Phase 1. The I-2 SQLite migration with WAL mode fixes this
    structurally and is the correct fix for any multi-writer future.
    """
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def capture_llm_response(
    *,
    row_id: str,
    prompt_template: str,
    prompt_filled: str,
    model_id: str,
    endpoint_type: Literal["openai_compat", "native"],
    request_payload: dict[str, Any],
    response: Any,
    repeat_call: Callable[[], Any] | None = None,
    capture_log_path: Path | str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Record one LLM response into the capture log.

    Fail-soft: ANY exception during capture (including the repeat call)
    is caught and logged. The function never raises.

    Parameters
    ----------
    row_id:
        Caller-provided identifier for the row (e.g. signal_id, cycle_id +
        prompt_idx). Flows through verbatim to the record.
    prompt_template:
        The static prompt template text BEFORE substitution.
    prompt_filled:
        The full prompt text AFTER substitution, exactly as sent to the LLM.
    model_id:
        The ``model`` field as sent to Ollama (e.g. ``"qwen3:14b"``).
    endpoint_type:
        One of ``"openai_compat"`` or ``"native"``. The signal_analyzer
        OpenAI-compat path uses ``"openai_compat"``; the governance native
        ``/api/generate`` path uses ``"native"``. Recorded verbatim — capture
        does NOT validate the endpoint string so future endpoints can be
        added without code changes here.
    request_payload:
        Snapshot of the request body. ``seed`` / ``temperature`` / ``num_ctx``
        are extracted into first-class columns; everything else under
        ``options`` is hashed into ``sampler_options_hash``.
    response:
        The full LLM response object (already JSON-loaded). Canonical-JSON
        hashed into ``response_hash`` and recorded verbatim in ``response``.
    repeat_call:
        Optional zero-arg callable that re-runs the same LLM request and
        returns the response. When supplied, capture compares the two
        canonical hashes and writes ``repeat_verification`` accordingly.
    capture_log_path:
        Override for the JSONL log path (default:
        ``logs/edge_replay/llm_capture.jsonl`` under the repo root).
    logger:
        Optional logger for fail-soft warnings. Defaults to the module
        logger ``scripts.edge_replay.llm_capture``.
    """
    eff_logger = logger or logging.getLogger(__name__)
    try:
        # ------------------------------------------------------------------
        # Static identity fields. Each lookup is cached; uncached lookups
        # tolerate missing endpoints (returns None).
        # ------------------------------------------------------------------
        model_digest = _get_model_digest(model_id)
        ollama_version = _get_ollama_version()
        hardware_backend_class = _get_hardware_backend_class()

        # ------------------------------------------------------------------
        # Sampler + prompt hashes.
        # ------------------------------------------------------------------
        sampler_fields = _extract_sampler_fields(request_payload)
        prompt_template_hash = _sha256_hex(prompt_template)
        prompt_filled_hash = _sha256_hex(prompt_filled)
        response_hash = _hash_canonical(response)

        # ------------------------------------------------------------------
        # Repeat-verification.
        #
        # We run the repeat call AFTER computing the primary hash so a slow
        # second call does not delay the primary hashing path. The repeat
        # call is treated as a probe: any exception (network, timeout,
        # malformed JSON) tags the row as nondeterministic — better
        # signal than silently dropping verification.
        # ------------------------------------------------------------------
        if repeat_call is None:
            repeat_verification = "skipped"
            repeat_response: Any | None = None
            repeat_response_hash: str | None = None
        else:
            try:
                repeat_response = repeat_call()
                repeat_response_hash = _hash_canonical(repeat_response)
                if repeat_response_hash == response_hash:
                    repeat_verification = "verified"
                else:
                    repeat_verification = "nondeterministic"
            except Exception as exc:  # noqa: BLE001 — repeat-call failure is signal
                eff_logger.warning(
                    "I-3 llm_capture: repeat_call raised %s; marking nondeterministic",
                    exc.__class__.__name__,
                )
                repeat_verification = "nondeterministic"
                repeat_response = None
                repeat_response_hash = None

        # ------------------------------------------------------------------
        # Assemble the record. Order is irrelevant on disk (sorted by
        # _append_jsonl) but kept readable here.
        # ------------------------------------------------------------------
        captured_at_utc = datetime.now(timezone.utc).isoformat()
        record: dict[str, Any] = {
            "row_id": row_id,
            "prompt_template_hash": prompt_template_hash,
            "prompt_filled_hash": prompt_filled_hash,
            "model_id": model_id,
            "model_digest": model_digest,
            "ollama_version": ollama_version,
            "endpoint_type": endpoint_type,
            "seed": sampler_fields["seed"],
            "temperature": sampler_fields["temperature"],
            "num_ctx": sampler_fields["num_ctx"],
            "sampler_options_hash": sampler_fields["sampler_options_hash"],
            "hardware_backend_class": hardware_backend_class,
            "response_hash": response_hash,
            "response": response,
            "request_payload": request_payload,
            "repeat_verification": repeat_verification,
            "captured_at_utc": captured_at_utc,
        }
        if repeat_verification == "nondeterministic" and repeat_response is not None:
            record["repeat_response"] = repeat_response
            record["repeat_response_hash"] = repeat_response_hash

        # ------------------------------------------------------------------
        # Write. mkdir failure must not propagate.
        # ------------------------------------------------------------------
        target = Path(capture_log_path) if capture_log_path is not None else _DEFAULT_CAPTURE_PATH
        _ensure_parent_dir(target)
        _append_jsonl(target, record)

    except Exception as exc:  # noqa: BLE001 — fail-soft contract
        # Best-effort log; never re-raise. Capture is additive only.
        try:
            eff_logger.warning(
                "I-3 llm_capture: write skipped due to %s: %s",
                exc.__class__.__name__,
                exc,
            )
        except Exception:  # noqa: BLE001 — even logging must not raise
            pass
        return
