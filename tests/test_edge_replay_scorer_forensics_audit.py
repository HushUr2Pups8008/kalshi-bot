import json

from scripts.edge_replay.scorer_forensics_audit import (
    apply_episode_gate,
    audit,
    market_implied_win_prob,
    price_unit_audit,
    summarize_trade_set,
)
from scripts.edge_replay.score_counterfactual_pnl import score_candidate


def test_market_implied_win_prob_uses_price_not_coin_flip():
    yes_longshot = {"side": "yes", "market_yes_price": 1.0}
    no_longshot = {"side": "no", "market_yes_price": 1.0}

    assert market_implied_win_prob(yes_longshot) == 0.01
    assert market_implied_win_prob(no_longshot) == 0.99


def test_price_unit_audit_recognizes_yes_price_dollars_snippets(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps({"KX": [{"ts": "2026-05-01T00:00:00Z", "yes_price": 1.0, "source": "live_trades"}]}),
        encoding="utf-8",
    )
    endpoint = tmp_path / "endpoint.json"
    endpoint.write_text(
        json.dumps(
            {
                "probes": [
                    {
                        "ticker": "KX",
                        "variant": "documented_live_trades",
                        "endpoint": "/markets/trades",
                        "body_snippet": '{"trades":[{"yes_price_dollars":"0.0100"}]}',
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = price_unit_audit(prices, endpoint)

    assert report["verdict"] == "cents_consistent"
    assert report["endpoint_snippet_count_with_yes_price_dollars"] == 1
    assert report["source_summary"]["live_trades"]["min"] == 1.0


def test_episode_gate_applies_readiness_price_sanity_and_cooldown():
    base = {
        "ticker": "KX",
        "decision_kind": "dossier_update",
        "resolved_yes": False,
        "model_prob": 0.80,
        "confidence": 0.90,
        "market_yes_price": 20.0,
        "edge": 0.60,
        "signal_source": "source",
        "series_ticker": "KX",
        "signal_type": "state",
        "news_class": "news",
    }
    scored = [
        score_candidate({**base, "decision_ts": "2026-05-01T00:00:00+00:00"}, readiness_confidence=0.05),
        score_candidate({**base, "decision_ts": "2026-05-01T01:00:00+00:00"}, readiness_confidence=0.05),
        score_candidate(
            {**base, "ticker": "KXLOW", "decision_ts": "2026-05-01T00:00:00+00:00", "market_yes_price": 1.0},
            readiness_confidence=0.05,
        ),
    ]

    _, rows, skipped = apply_episode_gate(
        scored,
        require_readiness=True,
        require_price_sanity=True,
        require_signed_edge=True,
    )

    assert sum(1 for row in rows if row["would_have_traded"]) == 1
    assert skipped["paper_ticker_cooldown"] == 1
    assert skipped["paper_price_sanity"] == 1


def test_audit_outputs_production_proxy_without_ic16_slice(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    rows = []
    for index in range(12):
        rows.append(
            {
                "ticker": f"KX{index}",
                "decision_ts": f"2026-05-01T00:{index:02d}:00+00:00",
                "decision_kind": "dossier_update",
                "resolved_yes": False,
                "model_prob": 0.80,
                "confidence": 0.90,
                "market_yes_price": 20.0,
                "edge": 0.60,
                "signal_source": "source",
                "series_ticker": "KX",
                "signal_type": "state",
                "news_class": "news",
            }
        )
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"KX": [{"ts": "2026-05-01T00:00:00Z", "yes_price": 20.0}]}), encoding="utf-8")
    endpoint = tmp_path / "endpoint.json"
    endpoint.write_text(json.dumps({"probes": []}), encoding="utf-8")

    report, corrected = audit(dataset, prices, endpoint)

    assert report["verdict"]["production_proxy_trades"] == 12
    assert report["verdict"]["production_proxy_ic16_accepted_slices"] == 0
    assert corrected["forensics"]["accepted_ic16_slices"] == 0


def test_summarize_trade_set_reports_market_expected_wins():
    rows = [
        {
            "would_have_traded": True,
            "would_have_won": False,
            "side": "yes",
            "market_yes_price": 1.0,
            "counterfactual_pnl": -0.01,
        }
        for _ in range(10)
    ]

    summary = summarize_trade_set("longshots", rows)

    assert summary["trades"] == 10
    assert summary["wins"] == 0
    assert summary["market_implied_expected_wins"] == 0.1
