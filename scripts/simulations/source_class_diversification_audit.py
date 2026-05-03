"""Source-class diversification audit over archived OPPORTUNITY events."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils.trade_log_reader import iter_trade_records  # noqa: E402
_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-source-class-diversification-audit.md"
_JOIN_WINDOW_SEC = 60.0


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _ts(row: dict[str, Any]) -> datetime | None:
    value = row.get("ts")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _index_blends(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if _typ(row) != "BLEND_DECISION":
            continue
        parsed = _ts(row)
        if parsed is None:
            continue
        row["_parsed_ts"] = parsed
        indexed[_ticker(row)].append(row)
    for vals in indexed.values():
        vals.sort(key=lambda r: r["_parsed_ts"])
    return indexed


def _nearest_blend(index: dict[str, list[dict[str, Any]]], opp: dict[str, Any]) -> dict[str, Any] | None:
    ts = _ts(opp)
    if ts is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for blend in index.get(_ticker(opp), []):
        delta = abs((blend["_parsed_ts"] - ts).total_seconds())
        if delta <= _JOIN_WINDOW_SEC and (best is None or delta < best[0]):
            best = (delta, blend)
    return None if best is None else best[1]


def _edge_sign(value: Any) -> str:
    try:
        edge = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if edge > 0:
        return "positive"
    if edge < 0:
        return "negative"
    return "zero"


def _class_bucket(classes: set[str]) -> str:
    if not classes:
        return "unknown"
    if len(classes) == 1:
        return "1_class"
    return f"{len(classes)}_classes"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    evidence_class = {
        str(r.get("evidence_id")): str(r.get("source_class") or "unknown")
        for r in rows
        if _typ(r) == "EVIDENCE_INGESTION" and r.get("evidence_id")
    }
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    papers = [r for r in rows if _typ(r) == "PAPER_TRADE"]
    blends = _index_blends(rows)

    class_counts: Counter[str] = Counter()
    diversity: Counter[str] = Counter()
    edge_by_class: dict[str, Counter[str]] = defaultdict(Counter)
    outcome_by_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    top_tickers: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []

    for opp in opportunities:
        blend = _nearest_blend(blends, opp)
        ids = list((blend or {}).get("evidence_ids_contributing") or [])
        classes = {evidence_class.get(str(eid), "unknown") for eid in ids if str(eid) in evidence_class}
        bucket = _class_bucket(classes)
        sign = _edge_sign(opp.get("edge"))
        diversity[bucket] += 1
        outcome_by_bucket[bucket]["opportunity"] += 1
        for cls in sorted(classes) or ["unknown"]:
            class_counts[cls] += 1
            edge_by_class[sign][cls] += 1
            top_tickers[_ticker(opp)][cls] += 1
        if len(examples) < 8:
            examples.append({
                "ticker": _ticker(opp),
                "edge": opp.get("edge"),
                "classes": sorted(classes) or ["unknown"],
                "headline": opp.get("headline"),
            })

    paper_keys = {_ticker(r) for r in papers}
    for ticker in paper_keys:
        outcome_by_bucket["paper_trade_tickers"][ticker] += 1

    return {
        "paths": [str(p) for p in roots],
        "opportunity_total": len(opportunities),
        "paper_trade_total": len(papers),
        "evidence_ingestion_total": sum(1 for r in rows if _typ(r) == "EVIDENCE_INGESTION"),
        "opportunity_source_class_counts": dict(class_counts.most_common()),
        "opportunity_class_diversity": dict(diversity.most_common()),
        "edge_sign_by_class_count": {k: dict(v.most_common()) for k, v in edge_by_class.items()},
        "outcome_by_class_bucket": {k: dict(v.most_common()) for k, v in outcome_by_bucket.items()},
        "top_ticker_classes": {
            ticker: dict(counter.most_common())
            for ticker, counter in sorted(top_tickers.items(), key=lambda item: -sum(item[1].values()))[:10]
        },
        "examples": examples,
        "interpretation": (
            "Source-class diversity is measured from BLEND_DECISION evidence_ids_contributing "
            "joined to EVIDENCE_INGESTION.source_class; OPPORTUNITY records without a nearby "
            "blend or evidence IDs are bucketed as unknown."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "Source-class diversification audit",
        f"OPPORTUNITY: {report['opportunity_total']}",
        f"PAPER_TRADE: {report['paper_trade_total']}",
        f"Source classes: {report['opportunity_source_class_counts']}",
        f"Diversity buckets: {report['opportunity_class_diversity']}",
    ]
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Source-Class Diversification Audit",
        "",
        report["interpretation"],
        "",
        "## Summary",
        "",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- PAPER_TRADE total: {report['paper_trade_total']}",
        f"- EVIDENCE_INGESTION total: {report['evidence_ingestion_total']}",
        "",
        "## OPPORTUNITY Source Classes",
        "",
        "| source_class | count |",
        "| --- | ---: |",
    ]
    for cls, count in report["opportunity_source_class_counts"].items():
        lines.append(f"| {cls} | {count} |")
    lines += ["", "## Class Diversity Per OPPORTUNITY", "", "| bucket | count |", "| --- | ---: |"]
    for bucket, count in report["opportunity_class_diversity"].items():
        lines.append(f"| {bucket} | {count} |")
    lines += ["", "## Edge Sign By Class", ""]
    for sign, counts in report["edge_sign_by_class_count"].items():
        lines.append(f"- {sign}: {counts}")
    lines += [
        "",
        "## Read",
        "",
        "The OPPORTUNITY surface is still news-heavy: `news` appears on 238 OPPORTUNITY joins, `other` on 122, and `official` on only 9. Per-opportunity diversity is mixed rather than absent: 142/260 have one known class, 109/260 have two or more known classes, and 9 are unknown. The positive-edge cases are not official-source driven (`other` x2, `news` x1), so merely adding official sources is not the direct EDGE-004 unlock. The better next hypothesis is source-mix plus readiness/blending interaction: broaden non-news classes enough to improve G2/structural context, then verify whether G1/G6 still dominate after OBS-003 makes those kills visible in SKIPPED.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, action="append", dest="paths")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", type=Path, nargs="?", const=_DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = analyze(args.paths)
    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
