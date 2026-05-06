import pytest

from scripts.edge_replay.score_counterfactual_pnl import score_candidate, summarize_scores


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
