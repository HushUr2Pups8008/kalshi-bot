"""Lever E source/source-class corroboration sizing over archived OPPORTUNITY rows."""
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
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md"
_JOIN_WINDOW_SEC = 60.0


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _headline(row: dict[str, Any]) -> str:
    return str(row.get("headline") or row.get("signal_headline") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("signal_source") or "")


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (_ticker(row), _headline(row), _source(row))


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ts(row: dict[str, Any]) -> datetime | None:
    value = row.get("ts")
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _edge_sign(value: Any) -> str:
    edge = _float(value)
    if edge > 0:
        return "positive"
    if edge < 0:
        return "negative"
    return "zero"


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
        ts = _ts(row)
        if ts is None:
            continue
        row["_parsed_ts"] = ts
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


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    evidence_class = {
        str(r.get("evidence_id")): str(r.get("source_class") or "unknown")
        for r in rows
        if _typ(r) == "EVIDENCE_INGESTION" and r.get("evidence_id")
    }
    evidence_source = {
        str(r.get("evidence_id")): str(r.get("source") or "unknown")
        for r in rows
        if _typ(r) == "EVIDENCE_INGESTION" and r.get("evidence_id")
    }
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    paper_keys = {_key(r) for r in rows if _typ(r) == "PAPER_TRADE"}
    blends = _index_blends(rows)

    class_count_dist: Counter[int] = Counter()
    source_count_dist: Counter[int] = Counter()
    edge_by_class_count: dict[int, Counter[str]] = defaultdict(Counter)
    edge_by_source_count: dict[int, Counter[str]] = defaultdict(Counter)
    paper_by_class_count: Counter[int] = Counter()
    paper_by_source_count: Counter[int] = Counter()
    top_cut_tickers: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []

    for opp in opportunities:
        blend = _nearest_blend(blends, opp)
        ids = [str(eid) for eid in ((blend or {}).get("evidence_ids_contributing") or [])]
        classes = {evidence_class[eid] for eid in ids if eid in evidence_class}
        sources = {evidence_source[eid] for eid in ids if eid in evidence_source}
        n_classes = len(classes)
        n_sources = len(sources)
        class_count_dist[n_classes] += 1
        source_count_dist[n_sources] += 1
        edge_by_class_count[n_classes][_edge_sign(opp.get("edge"))] += 1
        edge_by_source_count[n_sources][_edge_sign(opp.get("edge"))] += 1
        if _key(opp) in paper_keys:
            paper_by_class_count[n_classes] += 1
            paper_by_source_count[n_sources] += 1
        for threshold in (2, 3):
            if n_classes < threshold:
                top_cut_tickers[f"class_N>={threshold}"][_ticker(opp)] += 1
            if n_sources < threshold:
                top_cut_tickers[f"source_N>={threshold}"][_ticker(opp)] += 1
        if len(examples) < 10:
            examples.append(
                {
                    "ticker": _ticker(opp),
                    "edge": opp.get("edge"),
                    "source_class_count": n_classes,
                    "source_classes": sorted(classes),
                    "source_count": n_sources,
                    "sources": sorted(sources),
                    "headline": _headline(opp),
                }
            )

    def _thresholds(
        dist: Counter[int],
        paper_dist: Counter[int],
        prefix: str,
    ) -> dict[str, dict[str, Any]]:
        thresholds: dict[str, dict[str, Any]] = {}
        total = len(opportunities)
        for threshold in (2, 3):
            retained = sum(count for n, count in dist.items() if n >= threshold)
            cut = total - retained
            name = f"N>={threshold}"
            thresholds[name] = {
                "retained_opportunities": retained,
                "cut_opportunities": cut,
                "retention_rate": retained / total if total else 0.0,
                "cut_rate": cut / total if total else 0.0,
                "retained_paper_trades": sum(
                    count for n, count in paper_dist.items() if n >= threshold
                ),
                "top_cut_tickers": dict(top_cut_tickers[f"{prefix}_N>={threshold}"].most_common(10)),
            }
        return thresholds

    total = len(opportunities)

    return {
        "paths": [str(p) for p in roots],
        "opportunity_total": total,
        "paper_trade_total": len(paper_keys),
        "source_class_count_distribution": dict(sorted(class_count_dist.items())),
        "source_count_distribution": dict(sorted(source_count_dist.items())),
        "edge_sign_by_source_class_count": {
            str(n): dict(counter.most_common())
            for n, counter in sorted(edge_by_class_count.items())
        },
        "edge_sign_by_source_count": {
            str(n): dict(counter.most_common())
            for n, counter in sorted(edge_by_source_count.items())
        },
        "paper_trades_by_source_class_count": dict(sorted(paper_by_class_count.items())),
        "paper_trades_by_source_count": dict(sorted(paper_by_source_count.items())),
        "source_class_thresholds": _thresholds(class_count_dist, paper_by_class_count, "class"),
        "source_thresholds": _thresholds(source_count_dist, paper_by_source_count, "source"),
        "examples": examples,
        "interpretation": (
            "Distinct sources and source classes are recovered from BLEND_DECISION.evidence_ids_contributing "
            "joined to EVIDENCE_INGESTION.source/source_class. OPPORTUNITY rows without a joined blend "
            "or known evidence ids count as 0-source/0-class and would be cut by Lever E."
        ),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "Lever E source/source-class corroboration sizing",
        f"OPPORTUNITY: {report['opportunity_total']}",
        f"Source-class distribution: {report['source_class_count_distribution']}",
        f"Source-instance distribution: {report['source_count_distribution']}",
    ]
    for name, row in report["source_class_thresholds"].items():
        lines.append(
            f"class {name}: retained {row['retained_opportunities']} "
            f"({row['retention_rate']:.1%}), paper {row['retained_paper_trades']}"
        )
    for name, row in report["source_thresholds"].items():
        lines.append(
            f"source {name}: retained {row['retained_opportunities']} "
            f"({row['retention_rate']:.1%}), paper {row['retained_paper_trades']}"
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lever E Source/Source-Class Corroboration Sizing",
        "",
        report["interpretation"],
        "",
        "## Summary",
        "",
        f"- OPPORTUNITY total: {report['opportunity_total']}",
        f"- PAPER_TRADE total: {report['paper_trade_total']}",
        "",
        "## Source-Class Diversity Distribution",
        "",
        "| distinct source classes | OPPORTUNITY | edge signs | paper trades |",
        "| ---: | ---: | --- | ---: |",
    ]
    paper_by_count = report["paper_trades_by_source_class_count"]
    for n, count in report["source_class_count_distribution"].items():
        lines.append(
            f"| {n} | {count} | {report['edge_sign_by_source_class_count'].get(str(n), {})} | "
            f"{paper_by_count.get(n, 0)} |"
        )
    lines += [
        "",
        "## Source-Instance Diversity Distribution",
        "",
        "| distinct sources | OPPORTUNITY | edge signs | paper trades |",
        "| ---: | ---: | --- | ---: |",
    ]
    paper_by_source = report["paper_trades_by_source_count"]
    for n, count in report["source_count_distribution"].items():
        lines.append(
            f"| {n} | {count} | {report['edge_sign_by_source_count'].get(str(n), {})} | "
            f"{paper_by_source.get(n, 0)} |"
        )
    lines += [
        "",
        "## Source-Class Threshold Counterfactual",
        "",
        "| threshold | retained OPPORTUNITY | cut OPPORTUNITY | retention | retained PAPER_TRADE |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, row in report["source_class_thresholds"].items():
        lines.append(
            f"| {name} | {row['retained_opportunities']} | {row['cut_opportunities']} | "
            f"{row['retention_rate']:.1%} | {row['retained_paper_trades']} |"
        )
    lines += [
        "",
        "## Source-Instance Threshold Counterfactual",
        "",
        "| threshold | retained OPPORTUNITY | cut OPPORTUNITY | retention | retained PAPER_TRADE |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, row in report["source_thresholds"].items():
        lines.append(
            f"| {name} | {row['retained_opportunities']} | {row['cut_opportunities']} | "
            f"{row['retention_rate']:.1%} | {row['retained_paper_trades']} |"
        )
    lines += ["", "## Read", ""]
    class_n2 = report["source_class_thresholds"]["N>=2"]
    class_n3 = report["source_class_thresholds"]["N>=3"]
    source_n2 = report["source_thresholds"]["N>=2"]
    source_n3 = report["source_thresholds"]["N>=3"]
    lines += [
        f"Using source classes, `N>=2` would retain {class_n2['retained_opportunities']}/{report['opportunity_total']} OPPORTUNITY records and cut {class_n2['cut_opportunities']}; `N>=3` would retain {class_n3['retained_opportunities']} and cut {class_n3['cut_opportunities']}.",
        "",
        f"Using distinct source instances, `N>=2` would retain {source_n2['retained_opportunities']}/{report['opportunity_total']} OPPORTUNITY records and cut {source_n2['cut_opportunities']}; `N>=3` would retain {source_n3['retained_opportunities']} and cut {source_n3['cut_opportunities']}.",
        "",
        "This is a high-blast-radius filter. Source-class N>=2 removes a large single-class tail; source-instance N>=2 removes the entire historical OPPORTUNITY set because every joined candidate has at most one distinct source instance. Use the table above as the pre-deploy expectation, not as a direct EV claim.",
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
