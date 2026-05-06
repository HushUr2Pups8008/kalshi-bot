import pytest

from scripts.edge_replay.score_counterfactual_pnl import bootstrap_ci, score_candidate, summarize_scores


def test_score_candidate_scores_yes_contract_against_resolution():
    row = {
        "ticker": "KXYES",
        "side": "yes",
        "contracts": 2,
        "market_yes_price": 40.0,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXYES",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(1.20)


def test_score_candidate_scores_no_contract_against_resolution():
    row = {
        "ticker": "KXNO",
        "side": "no",
        "contracts": 5,
        "market_yes_price": 50.0,
        "edge": 0.068,
        "resolved_yes": True,
        "signal_source": "VitalLaw.com",
        "series_ticker": "KXFISAEXTEND",
        "signal_type": "llm",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is False
    assert scored["counterfactual_pnl"] == pytest.approx(-2.50)


def test_summarize_scores_reports_no_positive_ev_slice():
    scored = [
        score_candidate(
            {
                "ticker": "KXNO",
                "side": "no",
                "contracts": 5,
                "market_yes_price": 50.0,
                "edge": 0.068,
                "resolved_yes": True,
                "signal_source": "VitalLaw.com",
                "series_ticker": "KXFISAEXTEND",
                "signal_type": "llm",
            }
        )
    ]

    summary = summarize_scores(scored)

    assert summary["positive_ev_slices"] == []
    assert summary["overall"]["pnl"] == pytest.approx(-2.50)
    assert summary["groups"][0]["group"] == {
        "signal_source": "VitalLaw.com",
        "series_ticker": "KXFISAEXTEND",
        "signal_type": "llm",
        "news_class": "unknown",
    }


def test_synthetic_positive_ev_slice_is_detected():
    scored = [
        score_candidate(
            {
                "ticker": f"KXPOS{i}",
                "side": "yes",
                "contracts": 1,
                "market_yes_price": 40.0,
                "edge": 0.20,
                "resolved_yes": True,
                "signal_source": "Reuters",
                "series_ticker": "KXPOS",
                "signal_type": "llm",
                "news_class": "publisher_rss",
            }
        )
        for i in range(4)
    ]

    summary = summarize_scores(scored)

    assert len(summary["positive_ev_slices"]) == 1
    assert summary["positive_ev_slices"][0]["ev_ci_95_lo"] > 0


def test_bootstrap_ci_uses_resampling_not_point_interval():
    lo, hi = bootstrap_ci([1.0, -1.0, 1.0, 1.0], iterations=200, seed=7)

    assert lo < hi
    assert lo != pytest.approx(0.5)
    assert hi != pytest.approx(0.5)


def test_readiness_gate_marks_left_on_table_when_model_would_have_won_without_price():
    scored = score_candidate(
        {
            "ticker": "KXLEFT",
            "decision_kind": "dossier_update",
            "model_prob": 0.72,
            "confidence": 0.86,
            "resolved_yes": True,
            "signal_source": "Reuters",
            "series_ticker": "KXLEFT",
            "signal_type": "state",
            "news_class": "publisher_rss",
        },
        readiness_confidence=0.80,
        readiness_yes_threshold=0.60,
    )

    assert scored["readiness_admitted"] is True
    assert scored["left_on_table"] is True
    assert scored["would_have_won_if_taken"] is True
    assert scored["would_have_traded"] is False
