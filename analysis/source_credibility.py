"""
Source credibility tracker.

Maintains a per-source record of how many paper trade signals were correct
(resolved in the direction we bet) vs. incorrect. After CREDIBILITY_MIN_SAMPLE
resolved trades, the credibility score influences Kelly bet sizing via a
multiplier applied to the kelly_fraction.

Multiplier formula:
    multiplier = CREDIBILITY_MIN_MULT + accuracy * (CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT)
    e.g. 100% accuracy → 1.5x Kelly | 50% accuracy → 1.0x | 0% → 0.5x

The tracker is backed by the same SQLite database as the paper trader.
It is updated on every market resolution and queried on every trade entry.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DATA_DIR,
    CREDIBILITY_MIN_SAMPLE,
    CREDIBILITY_MIN_MULT,
    CREDIBILITY_MAX_MULT,
)
from utils.logger import get_logger

log = get_logger("source_credibility")

DB_PATH = DATA_DIR / "paper_trades.db"

_DDL = """
CREATE TABLE IF NOT EXISTS source_credibility (
    source        TEXT PRIMARY KEY,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    total         INTEGER DEFAULT 0,
    accuracy      REAL    DEFAULT 0.5,
    multiplier    REAL    DEFAULT 1.0,
    last_updated  TEXT
);
"""


class SourceCredibility:
    """
    Tracks and applies per-source signal reliability scores.

    Thread-safe for single-process use (SQLite write-ahead locking).
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_DDL)
        self._conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_multiplier(self, source: str) -> float:
        """
        Return the Kelly multiplier for a source.

        Returns 1.0 (neutral) if the source has fewer than CREDIBILITY_MIN_SAMPLE
        resolved trades — we don't penalise or reward sources with thin data.
        """
        row = self._conn.execute(
            "SELECT total, multiplier FROM source_credibility WHERE source = ?",
            (source,),
        ).fetchone()

        if row is None or row["total"] < CREDIBILITY_MIN_SAMPLE:
            return 1.0

        return float(row["multiplier"])

    def get_all(self) -> list[dict]:
        """Return all credibility records as a list of dicts, sorted by accuracy desc."""
        rows = self._conn.execute(
            "SELECT * FROM source_credibility ORDER BY accuracy DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self, source: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM source_credibility WHERE source = ?", (source,)
        ).fetchone()
        return dict(row) if row else None

    # ── Write ─────────────────────────────────────────────────────────────────

    def record_outcome(self, source: str, was_correct: bool) -> None:
        """
        Record the outcome of a resolved trade signal for a given source.

        was_correct=True  → our signal direction matched the market resolution
        was_correct=False → our signal was wrong
        """
        now = datetime.now(timezone.utc).isoformat()

        # Upsert the source record
        self._conn.execute(
            """
            INSERT INTO source_credibility (source, wins, losses, total, accuracy, multiplier, last_updated)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                wins         = wins + excluded.wins,
                losses       = losses + excluded.losses,
                total        = total + 1,
                last_updated = excluded.last_updated
            """,
            (
                source,
                1 if was_correct else 0,
                0 if was_correct else 1,
                1.0 if was_correct else 0.0,
                1.0,   # placeholder; recalculated below
                now,
            ),
        )
        self._conn.commit()

        # Recalculate accuracy and multiplier from fresh totals
        row = self._conn.execute(
            "SELECT wins, total FROM source_credibility WHERE source = ?", (source,)
        ).fetchone()

        if row and row["total"] > 0:
            accuracy   = row["wins"] / row["total"]
            multiplier = CREDIBILITY_MIN_MULT + accuracy * (CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT)
            self._conn.execute(
                "UPDATE source_credibility SET accuracy = ?, multiplier = ? WHERE source = ?",
                (round(accuracy, 4), round(multiplier, 4), source),
            )
            self._conn.commit()

            log.info(
                "Source credibility updated — %s: %d/%d (%.1f%%) → multiplier=%.2fx%s",
                source,
                row["wins"],
                row["total"],
                accuracy * 100,
                multiplier,
                "" if row["total"] >= CREDIBILITY_MIN_SAMPLE
                else f" (inactive — need {CREDIBILITY_MIN_SAMPLE - row['total']} more samples)",
            )

    # ── Report helper ─────────────────────────────────────────────────────────

    def format_table(self) -> str:
        """Return a formatted credibility table for inclusion in reports."""
        from tabulate import tabulate

        records = self.get_all()
        if not records:
            return "  No source credibility data yet."

        rows = []
        for r in records:
            active = "YES" if r["total"] >= CREDIBILITY_MIN_SAMPLE else f"no ({r['total']}/{CREDIBILITY_MIN_SAMPLE})"
            rows.append([
                r["source"][:30],
                r["wins"],
                r["losses"],
                r["total"],
                f"{r['accuracy']:.1%}",
                f"{r['multiplier']:.2f}x",
                active,
            ])

        return tabulate(
            rows,
            headers=["Source", "Wins", "Losses", "Total", "Accuracy", "Multiplier", "Active?"],
            tablefmt="simple",
        )
