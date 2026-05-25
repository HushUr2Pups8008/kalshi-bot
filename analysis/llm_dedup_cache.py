"""PROFIT-ALIGN-010 (2026-05-25) — runtime LLM-result dedup cache.

The bot polls multiple feeds; the same news event commonly appears at 2-3
sources within minutes (Google News aggregation, RSS overlap). Each (headline,
market) candidate triggers a full LLM analysis call. With local Ollama qwen3,
the cost is wallclock latency (~5-30s per call), not money — but the
duplication saturates the LLM stage and crowds out NEW candidates.

This module provides a tiny in-process cache keyed by the exact prompt text
sent to the LLM. Cache hits return the previously-computed LLM verdict fields
``(confidence, reasoning, direction, magnitude)``; callers recompute final
probability against the current market price.

Distinct from the replay-CI cache at ``scripts/edge_replay/llm_cache.py``
(that one's purpose is deterministic replay; this one is wallclock
deduplication at the runtime path).

Scope guardrails:

  - Key from the full prompt, not only headline/title. Source, body summary,
    market resolution, and close time are prompt inputs and must affect cache
    identity.
  - Do not key on market price. The runtime prompt intentionally excludes
    current price; callers cache verdict fields and recompute final probability
    against the current market quote.
  - TTL governed by ``cfg.llm_dedup_cache_ttl_seconds`` (default 900s = 15
    min). Set to 0 to disable the cache entirely.
  - In-process dict; not persisted across restarts. New cache on each boot.
  - Bounded by ``_MAX_ENTRIES`` (10000); oldest evicted on overflow.

No env mutation, no behavior change beyond skipping the redundant LLM call.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any, Optional

_MAX_ENTRIES: int = 10000

# Module-level cache: key -> (cached_result_tuple, inserted_monotonic_ts).
_cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()


def _content_hash(
    prompt_text: str | None,
) -> str:
    """Stable cache key from the exact LLM prompt text."""
    h = hashlib.sha256()
    h.update((prompt_text or "").strip().encode("utf-8", errors="ignore"))
    return h.hexdigest()


def lookup(
    prompt_text: str | None,
    *,
    ttl_seconds: int,
    now_monotonic: Optional[float] = None,
) -> Optional[Any]:
    """Return cached LLM result if present and within TTL, else None.

    ``ttl_seconds <= 0`` disables the cache (always returns None).
    """
    if ttl_seconds <= 0:
        return None
    if now_monotonic is None:
        now_monotonic = time.monotonic()
    key = _content_hash(prompt_text)
    entry = _cache.get(key)
    if entry is None:
        return None
    result, inserted = entry
    if (now_monotonic - inserted) > ttl_seconds:
        # Expired. Evict so the entry doesn't linger past TTL.
        _cache.pop(key, None)
        return None
    # LRU touch
    _cache.move_to_end(key)
    return result


def store(
    prompt_text: str | None,
    result: Any,
    *,
    ttl_seconds: int,
    now_monotonic: Optional[float] = None,
) -> None:
    """Insert (or refresh) a cache entry. No-op when TTL ≤ 0."""
    if ttl_seconds <= 0:
        return
    if now_monotonic is None:
        now_monotonic = time.monotonic()
    key = _content_hash(prompt_text)
    _cache[key] = (result, now_monotonic)
    _cache.move_to_end(key)
    # Bounded eviction
    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)


def clear() -> None:
    """Test/debug aid: drop all cache entries."""
    _cache.clear()


def stats() -> dict[str, int]:
    """Quick introspection: how many entries currently held."""
    return {"entries": len(_cache), "max_entries": _MAX_ENTRIES}
