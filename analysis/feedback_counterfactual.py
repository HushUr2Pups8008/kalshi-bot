"""Immutable, decision-time provenance for feedback multipliers.

The runtime feedback stores intentionally expose plain floats to existing
callers. This module provides their companion receipts for observability and
offline replay without changing sizing or admission decisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


FEEDBACK_ALGORITHM_VERSION = "canonical-feedback-v1"


@dataclass(frozen=True)
class FeedbackMultiplierReceipt:
    """The canonical basis and result used for one feedback multiplier read."""

    channel: str
    key: str
    series_ticker: str | None
    applied_multiplier: float
    status: str
    canonical_basis_sha256: str | None
    delivered_event_count: int
    effective_sample_count: int
    algorithm_version: str
    as_of: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "key": self.key,
            "series_ticker": self.series_ticker,
            "applied_multiplier": self.applied_multiplier,
            "status": self.status,
            "canonical_basis_sha256": self.canonical_basis_sha256,
            "delivered_event_count": self.delivered_event_count,
            "effective_sample_count": self.effective_sample_count,
            "algorithm_version": self.algorithm_version,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class FeedbackDecisionRecord:
    """One append-only decision-time feedback counterfactual snapshot."""

    lifecycle_id: str
    venue: str
    ticker: str
    source: str
    series_ticker: str | None
    decision_at: str
    source_receipt: FeedbackMultiplierReceipt
    keyword_receipts: tuple[FeedbackMultiplierReceipt, ...]
    probability_actual: float
    probability_keyword_neutral: float | None
    keyword_counterfactual_status: str
    sizing_inputs: dict[str, Any]
    actual: dict[str, Any]
    source_neutral: dict[str, Any]
    keyword_neutral: dict[str, Any] | None
    all_neutral: dict[str, Any] | None
    gate: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feedback_decision_version": 1,
            "lifecycle_id": self.lifecycle_id,
            "venue": self.venue,
            "ticker": self.ticker,
            "source": self.source,
            "series_ticker": self.series_ticker,
            "decision_at": self.decision_at,
            "source_receipt": self.source_receipt.as_dict(),
            "keyword_receipts": [receipt.as_dict() for receipt in self.keyword_receipts],
            "probability_actual": self.probability_actual,
            "probability_keyword_neutral": self.probability_keyword_neutral,
            "keyword_counterfactual_status": self.keyword_counterfactual_status,
            "sizing_inputs": self.sizing_inputs,
            "actual": self.actual,
            "source_neutral": self.source_neutral,
            "keyword_neutral": self.keyword_neutral,
            "all_neutral": self.all_neutral,
            "gate": self.gate,
        }


@dataclass
class KeywordFeedbackCollector:
    """In-memory keyword feedback trace for one estimator call.

    It is deliberately optional. The estimator returns its historical tuple and
    changes no decision when no collector is supplied.
    """

    _keyword_receipts: list[FeedbackMultiplierReceipt] = field(default_factory=list)
    actual_shift: float = 0.0
    neutral_shift: float = 0.0
    keyword_probability_actual: float | None = None
    keyword_probability_neutral: float | None = None
    keyword_counterfactual_status: str = "not_collected"

    @property
    def keyword_receipts(self) -> tuple[FeedbackMultiplierReceipt, ...]:
        return tuple(self._keyword_receipts)

    def record_group(
        self,
        *,
        direction: str,
        actual_weight: float,
        neutral_weight: float,
        receipt: FeedbackMultiplierReceipt | None,
    ) -> None:
        sign = 1.0 if direction == "yes" else -1.0
        self.actual_shift += sign * actual_weight
        self.neutral_shift += sign * neutral_weight
        if receipt is not None:
            self._keyword_receipts.append(receipt)

    def record_probabilities(
        self,
        *,
        actual: float,
        neutral: float,
        status: str,
    ) -> None:
        self.keyword_probability_actual = actual
        self.keyword_probability_neutral = neutral
        self.keyword_counterfactual_status = status


def canonical_delivery_basis_sha256(events: Iterable[Any]) -> tuple[str, int]:
    """Hash delivered outbox identity without retaining mutable telemetry."""

    identities = [
        {
            "outbox_id": str(getattr(event, "outbox_id", "")),
            "trade_id": str(getattr(event, "trade_id", "")),
            "payload_sha256": hashlib.sha256(
                str(getattr(event, "payload_json", "")).encode("utf-8")
            ).hexdigest(),
        }
        for event in events
    ]
    identities.sort(
        key=lambda item: (
            item["outbox_id"],
            item["trade_id"],
            item["payload_sha256"],
        )
    )
    material = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(material).hexdigest(), len(identities)
