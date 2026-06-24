from __future__ import annotations

from types import SimpleNamespace

from kalshi.series_metadata import SettlementSource
from tests._helpers import write_jsonl


def test_counterfactual_llm_eval_extracts_neutral_none_no_keyword_context_ready_rows(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "Reuters",
                "headline": "Talks continue before possible visit",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
                "llm_confidence": 0.82,
                "rules_primary": "Market resolves Yes if Trump visits Iran by July 1.",
                "rules_secondary": "Visits by other officials do not count.",
                "settlement_source_names": ["Reuters", "Associated Press"],
                "settlement_source_urls": ["https://reuters.com", "https://apnews.com"],
                "contract_terms_url": "https://kalshi.com/markets/KXVISITIRAN",
                "retrieval_mode": "source_hint",
                "source_hint_domain": "reuters.com",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:01:00Z",
                "ticker": "KXOTHER",
                "source": "Reuters",
                "headline": "Actionable LLM signal without keywords",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "yes",
                "llm_magnitude": "moderate",
            },
        ],
    )

    report = build_eval_report(path, since="2026-06-21T00:00:00Z")

    assert report["target_counts"] == {
        "neutral_none_no_keywords": 1,
        "context_ready": 1,
        "missing_contract_context": 0,
    }
    case = report["cases"][0]
    assert case["eval_status"] == "context_ready"
    assert case["prompt_context"]["rules_primary"].startswith("Market resolves Yes")
    assert case["prompt_context"]["settlement_source_names"] == ["Reuters", "Associated Press"]
    assert case["retrieval_mode"] == "source_hint"


def test_counterfactual_llm_eval_marks_historical_rows_missing_contract_context(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report, format_text_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "The Washington Post",
                "headline": "Talks continue before possible visit",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
                "llm_confidence": 0.9,
            },
        ],
    )

    report = build_eval_report(path, since="2026-06-21T00:00:00Z")

    assert report["target_counts"] == {
        "neutral_none_no_keywords": 1,
        "context_ready": 0,
        "missing_contract_context": 1,
    }
    assert report["cases"][0]["eval_status"] == "missing_contract_context"
    text = format_text_report(report)
    assert "missing_contract_context: 1" in text


def test_counterfactual_llm_eval_can_hydrate_historical_rows_from_market_details(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "The Washington Post",
                "headline": "Vance arrives near Iran talks before possible visit",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
                "llm_confidence": 0.9,
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:05:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "Reuters",
                "headline": "Second row same market",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
            },
        ],
    )
    calls: list[str] = []

    def market_detail_provider(ticker):
        calls.append(ticker)
        return SimpleNamespace(
            rules_primary="Market resolves Yes if JD Vance visits Iran before Jul 1.",
            rules_secondary="Official settlement follows listed sources.",
            contract_terms_url="https://kalshi.com/markets/KXVISITIRAN",
            settlement_sources=(
                SettlementSource(label="Reuters", url="https://www.reuters.com/world/"),
            ),
        )

    report = build_eval_report(
        path,
        since="2026-06-21T00:00:00Z",
        market_detail_provider=market_detail_provider,
    )

    assert report["target_counts"] == {
        "neutral_none_no_keywords": 2,
        "context_ready": 2,
        "missing_contract_context": 0,
        "hydrated_contract_context": 2,
    }
    assert calls == ["KXVISITIRAN-26JUL01-JVAN"]
    assert report["cases"][0]["eval_status"] == "context_ready"
    assert report["cases"][0]["prompt_context"]["context_source"] == "kalshi_market_detail"
    assert report["cases"][0]["prompt_context"]["settlement_source_names"] == ["Reuters"]


def test_counterfactual_llm_eval_leaves_row_missing_when_market_detail_has_no_context(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "The Washington Post",
                "headline": "Vance arrives near Iran talks before possible visit",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
            },
        ],
    )

    report = build_eval_report(
        path,
        since="2026-06-21T00:00:00Z",
        market_detail_provider=lambda ticker: SimpleNamespace(),
    )

    assert report["target_counts"] == {
        "neutral_none_no_keywords": 1,
        "context_ready": 0,
        "missing_contract_context": 1,
        "hydrated_contract_context": 0,
    }
    assert report["cases"][0]["eval_status"] == "missing_contract_context"


def test_counterfactual_model_eval_compares_context_ready_cases_only(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report, run_model_eval

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "Reuters",
                "headline": "Vance plans Tehran visit before deadline",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
                "llm_confidence": 0.82,
                "rules_primary": "Market resolves Yes if JD Vance visits Iran before Jul 1.",
                "settlement_source_names": ["Reuters"],
                "settlement_source_urls": ["https://www.reuters.com/world/"],
                "contract_terms_url": "https://kalshi.com/markets/KXVISITIRAN",
            },
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:05:00Z",
                "ticker": "KXTRUMPUAP-26MAY-JUL01",
                "source": "The Guardian",
                "headline": "Historical row without enriched context",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
            },
        ],
    )
    calls: list[tuple[str, str]] = []

    def qwen(case):
        calls.append(("qwen2.5:7b", case["ticker"]))
        return {"direction": "neutral", "magnitude": "none", "confidence": 0.74}

    def stronger(case):
        calls.append(("stronger-local", case["ticker"]))
        return {"direction": "yes", "magnitude": "moderate", "confidence": 0.81}

    report = build_eval_report(path, since="2026-06-21T00:00:00Z")
    evaluated = run_model_eval(
        report,
        {
            "qwen2.5:7b": qwen,
            "stronger-local": stronger,
        },
    )

    assert evaluated["model_eval_status"] == "completed"
    assert evaluated["target_counts"]["evaluated_context_ready"] == 1
    assert evaluated["target_counts"]["skipped_missing_contract_context"] == 1
    assert calls == [
        ("qwen2.5:7b", "KXVISITIRAN-26JUL01-JVAN"),
        ("stronger-local", "KXVISITIRAN-26JUL01-JVAN"),
    ]
    ready_case = evaluated["cases"][0]
    assert ready_case["model_results"]["qwen2.5:7b"]["paper_candidate_positive"] is False
    assert ready_case["model_results"]["stronger-local"]["paper_candidate_positive"] is True
    assert ready_case["model_results"]["stronger-local"]["direction"] == "yes"


def test_counterfactual_model_eval_skips_without_context_ready_cases(tmp_path):
    from scripts.counterfactual_llm_eval import build_eval_report, run_model_eval

    path = tmp_path / "trades.jsonl"
    write_jsonl(
        path,
        [
            {
                "type": "ANALYSIS_REJECTED",
                "ts": "2026-06-21T01:00:00Z",
                "ticker": "KXVISITIRAN-26JUL01-JVAN",
                "source": "The Washington Post",
                "headline": "Vance arrives near Iran talks",
                "reason": "no_keywords",
                "rejection_category": "post_llm_neutral_empty_keywords",
                "llm_direction": "neutral",
                "llm_magnitude": "none",
            },
        ],
    )
    calls: list[str] = []

    def evaluator(case):
        calls.append(case["ticker"])
        return {"direction": "yes", "magnitude": "moderate", "confidence": 0.8}

    report = build_eval_report(path, since="2026-06-21T00:00:00Z")
    evaluated = run_model_eval(report, {"stronger-local": evaluator})

    assert evaluated["model_eval_status"] == "skipped_no_context_ready_cases"
    assert evaluated["target_counts"]["evaluated_context_ready"] == 0
    assert calls == []
