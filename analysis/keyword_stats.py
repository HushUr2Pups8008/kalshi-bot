"""
KeywordStats: per-(keyword, series_ticker) accuracy multiplier.

Reads from the keyword_outcomes table (written at every market resolution since
v0.19.0) and provides a get_multiplier(keyword, series_ticker) method that
returns a float in [0.5, 1.5] for use in _keyword_score().

Design:
  - Loaded at startup, refreshed every 6 hours. Thread-safe via RLock.
  - MIN_SAMPLES=10 required for a specific (keyword, series_ticker) pair.
  - Falls back to aggregate accuracy across all series if specific pair
    has fewer than MIN_SAMPLES.
  - Falls back to 1.0 (neutral) if aggregate also has fewer than MIN_SAMPLES.
  - Multiplier is linear: 0% accuracy -> 0.5x, 50% -> 1.0x, 100% -> 1.5x.
    Formula: multiplier = 0.5 + accuracy (clamped to [0.5, 1.5]).

Why per-(keyword, series_ticker)?
  "ceasefire" is predictive for Ukraine/Russia markets but noisy for Middle East.
  Aggregating across all series dilutes signal. keyword_outcomes already captures
  series_ticker so we just need to read it.

Why [0.5, 1.5] range?
  Mirrors source_credibility multiplier range -- consistent mental model.
  Continuous range avoids cliff-edge suppression for borderline keywords.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("keyword_stats")

MIN_SAMPLES  = 10      # below this, return neutral 1.0
REFRESH_SECS = 6 * 3600  # 6 hours between DB refreshes


class KeywordStats:
    """
    Per-(keyword, series_ticker) accuracy multiplier from keyword_outcomes.

    Usage:
        ks = KeywordStats(db_path)
        mult = ks.get_multiplier("ceasefire", "KXUKR")  # -> e.g. 1.3
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock    = threading.RLock()
        # _specific[keyword][series_ticker] = (n, wins)
        self._specific: dict[str, dict[str, tuple[int, int]]] = {}
        # _aggregate[keyword] = (n, wins) across all series
        self._aggregate: dict[str, tuple[int, int]] = {}
        self._last_loaded: float = 0.0
        self._load()

    def _load(self) -> None:
        """Query keyword_outcomes and rebuild the in-memory accuracy tables."""
        try:
            conn = sqlite3.connect(str(self._db_path))
            try:
                rows = conn.execute(
                    """
                    SELECT keyword, series_ticker, count(*) AS n, sum(correct) AS wins
                    FROM keyword_outcomes
                    GROUP BY keyword, series_ticker
                    """
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("[KEYWORD_STATS] Load failed: %s", exc)
            return

        with self._lock:
            self._specific = {}
            agg: dict[str, list[int]] = {}  # keyword -> [n, wins]
            for keyword, series_ticker, n, wins in rows:
                if keyword not in self._specific:
                    self._specific[keyword] = {}
                self._specific[keyword][series_ticker] = (int(n), int(wins))
                if keyword not in agg:
                    agg[keyword] = [0, 0]
                agg[keyword][0] += int(n)
                agg[keyword][1] += int(wins)
            self._aggregate = {k: (v[0], v[1]) for k, v in agg.items()}
            self._last_loaded = time.monotonic()

        pair_count = sum(len(v) for v in self._specific.values())
        log.info(
            "[KEYWORD_STATS] Loaded %d (keyword, series) accuracy pairs from keyword_outcomes",
            pair_count,
        )

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._last_loaded > REFRESH_SECS:
            self._load()

    def get_multiplier(self, keyword: str, series_ticker: str) -> float:
        """
        Return accuracy multiplier in [0.5, 1.5].

        1.0 = neutral (insufficient data or 50% accuracy).
        >1.0 = keyword has been predictive on this series.
        <1.0 = keyword has been anti-predictive (penalised).

        Falls back from (keyword, series_ticker) -> (keyword, all series) -> 1.0.
        """
        self._maybe_refresh()
        with self._lock:
            # Try series-specific first
            series_map = self._specific.get(keyword, {})
            n, wins = series_map.get(series_ticker, (0, 0))
            if n >= MIN_SAMPLES:
                accuracy = wins / n
                return max(0.5, min(1.5, 0.5 + accuracy))

            # Fall back to aggregate across all series
            n_agg, wins_agg = self._aggregate.get(keyword, (0, 0))
            if n_agg >= MIN_SAMPLES:
                accuracy = wins_agg / n_agg
                return max(0.5, min(1.5, 0.5 + accuracy))

        return 1.0  # insufficient data -- neutral

    def refresh(self) -> None:
        """Force an immediate reload (call from scheduler or tests)."""
        self._load()

    def summary(self) -> list[tuple[str, str, int, float, float]]:
        """
        Return sorted list of (keyword, series_ticker, n, accuracy, multiplier)
        for all pairs with >= MIN_SAMPLES. Used by performance_analysis.py.
        """
        self._maybe_refresh()
        rows = []
        with self._lock:
            for keyword, series_map in self._specific.items():
                for series_ticker, (n, wins) in series_map.items():
                    if n >= MIN_SAMPLES:
                        accuracy = wins / n
                        mult = max(0.5, min(1.5, 0.5 + accuracy))
                        rows.append((keyword, series_ticker, n, accuracy, mult))
        rows.sort(key=lambda r: r[2], reverse=True)  # sort by sample count desc
        return rows
