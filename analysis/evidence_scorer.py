"""Evidence quality scorer — S2.3.

Pure function layer (INV-4). No I/O, no DB access, no network calls.

Implements:
  BSR-5: Same-class diminishing returns within rolling half-life window.
  BSR-7: Evidence independence approximation (source class + n-gram overlap).
"""

from __future__ import annotations

import re

from analysis.evidence_types import Evidence, EvidenceScore

# ── Constants ─────────────────────────────────────────────────────────────────

# BSR-7: n-gram overlap below this threshold is required for independence.
NGRAM_OVERLAP_THRESHOLD: float = 0.35

_NGRAM_SIZE: int = 2

# Per-class base quality scores. "other" / unknown classes fall back to _DEFAULT_QUALITY.
_SOURCE_CLASS_QUALITY: dict[str, float] = {
    "official": 0.85,
    "news":     0.70,
    "analysis": 0.60,
    "market":   0.55,
    "social":   0.35,
    "other":    0.25,
}
_DEFAULT_QUALITY: float = 0.25

# ── N-gram helpers ────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower())


def _extract_ngrams(text: str, n: int = _NGRAM_SIZE) -> frozenset[str]:
    tokens = _clean(text).split()
    if len(tokens) < n:
        return frozenset(tokens)
    return frozenset(" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def ngram_overlap(headline_a: str, headline_b: str) -> float:
    """Jaccard similarity over word bigrams (exported for testing and dossier_builder)."""
    ga = _extract_ngrams(headline_a)
    gb = _extract_ngrams(headline_b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ── Source quality ────────────────────────────────────────────────────────────

def source_quality(source_class: str) -> float:
    """Return base quality score for a given source class."""
    return _SOURCE_CLASS_QUALITY.get(source_class, _DEFAULT_QUALITY)


# ── Main scorer ───────────────────────────────────────────────────────────────

def score_evidence(
    evidence: Evidence,
    recent_market_evidence: list[Evidence],
) -> EvidenceScore:
    """Score one evidence item against the recent half-life window for its market.

    ``recent_market_evidence`` must contain only evidence for the same market
    that falls within the current half-life window (window management is the
    caller's responsibility — tasks/accumulation_task.py, S2.5).

    BSR-7: independence requires different source class AND ngram overlap < 0.35.
    BSR-5: same-class signals apply diminishing returns weight / n.
    """
    # ── Duplicate detection (exact content hash) ──────────────────────────────
    is_duplicate = any(
        e.content_hash == evidence.content_hash and e.evidence_id != evidence.evidence_id
        for e in recent_market_evidence
    )

    # ── N-gram overlap check (BSR-7 condition 2) ──────────────────────────────
    max_overlap = max(
        (ngram_overlap(evidence.headline, e.headline) for e in recent_market_evidence),
        default=0.0,
    )
    low_ngram_overlap = max_overlap < NGRAM_OVERLAP_THRESHOLD

    # ── Source class independence check (BSR-7 condition 1) ───────────────────
    existing_classes = {e.source_class for e in recent_market_evidence}
    different_class = evidence.source_class not in existing_classes

    # ── BSR-7: independent iff different class AND low overlap AND not duplicate
    is_independent = different_class and low_ngram_overlap and not is_duplicate

    # ── BSR-5: same-class count in window ─────────────────────────────────────
    same_class_count = sum(
        1 for e in recent_market_evidence if e.source_class == evidence.source_class
    )

    # ── Quality and weight ────────────────────────────────────────────────────
    quality = source_quality(evidence.source_class)

    if is_independent:
        # Cross-class, fresh signal: full quality weight.
        original_weight = quality
        correlation_discount_applied = False
    else:
        # BSR-5: nth same-class signal → weight / n (1-indexed position).
        n = same_class_count + 1
        original_weight = quality / n
        correlation_discount_applied = True

    return EvidenceScore(
        evidence_id=evidence.evidence_id,
        source_class=evidence.source_class,
        quality_score=quality,
        original_weight=original_weight,
        is_duplicate=is_duplicate,
        correlation_discount_applied=correlation_discount_applied,
        is_independent=is_independent,
        same_class_count=same_class_count,
        implied_probability=evidence.implied_probability,
    )
