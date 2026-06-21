"""
Source quality tracker.

Tracks posts_seen, signals, opportunities, and trades per source.
Uses signal_rate (signals / posts_seen) as a fast-feedback quality metric
that is actionable within 24-48 hours -- unlike win_rate (source_credibility)
which requires 10+ resolved trades per source (weeks to months).

Quality gates (configurable via env vars):
  - is_suppressed  : posts_seen >= ZERO_SIGNAL_POSTS and signals == 0
  - is_low_quality : posts_seen >= MIN_POSTS_TO_JUDGE and rate < LOW_SIGNAL_RATE

Core subreddits (REDDIT_CORE_SUBREDDITS) are always exempt from suppression.

Backed by the same SQLite DB as the paper trader. Writes are batched in memory
and flushed every FLUSH_INTERVAL seconds to avoid write contention (RSS runs
~100 posts/min; per-write SQLite calls would cause lock contention).

Source key format: whatever string news.source carries, e.g. "r/worldnews",
"Reuters", "GDELT". Consumers responsible for consistent naming.
"""

import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DATA_DIR,
    REDDIT_CORE_SUBREDDITS,
    SOURCE_STATS_MIN_POSTS,
    SOURCE_STATS_LOW_SIGNAL_RATE,
    SOURCE_STATS_ZERO_SIGNAL_POSTS,
)
from utils.logger import get_logger

log = get_logger("source_stats")

DB_PATH = DATA_DIR / "paper_trades.db"

# Seconds between DB flushes.  Same rhythm as the RSS poll interval so we
# never accumulate more than ~100 posts worth of un-flushed increments.
FLUSH_INTERVAL = 60

_DDL = """
CREATE TABLE IF NOT EXISTS source_stats (
    source        TEXT PRIMARY KEY,
    posts_seen    INTEGER DEFAULT 0,
    signals       INTEGER DEFAULT 0,
    opportunities INTEGER DEFAULT 0,
    trades        INTEGER DEFAULT 0,
    last_signal   TEXT,
    last_updated  TEXT
);
"""

# Set of bare subreddit names (no "r/" prefix) that are always polled.
# Sources matching "r/<name>" where <name> is in this set are never suppressed.
_CORE_BARE: frozenset = frozenset(REDDIT_CORE_SUBREDDITS)


def _is_core(source: str) -> bool:
    """Return True if source is a core subreddit (always exempt from suppression)."""
    if source.startswith("r/"):
        return source[2:] in _CORE_BARE
    return source in _CORE_BARE


