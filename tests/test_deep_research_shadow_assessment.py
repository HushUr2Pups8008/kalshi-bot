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
