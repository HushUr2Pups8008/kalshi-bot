"""Tests for tasks/stats/regime_prior_audit — observability for _SERIES_PRIORS rot.

_SERIES_PRIORS is hand-curated and tied to the political moment; entries orphan
when Kalshi retires a series, and new opportunity-producing series may lack a
prior. This audit surfaces both WITHOUT touching the trade path (pure detection
over the prior keys + recent market universe + opportunity history). It is the
safe, observability-only Phase 1 of the _SERIES_PRIORS durability scope.
"""

from tasks.stats.regime_prior_audit import (
    find_orphaned_priors,
    find_missing_prior_candidates,
)


def test_find_orphaned_priors_flags_priors_with_no_recent_market():
    priors = frozenset({"KXTRUMPIRAN", "KXOLDELECTION", "KXCPIYOY"})
    seen = frozenset({"KXTRUMPIRAN", "KXCPIYOY", "KXNEWTHING"})
    assert find_orphaned_priors(priors, seen) == frozenset({"KXOLDELECTION"})


def test_find_orphaned_priors_empty_when_all_present():
    priors = frozenset({"KXA", "KXB"})
    assert find_orphaned_priors(priors, frozenset({"KXA", "KXB", "KXC"})) == frozenset()


def test_find_missing_prior_candidates_flags_producing_series_without_prior():
    opp_counts = {"KXNEWHOT": 5, "KXBARELY": 2, "KXHASPRIOR": 9}
    priors = frozenset({"KXHASPRIOR"})
    # KXNEWHOT: >=3 opps and no prior -> candidate. KXBARELY: below min.
    # KXHASPRIOR: already has a prior -> not a candidate.
    assert find_missing_prior_candidates(opp_counts, priors, min_opps=3) == {"KXNEWHOT": 5}


def test_find_missing_prior_candidates_respects_min_opps_threshold():
    opp_counts = {"KXEDGE": 3}
    assert find_missing_prior_candidates(opp_counts, frozenset(), min_opps=3) == {"KXEDGE": 3}
    assert find_missing_prior_candidates(opp_counts, frozenset(), min_opps=4) == {}
