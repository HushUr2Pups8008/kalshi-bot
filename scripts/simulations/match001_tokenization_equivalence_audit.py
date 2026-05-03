"""Audit MATCH-001 B' simulation tokenization against production tokenizer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.market_matcher import _tokenize  # noqa: E402
from scripts.simulations.match001_bprime_anchor_sizing import bprime_suppresses  # noqa: E402
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-match001-tokenization-equivalence-audit.md"


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("market_ticker") or "")


def _overlap(row: dict[str, Any]) -> set[str]:
    return {str(token).lower() for token in (row.get("matched_tokens") or []) if str(token).strip()}


def _load(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(iter_trade_records(path))
    return rows


def _shape_flags(row: dict[str, Any]) -> bool:
    flags = set(row.get("heuristic_flags") or [])
    near_threshold_weak = "near_threshold_score" in flags and (
        "minimal_overlap" in flags or "single_named_entity_only" in flags
    )
    pure_single_entity = "single_named_entity_only" in flags and "minimal_overlap" in flags
    return bool(flags) and (near_threshold_weak or pure_single_entity)


def production_current_suppresses(row: dict[str, Any]) -> bool:
    ticker_lower = _ticker(row).lower()
    token_not_in_ticker = not any(token in ticker_lower for token in _overlap(row))
    return _shape_flags(row) and token_not_in_ticker


def bprime_tokenize_setdiff_suppresses(row: dict[str, Any]) -> bool:
    ticker_tokens = _tokenize(_ticker(row))
    has_supporting_non_ticker = bool(_overlap(row) - ticker_tokens)
    return _shape_flags(row) and not has_supporting_non_ticker


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    matches = [r for path in roots for r in iter_trade_records(path) if _typ(r) == "MATCH_DIAGNOSTIC"]
    sim = [r for r in matches if bprime_suppresses(r)]
    current = [r for r in matches if production_current_suppresses(r)]
    setdiff = [r for r in matches if bprime_tokenize_setdiff_suppresses(r)]

    sim_keys = {json.dumps([_ticker(r), r.get("headline"), r.get("source")]) for r in sim}
    current_keys = {json.dumps([_ticker(r), r.get("headline"), r.get("source")]) for r in current}
    setdiff_keys = {json.dumps([_ticker(r), r.get("headline"), r.get("source")]) for r in setdiff}
    examples = []
    for row in matches:
        if bprime_suppresses(row) == bprime_tokenize_setdiff_suppresses(row):
            continue
        examples.append(
            {
                "ticker": _ticker(row),
                "ticker_tokens": sorted(_tokenize(_ticker(row))),
                "matched_tokens": sorted(_overlap(row)),
                "bprime_substring_suppresses": bprime_suppresses(row),
                "bprime_tokenize_setdiff_suppresses": bprime_tokenize_setdiff_suppresses(row),
                "headline": row.get("headline"),
            }
        )
        if len(examples) >= 10:
            break

    return {
        "paths": [str(p) for p in roots],
        "match_diagnostic_total": len(matches),
        "simulation_bprime_substring_suppressed_keys": len(sim_keys),
        "production_current_pre_bprime_suppressed_keys": len(current_keys),
        "bprime_tokenize_setdiff_suppressed_keys": len(setdiff_keys),
        "simulation_vs_tokenize_setdiff_symmetric_diff": len(sim_keys ^ setdiff_keys),
        "tokenize_setdiff_minus_simulation": len(setdiff_keys - sim_keys),
        "simulation_minus_tokenize_setdiff": len(sim_keys - setdiff_keys),
        "ticker_token_examples": {
            "KXTRUMPIRAN-26MAY01": sorted(_tokenize("KXTRUMPIRAN-26MAY01")),
            "KXMOCTRUMP25-26-MAY01": sorted(_tokenize("KXMOCTRUMP25-26-MAY01")),
        },
        "divergence_examples": examples,
        "interpretation": (
            "The current simulation models B' with raw substring membership against the ticker. "
            "A post-fix implementation that literally uses _tokenize(ticker) set-difference "
            "would produce a different suppression surface because hyphenated tickers remain "
            "single tokens under _tokenize."
        ),
    }


def render(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "MATCH-001 tokenization equivalence audit",
            f"Simulation B' substring keys: {report['simulation_bprime_substring_suppressed_keys']}",
            f"Current pre-B' production keys: {report['production_current_pre_bprime_suppressed_keys']}",
            f"B' _tokenize setdiff keys: {report['bprime_tokenize_setdiff_suppressed_keys']}",
            f"Sim vs _tokenize setdiff diff: {report['simulation_vs_tokenize_setdiff_symmetric_diff']}",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MATCH-001 B' Tokenization Equivalence Audit",
        "",
        report["interpretation"],
        "",
        "## Summary",
        "",
        f"- MATCH_DIAGNOSTIC total: {report['match_diagnostic_total']}",
        f"- Simulation B' substring suppressed keys: {report['simulation_bprime_substring_suppressed_keys']}",
        f"- Current pre-B' production suppressed keys: {report['production_current_pre_bprime_suppressed_keys']}",
        f"- B' with production `_tokenize(ticker)` set-diff suppressed keys: {report['bprime_tokenize_setdiff_suppressed_keys']}",
        f"- Simulation vs `_tokenize(ticker)` symmetric diff: {report['simulation_vs_tokenize_setdiff_symmetric_diff']}",
        f"- `_tokenize` examples: {report['ticker_token_examples']}",
        "",
        "## Divergence Examples",
        "",
        "| ticker | ticker tokens | matched tokens | B' substring suppresses | B' tokenized setdiff suppresses | headline |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["divergence_examples"]:
        lines.append(
            f"| {row['ticker']} | {row['ticker_tokens']} | {row['matched_tokens']} | "
            f"{row['bprime_substring_suppresses']} | {row['bprime_tokenize_setdiff_suppresses']} | "
            f"{row['headline']} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
        "The 1,076-key B' estimate is valid only for the substring-membership interpretation used by the simulation. It is not valid for a literal `_tokenize(ticker)` set-difference implementation: `_tokenize('KXTRUMPIRAN-26MAY01')` returns one hyphenated token, so matched tokens like `trump` and `iran` are treated as non-ticker support and the suppression surface shrinks materially. The MATCH-001 landing spec should either codify substring membership or re-run sizing after changing the tokenizer semantics.",
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
