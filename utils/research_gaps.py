"""Backward-compatible research gap questions for retry planning."""

from __future__ import annotations

from collections.abc import Iterable


_QUESTIONS_BY_REASON = {
    "no_research_hits": "Which reliable source has current contract-specific evidence?",
    "missing_resolution_source": (
        "Which official or settlement-aligned source reports the contract-window result?"
    ),
    "insufficient_corroboration": (
        "Which independent reliable source corroborates the settlement evidence?"
    ),
    "official_data_pending": (
        "When will the official target-period data be released, and what result does it report?"
    ),
    "missing_counter_evidence": "What credible evidence contradicts the proposed trade side?",
    "neutral_only_evidence": "Which current evidence supports either settlement direction?",
    "ambiguous_direction": "Which contract-specific fact resolves the directional ambiguity?",
    "unresolved_contradiction": "Which reliable source resolves the conflicting claims?",
    "missing_probability_estimate": (
        "What contract-specific evidence supports a calibrated probability estimate?"
    ),
    "missing_estimated_probability": (
        "What contract-specific evidence supports a calibrated probability estimate?"
    ),
    "missing_market_price": "What is the current executable market price for each side?",
    "missing_price_edge": "What current executable price and estimated edge support a trade?",
    "missing_reasoning": "How does the evidence map to the contract wording and selected side?",
    "direction_reason_conflict": (
        "Which contract-specific evidence resolves the conflict between direction and reasoning?"
    ),
    "cached_dossier_insufficient": (
        "Which fresh contract-specific evidence is missing from the cached dossier?"
    ),
    "cached_dossier_unvetted": (
        "Which evidence is required to produce a vetted directional dossier verdict?"
    ),
    "persistence_status_unverified": (
        "Can the latest research run be verified as durably persisted?"
    ),
    "research_provider_error": (
        "Which alternate reliable source can complete the failed provider lane?"
    ),
    "research_adjudicator_error": (
        "Which structured evidence resolves the failed adjudication?"
    ),
    "no_reliable_source_path": (
        "Which reliable source has authority over this contract's settlement fact?"
    ),
    "source_freshness_ttl_exceeded": (
        "Which fresh source updates the stale contract-specific evidence?"
    ),
    "generic_summary": "Which specific evidence and reasoning make this result actionable?",
    "research_timeout": "Which unqueried reliable source can complete the missing evidence lane?",
}


def research_questions_for_skip(
    skip_reason: str | None,
    existing: Iterable[str] = (),
) -> tuple[str, ...]:
    questions = []
    seen = set()
    for value in existing:
        question = " ".join(str(value or "").split())[:240]
        if question and question not in seen:
            seen.add(question)
            questions.append(question)
    derived = _QUESTIONS_BY_REASON.get(str(skip_reason or ""))
    if derived:
        derived_intent = research_gap_query_intent(derived)[0]
        existing_intents = {research_gap_query_intent(question)[0] for question in questions}
        if derived_intent not in existing_intents:
            questions.insert(0, derived)
    return tuple(questions[:8])


def research_gap_query_intent(question: str) -> tuple[str, str]:
    lower = str(question or "").lower()
    if any(term in lower for term in ("official", "settlement", "target-period", "released")):
        return "official_resolution", "official_primary"
    if any(term in lower for term in ("contradict", "conflict", "against", "proposed trade side")):
        return "disconfirming", "reputable_secondary"
    if "market price" in lower:
        return "market_price", "market_price"
    if "probability" in lower:
        return "base_rate", "reputable_secondary"
    return "supporting", "reputable_secondary"
