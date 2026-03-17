"""
Cross-source headline deduplication.

Reuters, AP, and BBC often publish the same story within minutes of each other.
Without dedup, each copy triggers a separate LLM call (~30-60s CPU) and can
produce duplicate trades on the same market.

Algorithm:
  1. Normalize the headline (lowercase, strip punctuation, sort tokens).
  2. Compare against recent headlines using rapidfuzz token_sort_ratio.
  3. If similarity >= THRESHOLD within TTL window, treat as a duplicate.

Constants:
  THRESHOLD  — 85 (out of 100). Catches paraphrases; strict enough to pass
                genuinely different stories sharing a few keywords.
  TTL        — 15 minutes. After that the headline is evicted and the story
               could trigger again (unlikely but acceptable).
  MAX_CACHE  — 500 entries. Bounds memory regardless of TTL.
"""

import time
from collections import OrderedDict

from rapidfuzz import fuzz

from utils.logger import get_logger

log = get_logger("dedup")

# How similar two normalized headlines must be to be considered duplicates
THRESHOLD: int = 85

# Seconds before a cached headline expires and can match again
TTL: int = 15 * 60   # 15 minutes

# Maximum cached entries (oldest evicted first)
MAX_CACHE: int = 500


def _normalize(headline: str) -> str:
    """Lowercase, strip non-alpha, sort tokens — makes token_sort_ratio work well."""
    import re
    tokens = re.sub(r"[^a-z0-9 ]", " ", headline.lower()).split()
    return " ".join(sorted(set(tokens)))


class HeadlineDedup:
    """
    Thread-safe (GIL) dedup cache for news headlines.

    Usage:
        dedup = HeadlineDedup()
        if dedup.is_duplicate(headline):
            return   # skip
        # ... process headline
    """

    def __init__(
        self,
        threshold: int = THRESHOLD,
        ttl: int = TTL,
        max_cache: int = MAX_CACHE,
    ):
        self._threshold = threshold
        self._ttl       = ttl
        self._max_cache = max_cache
        # OrderedDict[normalized_headline, timestamp] — insertion order = age order
        self._cache: OrderedDict[str, float] = OrderedDict()

    def _evict_expired(self) -> None:
        """Remove entries older than TTL."""
        now = time.monotonic()
        # Walk from oldest (front) — stop at first non-expired
        while self._cache:
            key, ts = next(iter(self._cache.items()))
            if now - ts > self._ttl:
                self._cache.popitem(last=False)
            else:
                break

    def is_duplicate(self, headline: str, source: str = "") -> bool:
        """
        Return True if a similar headline was seen within the TTL window.
        Adds the headline to the cache if it is NOT a duplicate.
        """
        self._evict_expired()

        # Enforce max cache size (evict oldest first)
        while len(self._cache) >= self._max_cache:
            self._cache.popitem(last=False)

        norm = _normalize(headline)
        if not norm:
            return False

        for cached_norm in self._cache:
            score = fuzz.token_sort_ratio(norm, cached_norm)
            if score >= self._threshold:
                log.debug(
                    "Dedup suppressed [%s] score=%d: %s",
                    source, score, headline[:80],
                )
                return True

        # Novel headline — add to cache
        self._cache[norm] = time.monotonic()
        return False
