"""
KeywordStats: per-(keyword, series_ticker) accuracy multiplier.

``keyword_outcomes`` remains mutable telemetry written at market resolution.
Runtime multipliers are derived only from immutable canonical settlement payloads
whose required local consumers completed delivery. Delivery completion is not an
assertion of realized trading profit.

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

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from analysis.feedback_counterfactual import (
    FEEDBACK_ALGORITHM_VERSION,
    FeedbackMultiplierReceipt,
    canonical_delivery_basis_sha256,
)
from trading.settlement_store import SettlementStore
from utils.logger import get_logger

# Use the project's logger factory so messages reach bot.log + errors.log.
# Raw `logging.getLogger(...)` returns a logger with no handlers, and the
# root logger has no handlers either, so its records are silently dropped.
log = get_logger("keyword_stats")

MIN_SAMPLES  = 10      # below this, return neutral 1.0
REFRESH_SECS = 6 * 3600  # 6 hours between DB refreshes


class KeywordStats:
    """
    Per-(keyword, series_ticker) accuracy multiplier from delivered settlements.

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
        self._canonical_basis_sha256: str | None = None
        self._canonical_event_count = 0
        self._canonical_as_of: str | None = None
        self._load()

    def _load(self) -> None:
        """Rebuild runtime accuracy tables from immutable delivered payloads."""
        specific: dict[str, dict[str, tuple[int, int]]] = {}
        aggregate: dict[str, list[int]] = {}
        try:
            now = datetime.now(timezone.utc)
            with SettlementStore(self._db_path, read_only=True) as store:
                events = tuple(store.canonical_delivery_complete_outbox_payloads(now=now))

            for event in events:
                payload = json.loads(event.payload_json)
                if (
                    not isinstance(payload, dict)
                    or payload.get("trade_id") != event.trade_id
                ):
                    raise ValueError("canonical outbox payload identity mismatch")

                series_ticker = payload.get("series_ticker")
                if not isinstance(series_ticker, str) or not series_ticker:
                    raise ValueError("canonical outbox series_ticker is invalid")

                keyword_outcomes = payload.get("keyword_outcomes")
                if not isinstance(keyword_outcomes, list):
                    raise ValueError("canonical outbox keyword_outcomes is invalid")

                for outcome in keyword_outcomes:
                    if not isinstance(outcome, dict):
                        raise ValueError("canonical keyword outcome is invalid")
                    keyword = outcome.get("keyword")
                    correct = outcome.get("correct")
                    if not isinstance(keyword, str) or not keyword:
                        raise ValueError("canonical keyword is invalid")
                    if correct is None:
                        continue
                    if type(correct) is not bool:
                        raise ValueError("canonical keyword correctness is invalid")

                    series_map = specific.setdefault(keyword, {})
                    count, wins = series_map.get(series_ticker, (0, 0))
                    series_map[series_ticker] = (count + 1, wins + int(correct))
                    aggregate_counts = aggregate.setdefault(keyword, [0, 0])
                    aggregate_counts[0] += 1
                    aggregate_counts[1] += int(correct)
        except Exception as exc:
            log.warning(
                "[KEYWORD_STATS] Canonical delivery unavailable; clearing runtime data: %s",
                exc,
            )
            specific = {}
            aggregate = {}
            basis_sha256 = None
            event_count = 0
            as_of = datetime.now(timezone.utc).isoformat()
        else:
            basis_sha256, event_count = canonical_delivery_basis_sha256(events)
            as_of = now.isoformat()

        with self._lock:
            self._specific = specific
            self._aggregate = {key: (counts[0], counts[1]) for key, counts in aggregate.items()}
            self._last_loaded = time.monotonic()
            self._canonical_basis_sha256 = basis_sha256
            self._canonical_event_count = event_count
            self._canonical_as_of = as_of

        pair_count = sum(len(v) for v in self._specific.values())
        log.info(
            "[KEYWORD_STATS] Loaded %d (keyword, series) accuracy pairs from canonical delivery",
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
        return self.get_multiplier_with_receipt(keyword, series_ticker)[0]

    def get_multiplier_with_receipt(
        self,
        keyword: str,
        series_ticker: str,
    ) -> tuple[float, FeedbackMultiplierReceipt]:
        """Return the runtime multiplier and its immutable canonical basis."""
        self._maybe_refresh()
        observed_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            basis_sha256 = self._canonical_basis_sha256
            event_count = self._canonical_event_count
            basis_as_of = self._canonical_as_of or observed_at
            if basis_sha256 is None:
                return 1.0, FeedbackMultiplierReceipt(
                    channel="keyword",
                    key=keyword,
                    series_ticker=series_ticker,
                    applied_multiplier=1.0,
                    status="neutral_unavailable",
                    canonical_basis_sha256=None,
                    delivered_event_count=0,
                    effective_sample_count=0,
                    algorithm_version=FEEDBACK_ALGORITHM_VERSION,
                    as_of=basis_as_of,
                )

            # Try series-specific first
            series_map = self._specific.get(keyword, {})
            n, wins = series_map.get(series_ticker, (0, 0))
            if n >= MIN_SAMPLES:
                accuracy = wins / n
                multiplier = max(0.5, min(1.5, 0.5 + accuracy))
                return multiplier, FeedbackMultiplierReceipt(
                    channel="keyword",
                    key=keyword,
                    series_ticker=series_ticker,
                    applied_multiplier=multiplier,
                    status="canonical",
                    canonical_basis_sha256=basis_sha256,
                    delivered_event_count=event_count,
                    effective_sample_count=n,
                    algorithm_version=FEEDBACK_ALGORITHM_VERSION,
                    as_of=basis_as_of,
                )

            # Fall back to aggregate across all series
            n_agg, wins_agg = self._aggregate.get(keyword, (0, 0))
            if n_agg >= MIN_SAMPLES:
                accuracy = wins_agg / n_agg
                multiplier = max(0.5, min(1.5, 0.5 + accuracy))
                return multiplier, FeedbackMultiplierReceipt(
                    channel="keyword",
                    key=keyword,
                    series_ticker=series_ticker,
                    applied_multiplier=multiplier,
                    status="canonical",
                    canonical_basis_sha256=basis_sha256,
                    delivered_event_count=event_count,
                    effective_sample_count=n_agg,
                    algorithm_version=FEEDBACK_ALGORITHM_VERSION,
                    as_of=basis_as_of,
                )

            sample_count = max(n, n_agg)
        return 1.0, FeedbackMultiplierReceipt(
            channel="keyword",
            key=keyword,
            series_ticker=series_ticker,
            applied_multiplier=1.0,
            status="neutral_insufficient_sample",
            canonical_basis_sha256=basis_sha256,
            delivered_event_count=event_count,
            effective_sample_count=sample_count,
            algorithm_version=FEEDBACK_ALGORITHM_VERSION,
            as_of=basis_as_of,
        )

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
