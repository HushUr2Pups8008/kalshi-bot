"""Lever A.1+ per-source ranking within the `specialist_analyst` class.

Codex's class-level audit (`scripts/simulations/lever_a1_plus_candidate_feed_sizing.py`)
ranked feed CLASSES (specialist_analyst > government_bulletin > market_microstructure
> mainstream_news on 13-day archive). This audit drills down ONE level: among the
sources INSIDE the `specialist_analyst` class, which individual sources produced
the OPP and PAPER_TRADE volume?

Pre-deploy goal: when A.1+1 onboards 1-2 new specialist analyst URLs (warontherocks /
csis / ISW / CFR / Atlantic Council), we need a baseline reference of which existing
specialist sources produced the historical signal. That tells us:

  - Which existing sources are load-bearing — DO NOT REMOVE them when A.1+1 ships.
  - Which existing sources are dead weight — candidates for replacement / pruning
    if RSS_FEEDS hits a polling-rate ceiling.
  - Which addressable headlines are *already* covered by existing sources — sets
    the upper bound on additional lift from a new feed in the same sub-niche.

Usage:

    python scripts/simulations/lever_a1_plus_specialist_analyst_per_source_sizing.py
    python scripts/simulations/lever_a1_plus_specialist_analyst_per_source_sizing.py --json

Reads the same Mac archive Codex's class-level audit reads
(`mac_archive/macbook_2026-05-01_import/logs/trades`) so the two audits stay
methodologically comparable.

Output columns:
  - `source`: canonical source token (kyivpost, bellingcat, etc.)
  - `match_diagnostic`: total MATCH_DIAGNOSTIC events
  - `opportunity`: total OPPORTUNITY events
  - `paper_trade`: total PAPER_TRADE events (gold standard)
  - `share_of_class_opp_pct`: this source's share of class-level OPP volume
  - `share_of_class_paper_pct`: this source's share of class-level PAPER_TRADE
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.simulations.lever_a1_plus_candidate_feed_sizing import (  # noqa: E402
    _headline,
    _source,
    _typ,
    feed_class,
)
from utils.trade_log_reader import iter_trade_records  # noqa: E402

_DEFAULT_PATHS = (_REPO_ROOT / "mac_archive/macbook_2026-05-01_import/logs/trades",)

# Canonical sub-niche tokens INSIDE the specialist_analyst class. Mirrors the
# token list in `feed_class()` for the specialist_analyst branch. Kept as a
# tuple of (canonical_label, match_tokens) so a source like "Kyiv Independent"
# vs "Kyiv Post" can be ranked separately even though both contain "kyiv".
_SUB_NICHE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kyiv_post", ("kyiv post",)),
    ("kyiv_independent", ("kyiv independent",)),
    ("kyiv_other", ("kyiv",)),  # falls through if neither of the above matched
    ("times_of_israel", ("times of israel",)),
    ("iran_international", ("iran international",)),
    ("bellingcat", ("bellingcat",)),
    ("defense_news", ("defense news",)),
    ("breaking_defense", ("breaking defense",)),
    ("defense_one", ("defense one",)),
    ("foreign_policy", ("foreign policy",)),
    ("vital_law", ("vital-law", "vitallaw")),
    # A.1+1 candidates (today's archive will show 0 — that is expected and
    # used as the pre-deploy zero-baseline for post-deploy attribution).
    ("war_on_the_rocks", ("war on the rocks", "warontherocks")),
    ("csis", ("csis", "center for strategic")),
    ("isw", ("institute for the study of war", "understandingwar")),
    ("cfr", ("council on foreign relations", "cfr.org")),
    ("atlantic_council", ("atlantic council", "atlanticcouncil")),
)


def sub_niche(source: str) -> str:
    """Return the canonical sub-niche token within specialist_analyst.

    Order matters: more-specific labels first (kyiv_post / kyiv_independent
    before the generic kyiv_other catchall). Returns "other_specialist" if
    the source is in the specialist_analyst class but doesn't match any
    sub-niche token (residual bucket — flags a missing-token gap).
    """
    lower = source.lower()
    for label, tokens in _SUB_NICHE_TOKENS:
        if any(token in lower for token in tokens):
            return label
    return "other_specialist"


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows: list[dict[str, Any]] = []
    for path in roots:
        rows.extend(iter_trade_records(path))

    per_source: dict[str, Counter[str]] = defaultdict(Counter)
    class_totals: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        typ = _typ(row)
        if typ not in {"MATCH_DIAGNOSTIC", "OPPORTUNITY", "PAPER_TRADE"}:
            continue
        if feed_class(_source(row)) != "specialist_analyst":
            continue
        sn = sub_niche(_source(row))
        per_source[sn][typ] += 1
        class_totals[typ] += 1
        if typ == "PAPER_TRADE" and len(examples[sn]) < 3:
            examples[sn].append(
                {
                    "source": _source(row),
                    "ticker": row.get("ticker") or row.get("market_ticker"),
                    "headline": _headline(row),
                    "match_score": row.get("match_score"),
                }
            )

    rows_out = []
    for sn, _ in _SUB_NICHE_TOKENS:
        c = per_source.get(sn, Counter())
        rows_out.append(_row(sn, c, class_totals, examples))
    # Residual bucket for any specialist_analyst source not matching any token.
    if "other_specialist" in per_source:
        rows_out.append(_row("other_specialist", per_source["other_specialist"], class_totals, examples))

    ranked = sorted(rows_out, key=lambda r: (r["paper_trade"], r["opportunity"], r["match_diagnostic"]), reverse=True)
    return {
        "paths": [str(p) for p in roots],
        "class_totals": dict(class_totals),
        "rows": rows_out,
        "ranked_by_paper_trade_then_opp": [r["source"] for r in ranked],
        "recommendation": _recommendation(ranked, class_totals),
    }


def _row(sn: str, c: Counter, class_totals: Counter, examples: dict) -> dict[str, Any]:
    md = c.get("MATCH_DIAGNOSTIC", 0)
    opp = c.get("OPPORTUNITY", 0)
    pt = c.get("PAPER_TRADE", 0)
    cls_opp = class_totals.get("OPPORTUNITY", 0) or 1
    cls_pt = class_totals.get("PAPER_TRADE", 0) or 1
    return {
        "source": sn,
        "match_diagnostic": md,
        "opportunity": opp,
        "paper_trade": pt,
        "share_of_class_opp_pct": round(100.0 * opp / cls_opp, 1),
        "share_of_class_paper_pct": round(100.0 * pt / cls_pt, 1),
        "examples": examples.get(sn, []),
    }


def _recommendation(ranked: list[dict[str, Any]], class_totals: Counter) -> str:
    pt_total = class_totals.get("PAPER_TRADE", 0)
    opp_total = class_totals.get("OPPORTUNITY", 0)
    top_pt = ranked[0]
    if pt_total == 0:
        return "0 PAPER_TRADE in specialist_analyst class on archive — re-run after archive grows."
    pt_concentrated = top_pt["share_of_class_paper_pct"] >= 60.0
    return (
        f"Top PAPER_TRADE source: {top_pt['source']} ({top_pt['paper_trade']}/{pt_total} = "
        f"{top_pt['share_of_class_paper_pct']}%). "
        + ("Highly concentrated — preserve at all costs; A.1+1 should ADD diversification, not replace. " if pt_concentrated else "Distribution is multi-source; A.1+1 expansion is additive. ")
        + f"OPP volume top source: {ranked[0]['source']} (load-bearing for matching surface). "
        f"Total class OPP: {opp_total}, PAPER: {pt_total}."
    )


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Lever A.1+ specialist-analyst per-source ranking",
        "",
        f"_Class totals: MATCH_DIAGNOSTIC={report['class_totals'].get('MATCH_DIAGNOSTIC', 0)}, "
        f"OPP={report['class_totals'].get('OPPORTUNITY', 0)}, "
        f"PAPER_TRADE={report['class_totals'].get('PAPER_TRADE', 0)}_",
        "",
        f"**Recommendation:** {report['recommendation']}",
        "",
        "| source | MATCH_DIAGNOSTIC | OPP | PAPER | %class_OPP | %class_PAPER |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in report["rows"]:
        lines.append(
            f"| `{r['source']}` | {r['match_diagnostic']} | {r['opportunity']} | "
            f"{r['paper_trade']} | {r['share_of_class_opp_pct']} | {r['share_of_class_paper_pct']} |"
        )
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
