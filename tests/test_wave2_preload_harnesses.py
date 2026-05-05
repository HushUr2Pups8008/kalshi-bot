from __future__ import annotations

import pytest

from tasks.trade_readiness_gate import evaluate_readiness


class _BlendResult:
    source_lane = "fast"
    blended_confidence = 0.045
    disagreement_score = 0.0
    default_min_edge = 0.02


_LEVER_B_G1_004_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever B G1=0.04 spec not yet landed. "
    "A fast-lane candidate with scaled confidence 0.045 fails current G1=0.05 "
    "but must pass after the conservative G1=0.04 deploy."
)


def test_wave2_a1plus_option_a_harness_is_strict_xfail():
    marker = pytest.mark.xfail
    from tests import test_lever_a1plus_feed_config as feed_config

    test_func = feed_config.test_at_least_one_specialist_analyst_url_in_rss_feeds
    xfail_marks = [
        mark for mark in getattr(test_func, "pytestmark", [])
        if mark.name == marker().mark.name
    ]

    assert xfail_marks, "A.1+ option-A feed config harness must remain xfail-marked pre-deploy"
    assert xfail_marks[0].kwargs.get("strict") is True


def test_wave2_a1plus_option_b_harness_is_strict_xfail():
    marker = pytest.mark.xfail
    from tests import test_lever_a1plus_feed_config as feed_config

    test_func = feed_config.test_vital_law_or_legal_analyst_feed_present_post_a1plus
    xfail_marks = [
        mark for mark in getattr(test_func, "pytestmark", [])
        if mark.name == marker().mark.name
    ]

    assert xfail_marks, "A.1+ option-B legal feed harness must remain xfail-marked pre-deploy"
    assert xfail_marks[0].kwargs.get("strict") is True


@pytest.mark.xfail(reason=_LEVER_B_G1_004_XFAIL_REASON, strict=True)
def test_lever_b_g1_004_admits_candidate_between_004_and_005():
    decision = evaluate_readiness(_BlendResult(), regime_confidence=1.0)

    assert decision.passed is True
    assert "G1_blended_confidence" not in decision.failure_reasons
