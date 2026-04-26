"""Evidence composition for the governance agent.

Pure functions — no I/O beyond what the adapter already did. Three pieces:
- select_candidates_for_cadence(): which (action, target) pairs to ask about
- compose_evidence_for_candidate(): build the evidence dict for a single LLM call
- summarize_evidence_for_audit(): trim the evidence dict for the JSONL audit log

Per spec §8.3, 'fast' cadence is invoked every 2h and must bound LLM cost.
'deep' is daily and may sweep more thoroughly. 'weekly_review' is a Phase 4
concern; in Phase 2 it returns an empty candidate list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Candidate:
    """A (action, target) pair the agent will ask the LLM to opine on.

    `evidence_pointer` is a key into the audit-data dict; the prompt renderer
    follows the pointer to extract per-target metrics. We carry the pointer
    rather than the raw evidence so candidates remain cheap to construct
    and easy to deduplicate.
    """
    action: Literal["disable_source", "disable_keyword", "tune_threshold"]
    target: str
    evidence_pointer: dict[str, Any]


_VALID_CADENCES = {"fast", "deep", "weekly_review"}


def _disable_source_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """A source is a candidate when (a) Reddit audit classifies it as
    all_stale / no_matches with ingestion >= 20, OR (b) source-market
    alignment shows it consistently anchoring at >= 0.95 across a
    meaningful sample."""
    out: list[Candidate] = []

    # (a) Reddit audit — explicit problem classifications.
    reddit_subs = audit.get("reddit", {}).get("subs", []) or []
    problem_classifications = {"all_stale", "no_matches", "match_dead"}
    for sub in sorted(
        reddit_subs,
        key=lambda s: -int(s.get("ingestion", 0) or 0),
    ):
        if sub.get("classification") not in problem_classifications:
            continue
        if int(sub.get("ingestion", 0) or 0) < 20:
            continue
        out.append(Candidate(
            action="disable_source",
            target=str(sub["source"]),
            evidence_pointer={"reddit_sub_index": reddit_subs.index(sub)},
        ))

    # (b) Alignment audit — high-volume sources that are universally anchoring.
    pairs = audit.get("alignment", {}).get("pairs", []) or []
    by_source: dict[str, dict[str, Any]] = {}
    for p in pairs:
        src = p.get("source")
        if not src:
            continue
        s = by_source.setdefault(src, {"n": 0, "anchored": 0})
        s["n"] += int(p.get("n", 0) or 0)
        s["anchored"] += int(p.get("anchor", 0) or 0)
    for src, stats in sorted(by_source.items(), key=lambda kv: -kv[1]["n"]):
        if stats["n"] < 10:
            continue
        rate = stats["anchored"] / stats["n"] if stats["n"] else 0.0
        if rate < 0.95:
            continue
        if any(c.target == src for c in out):
            continue  # dedup against (a)
        out.append(Candidate(
            action="disable_source",
            target=src,
            evidence_pointer={"alignment_source": src},
        ))

    if max_count is not None:
        out = out[:max_count]
    return out


def _disable_keyword_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """Currently a placeholder hook: returns no candidates in Phase 2 unless
    the keywords audit explicitly flagged risky phrases. Future expansion
    (Phase 4 self-review) will broaden this."""
    out: list[Candidate] = []
    phrases = audit.get("keywords", {}).get("candidate_phrases", []) or []
    for p in phrases:
        if p.get("category") == "person":
            # person-class phrases are legitimately predictive but high-volume;
            # not auto-candidates. agent can still consider them via deep sweep.
            continue
        # No automatic flagging in Phase 2. Reserved for Phase 4.
    if max_count is not None:
        out = out[:max_count]
    return out


def _tune_threshold_candidates(
    audit: dict[str, Any], *, max_count: int | None = None,
) -> list[Candidate]:
    """Reserved for Phase 4 (no thresholds tuned in Phase 2)."""
    return []


def select_candidates_for_cadence(
    audit: dict[str, Any],
    *,
    cadence: str,
    max_per_bucket: int = 5,
) -> list[Candidate]:
    """Return the candidates the agent will evaluate this cycle."""
    if cadence not in _VALID_CADENCES:
        raise ValueError(f"unknown cadence: {cadence!r}")

    if cadence == "weekly_review":
        return []

    if cadence == "fast":
        cap: int | None = max_per_bucket
    else:  # deep
        cap = None

    return (
        _disable_source_candidates(audit, max_count=cap)
        + _disable_keyword_candidates(audit, max_count=cap)
        + _tune_threshold_candidates(audit, max_count=cap)
    )


def compose_evidence_for_candidate(
    candidate: Candidate,
    audit: dict[str, Any],
    adapter,  # GovernanceAdapter
) -> dict[str, Any]:
    """Build the evidence dict for a single LLM call.

    The shape is action-specific; the prompt-renderer dispatches on
    candidate.action. Common keys (candidate_action, target,
    active_market_count, active_market_titles_top, active_source_count,
    window_hours) appear regardless of action.
    """
    common = {
        "candidate_action": candidate.action,
        "target": candidate.target,
        "active_market_titles_top": adapter.get_active_market_titles()[:20],
        "active_market_count": len(adapter.get_active_market_titles()),
        "active_source_count": adapter.get_active_source_count(),
        "window_hours": 168,
    }

    if candidate.action == "disable_source":
        ingest = 0
        fresh = 0
        match = 0
        anchor_rate: float | None = None
        # Reddit audit lookup
        idx = candidate.evidence_pointer.get("reddit_sub_index")
        if isinstance(idx, int):
            sub = audit.get("reddit", {}).get("subs", [])[idx]
            ingest = int(sub.get("ingestion", 0) or 0)
            fresh = int(sub.get("fresh_passes", 0) or 0)
            match = int(sub.get("matches", 0) or 0)
        # Alignment audit lookup (overrides if both present)
        align_src = candidate.evidence_pointer.get("alignment_source")
        if align_src:
            pairs = audit.get("alignment", {}).get("pairs", []) or []
            n_total = sum(int(p.get("n", 0) or 0) for p in pairs if p.get("source") == align_src)
            anchored_total = sum(int(p.get("anchor", 0) or 0) for p in pairs if p.get("source") == align_src)
            ingest = max(ingest, n_total)
            anchor_rate = anchored_total / n_total if n_total else None
        return {
            **common,
            "ingestion_events": ingest,
            "fresh_pass_count": fresh,
            "match_count": match,
            "anchor_rate": anchor_rate,
            "recent_headline_sample": adapter.get_recent_headline_samples(
                candidate.target, k=5,
            ),
        }

    if candidate.action == "disable_keyword":
        return {
            **common,
            "candidate_phrase_summary": candidate.evidence_pointer,
        }

    if candidate.action == "tune_threshold":
        return {
            **common,
            "current_value": candidate.evidence_pointer.get("current_value"),
        }

    raise ValueError(f"unknown candidate.action: {candidate.action!r}")


def summarize_evidence_for_audit(evidence: dict[str, Any]) -> dict[str, Any]:
    """Trim a per-candidate evidence dict for the audit-log record. Implemented
    in Task 9."""
    raise NotImplementedError("Implemented in Task 9")
