"""
Source credibility tracker.

Maintains mutable per-source telemetry for diagnostics. Runtime sizing derives
its multiplier only from immutable canonical settlement payloads whose required
local consumers completed delivery. Delivery completion is feedback input, not
an assertion of realized trading profit.

Multiplier formula:
    multiplier = CREDIBILITY_MIN_MULT + accuracy * (CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT)
    e.g. 100% accuracy → 1.5x Kelly | 50% accuracy → 1.0x | 0% → 0.5x

The telemetry table is updated on every market resolution. Runtime reads fail
neutral whenever canonical delivery state is missing or invalid.
"""

import json
import math
import sqlite3
import threading
import time
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
from trading.settlement_store import SettlementStore
from utils.logger import get_logger

log = get_logger("source_credibility")

DB_PATH = DATA_DIR / "paper_trades.db"
CANONICAL_REFRESH_SECS = 60.0

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
            last_updated = CASE
                WHEN source_credibility.last_updated IS NULL
                  OR excluded.last_updated > source_credibility.last_updated
                THEN excluded.last_updated
                ELSE source_credibility.last_updated
            END
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
        self._canonical_lock = threading.RLock()
        self._canonical_outcomes: dict[str, tuple[float, float, int]] | None = None
        self._canonical_loaded_at = float("-inf")

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_multiplier(self, source: str) -> float:
        """
        Return the Kelly multiplier derived from canonical delivered settlements.

        ``source_credibility`` is retained as telemetry. Its mutable aggregate
        values never control runtime sizing. A missing, malformed, or
        non-conserved canonical delivery state fails neutral.
        """
        canonical_outcomes = self._canonical_outcomes_for_source(source)
        if canonical_outcomes is None:
            return 1.0

        weighted_wins, weighted_total, sample_count = canonical_outcomes
        if sample_count < CREDIBILITY_MIN_SAMPLE or weighted_total <= 0.0:
            return 1.0

        accuracy = weighted_wins / weighted_total
        return CREDIBILITY_MIN_MULT + accuracy * (
            CREDIBILITY_MAX_MULT - CREDIBILITY_MIN_MULT
        )

    def _canonical_outcomes_for_source(
        self,
        source: str,
    ) -> tuple[float, float, int] | None:
        """Return cached delivered directional outcomes, or ``None`` when untrusted."""
        self._maybe_refresh_canonical_outcomes()
        with self._canonical_lock:
            if self._canonical_outcomes is None:
                return None
            return self._canonical_outcomes.get(source, (0.0, 0.0, 0))

    def _maybe_refresh_canonical_outcomes(self) -> None:
        """Refresh the full canonical aggregate at a bounded cadence."""
        with self._canonical_lock:
            if time.monotonic() - self._canonical_loaded_at < CANONICAL_REFRESH_SECS:
                return
            self._canonical_outcomes = self._load_canonical_outcomes()
            self._canonical_loaded_at = time.monotonic()

    def _load_canonical_outcomes(self) -> dict[str, tuple[float, float, int]] | None:
        """Rebuild time-decayed source outcomes from canonical delivered payloads."""
        try:
            with SettlementStore(self._db_path, read_only=True) as store:
                events = store.canonical_delivery_complete_outbox_payloads(
                    now=datetime.now(timezone.utc)
                )
        except Exception as exc:
            log.warning(
                "[SOURCE_CREDIBILITY] Canonical delivery unavailable; neutral multiplier: %s",
                exc,
            )
            return None

        aggregate: dict[str, list[float]] = {}
        now = datetime.now(timezone.utc)
        try:
            for event in events:
                payload = json.loads(event.payload_json)
                if (
                    not isinstance(payload, dict)
                    or payload.get("trade_id") != event.trade_id
                ):
                    raise ValueError("canonical outbox payload identity mismatch")

                payload_source = payload.get("signal_source")
                if not isinstance(payload_source, str) or not payload_source:
                    raise ValueError("canonical outbox signal_source is invalid")

                if "won" not in payload:
                    raise ValueError("canonical outbox won flag is missing")
                won = payload["won"]
                if won is None:
                    continue
                if type(won) is not bool:
                    raise ValueError("canonical outbox won flag is invalid")

                settled_at_text = payload.get("settled_at")
                if not isinstance(settled_at_text, str) or not settled_at_text:
                    raise ValueError("canonical outbox settled_at is invalid")
                settled_at = datetime.fromisoformat(settled_at_text)
                if settled_at.tzinfo is None:
                    raise ValueError("canonical outbox settled_at must be timezone-aware")
                if CREDIBILITY_HALF_LIFE_DAYS <= 0:
                    raise ValueError("CREDIBILITY_HALF_LIFE_DAYS must be positive")

                age_days = max(0.0, (now - settled_at).total_seconds() / 86400)
                weight = math.exp(
                    -math.log(2) / CREDIBILITY_HALF_LIFE_DAYS * age_days
                )
                values = aggregate.setdefault(payload_source, [0.0, 0.0, 0.0])
                values[0] += weight * int(won)
                values[1] += weight
                values[2] += 1.0
        except (OverflowError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log.warning(
                "[SOURCE_CREDIBILITY] Canonical delivery malformed; neutral multiplier: %s",
                exc,
            )
            return None

        return {
            source: (values[0], values[1], int(values[2]))
            for source, values in aggregate.items()
        }

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
        resolved outcomes (CREDIBILITY_HALF_LIFE_DAYS=30 by default) for
        telemetry only. ``get_multiplier`` never uses this mutable aggregate.
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
            "Source credibility telemetry updated -- %s: %d/%d raw (%.1f%% time-decayed) -> multiplier=%.2fx%s",
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
