#!/usr/bin/env python3
"""Assess the offline deep-research shadow experiment output.

Input is the JSON emitted by ``scripts.counterfactual_llm_eval`` after model
evaluation. This scorer is intentionally conservative: model-positive rescued
rows are only a shadow signal until resolved counterfactual P&L and latency
slippage are measured.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolved_pnl_metrics(report: dict[str, Any]) -> dict[str, Any]:
    raw = report.get("resolved_counterfactual_pnl")
    if not isinstance(raw, dict):
        return {"status": "missing"}
    resolved = _safe_int(raw.get("resolved_trades"))
    net_pnl = _safe_float(raw.get("net_pnl"))
    roi = _safe_float(raw.get("roi_on_deployed"))
    return {
        "status": "pass" if resolved >= 10 and net_pnl > 0.0 and roi > 0.0 else "fail",
        "resolved_trades": resolved,
        "net_pnl": net_pnl,
        "roi_on_deployed": roi,
    }


def _latency_slippage_metrics(report: dict[str, Any]) -> dict[str, Any]:
    raw = report.get("latency_slippage_replay")
    if not isinstance(raw, dict):
        return {"status": "missing"}
    replayed = _safe_int(raw.get("replayed_cases"))
    p95_latency = _safe_float(raw.get("p95_latency_seconds"))
    edge_after_slippage = _safe_float(raw.get("avg_net_edge_after_slippage"))
    max_slippage_cents = _safe_float(raw.get("max_slippage_cents"))
    return {
        "status": (
            "pass"
            if replayed >= 10
            and 0.0 < p95_latency <= 12.0
            and edge_after_slippage > 0.0
            and max_slippage_cents <= 2.0
            else "fail"
        ),
        "replayed_cases": replayed,
        "p95_latency_seconds": p95_latency,
        "avg_net_edge_after_slippage": edge_after_slippage,
        "max_slippage_cents": max_slippage_cents,
    }


def _positive_cases(report: dict[str, Any]) -> list[dict[str, Any]]:
    positives: list[dict[str, Any]] = []
    for case in report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        model_results = case.get("model_results")
        if not isinstance(model_results, dict):
            continue
        for model_name, result in model_results.items():
            if not isinstance(result, dict) or not result.get("paper_candidate_positive"):
                continue
            positives.append(
                {
                    "model": model_name,
                    "ts": case.get("ts"),
                    "ticker": case.get("ticker"),
                    "source": case.get("source"),
                    "headline": case.get("headline"),
                    "direction": result.get("direction"),
                    "magnitude": result.get("magnitude"),
                    "confidence": result.get("confidence"),
                }
            )
    positives.sort(
        key=lambda row: (
            str(row.get("model") or ""),
            str(row.get("ticker") or ""),
            str(row.get("ts") or ""),
        )
    )
    return positives


def _model_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for model_name, summary in (report.get("model_summary") or {}).items():
        if not isinstance(summary, dict):
            continue
        evaluated = _safe_int(summary.get("evaluated"))
        positives = _safe_int(summary.get("paper_candidate_positive"))
        errors = _safe_int(summary.get("errors"))
        rows[str(model_name)] = {
            "evaluated": evaluated,
            "paper_candidate_positive": positives,
            "errors": errors,
            "positive_rate": (positives / evaluated) if evaluated else 0.0,
        }
    return rows


def assess_report(report: dict[str, Any]) -> dict[str, Any]:
    counts = report.get("target_counts") or {}
    models = _model_rows(report)
    evaluated_cases = sum(row["evaluated"] for row in models.values())
    positive_cases = _positive_cases(report)
    positive_total = len(positive_cases)
    error_total = sum(row["errors"] for row in models.values())
    resolved_pnl = _resolved_pnl_metrics(report)
    latency_slippage = _latency_slippage_metrics(report)

    risk_flags: list[str] = []
    if resolved_pnl["status"] == "missing":
        risk_flags.append("missing_resolved_counterfactual_pnl")
    elif resolved_pnl["status"] != "pass":
        risk_flags.append("counterfactual_pnl_not_profitable")
    if latency_slippage["status"] == "missing":
        risk_flags.append("missing_latency_slippage_replay")
    elif latency_slippage["status"] != "pass":
        risk_flags.append("latency_slippage_replay_not_safe")
    live_trade_ready = False
    if evaluated_cases <= 0:
        verdict = "NO_EVALUATED_CASES"
        recommendation = "Do not implement live blocking research; no evaluated shadow cases."
    elif positive_total <= 0:
        verdict = "NO_PROFIT_SIGNAL"
        recommendation = "Do not implement live blocking research; no model-positive rescue cases."
    elif error_total > 0:
        verdict = "SHADOW_INCONCLUSIVE_ERRORS"
        recommendation = "Repeat shadow evaluation before any implementation; model errors make this run inconclusive."
    elif risk_flags:
        verdict = "SHADOW_PROMISING_NO_LIVE"
        recommendation = (
            "Continue shadow/replay only. Require profitable resolved "
            "counterfactual P&L and latency/slippage replay before production."
        )
    else:
        verdict = "SHADOW_PROFIT_READY"
        live_trade_ready = True
        recommendation = (
            "Eligible for guarded production rollout after operator review; "
            "keep capital caps and post-deploy monitoring active."
        )

    sources = Counter(str(row.get("source") or "UNKNOWN") for row in positive_cases)
    tickers = Counter(str(row.get("ticker") or "UNKNOWN") for row in positive_cases)
    return {
        "verdict": verdict,
        "recommendation": recommendation,
        "live_trade_ready": live_trade_ready,
        "model_eval_status": report.get("model_eval_status"),
        "target_counts": counts,
        "evaluated_cases": evaluated_cases,
        "positive_cases": positive_total,
        "error_cases": error_total,
        "models": models,
        "top_positive_sources": sources.most_common(10),
        "top_positive_tickers": tickers.most_common(10),
        "top_positive_cases": positive_cases[:20],
        "resolved_counterfactual_pnl": resolved_pnl,
        "latency_slippage_replay": latency_slippage,
        "risk_flags": risk_flags,
    }


def format_text(assessment: dict[str, Any]) -> str:
    lines = [
        "Deep Research Shadow Experiment Assessment",
        f"Verdict: {assessment['verdict']}",
        f"Recommendation: {assessment['recommendation']}",
        f"Live trade ready: {assessment['live_trade_ready']}",
        f"Evaluated cases: {assessment['evaluated_cases']}",
        f"Positive cases: {assessment['positive_cases']}",
        f"Error cases: {assessment['error_cases']}",
        "Resolved counterfactual P&L: "
        + json.dumps(assessment["resolved_counterfactual_pnl"], sort_keys=True),
        "Latency/slippage replay: "
        + json.dumps(assessment["latency_slippage_replay"], sort_keys=True),
        "Models:",
    ]
    for model_name, row in assessment["models"].items():
        lines.append(
            "  "
            f"{model_name}: evaluated={row['evaluated']} "
            f"positive={row['paper_candidate_positive']} "
            f"positive_rate={row['positive_rate']:.1%} "
            f"errors={row['errors']}"
        )
    if assessment["top_positive_sources"]:
        lines.append("Top positive sources:")
        for source, count in assessment["top_positive_sources"]:
            lines.append(f"  {source}: {count}")
    if assessment["top_positive_cases"]:
        lines.append("Top positive cases:")
        for row in assessment["top_positive_cases"]:
            lines.append(
                "  "
                f"{row.get('ts') or 'n/a'} {row.get('ticker') or 'n/a'} "
                f"source={row.get('source') or 'n/a'} "
                f"{row.get('direction') or 'n/a'}/{row.get('magnitude') or 'n/a'} "
                f"conf={row.get('confidence')}"
            )
    lines.append("Risk flags: " + ", ".join(assessment["risk_flags"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    assessment = assess_report(report)
    if args.json:
        print(json.dumps(assessment, indent=2, sort_keys=True))
    else:
        print(format_text(assessment), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
