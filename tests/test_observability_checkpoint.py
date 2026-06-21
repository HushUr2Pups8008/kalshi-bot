"""Tests for tasks/stats/observability_checkpoint.

Both operator reports (daily_review, pipeline_impact_audit) source one unified
observability snapshot: readiness gate, edge-prioritization A/B verdict, and the
rot-audit artifacts. The summary extraction must be robust to missing/partial
inputs (an audit artifact may not exist yet) — it should degrade to 'n/a', never
crash a report. That extraction is pure and tested here.
"""

from tasks.stats.observability_checkpoint import summarize


def _full_checkpoint():
    return {
        "flag_enabled": True,
        "readiness": {
            "readiness": "NOT_READY",
            "post_clean_start_production_proxy_complete_rows": 8,
            "min_trades_required": 200,
            "reason": "rows 8 < min_trades 200",
        },
        "edge_ab": {
            "resolved_ev": {"verdict": "insufficient AFTER data (0/10) — keep accumulating",
                            "before": {"mean_pnl_ev": -0.5, "resolved": 5, "win_rate": 0.4},
                            "after": {"mean_pnl_ev": None, "resolved": 0}},
            "opportunity_rate": {"before_per_day": 5.6, "after_per_day": 0.0},
        },
        "feed_health": {"by_health": {"live": 21}, "unhealthy": []},
        "regime_priors": {"orphaned_priors": ["KXVANCEPAKISTAN", "KXEFFTARIFF"],
                          "missing_prior_candidates": {"KXTRUMPMENTION": 17}},
        "market_horizon": {"days_quantiles": {"p90": 14.9}, "flags": []},
        "edge_series": {"KXTRUMPIRAN": {}, "KXTRUMPCHINA": {}},
        "matcher_weights": {"KXFOO:token": {}, "KXBAR:tok": {}},
    }


def test_summarize_extracts_headlines():
    s = summarize(_full_checkpoint())
    assert s["readiness_verdict"] == "NOT_READY"
    assert s["readiness_complete"] == 8 and s["readiness_required"] == 200
    assert s["edge_flag"] == "ON"
    assert "insufficient" in s["ab_verdict"].lower()
    assert s["ab_opps_before"] == 5.6 and s["ab_opps_after"] == 0.0
    assert s["feed_unhealthy"] == 0 and s["feed_live"] == 21
    assert s["regime_orphans"] == 2 and s["regime_candidates"] == 1
    assert s["market_drift_flags"] == 0
    assert s["edge_series_count"] == 2
    assert s["matcher_tokens"] == 2


def test_summarize_robust_to_empty():
    s = summarize({})
    assert s["readiness_verdict"] == "n/a"
    assert s["edge_flag"] in ("OFF", "n/a")
    assert s["feed_unhealthy"] == 0
    assert s["regime_orphans"] == 0
    assert s["ab_verdict"] == "n/a"


def test_summarize_robust_to_error_reports():
    # A sub-collector may have failed and stored an {"error": ...} report.
    s = summarize({"readiness": {"error": "db locked"}, "edge_ab": {"error": "no marker"}})
    assert s["readiness_verdict"] == "n/a"
    assert s["ab_verdict"] == "n/a"