class SourceStats:
    """
    Tracks per-source signal quality for adaptive subreddit selection.

    In-memory counters are accumulated and flushed to SQLite every
    FLUSH_INTERVAL seconds.  Core subreddits are always exempt from suppression.

    Thread-safe via a lock on the in-memory pending dict.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn    = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_DDL)
        self._conn.commit()

        # In-memory pending increments: source -> field -> delta
        self._pending: dict[str, dict[str, int]] = defaultdict(
            lambda: {"posts_seen": 0, "signals": 0, "opportunities": 0, "trades": 0}
        )
        # Cache of DB state (refreshed on every flush)
        self._db_cache: dict[str, dict] = {}
        self._load_from_db()
        self._lock = threading.Lock()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        rows = self._conn.execute("SELECT * FROM source_stats").fetchall()
        self._db_cache = {r["source"]: dict(r) for r in rows}

    def _get_totals(self, source: str) -> dict:
        """Combine DB state with pending in-memory increments for a source."""
        db = self._db_cache.get(source, {
            "posts_seen": 0, "signals": 0, "opportunities": 0, "trades": 0,
        })
        p = self._pending.get(source, {})
        return {
            "posts_seen":    db.get("posts_seen",    0) + p.get("posts_seen",    0),
            "signals":       db.get("signals",       0) + p.get("signals",       0),
            "opportunities": db.get("opportunities", 0) + p.get("opportunities", 0),
            "trades":        db.get("trades",        0) + p.get("trades",        0),
        }

    # ── Increment API (call from main.py) ─────────────────────────────────────

    def increment_posts(self, source: str) -> None:
        """Call on every non-duplicate news item after dedup."""
        with self._lock:
            self._pending[source]["posts_seen"] += 1

    def increment_signals(self, source: str) -> None:
        """Call when at least one keyword fires for a news item / market pair."""
        with self._lock:
            self._pending[source]["signals"] += 1

    def increment_opportunities(self, source: str) -> None:
        """Call when an opportunity is logged (market match with positive edge)."""
        with self._lock:
            self._pending[source]["opportunities"] += 1

    def increment_trades(self, source: str) -> None:
        """Call when executor returns a trade_id (trade was placed)."""
        with self._lock:
            self._pending[source]["trades"] += 1

    # ── Quality gates (used by subreddit_selector) ────────────────────────────

    def is_suppressed(self, source: str) -> bool:
        """
        True if source has many posts but zero signals -- provably low quality.

        Threshold: SOURCE_STATS_ZERO_SIGNAL_POSTS posts with 0 signals.
        Core subreddits are always exempt.
        """
        if _is_core(source):
            return False
        t = self._get_totals(source)
        return t["posts_seen"] >= SOURCE_STATS_ZERO_SIGNAL_POSTS and t["signals"] == 0

    def is_low_quality(self, source: str) -> bool:
        """
        True if source has enough data to judge but signal rate is very low.

        Threshold: SOURCE_STATS_MIN_POSTS posts with rate < SOURCE_STATS_LOW_SIGNAL_RATE.
        Core subreddits are always exempt.
        """
        if _is_core(source):
            return False
        t = self._get_totals(source)
        if t["posts_seen"] < SOURCE_STATS_MIN_POSTS:
            return False
        return (t["signals"] / t["posts_seen"]) < SOURCE_STATS_LOW_SIGNAL_RATE

    def get_signal_rate(self, source: str) -> Optional[float]:
        """
        Return signal rate (signals / posts_seen), or None if fewer than
        SOURCE_STATS_MIN_POSTS posts have been seen (insufficient data to judge).
        """
        t = self._get_totals(source)
        if t["posts_seen"] < SOURCE_STATS_MIN_POSTS:
            return None
        return t["signals"] / t["posts_seen"]

    def ranking_score(self, source: str) -> float:
        """
        Score for sorting subreddits: higher is better.

        Returns the signal rate when enough data is available (>= MIN_POSTS),
        otherwise returns 1.0 (neutral -- sort new sources in original order).
        This ensures unknown subreddits are tried before known-garbage ones.
        """
        rate = self.get_signal_rate(source)
        return rate if rate is not None else 1.0

    # ── Persistence ───────────────────────────────────────────────────────────

    def flush(self) -> None:
        """
        Write pending in-memory increments to DB.

        Called periodically (piggybacked on _auto_resolve_task every 30 min).
        After flush the DB cache is refreshed so quality-gate reads reflect
        the latest data.
        """
        with self._lock:
            if not self._pending:
                return
            # Only flush sources with non-zero deltas
            to_flush = {
                s: dict(d) for s, d in self._pending.items()
                if any(d.values())
            }
            self._pending.clear()

        if not to_flush:
            return

        now = datetime.now(timezone.utc).isoformat()
        for source, deltas in to_flush.items():
            self._conn.execute(
                """
                INSERT INTO source_stats
                    (source, posts_seen, signals, opportunities, trades, last_signal, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    posts_seen    = posts_seen    + excluded.posts_seen,
                    signals       = signals       + excluded.signals,
                    opportunities = opportunities + excluded.opportunities,
                    trades        = trades        + excluded.trades,
                    last_signal   = CASE WHEN excluded.signals > 0
                                         THEN excluded.last_updated
                                         ELSE last_signal END,
                    last_updated  = excluded.last_updated
                """,
                (
                    source,
                    deltas.get("posts_seen",    0),
                    deltas.get("signals",       0),
                    deltas.get("opportunities", 0),
                    deltas.get("trades",        0),
                    now,   # last_signal placeholder -- overridden by CASE above
                    now,
                ),
            )
        self._conn.commit()
        self._load_from_db()
        log.debug("Source stats flushed: %d sources updated", len(to_flush))

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_all(self) -> list[dict]:
        """
        Return all source records (DB + pending) as a list of dicts, sorted by
        signal rate descending.  Used by performance_analysis.py.
        """
        all_sources = set(self._db_cache.keys()) | set(self._pending.keys())
        rows = []
        for source in all_sources:
            t = self._get_totals(source)
            t["source"] = source
            t["signal_rate"] = (
                t["signals"] / t["posts_seen"] if t["posts_seen"] > 0 else 0.0
            )
            suppressed  = self.is_suppressed(source)
            low_quality = self.is_low_quality(source)
            if suppressed:
                t["quality"] = "SUPPRESSED (>%d posts, 0 signals)" % SOURCE_STATS_ZERO_SIGNAL_POSTS
            elif low_quality:
                t["quality"] = "Low quality"
            elif t["posts_seen"] < SOURCE_STATS_MIN_POSTS:
                t["quality"] = "Insufficient data"
            else:
                t["quality"] = "Good"
            rows.append(t)
        return sorted(rows, key=lambda r: -r["signal_rate"])


def read_lifetime_totals(db_path: Path = DB_PATH) -> dict[str, dict]:
    """Read-only snapshot of the lifetime per-source funnel from source_stats.

    Returns ``{source: {posts_seen, signals, opportunities, trades, last_signal}}``
    covering the source's ENTIRE recorded history (not a window). ``last_signal``
    is the ISO-UTC timestamp of the source's most recent SIGNAL (or ``None``);
    the scorecard uses it to recency-bound the lifetime-yield veto so a source
    that fired once and went dead is not granted permanent immunity from a
    "remove" recommendation.

    Fail-soft: returns ``{}`` when the DB file or the source_stats table is
    absent (a fresh clone / partial fixture) or when the file is transiently
    locked by the bot's SourceStats writer. Opens its own short-lived read
    connection with a busy_timeout -- never writes, never holds the SourceStats
    write connection, safe to call from a reporting process while the bot runs.
    """
    if not Path(db_path).exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        try:
            # fetchall() is inside the try so a 'database is locked' raised during
            # the fetch (not just the execute) also fails soft to {}.
            rows = conn.execute(
                "SELECT source, posts_seen, signals, opportunities, trades, last_signal "
                "FROM source_stats"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {
            row[0]: {
                "posts_seen": row[1] or 0,
                "signals": row[2] or 0,
                "opportunities": row[3] or 0,
                "trades": row[4] or 0,
                "last_signal": row[5],
            }
            for row in rows
        }
    finally:
        conn.close()
