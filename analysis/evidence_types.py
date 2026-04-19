"""Shared domain types for the evidence scoring and dossier belief system.

These types are consumed by:
- analysis/evidence_scorer.py  (S2.3)
- analysis/dossier_builder.py  (S2.4)
- tasks/evidence_store.py      (S2.2, Codex)
- tasks/accumulation_task.py   (S2.5, Codex)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    """A single piece of scored evidence submitted to the accumulation lane."""

    evidence_id: str          # immutable, globally unique
    market_ticker: str
    source: str               # concrete source label
    source_class: str         # normalized class: "official" | "news" | "analysis" | "market" | "social" | "other"
    headline: str
    ingested_ts: str          # UTC ISO timestamp
    implied_probability: float  # 0..1 — what this evidence implies for market resolution
    content_hash: str         # deterministic identity; NOT unique (duplicates are auditable)
    url: str | None = None
    published_ts: str | None = None


@dataclass(frozen=True)
class EvidenceScore:
    """Scorer output for one evidence item against a market's recent evidence window."""

    evidence_id: str
    source_class: str
    quality_score: float            # raw source-class quality, 0..1
    original_weight: float          # quality adjusted for same-class diminishing returns (BSR-5)
    is_duplicate: bool              # True when content_hash matches an existing item in window
    correlation_discount_applied: bool  # True when original_weight < quality_score
    is_independent: bool            # BSR-7: different source class AND n-gram overlap < threshold
    same_class_count: int           # count of same-class items already in window (before this one)
    implied_probability: float      # carried through from Evidence for use in belief update


@dataclass(frozen=True)
class Dossier:
    """Per-market accumulated belief state.

    This is the authoritative slow-path belief state (INV-3). It is immutable —
    dossier_builder.update_dossier returns a new instance rather than mutating.

    Fields align 1:1 with the dossiers table defined in docs/evidence_store_schema.md.
    """

    market_ticker: str
    dossier_version: int             # increments on every persisted update
    confidence: float                # 0.0..0.95 — current raw confidence
    drift_suspect: bool              # BSR-3 freeze flag
    in_recovery: bool                # BSR-3 recovery mode flag
    created_ts: str                  # UTC ISO — set once at creation
    updated_ts: str                  # UTC ISO — updated on every update_dossier call

    current_estimate: float | None = None   # accumulated probability estimate, 0..1
    prior_estimate: float | None = None     # last cross-class anchor (BSR-3)
    freeze_started_ts: str | None = None    # UTC ISO when drift freeze began (BSR-3)
    recovery_started_ts: str | None = None  # UTC ISO when recovery began (BSR-3)
    recovery_until_ts: str | None = None    # UTC ISO expected recovery end (BSR-3)
    last_cross_class_state_update_ts: str | None = None  # BSR-3 anchor tracking
