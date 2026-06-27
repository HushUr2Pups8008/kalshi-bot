from __future__ import annotations

import json


def test_assessment_marks_positive_cases_as_shadow_only(tmp_path):
    from scripts.deep_research_shadow_assessment import assess_report

    report = {
        "target_counts": {
            "neutral_none_no_keywords": 10,
            "context_ready": 10,
            "evaluated_context_ready": 10,
        },
        "model_eval_status": "completed",
        "model_summary": {
            "qwen2.5:7b": {
                "evaluated": 10,
                "paper_candidate_positive": 2,
                "errors": 0,
            }
        },
        "cases": [
            {
                "ticker": "KXONE",
                "source": "Reuters",
                "model_results": {
                    "qwen2.5:7b": {
                        "direction": "yes",
                        "magnitude": "moderate",
                        "confidence": 0.72,
                        "paper_candidate_positive": True,
                    }
                },
            },
            {
                "ticker": "KXTWO",
                "source": "AP",
                "model_results": {
                    "qwen2.5:7b": {
                        "direction": "neutral",
                        "magnitude": "none",
                        "confidence": 0.84,
                        "paper_candidate_positive": False,
                    }
                },
            },
        ],
    }

    assessment = assess_report(report)

    assert assessment["verdict"] == "SHADOW_PROMISING_NO_LIVE"
    assert assessment["live_trade_ready"] is False
    assert assessment["models"]["qwen2.5:7b"]["positive_rate"] == 0.2
    assert assessment["risk_flags"] == ["missing_resolved_counterfactual_pnl", "missing_latency_slippage_replay"]
    assert assessment["top_positive_cases"][0]["ticker"] == "KXONE"


def test_assessment_rejects_zero_positive_experiment():
    from scripts.deep_research_shadow_assessment import assess_report

    report = {
        "target_counts": {"neutral_none_no_keywords": 4, "context_ready": 4},
        "model_eval_status": "completed",
        "model_summary": {"qwen2.5:7b": {"evaluated": 4, "paper_candidate_positive": 0, "errors": 0}},
        "cases": [],
    }

    assessment = assess_report(report)

    assert assessment["verdict"] == "NO_PROFIT_SIGNAL"
    assert assessment["recommendation"] == "Do not implement live blocking research; no model-positive rescue cases."


def test_assessment_can_graduate_after_profitable_resolved_and_latency_replay():
    from scripts.deep_research_shadow_assessment import assess_report

    report = {
        "target_counts": {"neutral_none_no_keywords": 20, "context_ready": 20},
        "model_eval_status": "completed",
        "model_summary": {
            "qwen2.5:7b": {
                "evaluated": 20,
                "paper_candidate_positive": 12,
                "errors": 0,
            }
        },
        "resolved_counterfactual_pnl": {
            "resolved_trades": 10,
            "net_pnl": 42.5,
            "roi_on_deployed": 0.071,
        },
        "latency_slippage_replay": {
            "replayed_cases": 10,
            "p95_latency_seconds": 4.2,
            "avg_net_edge_after_slippage": 0.034,
            "max_slippage_cents": 1.5,
        },
        "cases": [
            {
                "ticker": "KXONE",
                "source": "Reuters",
                "model_results": {
                    "qwen2.5:7b": {
                        "direction": "yes",
                        "magnitude": "moderate",
                        "confidence": 0.72,
                        "paper_candidate_positive": True,
                    }
                },
            }
        ],
    }

    assessment = assess_report(report)

    assert assessment["verdict"] == "SHADOW_PROFIT_READY"
    assert assessment["live_trade_ready"] is True
    assert assessment["risk_flags"] == []
    assert assessment["resolved_counterfactual_pnl"]["status"] == "pass"
    assert assessment["latency_slippage_replay"]["status"] == "pass"


def test_assessment_blocks_bad_resolved_or_slippage_metrics():
    from scripts.deep_research_shadow_assessment import assess_report

    report = {
        "target_counts": {"neutral_none_no_keywords": 20, "context_ready": 20},
        "model_eval_status": "completed",
        "model_summary": {
            "qwen2.5:7b": {
                "evaluated": 20,
                "paper_candidate_positive": 12,
                "errors": 0,
            }
        },
        "resolved_counterfactual_pnl": {
            "resolved_trades": 10,
            "net_pnl": -1.0,
            "roi_on_deployed": -0.002,
        },
        "latency_slippage_replay": {
            "replayed_cases": 10,
            "p95_latency_seconds": 30.0,
            "avg_net_edge_after_slippage": -0.01,
            "max_slippage_cents": 4.0,
        },
        "cases": [
            {
                "ticker": "KXONE",
                "source": "Reuters",
                "model_results": {
                    "qwen2.5:7b": {
                        "direction": "yes",
                        "magnitude": "moderate",
                        "confidence": 0.72,
                        "paper_candidate_positive": True,
                    }
                },
            }
        ],
    }

    assessment = assess_report(report)

    assert assessment["verdict"] == "SHADOW_PROMISING_NO_LIVE"
    assert assessment["live_trade_ready"] is False
    assert assessment["risk_flags"] == [
        "counterfactual_pnl_not_profitable",
        "latency_slippage_replay_not_safe",
    ]


def test_cli_writes_json_assessment(tmp_path, capsys):
    from scripts.deep_research_shadow_assessment import main

    report_path = tmp_path / "eval.json"
    report_path.write_text(
        json.dumps(
            {
                "target_counts": {"neutral_none_no_keywords": 1, "context_ready": 1},
                "model_eval_status": "completed",
                "model_summary": {"qwen2.5:7b": {"evaluated": 1, "paper_candidate_positive": 0, "errors": 0}},
                "cases": [],
            }
        ),
        encoding="utf-8",
    )

    assert main([str(report_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["evaluated_cases"] == 1
    assert out["verdict"] == "NO_PROFIT_SIGNAL"
