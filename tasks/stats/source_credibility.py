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

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    DATA_DIR,
    CREDIBILITY_MIN_SAMPLE,
    CREDIBILITY_MIN_MULT,
    CREDIBILITY_MAX_MULT,
    CREDIBILITY_HALF_LIFE_DAYS,
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


def record_outcome_in_transaction(
    connection: sqlite3.Connection,
    *,
    source: str,
    was_correct: bool,
    updated_at: str,
) -> None:
    """Update deterministic source statistics without committing the caller's transaction."""
    connection.execute(
        """
        INSERT INTO source_credibility (
            source, wins, losses, total, accuracy, multiplier, last_updated
        ) VALUES (?, ?, ?, 1, ?, 1.0, ?)
        ON CONFLICT(source) DO UPDATE SET
            wins = wins + excluded.wins,
            losses = losses + excluded.losses,
            total = total + 1,
            last_updated = excluded.last_updated
        """,
        (
            source,
            int(was_correct),
            int(not was_correct),
            float(was_correct),
            updated_at,
        ),
    )
    row = connection.execute(
        "SELECT wins, total FROM source_credibility WHERE source=?",
        (source,),
    ).fetchone()
    accuracy = int(row[0]) / int(row[1])
    multiplier = CREDIBILITY_MIN_MULT + accuracy * (
        CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT
    )
    connection.execute(
        """
        UPDATE source_credibility
        SET accuracy=?, multiplier=?
        WHERE source=?
        """,
        (round(accuracy, 4), round(multiplier, 4), source),
    )


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

    def _time_decayed_accuracy(self, source: str) -> tuple[float, int]:
        """
        Compute exponentially time-decayed accuracy for a source.

        Queries resolved paper_trades for this source and weights each
        outcome by exp(-ln2 / half_life * age_days). Recent wins/losses
        count more than old ones, preventing stale data from permanently
        fixing the multiplier.

        Returns (decayed_accuracy, n_resolved).
        """
        rows = self._conn.execute(
            """
            SELECT ts,
                   CASE WHEN (side='yes' AND resolved_yes=1)
                             OR (side='no' AND resolved_yes=0)
                        THEN 1 ELSE 0 END AS was_correct
            FROM paper_trades
            WHERE signal_source = ? AND resolved = 1
            ORDER BY ts ASC
            """,
            (source,),
        ).fetchall()

        n = len(rows)
        if n == 0:
            return 0.5, 0

        now = datetime.now(timezone.utc)
        half_life = CREDIBILITY_HALF_LIFE_DAYS
        w_sum   = 0.0
        w_total = 0.0
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["ts"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                dt_days = max(0.0, (now - ts).total_seconds() / 86400)
            except (ValueError, TypeError):
                dt_days = 0.0
            weight   = math.exp(-math.log(2) / half_life * dt_days)
            w_sum   += weight * r["was_correct"]
            w_total += weight

        accuracy = w_sum / w_total if w_total > 0 else 0.5
        return accuracy, n

    def record_outcome(self, source: str, was_correct: bool) -> None:
        """
        Record the outcome of a resolved trade signal for a given source.

        was_correct=True  -- our signal direction matched the market resolution
        was_correct=False -- our signal was wrong

        Accuracy is computed as a time-decayed weighted average over all
        resolved outcomes (CREDIBILITY_HALF_LIFE_DAYS=30 by default), so
        stale data from months ago decays naturally and does not permanently
        fix the multiplier.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Upsert raw win/loss counts (kept for display in the credibility table)
        self._conn.execute(
            """
            INSERT INTO source_credibility (source, wins, losses, total, accuracy, multiplier, last_updated)
            VALUES (?, ?, ?, 1, ?, 1.0, ?)
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
                now,
            ),
        )
        self._conn.commit()

        # Recompute time-decayed accuracy from paper_trades (committed by caller)
        accuracy, n_resolved = self._time_decayed_accuracy(source)
        multiplier = CREDIBILITY_MIN_MULT + accuracy * (CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT)
        self._conn.execute(
            "UPDATE source_credibility SET accuracy = ?, multiplier = ? WHERE source = ?",
            (round(accuracy, 4), round(multiplier, 4), source),
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT wins, total FROM source_credibility WHERE source = ?", (source,)
        ).fetchone()
        wins_raw = row["wins"] if row else 0
        total    = row["total"] if row else n_resolved

        log.info(
            "Source credibility updated -- %s: %d/%d raw (%.1f%% time-decayed) -> multiplier=%.2fx%s",
            source,
            wins_raw,
            total,
            accuracy * 100,
            multiplier,
            "" if total >= CREDIBILITY_MIN_SAMPLE
            else " (inactive -- need %d more samples)" % (CREDIBILITY_MIN_SAMPLE - total),
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
