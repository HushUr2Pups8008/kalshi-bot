#!/usr/bin/env python3
"""Offline eval-set builder for neutral/none no-keyword LLM false negatives."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.diagnostics_script_helpers import (
    add_exclude_test_arg,
    is_test_record_source_or_signal_source as is_test_record,
)
from utils.trade_log_reader import iter_trade_records

TARGET_CATEGORY = "post_llm_neutral_empty_keywords"
TARGET_REASON = "no_keywords"
ModelEvaluator = Callable[[dict[str, Any]], dict[str, Any]]
MarketDetailProvider = Callable[[str], Any]


def _parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _record_ts(record: dict[str, Any]) -> datetime | None:
    return _parse_ts(record.get("ts") or record.get("timestamp"))


def _target_row(record: dict[str, Any]) -> bool:
    return (
        record.get("type") == "ANALYSIS_REJECTED"
        and record.get("reason") == TARGET_REASON
        and record.get("rejection_category") == TARGET_CATEGORY
        and str(record.get("llm_direction") or "").strip().lower() == "neutral"
        and str(record.get("llm_magnitude") or "").strip().lower() == "none"
    )


def _list_field(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key)
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _prompt_context(record: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in (
        "rules_primary",
        "rules_secondary",
        "contract_terms_url",
        "retrieval_mode",
        "source_hint_domain",
        "source_hint_query",
        "source_class",
        "research_status",
        "research_summary",
        "research_skip_reason",
        "research_model_direction",
        "research_model_probability_yes",
    ):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            context[key] = value.strip()
    if isinstance(record.get("research_attempted"), bool):
        context["research_attempted"] = record["research_attempted"]
    names = _list_field(record, "settlement_source_names")
    urls = _list_field(record, "settlement_source_urls")
    research_queries = _list_field(record, "research_queries")
    research_urls = _list_field(record, "research_urls")
    if names:
        context["settlement_source_names"] = names
    if urls:
        context["settlement_source_urls"] = urls
    if research_queries:
        context["research_queries"] = research_queries
    if research_urls:
        context["research_urls"] = research_urls
    return context


def _text_attr(obj: Any, name: str) -> str:
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return value.strip() if isinstance(value, str) else ""


def _context_from_market_detail(market: Any) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("rules_primary", "rules_secondary", "contract_terms_url"):
        value = _text_attr(market, key)
        if value:
            context[key] = value

    raw_sources = (
        market.get("settlement_sources", ())
        if isinstance(market, dict)
        else getattr(market, "settlement_sources", ())
    ) or ()
    source_names: list[str] = []
    source_urls: list[str] = []
    for source in raw_sources:
        name = _text_attr(source, "name") or _text_attr(source, "label")
        url = _text_attr(source, "url")
        if name:
            source_names.append(name)
        if url:
            source_urls.append(url)
    if source_names:
        context["settlement_source_names"] = source_names
    if source_urls:
        context["settlement_source_urls"] = source_urls
    if context:
        context["context_source"] = "kalshi_market_detail"
    return context


def _context_ready(context: dict[str, Any]) -> bool:
    return bool(
        context.get("rules_primary")
        or context.get("rules_secondary")
        or context.get("settlement_source_names")
        or context.get("contract_terms_url")
    )

def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _case_optional_fields(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "llm_capture_row_id",
        "evidence_id",
        "market_ticker",
        "series_ticker",
        "venue",
        "llm_latency_ms",
        "llm_total_stage_ms",
        "llm_queue_wait_ms",
        "llm_http_round_trip_ms",
        "yes_bid_cents",
        "yes_ask_cents",
        "no_bid_cents",
        "no_ask_cents",
        "executable_price_cents",
        "entry_price_cents",
        "market_yes_price",
        "price_source",
        "price_method",
        "decision_ts",
        "research_model_probability_yes",
        "research_started_ts",
        "research_completed_ts",
        "research_duration_ms",
        "research_min_published_at",
        "research_max_published_at",
        "research_min_retrieved_at",
        "research_max_retrieved_at",
        "research_evidence_min_age_seconds",
        "research_evidence_max_age_seconds",
    )
    return {
        key: record[key]
        for key in keys
        if key in record and record.get(key) is not None
    }


def _paper_candidate_positive(verdict: dict[str, Any], min_confidence: float) -> bool:
    direction = str(verdict.get("direction") or "").strip().lower()
    magnitude = str(verdict.get("magnitude") or "").strip().lower()
    confidence = _float_or_none(verdict.get("confidence"))
    return bool(
        direction in {"yes", "no"}
        and magnitude not in {"", "none", "neutral"}
        and confidence is not None
        and confidence >= min_confidence
    )

def _normalize_model_result(
    model_name: str,
    result: dict[str, Any],
    *,
    min_confidence: float,
) -> dict[str, Any]:
    verdict = {
        "model": model_name,
        "direction": str(result.get("direction") or "").strip().lower(),
        "magnitude": str(result.get("magnitude") or "").strip().lower(),
        "confidence": _float_or_none(result.get("confidence")),
    }
    if "estimated_probability_yes" in result:
        verdict["estimated_probability_yes"] = _float_or_none(
            result.get("estimated_probability_yes")
        )
    if "raw_response" in result:
        verdict["raw_response"] = result["raw_response"]
    if "reason" in result:
        verdict["reason"] = result["reason"]
    verdict["paper_candidate_positive"] = _paper_candidate_positive(
        verdict,
        min_confidence,
    )
    return verdict

def _build_eval_prompt(case: dict[str, Any]) -> str:
    context = case.get("prompt_context")
    if not isinstance(context, dict):
        context = {}
    lines = [
        "Evaluate whether this rejected paper-trade candidate is a false negative.",
        "Return JSON only with keys: direction, magnitude, estimated_probability_yes, confidence, reason.",
        "direction must be yes, no, or neutral.",
        "magnitude must be none, weak, moderate, or strong.",
        "estimated_probability_yes is the probability the market resolves YES, not confidence.",
        "",
        f"MARKET TICKER: {case.get('ticker') or ''}",
        f"NEWS HEADLINE: {case.get('headline') or ''}",
        f"SOURCE: {case.get('source') or ''}",
        (
            "ORIGINAL MODEL VERDICT: "
            f"direction={case.get('llm_direction') or ''} "
            f"magnitude={case.get('llm_magnitude') or ''} "
            f"confidence={case.get('llm_confidence') or ''}"
        ),
    ]
    for key, label in (
        ("rules_primary", "CONTRACT RULES PRIMARY"),
        ("rules_secondary", "CONTRACT RULES SECONDARY"),
        ("contract_terms_url", "MARKET TERMS URL"),
        ("source_class", "SOURCE CLASS"),
        ("retrieval_mode", "RETRIEVAL MODE"),
        ("source_hint_domain", "SOURCE HINT DOMAIN"),
        ("source_hint_query", "SOURCE HINT QUERY"),
        ("research_status", "RESEARCH STATUS"),
        ("research_summary", "RESEARCH SUMMARY"),
        ("research_skip_reason", "RESEARCH SKIP REASON"),
        ("research_model_direction", "RESEARCH MODEL DIRECTION"),
    ):
        value = context.get(key)
        if value:
            lines.append(f"{label}: {value}")
    names = context.get("settlement_source_names")
    if isinstance(names, list) and names:
        lines.append("SETTLEMENT SOURCES: " + ", ".join(str(name) for name in names))
    urls = context.get("settlement_source_urls")
    if isinstance(urls, list) and urls:
        lines.append("SETTLEMENT SOURCE URLS: " + ", ".join(str(url) for url in urls))
    research_queries = context.get("research_queries")
    if isinstance(research_queries, list) and research_queries:
        lines.append("RESEARCH QUERIES: " + " | ".join(str(query) for query in research_queries))
    research_urls = context.get("research_urls")
    if isinstance(research_urls, list) and research_urls:
        lines.append("RESEARCH URLS: " + " | ".join(str(url) for url in research_urls))
    return "\n".join(lines)

def _parse_model_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        stripped = stripped[start : end + 1]
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON must be an object")
    parsed["raw_response"] = text
    return parsed

def make_ollama_evaluator(
    model_name: str,
    *,
    base_url: str,
    timeout_seconds: float,
) -> ModelEvaluator:
    def evaluate(case: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You audit prediction-market paper-trade false negatives.",
                },
                {"role": "user", "content": _build_eval_prompt(case)},
            ],
            "temperature": 0,
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body)
        content = parsed["choices"][0]["message"]["content"]
        return _parse_model_json(content)

    return evaluate

def run_model_eval(
    report: dict[str, Any],
    evaluators: dict[str, ModelEvaluator],
    *,
    min_confidence: float = 0.5,
) -> dict[str, Any]:
    evaluated_report = copy.deepcopy(report)
    cases = evaluated_report.get("cases")
    if not isinstance(cases, list):
        evaluated_report["model_eval_status"] = "skipped_no_cases"
        return evaluated_report

    ready_cases = [case for case in cases if case.get("eval_status") == "context_ready"]
    counts = dict(evaluated_report.get("target_counts") or {})
    counts["evaluated_context_ready"] = len(ready_cases)
    counts["skipped_missing_contract_context"] = len(cases) - len(ready_cases)
    evaluated_report["target_counts"] = counts

    if not evaluators:
        evaluated_report["model_eval_status"] = "not_run_no_model_evaluators"
        return evaluated_report
    if not ready_cases:
        evaluated_report["model_eval_status"] = "skipped_no_context_ready_cases"
        evaluated_report["model_summary"] = {}
        return evaluated_report

    summary: dict[str, dict[str, int]] = {
        name: {"evaluated": 0, "paper_candidate_positive": 0, "errors": 0}
        for name in evaluators
    }
    saw_error = False
    for case in cases:
        if case.get("eval_status") != "context_ready":
            continue
        model_results: dict[str, dict[str, Any]] = {}
        for model_name, evaluator in evaluators.items():
            summary[model_name]["evaluated"] += 1
            try:
                result = _normalize_model_result(
                    model_name,
                    evaluator(case),
                    min_confidence=min_confidence,
                )
            except Exception as exc:  # pragma: no cover - exercised by real model calls
                saw_error = True
                summary[model_name]["errors"] += 1
                result = {
                    "model": model_name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "paper_candidate_positive": False,
                }
            if result["paper_candidate_positive"]:
                summary[model_name]["paper_candidate_positive"] += 1
            model_results[model_name] = result
        case["model_results"] = model_results

    evaluated_report["model_summary"] = summary
    evaluated_report["model_eval_status"] = (
        "completed_with_errors" if saw_error else "completed"
    )
    return evaluated_report


def _positive_model_results(report: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    positives: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case in report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        model_results = case.get("model_results")
        if not isinstance(model_results, dict):
            continue
        for result in model_results.values():
            if isinstance(result, dict) and result.get("paper_candidate_positive"):
                positives.append((case, result))
    return positives


def _positive_case_key(case: dict[str, Any], fallback_index: int) -> tuple[str, str]:
    capture_id = case.get("llm_capture_row_id")
    if capture_id:
        return ("llm_capture_row_id", str(capture_id))
    fallback_parts = [
        str(case.get("ticker") or case.get("market_ticker") or ""),
        str(case.get("ts") or case.get("decision_ts") or ""),
        str(case.get("headline") or case.get("trigger_headline") or ""),
    ]
    fallback = "|".join(part for part in fallback_parts if part)
    if fallback:
        return ("case", fallback)
    return ("index", str(fallback_index))


def _result_replay_rank(case: dict[str, Any], result: dict[str, Any]) -> tuple[int, float]:
    direction = str(result.get("direction") or "").strip().lower()
    probability = _side_probability(result, direction)
    has_replay_fields = (
        probability is not None
        and _executable_side_price_cents(case) is not None
        and _decision_side_quote_cents(case, direction) is not None
    )
    return (
        1 if has_replay_fields else 0,
        probability if probability is not None else -1.0,
    )


def _unique_positive_case_results(
    report: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for index, (case, result) in enumerate(_positive_model_results(report)):
        key = _positive_case_key(case, index)
        current = selected.get(key)
        if current is None:
            selected[key] = (case, result)
            order.append(key)
            continue
        if _result_replay_rank(case, result) > _result_replay_rank(*current):
            selected[key] = (case, result)
    return [selected[key] for key in order]


def _p95_seconds(milliseconds: list[float]) -> float | None:
    if not milliseconds:
        return None
    if len(milliseconds) == 1:
        return round(milliseconds[0] / 1000.0, 4)
    return round(statistics.quantiles(sorted(milliseconds), n=20)[18] / 1000.0, 4)


def _executable_side_price_cents(case: dict[str, Any]) -> float | None:
    if "executable_price_cents" in case:
        return _float_or_none(case.get("executable_price_cents"))
    if "entry_price_cents" in case:
        return _float_or_none(case.get("entry_price_cents"))
    return None


def _decision_side_quote_cents(case: dict[str, Any], direction: str) -> float | None:
    direction = direction.lower()
    if direction == "yes":
        for key in ("yes_ask_cents", "market_yes_price"):
            price = _float_or_none(case.get(key))
            if price is not None:
                return price
    if direction == "no":
        price = _float_or_none(case.get("no_ask_cents"))
        if price is not None:
            return price
    return None


def _side_probability(result: dict[str, Any], direction: str) -> float | None:
    p_yes = _float_or_none(result.get("estimated_probability_yes"))
    if p_yes is None:
        return None
    p_yes = max(0.0, min(1.0, p_yes))
    if direction == "yes":
        return p_yes
    if direction == "no":
        return 1.0 - p_yes
    return None


def _has_latency(case: dict[str, Any]) -> bool:
    return (
        _float_or_none(case.get("llm_total_stage_ms")) is not None
        or _float_or_none(case.get("llm_latency_ms")) is not None
    )


def _has_freshness_timestamp(case: dict[str, Any]) -> bool:
    return any(
        case.get(key) is not None
        for key in (
            "research_min_published_at",
            "research_max_published_at",
            "research_min_retrieved_at",
            "research_max_retrieved_at",
            "research_evidence_min_age_seconds",
            "research_evidence_max_age_seconds",
        )
    )


def _shadow_field_completeness(report: dict[str, Any]) -> dict[str, int]:
    counts = {
        "positive_cases": 0,
        "llm_capture_row_id": 0,
        "estimated_probability_yes": 0,
        "latency": 0,
        "executable_price": 0,
        "decision_quote": 0,
        "freshness_timestamp": 0,
        "replay_ready_cases": 0,
        "freshness_ready_cases": 0,
    }
    for case, result in _unique_positive_case_results(report):
        counts["positive_cases"] += 1
        direction = str(result.get("direction") or "").strip().lower()
        has_capture = bool(case.get("llm_capture_row_id"))
        has_probability = _side_probability(result, direction) is not None
        has_latency = _has_latency(case)
        has_executable_price = _executable_side_price_cents(case) is not None
        has_decision_quote = _decision_side_quote_cents(case, direction) is not None
        has_freshness = _has_freshness_timestamp(case)

        if has_capture:
            counts["llm_capture_row_id"] += 1
        if has_probability:
            counts["estimated_probability_yes"] += 1
        if has_latency:
            counts["latency"] += 1
        if has_executable_price:
            counts["executable_price"] += 1
        if has_decision_quote:
            counts["decision_quote"] += 1
        if has_freshness:
            counts["freshness_timestamp"] += 1
        if (
            has_capture
            and has_probability
            and has_latency
            and has_executable_price
            and has_decision_quote
        ):
            counts["replay_ready_cases"] += 1
        if has_freshness:
            counts["freshness_ready_cases"] += 1
    return counts


def _latency_slippage_replay(report: dict[str, Any]) -> dict[str, Any]:
    latencies_ms: list[float] = []
    edges: list[float] = []
    slippages: list[float] = []
    skipped_missing_prices = 0
    for case, result in _unique_positive_case_results(report):
        latency = _float_or_none(case.get("llm_total_stage_ms")) or _float_or_none(
            case.get("llm_latency_ms")
        )
        if latency is not None:
            latencies_ms.append(latency)
        direction = str(result.get("direction") or "").strip().lower()
        executable_cents = _executable_side_price_cents(case)
        decision_quote_cents = _decision_side_quote_cents(case, direction)
        probability = _side_probability(result, direction)
        if executable_cents is None or decision_quote_cents is None:
            skipped_missing_prices += 1
            continue
        if probability is None:
            continue
        slippages.append(max(0.0, executable_cents - decision_quote_cents))
        edges.append(probability - (executable_cents / 100.0))
    if skipped_missing_prices and not edges:
        return {
            "status": "missing",
            "reason": "missing_executable_or_decision_price",
        }
    if not edges or not latencies_ms:
        return {"status": "missing"}
    return {
        "status": "computed",
        "replayed_cases": len(edges),
        "p95_latency_seconds": _p95_seconds(latencies_ms),
        "avg_net_edge_after_slippage": round(sum(edges) / len(edges), 6),
        "avg_slippage_cents": round(sum(slippages) / len(slippages), 4),
        "max_slippage_cents": round(max(slippages), 4),
    }


def _resolved_counterfactual_pnl(
    report: dict[str, Any],
    paper_trades_db: str | Path | None,
) -> dict[str, Any]:
    if paper_trades_db is None:
        return {"status": "missing"}
    keys = [
        str(case.get("llm_capture_row_id"))
        for case, _result in _unique_positive_case_results(report)
        if case.get("llm_capture_row_id")
    ]
    if not keys:
        return {"status": "missing", "join_key": "llm_capture_row_id"}
    placeholders = ",".join("?" for _ in keys)
    try:
        with sqlite3.connect(paper_trades_db) as conn:
            rows = conn.execute(
                f"""
                SELECT pnl_dollars, cost_dollars
                FROM paper_trades
                WHERE resolved = 1
                  AND llm_capture_row_id IN ({placeholders})
                  AND pnl_dollars IS NOT NULL
                  AND cost_dollars IS NOT NULL
                """,
                keys,
            ).fetchall()
    except sqlite3.Error:
        return {"status": "missing", "join_key": "llm_capture_row_id"}
    if not rows:
        return {"status": "missing", "join_key": "llm_capture_row_id"}
    net_pnl = sum(float(row[0]) for row in rows)
    deployed = sum(float(row[1]) for row in rows)
    return {
        "resolved_trades": len(rows),
        "net_pnl": round(net_pnl, 6),
        "deployed_capital": round(deployed, 6),
        "roi_on_deployed": round(net_pnl / deployed, 6) if deployed > 0 else 0.0,
        "join_key": "llm_capture_row_id",
    }


def add_shadow_replay_metrics(
    report: dict[str, Any],
    *,
    paper_trades_db: str | Path | None = None,
) -> dict[str, Any]:
    enriched = copy.deepcopy(report)
    enriched["resolved_counterfactual_pnl"] = _resolved_counterfactual_pnl(
        enriched,
        paper_trades_db,
    )
    enriched["latency_slippage_replay"] = _latency_slippage_replay(enriched)
    enriched["shadow_field_completeness"] = _shadow_field_completeness(enriched)
    return enriched


def build_eval_report(
    path: str | Path,
    *,
    since: str | datetime,
    until: str | datetime | None = None,
    exclude_test: bool = False,
    limit: int | None = None,
    ticker: str | None = None,
    market_detail_provider: MarketDetailProvider | None = None,
) -> dict[str, Any]:
    since_dt = _parse_ts(since)
    until_dt = _parse_ts(until)
    if since_dt is None:
        raise ValueError("since is required")

    cases: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    market_detail_cache: dict[str, Any] = {}
    for record in iter_trade_records(Path(path), since=since_dt, until=until_dt):
        if exclude_test and is_test_record(record):
            continue
        if ticker and str(record.get("ticker") or "") != ticker:
            continue
        ts = _record_ts(record)
        if ts is None or not _target_row(record):
            continue
        context = _prompt_context(record)
        hydrated = False
        if not _context_ready(context) and market_detail_provider:
            record_ticker = str(record.get("ticker") or "").strip()
            if record_ticker:
                if record_ticker not in market_detail_cache:
                    market_detail_cache[record_ticker] = market_detail_provider(
                        record_ticker
                    )
                market_context = _context_from_market_detail(
                    market_detail_cache[record_ticker]
                )
                if market_context:
                    context = {**context, **market_context}
                    hydrated = True
        ready = _context_ready(context)
        counts["neutral_none_no_keywords"] += 1
        counts["context_ready" if ready else "missing_contract_context"] += 1
        if hydrated and ready:
            counts["hydrated_contract_context"] += 1
        case = {
            "ts": _iso(ts),
            "ticker": record.get("ticker"),
            "source": record.get("source"),
            "headline": record.get("headline"),
            "llm_direction": record.get("llm_direction"),
            "llm_magnitude": record.get("llm_magnitude"),
            "llm_confidence": record.get("llm_confidence"),
            "retrieval_mode": context.get("retrieval_mode"),
            "eval_status": "context_ready" if ready else "missing_contract_context",
            "prompt_context": context,
        }
        case.update(_case_optional_fields(record))
        cases.append(case)
        if limit is not None and len(cases) >= limit:
            break

    for key in ("neutral_none_no_keywords", "context_ready", "missing_contract_context"):
        counts.setdefault(key, 0)
    if market_detail_provider:
        counts.setdefault("hydrated_contract_context", 0)
    return {
        "window": {"since": _iso(since_dt), "until": _iso(until_dt)},
        "target_counts": dict(counts),
        "cases": cases,
        "model_eval_status": "not_run_offline_eval_set_only",
    }


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Counterfactual LLM eval set",
        f"Window: {report['window']['since']} -> {report['window']['until'] or 'open'}",
        f"Model eval status: {report['model_eval_status']}",
        "Target counts:",
    ]
    for key, count in report["target_counts"].items():
        lines.append(f"  {key}: {count}")
    if report["cases"]:
        lines.append("Cases:")
    for case in report["cases"]:
        lines.append(
            f"  {case['ts']} {case['ticker']} "
            f"status={case['eval_status']} retrieval={case.get('retrieval_mode') or 'unknown'}"
        )
        model_results = case.get("model_results")
        if isinstance(model_results, dict):
            for model_name, result in model_results.items():
                if "error" in result:
                    lines.append(f"    {model_name}: error={result['error']}")
                    continue
                lines.append(
                    f"    {model_name}: direction={result.get('direction')} "
                    f"magnitude={result.get('magnitude')} "
                    f"confidence={result.get('confidence')} "
                    f"paper_candidate_positive={result.get('paper_candidate_positive')}"
                )
    model_summary = report.get("model_summary")
    if isinstance(model_summary, dict) and model_summary:
        lines.append("Model summary:")
        for model_name, summary in model_summary.items():
            lines.append(
                f"  {model_name}: evaluated={summary.get('evaluated', 0)} "
                f"paper_candidate_positive={summary.get('paper_candidate_positive', 0)} "
                f"errors={summary.get('errors', 0)}"
            )
    return "\n".join(lines) + "\n"


def _argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    parser.add_argument("--ticker")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--eval-ollama-model",
        action="append",
        default=[],
        help="Evaluate context-ready cases with an Ollama OpenAI-compatible model.",
    )
    parser.add_argument(
        "--ollama-base-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )
    parser.add_argument(
        "--ollama-timeout-seconds",
        type=float,
        default=float(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "60")),
    )
    parser.add_argument("--eval-min-confidence", type=float, default=0.5)
    parser.add_argument(
        "--hydrate-kalshi-market-details",
        action="store_true",
        help="Fetch read-only Kalshi market details to enrich historical rows.",
    )
    parser.add_argument(
        "--paper-trades-db",
        type=Path,
        help=(
            "Optional paper_trades.db path; when provided, attach resolved "
            "counterfactual P&L and latency/slippage replay metrics."
        ),
    )
    add_exclude_test_arg(parser, help_text="Exclude synthetic/test records")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argparser().parse_args(argv)
    market_detail_provider = None
    if args.hydrate_kalshi_market_details:
        from kalshi.rest_client import KalshiRestClient

        client = KalshiRestClient()
        market_detail_provider = client.get_market
    report = build_eval_report(
        args.path,
        since=args.since,
        until=args.until,
        exclude_test=args.exclude_test,
        limit=args.limit,
        ticker=args.ticker,
        market_detail_provider=market_detail_provider,
    )
    if args.eval_ollama_model:
        evaluators = {
            model: make_ollama_evaluator(
                model,
                base_url=args.ollama_base_url,
                timeout_seconds=args.ollama_timeout_seconds,
            )
            for model in args.eval_ollama_model
        }
        report = run_model_eval(
            report,
            evaluators,
            min_confidence=args.eval_min_confidence,
        )
    if args.paper_trades_db:
        report = add_shadow_replay_metrics(
            report,
            paper_trades_db=args.paper_trades_db,
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
