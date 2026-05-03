"""Lever A.1 source-classifier counterfactual over archived source labels."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from main import _source_class_for_evidence  # noqa: E402
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-lever-a1-source-classifier-counterfactual.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or row.get("signal_source") or "")


def classify_post_a1(source: str) -> str:
    """Counterfactual classifier from the Lever A.1 spec, without touching prod."""
    text = (source or "").strip()
    lower = text.lower()
    if text.startswith("r/"):
        return "social"
    if lower == "price_fade" or lower.startswith("kalshi://"):
        return "market"
    if any(token in lower for token in (
        ".gov",
        "white house",
        "state department",
        "defense department",
        "department of war",
        "federal reserve",
        "supreme court",
        "congress",
        "parliament",
        "ministry",
        "official",
        "un news",
        "united nations",
        "press releases",
        "european commission",
        "international atomic energy agency",
        "iaea",
    )):
        return "official"
    if text.endswith(" - Google News") or text.endswith(" - BingNews"):
        return "news"
    if any(token in lower for token in (
        "reuters",
        "associated press",
        "ap news",
        "bbc",
        "nyt",
        "guardian",
        "al jazeera",
        "france 24",
        "deutsche welle",
        "defense one",
        "defense news",
        "breaking defense",
        "foreign policy",
        "politico",
        "politics",
        "just in news",
    )):
        return "news"
    return "other"


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _distribution(rows: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    selected = [row for row in rows if _typ(row) == event_type]
    with_source = [row for row in selected if _source(row)]
    current = Counter(_source_class_for_evidence(_source(row)) for row in with_source)
    post = Counter(classify_post_a1(_source(row)) for row in with_source)
    flips = Counter(
        f"{_source_class_for_evidence(_source(row))}->{classify_post_a1(_source(row))}"
        for row in with_source
        if _source_class_for_evidence(_source(row)) != classify_post_a1(_source(row))
    )
    examples = []
    for row in with_source:
        before = _source_class_for_evidence(_source(row))
        after = classify_post_a1(_source(row))
        if before == after:
            continue
        examples.append(
            {
                "source": _source(row),
                "current": before,
                "post_a1": after,
                "ticker": row.get("ticker") or row.get("market_ticker"),
                "headline": row.get("headline") or row.get("signal_headline"),
            }
        )
        if len(examples) >= 10:
            break
    return {
        "event_total": len(selected),
        "with_source_string": len(with_source),
        "current_distribution": dict(current.most_common()),
        "post_a1_distribution": dict(post.most_common()),
        "flips": dict(flips.most_common()),
        "examples": examples,
    }


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    evidence = [row for row in rows if _typ(row) == "EVIDENCE_INGESTION"]
    evidence_with_source = [row for row in evidence if _source(row)]
    opp = _distribution(rows, "OPPORTUNITY")
    match = _distribution(rows, "MATCH_DIAGNOSTIC")
    return {
        "paths": [str(path) for path in roots],
        "evidence_ingestion_total": len(evidence),
        "evidence_ingestion_with_source_string": len(evidence_with_source),
        "opportunity_source_label_replay": opp,
        "match_diagnostic_source_label_replay": match,
        "target_official_opportunity_count": 30,
        "opportunity_target_status": (
            "PASS" if opp["post_a1_distribution"].get("official", 0) >= 30 else "FAIL"
        ),
        "limitation": (
            "Archived EVIDENCE_INGESTION records do not carry raw source labels, so the exact spec "
            "replay over each evidence source string is unavailable. OPPORTUNITY and MATCH_DIAGNOSTIC "
            "source labels are reported as archive-visible surrogates."
        ),
    }


def render(report: dict[str, Any]) -> str:
    opp = report["opportunity_source_label_replay"]
    match = report["match_diagnostic_source_label_replay"]
    return "\n".join(
        [
            "Lever A.1 source-classifier counterfactual",
            f"EVIDENCE_INGESTION source strings: {report['evidence_ingestion_with_source_string']}/{report['evidence_ingestion_total']}",
            f"OPPORTUNITY post-A.1 official: {opp['post_a1_distribution'].get('official', 0)}/{opp['event_total']} ({report['opportunity_target_status']} >=30 target)",
            f"MATCH_DIAGNOSTIC post-A.1 official: {match['post_a1_distribution'].get('official', 0)}/{match['event_total']}",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lever A.1 Source-Classifier Counterfactual",
        "",
        report["limitation"],
        "",
        "## Summary",
        "",
        f"- EVIDENCE_INGESTION rows: {report['evidence_ingestion_total']}",
        f"- EVIDENCE_INGESTION rows with raw source string: {report['evidence_ingestion_with_source_string']}",
        f"- OPPORTUNITY official target: {report['opportunity_source_label_replay']['post_a1_distribution'].get('official', 0)}/260 ({report['opportunity_target_status']} against >=30)",
        "",
    ]
    for label, key in (
        ("OPPORTUNITY Source-Label Replay", "opportunity_source_label_replay"),
        ("MATCH_DIAGNOSTIC Source-Label Replay", "match_diagnostic_source_label_replay"),
    ):
        row = report[key]
        lines += [
            f"## {label}",
            "",
            f"- rows: {row['event_total']}",
            f"- rows with source string: {row['with_source_string']}",
            f"- current distribution: `{row['current_distribution']}`",
            f"- post-A.1 distribution: `{row['post_a1_distribution']}`",
            f"- flips: `{row['flips']}`",
            "",
        ]
        if row["examples"]:
            lines += ["| source | current | post-A.1 | ticker | headline |", "| --- | --- | --- | --- | --- |"]
            for ex in row["examples"]:
                lines.append(
                    f"| {ex['source']} | {ex['current']} | {ex['post_a1']} | "
                    f"{ex['ticker']} | {ex['headline']} |"
                )
            lines.append("")
    lines += [
        "## Verdict",
        "",
        "The exact EVIDENCE_INGESTION replay promised by the spec is blocked by archive shape: the raw source labels are absent. On OPPORTUNITY source labels, the post-A.1 classifier does not reach the >=30/260 official target. On the broader MATCH_DIAGNOSTIC feed surface it does recover additional official labels, so the fix may improve upstream classification without proving an OPPORTUNITY-surface lift.",
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
