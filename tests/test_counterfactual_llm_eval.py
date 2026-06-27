from __future__ import annotations

import sqlite3
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
                "research_attempted": True,
                "research_status": "continue_researching",
                "research_queries": ["site:reuters.com Trump visits Iran"],
                "research_urls": ["https://reuters.com/world/test"],
                "research_summary": "Missing official confirmation.",
                "research_skip_reason": "missing_resolution_source",
                "llm_capture_row_id": "signal::KXVISITIRAN-26JUL01-JVAN::id-1",
                "evidence_id": "ev-1",
                "series_ticker": "KXVISITIRAN",
                "venue": "kalshi",
                "llm_latency_ms": 1500,
                "llm_total_stage_ms": 2200,
                "yes_ask_cents": 42,
                "no_ask_cents": 61,
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
    assert case["prompt_context"]["research_status"] == "continue_researching"
    assert case["prompt_context"]["research_queries"] == ["site:reuters.com Trump visits Iran"]
    assert case["prompt_context"]["research_urls"] == ["https://reuters.com/world/test"]
    assert case["retrieval_mode"] == "source_hint"
    assert case["llm_capture_row_id"] == "signal::KXVISITIRAN-26JUL01-JVAN::id-1"
    assert case["evidence_id"] == "ev-1"
    assert case["series_ticker"] == "KXVISITIRAN"
    assert case["venue"] == "kalshi"
    assert case["llm_latency_ms"] == 1500
    assert case["llm_total_stage_ms"] == 2200
    assert case["yes_ask_cents"] == 42
    assert case["no_ask_cents"] == 61


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
        return {
            "direction": "yes",
            "magnitude": "moderate",
            "confidence": 0.81,
            "estimated_probability_yes": 0.72,
        }

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
    assert ready_case["model_results"]["stronger-local"]["estimated_probability_yes"] == 0.72


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


def test_counterfactual_replay_metrics_join_resolved_pnl_and_slippage(tmp_path):
    from scripts.counterfactual_llm_eval import add_shadow_replay_metrics

    db_path = tmp_path / "paper_trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                llm_capture_row_id TEXT,
                resolved INTEGER,
                pnl_dollars REAL,
                cost_dollars REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?)",
            [
                ("signal::KXONE::id-1", 1, 7.5, 50.0),
                ("signal::KXTWO::id-2", 1, 2.5, 50.0),
                ("signal::KXTHREE::id-3", 0, None, 50.0),
            ],
        )

    report = {
        "cases": [
            {
                "ticker": "KXONE",
                "llm_capture_row_id": "signal::KXONE::id-1",
                "llm_total_stage_ms": 1000,
                "yes_ask_cents": 40,
                "executable_price_cents": 41.5,
                "research_min_retrieved_at": "2026-06-21T01:00:00Z",
                "model_results": {
                    "qwen": {
                        "direction": "yes",
                        "estimated_probability_yes": 0.65,
                        "paper_candidate_positive": True,
                    }
                },
            },
            {
                "ticker": "KXTWO",
                "llm_capture_row_id": "signal::KXTWO::id-2",
                "llm_total_stage_ms": 3000,
                "no_ask_cents": 35,
                "entry_price_cents": 36,
                "research_min_retrieved_at": "2026-06-21T01:01:00Z",
                "model_results": {
                    "qwen": {
                        "direction": "no",
                        "estimated_probability_yes": 0.30,
                        "paper_candidate_positive": True,
                    }
                },
            },
            {
                "ticker": "KXNEG",
                "llm_capture_row_id": "signal::KXNEG::id-9",
                "model_results": {
                    "qwen": {
                        "direction": "yes",
                        "estimated_probability_yes": 0.55,
                        "paper_candidate_positive": False,
                    }
                },
            },
        ]
    }

    enriched = add_shadow_replay_metrics(report, paper_trades_db=db_path)

    assert enriched["resolved_counterfactual_pnl"] == {
        "resolved_trades": 2,
        "net_pnl": 10.0,
        "deployed_capital": 100.0,
        "roi_on_deployed": 0.1,
        "join_key": "llm_capture_row_id",
    }
    assert enriched["latency_slippage_replay"]["replayed_cases"] == 2
    assert enriched["latency_slippage_replay"]["avg_net_edge_after_slippage"] == 0.2875
    assert enriched["latency_slippage_replay"]["max_slippage_cents"] == 1.5
    assert enriched["latency_slippage_replay"]["p95_latency_seconds"] >= 1.0
    assert enriched["shadow_field_completeness"] == {
        "positive_cases": 2,
        "llm_capture_row_id": 2,
        "estimated_probability_yes": 2,
        "latency": 2,
        "executable_price": 2,
        "decision_quote": 2,
        "freshness_timestamp": 2,
        "replay_ready_cases": 2,
        "freshness_ready_cases": 2,
    }


def test_counterfactual_replay_metrics_do_not_treat_price_level_as_slippage():
    from scripts.counterfactual_llm_eval import add_shadow_replay_metrics

    report = {
        "cases": [
            {
                "ticker": "KXONE",
                "llm_capture_row_id": "signal::KXONE::id-1",
                "llm_total_stage_ms": 1000,
                "yes_ask_cents": 40,
                "model_results": {
                    "qwen": {
                        "direction": "yes",
                        "estimated_probability_yes": 0.65,
                        "paper_candidate_positive": True,
                    }
                },
            },
        ]
    }

    enriched = add_shadow_replay_metrics(report)

    assert enriched["latency_slippage_replay"] == {
        "status": "missing",
        "reason": "missing_executable_or_decision_price",
    }


def test_counterfactual_replay_metrics_fail_closed_without_authoritative_fields():
    from scripts.counterfactual_llm_eval import add_shadow_replay_metrics

    report = {
        "cases": [
            {
                "ticker": "KXONE",
                "model_results": {
                    "qwen": {
                        "direction": "yes",
                        "paper_candidate_positive": True,
                    }
                },
            }
        ]
    }

    enriched = add_shadow_replay_metrics(report)

    assert enriched["resolved_counterfactual_pnl"]["status"] == "missing"
    assert enriched["latency_slippage_replay"]["status"] == "missing"
    assert enriched["shadow_field_completeness"] == {
        "positive_cases": 1,
        "llm_capture_row_id": 0,
        "estimated_probability_yes": 0,
        "latency": 0,
        "executable_price": 0,
        "decision_quote": 0,
        "freshness_timestamp": 0,
        "replay_ready_cases": 0,
        "freshness_ready_cases": 0,
    }
