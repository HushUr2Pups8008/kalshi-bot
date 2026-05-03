from __future__ import annotations

from scripts.simulations.governance_negative_control import _PROBES


def _probe_map():
    return {probe.probe_id: probe for probe in _PROBES}


def test_anchor_rate_regression_probes_cover_polarity_flip_surface():
    probes = _probe_map()

    for probe_id in (
        "NEG_A_high_anchor_no_edge_disable",
        "NEG_A_high_anchor_high_volume_disable",
        "NEG_A_low_anchor_high_match_keep",
        "NEG_A_mid_anchor_supporting_matches_keep",
        "NEG_A_anchor_absent_with_matches_keep",
        "NEG_A_va_regression_pos_sparse_disable",
    ):
        assert probe_id in probes

    assert probes["NEG_A_high_anchor_no_edge_disable"].expected_action == "disable_source"
    assert probes["NEG_A_high_anchor_high_volume_disable"].expected_action == "disable_source"
    assert probes["NEG_A_low_anchor_high_match_keep"].expected_action == "no_action"
    assert probes["NEG_A_mid_anchor_supporting_matches_keep"].expected_action == "no_action"
    assert probes["NEG_A_anchor_absent_with_matches_keep"].expected_action == "no_action"
    assert probes["NEG_A_va_regression_pos_sparse_disable"].expected_action == "disable_source"


def test_every_probe_uses_disable_source_evidence_schema():
    required = {
        "target",
        "window_hours",
        "active_market_count",
        "active_source_count",
        "active_market_titles_top",
        "ingestion_events",
        "fresh_pass_count",
        "match_count",
        "anchor_rate",
        "recent_headline_sample",
    }

    for probe in _PROBES:
        assert set(probe.evidence) == required
