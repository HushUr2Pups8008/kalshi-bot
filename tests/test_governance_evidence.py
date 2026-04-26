"""Evidence builder — candidate selection + per-candidate composition."""

from __future__ import annotations

import pytest

from governance.evidence import (
    Candidate,
    select_candidates_for_cadence,
    compose_evidence_for_candidate,
    summarize_evidence_for_audit,
)


def _audit_data_with_three_sources():
    """Synthetic audit data shaped like KalshiGovernanceAdapter.collect_audit_data
    output, simplified for unit testing."""
    return {
        "alignment": {
            "pairs": [
                {"source": "Reuters", "series_ticker": "KXTRUMPIRAN",
                 "n": 32, "anchor": 32, "anchor_rate": 1.0},
                {"source": "AP", "series_ticker": "KXTRUMPIRAN",
                 "n": 18, "anchor": 17, "anchor_rate": 17/18},
                {"source": "r/Turkey", "series_ticker": "KXMENA",
                 "n": 0, "anchor": 0, "anchor_rate": None},
            ],
            "overall_anchor_rate": 0.99,
            "overall_n": 50,
        },
        "keywords": {
            "no_keyword_misses": 12,
            "candidate_phrases": [
                {"phrase": "ceasefire", "count": 30, "category": "war"},
                {"phrase": "trump", "count": 200, "category": "person"},
            ],
        },
        "reddit": {
            "subs": [
                {"source": "r/Turkey", "ingestion": 408,
                 "fresh_passes": 7, "matches": 0,
                 "classification": "all_stale"},
                {"source": "r/worldnews", "ingestion": 200,
                 "fresh_passes": 80, "matches": 12,
                 "classification": "signaling"},
            ],
        },
        "freshness": {
            "sources": {
                "Reuters": {"observed_records": 250, "fresh_passes": 200,
                            "stale_rate": 0.2, "interpretation": "fast operational"},
                "BBC": {"observed_records": 4, "fresh_passes": 0,
                        "stale_rate": 1.0, "interpretation": "insufficient data"},
            },
        },
    }


def test_fast_cadence_returns_bounded_top_n():
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="fast", max_per_bucket=5)
    assert isinstance(candidates, list)
    # Each candidate is a Candidate(action, target, evidence_pointer)
    assert all(isinstance(c, Candidate) for c in candidates)
    # fast cadence caps at max_per_bucket per concern bucket; we have three
    # buckets: source, keyword, threshold.
    actions = {c.action for c in candidates}
    assert actions <= {"disable_source", "disable_keyword", "tune_threshold"}


def test_deep_cadence_returns_full_sweep():
    audit = _audit_data_with_three_sources()
    fast = select_candidates_for_cadence(audit, cadence="fast", max_per_bucket=1)
    deep = select_candidates_for_cadence(audit, cadence="deep", max_per_bucket=1)
    assert len(deep) >= len(fast), (
        "deep cadence must include at least as many candidates as fast"
    )


def test_weekly_review_yields_no_action_candidates_in_phase_2():
    """weekly_review evaluates past decisions for outcome correctness; in
    Phase 2 it returns an empty list (Phase 4 wires self-review)."""
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="weekly_review")
    assert candidates == []


def test_unknown_cadence_raises():
    audit = _audit_data_with_three_sources()
    with pytest.raises(ValueError, match="cadence"):
        select_candidates_for_cadence(audit, cadence="hourly")


def test_disable_source_candidates_only_for_problem_sources():
    """A source with high anchor_rate AND high ingestion volume AND zero
    matches (or all_stale classification) is a candidate. A signaling
    source is not."""
    audit = _audit_data_with_three_sources()
    candidates = select_candidates_for_cadence(audit, cadence="deep")
    targets = {c.target for c in candidates if c.action == "disable_source"}
    assert "r/Turkey" in targets, "all_stale Reddit sub should be a candidate"
    assert "r/worldnews" not in targets, "signaling sub should NOT be a candidate"


class _StubAdapter:
    """Tiny GovernanceAdapter test double — only the methods compose() needs."""
    def __init__(self, *, headline_samples=None, market_titles=None,
                 source_count=42):
        self._headlines = headline_samples or {}
        self._titles = market_titles or []
        self._count = source_count

    def collect_audit_data(self, window):
        raise AssertionError("compose() must not call collect_audit_data")

    def get_active_market_titles(self):
        return list(self._titles)

    def get_recent_headline_samples(self, source, k=5):
        return list(self._headlines.get(source, []))[:k]

    def get_active_source_count(self):
        return self._count

    def get_active_source_list(self):
        return []


def test_compose_evidence_for_disable_source_candidate():
    audit = _audit_data_with_three_sources()
    cand = Candidate(
        action="disable_source",
        target="r/Turkey",
        evidence_pointer={"reddit_sub_index": 0},
    )
    adapter = _StubAdapter(
        headline_samples={"r/Turkey": [
            "Turkey discussion 1",
            "Turkey discussion 2",
            "Turkey discussion 3",
        ]},
        market_titles=["Will X happen?", "Will Y happen?"],
        source_count=42,
    )
    evidence = compose_evidence_for_candidate(cand, audit, adapter)
    assert evidence["candidate_action"] == "disable_source"
    assert evidence["target"] == "r/Turkey"
    assert evidence["ingestion_events"] == 408
    assert evidence["fresh_pass_count"] == 7
    assert evidence["match_count"] == 0
    assert evidence["recent_headline_sample"] == [
        "Turkey discussion 1", "Turkey discussion 2", "Turkey discussion 3",
    ]
    assert evidence["active_market_count"] == 2
    assert "Will X happen?" in evidence["active_market_titles_top"]
    assert evidence["active_source_count"] == 42
    assert evidence["window_hours"] >= 1


def test_compose_evidence_excludes_pii_or_secret_fields():
    """Defensive: evidence is the LLM input. Anything that ends up in here
    can also end up in the audit log. No raw env vars, no PEM keys."""
    audit = _audit_data_with_three_sources()
    cand = Candidate(action="disable_source", target="r/Turkey",
                     evidence_pointer={"reddit_sub_index": 0})
    adapter = _StubAdapter(
        headline_samples={"r/Turkey": ["a"]}, market_titles=[], source_count=10,
    )
    evidence = compose_evidence_for_candidate(cand, audit, adapter)
    forbidden_substrings = ("BEGIN RSA", "API_KEY", "PRIVATE KEY")
    flat = repr(evidence)
    for s in forbidden_substrings:
        assert s not in flat
