"""MATCH-001 (B') false-suppression audit — complement to the false-negative audit.

Pre-deploy validation: of the records currently in `MATCH_SUPPRESSED`
(suppressed by the existing pre-fix logic), how many would FLIP to
kept under the post-fix B' predicate
(`scripts/simulations/match001_bprime_anchor_sizing.py:bprime_suppresses`)?

If a record is `MATCH_SUPPRESSED` today but B' would NOT suppress it,
B' would let it through downstream — i.e., the post-fix deploy would
accidentally un-suppress that match. Each flip should be categorised:

  - LEGITIMATE: existing logic over-suppressed; B' un-suppressing is
    a CORRECTION (lift signal). Heuristic: `match_score >= 0.20` OR
    `matched_tokens count >= 3` OR `low_token_overlap` flag absent.
  - LIKELY_NOISE: existing logic correctly suppressed; B' would
    accidentally let noise through. Heuristic: `match_score < 0.10`
    AND `minimal_overlap` flag present.
  - AMBIGUOUS: between the two — operator-judgement bucket.

Companion to:

  - `scripts/simulations/match001_bprime_false_negative_audit.py` — the
    other direction (records currently kept that B' would over-suppress).
  - `docs/governance/2026-05-03-match001-bprime-false-negative-audit.md`
    — Codex's prior complement audit (commit `8001a16`).

Output partition + heuristic-flag breakdown lets the operator size
the un-suppression risk before A.1+ / MATCH-001 lands.
"""

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

from scripts.simulations.match001_bprime_anchor_sizing import bprime_suppresses  # noqa: E402
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)


def _typ(row: dict[str, Any]) -> str:
    return str(row.get("type") or row.get("record_type") or "")


def _score(row: dict[str, Any]) -> float:
    try:
        return float(row.get("match_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _flags(row: dict[str, Any]) -> set[str]:
    return set(row.get("heuristic_flags") or [])


def _matched_tokens(row: dict[str, Any]) -> list[str]:
    return [str(t) for t in (row.get("matched_tokens") or [])]


def categorise(record: dict[str, Any]) -> str:
    """Return a coarse legitimacy label for an un-suppression candidate."""
    score = _score(record)
    flags = _flags(record)
    matched = _matched_tokens(record)

    if score >= 0.20 or len(matched) >= 3 or "low_token_overlap" not in flags:
        return "LEGITIMATE"
    if score < 0.10 and "minimal_overlap" in flags:
        return "LIKELY_NOISE"
    return "AMBIGUOUS"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows: list[dict[str, Any]] = []
    for path in roots:
        rows.extend(iter_trade_records(path))

    suppressed = [r for r in rows if _typ(r) == "MATCH_SUPPRESSED"]

    flip_records: list[dict[str, Any]] = []
    agree_records: list[dict[str, Any]] = []
    for r in suppressed:
        if bprime_suppresses(r):
            agree_records.append(r)
        else:
            flip_records.append(r)

    flip_categories: Counter[str] = Counter()
    flip_flag_combos: Counter[str] = Counter()
    flip_score_buckets: Counter[str] = Counter()
    examples_by_category: dict[str, list[dict[str, Any]]] = {
        "LEGITIMATE": [],
        "LIKELY_NOISE": [],
        "AMBIGUOUS": [],
    }
    for r in flip_records:
        cat = categorise(r)
        flip_categories[cat] += 1
        combo = "+".join(sorted(_flags(r))) or "<no_flags>"
        flip_flag_combos[combo] += 1
        s = _score(r)
        if s < 0.05:
            flip_score_buckets["lt_0.05"] += 1
        elif s < 0.10:
            flip_score_buckets["[0.05,0.10)"] += 1
        elif s < 0.20:
            flip_score_buckets["[0.10,0.20)"] += 1
        else:
            flip_score_buckets["ge_0.20"] += 1
        if len(examples_by_category[cat]) < 3:
            examples_by_category[cat].append(
                {
                    "ticker": r.get("ticker") or r.get("market_ticker"),
                    "headline": r.get("headline"),
                    "match_score": s,
                    "matched_tokens": _matched_tokens(r),
                    "heuristic_flags": sorted(_flags(r)),
                }
            )

    return {
        "paths": [str(p) for p in roots],
        "match_suppressed_total": len(suppressed),
        "agree_with_bprime": len(agree_records),
        "flips_to_kept_under_bprime": len(flip_records),
        "flip_categories": dict(flip_categories),
        "flip_flag_combos": dict(flip_flag_combos.most_common(10)),
        "flip_score_buckets": dict(flip_score_buckets),
        "examples_by_category": examples_by_category,
    }


def render(report: dict[str, Any]) -> str:
    pct = lambda n, d: round(100.0 * n / d, 1) if d else 0.0  # noqa: E731
    total = report["match_suppressed_total"]
    flips = report["flips_to_kept_under_bprime"]
    agree = report["agree_with_bprime"]
    lines = [
        "# MATCH-001 (B') false-suppression audit",
        "",
        f"- MATCH_SUPPRESSED total: **{total}**",
        f"- Agree with B' (B' would also suppress): **{agree}** ({pct(agree, total)}%)",
        f"- Flip to kept under B' (B' would NOT suppress — un-suppression risk): **{flips}** ({pct(flips, total)}%)",
        "",
        "## Flip categorisation",
        "",
        "| category | count | %_of_flips |",
        "| --- | ---: | ---: |",
    ]
    for cat in ("LEGITIMATE", "AMBIGUOUS", "LIKELY_NOISE"):
        n = report["flip_categories"].get(cat, 0)
        lines.append(f"| `{cat}` | {n} | {pct(n, flips)} |")
    lines += ["", "## Flip score-bucket distribution", "", "| match_score | count |", "| --- | ---: |"]
    for bucket in ("lt_0.05", "[0.05,0.10)", "[0.10,0.20)", "ge_0.20"):
        n = report["flip_score_buckets"].get(bucket, 0)
        lines.append(f"| `{bucket}` | {n} |")
    lines += ["", "## Top flip heuristic-flag combos", "", "| flag combo | count |", "| --- | ---: |"]
    for combo, n in report["flip_flag_combos"].items():
        lines.append(f"| `{combo}` | {n} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    report = analyze()
    if args.json:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
