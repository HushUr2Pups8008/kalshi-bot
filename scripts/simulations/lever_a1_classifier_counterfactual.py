"""Lever A.1 source-class classifier counterfactual harness.

Pre-staged during the PROFIT-PHASE2-001 soak per the Lever A Stage A.1 spec
§4 sizing methodology. Replays a canonical set of production source-label
strings through both the pre-fix `main.py:_source_class_for_evidence` and
the proposed post-fix classifier. Reports the distribution delta so the
operator (and Codex's full archive replay) can size the spec's "≥ 30/260
official" prediction empirically before deploy.

Read-only. No DB writes. Safe to run at any time.

The post-fix classifier is hardcoded here as `classify_post_fix()` —
this is the canonical reference shape the production
`main.py:_source_class_for_evidence` must reproduce after the §2.1 + §2.2
token-list patch lands. The corresponding xfail-strict tests in
tests/test_main_pipeline.py:TestSourceClassClassifierLeverA1 pin the
per-source production classifications; this script + its companion test
size the aggregate distribution lift.

Usage:

    python scripts/simulations/lever_a1_classifier_counterfactual.py
    # → prints distribution comparison table

    python scripts/simulations/lever_a1_classifier_counterfactual.py --json
    # → emits JSON for downstream tooling

The canonical source list (`_CANONICAL_SOURCES`) is the union of:
  - source strings observed in `config.py:RSS_FEEDS` titles
  - source strings observed in `feeds/google_news_monitor.py` outputs
  - per-feed inspection during 2026-05-03 spec drafting

It is *not* the full 13-day archive distribution — Codex's archive replay
audit (separate task) is responsible for the empirical lift number.
This harness pins the *shape* of the lift; Codex's audit pins the
numerical count.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

# Allow `python scripts/simulations/lever_a1_classifier_counterfactual.py`
# from the repo root (the import below depends on the repo root being on
# sys.path). Tests already get sys.path right via pytest's rootdir handling.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import the production pre-fix classifier directly so any production-side
# tweak shows up here automatically.
from main import _source_class_for_evidence as classify_pre_fix  # noqa: E402


# ---------------------------------------------------------------------------
# Post-fix classifier — canonical reference per Lever A.1 spec §2.1 + §2.2
# ---------------------------------------------------------------------------

def classify_post_fix(source: str) -> str:
    """Reference implementation of the post-Lever-A.1 classifier.

    Mirrors `main.py:_source_class_for_evidence` *with* the spec §2.1 / §2.2
    token-list expansions. The production fix should produce identical
    output to this function on every input.
    """
    source_text = (source or "").strip()
    lower = source_text.lower()
    if source_text.startswith("r/"):
        return "social"
    if lower == "price_fade" or lower.startswith("kalshi://"):
        return "market"
    if any(token in lower for token in (
        ".gov",
        "white house",
        "state department",
        "defense department",
        "department of war",            # NEW (§2.1)
        "department of defense",        # NEW (§2.1)
        "federal reserve",
        "supreme court",
        "congress",
        "parliament",
        "ministry",
        "official",
        "un news",                      # NEW (§2.1)
        "united nations",               # NEW (§2.1)
        "european commission",          # NEW (§2.1)
        "press releases",               # NEW (§2.1) — broad; positioned after specific
        "international atomic energy agency",  # NEW (§2.1)
        "iaea",                         # NEW (§2.1)
    )):
        return "official"
    if source_text.endswith(" - Google News") or source_text.endswith(" - BingNews"):
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
        "foreign policy",
        "politico",
        "politics",
        "just in news",
        "defense news",                 # NEW (§2.2)
        "breaking defense",             # NEW (§2.2)
    )):
        return "news"
    return "other"


# ---------------------------------------------------------------------------
# Canonical production source strings (as of 2026-05-03 RSS_FEEDS / feeds/*)
# ---------------------------------------------------------------------------

_CANONICAL_SOURCES: tuple[str, ...] = (
    # Currently misclassified — Lever A.1 spec §1 case for the fix:
    "Department of War News Feed",
    "UN News - Global perspective Human stories",
    "Press releases - RSS",
    "Top Stories From the International Atomic Energy Agency",
    "Defense News",
    "Breaking Defense",
    # Already classified correctly (positive controls):
    "News – The White House",
    "Reuters",
    "Associated Press",
    "BBC News",
    "Politico",
    "Al Jazeera English",
    "The Guardian",
    "r/worldnews",
    "r/politics",
    "price_fade",
    "Iran International",
    "Times of Israel",
    "The Kyiv Independent",
    "France 24 - International breaking news, top stories and headlines",
    "bellingcat",
    "Defense One",
    # Google News / Bing News dispatchers (already correctly classified as news):
    "Trump Iran deal - Google News",
    "Iran ceasefire - BingNews",
    # Generic / unknown that should stay `other`:
    "Some Random Blog",
    "anonymous wire",
)


def distribution(sources: Iterable[str], classifier) -> Counter:
    return Counter(classifier(s) for s in sources)


def render_comparison_table(pre: Counter, post: Counter) -> str:
    """Render the pre/post comparison as a markdown table."""
    classes = sorted(set(pre) | set(post))
    lines = [
        "| class | pre-fix | post-fix | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for cls in classes:
        a = pre.get(cls, 0)
        b = post.get(cls, 0)
        delta = b - a
        lines.append(f"| `{cls}` | {a} | {b} | {delta:+d} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON for downstream tooling.",
    )
    args = p.parse_args(argv)

    pre = distribution(_CANONICAL_SOURCES, classify_pre_fix)
    post = distribution(_CANONICAL_SOURCES, classify_post_fix)

    if args.json:
        payload = {
            "n_canonical_sources": len(_CANONICAL_SOURCES),
            "pre_fix_distribution": dict(pre),
            "post_fix_distribution": dict(post),
            "delta_official": post.get("official", 0) - pre.get("official", 0),
            "delta_news": post.get("news", 0) - pre.get("news", 0),
            "delta_other": post.get("other", 0) - pre.get("other", 0),
        }
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(f"# Lever A.1 classifier counterfactual ({len(_CANONICAL_SOURCES)} canonical sources)\n")
        print(render_comparison_table(pre, post))
        print()
        delta_official = post.get("official", 0) - pre.get("official", 0)
        delta_news = post.get("news", 0) - pre.get("news", 0)
        print(f"official: {pre.get('official', 0)} → {post.get('official', 0)} (delta {delta_official:+d})")
        print(f"news:     {pre.get('news', 0)} → {post.get('news', 0)} (delta {delta_news:+d})")
        print(f"other:    {pre.get('other', 0)} → {post.get('other', 0)} (delta {post.get('other', 0) - pre.get('other', 0):+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
