import pytest

from scripts.edge_replay.score_counterfactual_pnl import bootstrap_ci, score_candidate, summarize_scores
from scripts.edge_replay.scorer_forensics_audit import market_implied_win_prob


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


def test_score_candidate_preserves_legacy_no_side_yes_price_semantics():
    row = {
        "ticker": "KXNOLEGACY",
        "side": "no",
        "contracts": 1,
        "market_yes_price": 72.0,
        "edge": 0.20,
        "resolved_yes": False,
        "signal_source": "Reuters",
        "series_ticker": "KXNOLEGACY",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.72)


def test_score_candidate_scores_post_p0_paper_trade_alias_as_executed_price():
    row = {
        "ticker": "KXNOPOSTP0",
        "decision_kind": "paper_trade",
        "side": "no",
        "contracts": 1,
        "market_yes_price": 61.0,
        "edge": 0.20,
        "resolved_yes": False,
        "signal_source": "Reuters",
        "series_ticker": "KXNOPOSTP0",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.39)


def test_score_candidate_scores_post_p0_provenance_alias_as_executed_price():
    row = {
        "ticker": "KXNOPOSTP0PROV",
        "side": "no",
        "contracts": 1,
        "market_yes_price": 61.0,
        "price_method": "dollars_fixed_point",
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXNOPOSTP0PROV",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is False
    assert scored["counterfactual_pnl"] == pytest.approx(-0.61)


def test_score_candidate_treats_legacy_cents_provenance_as_yes_price():
    row = {
        "ticker": "KXNOLEGACYPROV",
        "side": "no",
        "contracts": 1,
        "market_yes_price": 72.0,
        "price_method": "legacy_cents",
        "edge": 0.20,
        "resolved_yes": False,
        "signal_source": "Reuters",
        "series_ticker": "KXNOLEGACYPROV",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.72)


def test_score_candidate_scores_no_executable_price_as_no_contract_cost():
    row = {
        "ticker": "KXNOASK",
        "side": "no",
        "contracts": 1,
        "market_yes_price": 72.0,
        "executable_price_cents": 61.0,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXNOASK",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is False
    assert scored["counterfactual_pnl"] == pytest.approx(-0.61)


def test_score_candidate_uses_executable_price_with_direction_alias_when_side_missing():
    row = {
        "ticker": "KXNOALIAS",
        "llm_direction": "no",
        "contracts": 1,
        "market_yes_price": 72.0,
        "executable_price_cents": 61.0,
        "edge": 0.20,
        "resolved_yes": False,
        "signal_source": "Reuters",
        "series_ticker": "KXNOALIAS",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["side"] == "no"
    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.39)


def test_score_candidate_preserves_legacy_model_price_side_inference_without_executable_price():
    row = {
        "ticker": "KXLEGACYDOSSIER",
        "contracts": 1,
        "model_prob": 0.38,
        "market_yes_price": 80.0,
        "edge": 0.20,
        "resolved_yes": False,
        "signal_source": "Reuters",
        "series_ticker": "KXLEGACYDOSSIER",
        "signal_type": "state",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["side"] == "no"
    assert scored["would_have_traded"] is True
    assert scored["would_have_won"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.80)


def test_score_candidate_skips_executable_price_with_ambiguous_missing_side():
    row = {
        "ticker": "KXAMBIG",
        "contracts": 1,
        "model_prob": 0.80,
        "market_yes_price": 72.0,
        "executable_price_cents": 61.0,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXAMBIG",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["side"] is None
    assert scored["would_have_traded"] is False
    assert scored["would_have_won"] is None
    assert scored["counterfactual_pnl"] == 0.0


def test_score_candidate_skips_post_p0_alias_with_ambiguous_missing_side():
    row = {
        "ticker": "KXAMBIGALIAS",
        "p0_contract_version": 1,
        "contracts": 1,
        "model_prob": 0.70,
        "market_yes_price": 61.0,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXAMBIGALIAS",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(row, min_edge=0.02, default_contracts=1)

    assert scored["side"] is None
    assert scored["would_have_traded"] is False
    assert scored["would_have_won"] is None
    assert scored["counterfactual_pnl"] == 0.0


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


def test_score_candidate_fails_closed_when_only_entry_price_cents_and_no_side():
    """F-11 P1-C reviewer finding #2: ``entry_price_cents`` is the
    executed-side entry price by definition. Inferring side from
    ``model_prob >= entry_price_cents / 100`` would mislabel NO trades as
    YES whenever the executed (NO-side) price sits below the model
    probability. When the only price source is the canonical key and no
    explicit side field is present, side inference must fail closed --
    side=None, would_have_traded=False, PnL=0 -- rather than guess.
    """
    canonical_row = {
        "ticker": "KXENTRYNOSIDE",
        "contracts": 1,
        # Canonical post-P1-A key only; no legacy market_yes_price and no side hint.
        "entry_price_cents": 40.0,
        "model_prob": 0.62,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXENTRYNOSIDE",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(canonical_row, min_edge=0.02, default_contracts=1)

    assert scored["side"] is None, (
        "side must NOT be inferred from entry_price_cents (executed-side); "
        "without an explicit side field the scorer must fail closed"
    )
    assert scored["would_have_traded"] is False
    assert scored["counterfactual_pnl"] == 0.0


def test_score_candidate_consumes_canonical_entry_price_cents_when_side_explicit():
    """F-11 P1-C: with an explicit side, score_candidate must consume the
    canonical ``entry_price_cents`` directly (no YES-midpoint flip) and
    produce the correct executed-side PnL. Guards against a regression
    that would force ``market_yes_price`` to be present for the canonical
    key to take effect.
    """
    canonical_row = {
        "ticker": "KXENTRY",
        "side": "yes",  # explicit side; bypasses _infer_side
        "contracts": 1,
        # Canonical post-P1-A key only; no legacy market_yes_price.
        "entry_price_cents": 40.0,
        "model_prob": 0.62,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXENTRY",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }

    scored = score_candidate(canonical_row, min_edge=0.02, default_contracts=1)

    assert scored["side"] == "yes"
    assert scored["would_have_traded"] is True
    assert scored["counterfactual_pnl"] == pytest.approx(0.60), (
        "PnL = (1 - 0.40) * 1 contract = 0.60 for a winning YES contract"
    )


def test_score_candidate_canonical_and_legacy_keys_produce_identical_pnl():
    """F-11 P1-C: with side and resolution held constant, a row carrying
    only ``entry_price_cents`` and a row carrying only ``market_yes_price``
    (treated as the YES-side price for an explicit YES trade) must score
    to the same PnL. The two reads use different code branches inside
    ``_contract_price``; this test pins they agree on identical inputs.
    """
    base = {
        "ticker": "KXPARITY",
        "side": "yes",  # explicit; avoids the alias-detector early-out
        "contracts": 1,
        "model_prob": 0.62,
        "edge": 0.20,
        "resolved_yes": True,
        "signal_source": "Reuters",
        "series_ticker": "KXPARITY",
        "signal_type": "llm",
        "news_class": "publisher_rss",
    }
    canonical_row = dict(base, entry_price_cents=40.0)
    legacy_row = dict(base, market_yes_price=40.0)

    canonical_scored = score_candidate(canonical_row, min_edge=0.02)
    legacy_scored = score_candidate(legacy_row, min_edge=0.02)

    assert canonical_scored["counterfactual_pnl"] == pytest.approx(legacy_scored["counterfactual_pnl"])
    assert canonical_scored["side"] == legacy_scored["side"] == "yes"
    assert canonical_scored["would_have_traded"] is True
    assert legacy_scored["would_have_traded"] is True
    assert canonical_scored["counterfactual_pnl"] == pytest.approx(0.60)


# F-11 P1-C fallback test: scorer_forensics_audit.market_implied_win_prob
# must accept legacy ``market_yes_price`` key for pre-bounce corpus rows.
def test_market_implied_win_prob_accepts_legacy_market_yes_price_key():
    """F-11 P1-C: market_implied_win_prob() in scorer_forensics_audit reads
    price via _entry_price_cents(), which falls back to market_yes_price for
    pre-bounce rows. Regression: must not return None for legacy records.
    """
    legacy_row = {"side": "yes", "market_yes_price": 40.0}  # no entry_price_cents
    canonical_row = {"side": "yes", "entry_price_cents": 40.0}  # canonical key

    prob_legacy = market_implied_win_prob(legacy_row)
    prob_canonical = market_implied_win_prob(canonical_row)

    assert prob_legacy is not None, "legacy market_yes_price key must be accepted via fallback"
    assert prob_canonical is not None, "canonical entry_price_cents key must work"
    assert abs(prob_legacy - prob_canonical) < 1e-9, (
        "both keys must produce the same probability for identical price"
    )
