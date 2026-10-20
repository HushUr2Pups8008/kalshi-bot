"""Counterfactual post-soak landing-order simulation over archived trade logs."""
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
_DEFAULT_REPORT = _REPO_ROOT / "docs/governance/2026-05-03-post-soak-landing-simulation.md"
_BLEND_JOIN_WINDOW_SEC = 60.0
_SERIES_WINDOW_SEC = 3600.0


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


def _bprime_suppresses(match: dict[str, Any]) -> bool:
    flags = set(match.get("heuristic_flags") or [])
    if not flags:
        return False
    overlap = {str(t).lower() for t in (match.get("matched_tokens") or []) if str(t).strip()}
    ticker_lower = _ticker(match).lower()
    has_supporting_non_ticker = any(token not in ticker_lower for token in overlap)
    near_threshold_weak = "near_threshold_score" in flags and (
        "minimal_overlap" in flags or "single_named_entity_only" in flags
    )
    pure_single_entity = "single_named_entity_only" in flags and "minimal_overlap" in flags
    return (not has_supporting_non_ticker) and (near_threshold_weak or pure_single_entity)


def _index_blends(blends: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in blends:
        ts = _ts(row)
        if ts is None:
            continue
        row["_parsed_ts"] = ts
        out[_ticker(row)].append(row)
    for vals in out.values():
        vals.sort(key=lambda r: r["_parsed_ts"])
    return out


def _nearest_blend(index: dict[str, list[dict[str, Any]]], opp: dict[str, Any]) -> dict[str, Any] | None:
    ts = _ts(opp)
    if ts is None:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for blend in index.get(_ticker(opp), []):
        delta = abs((blend["_parsed_ts"] - ts).total_seconds())
        if delta <= _BLEND_JOIN_WINDOW_SEC and (best is None or delta < best[0]):
            best = (delta, blend)
    return None if best is None else best[1]


def _series(row: dict[str, Any]) -> str:
    explicit = str(row.get("series_ticker") or "")
    if explicit:
        return explicit
    return _ticker(row).split("-", 1)[0]


def _exec002_suppressed_papers(papers: list[dict[str, Any]]) -> int:
    by_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for paper in papers:
        ts = _ts(paper)
        if ts is None:
            continue
        paper["_parsed_ts"] = ts
        by_series[_series(paper)].append(paper)
    suppressed = 0
    for vals in by_series.values():
        vals.sort(key=lambda r: r["_parsed_ts"])
        last: datetime | None = None
        for row in vals:
            ts = row["_parsed_ts"]
            if last is not None and (ts - last).total_seconds() < _SERIES_WINDOW_SEC:
                suppressed += 1
            else:
                last = ts
    return suppressed


def _row(step: str, opportunity: int, skipped: int, paper: int, note: str) -> dict[str, Any]:
    visible_exits = skipped + paper
    return {
        "step": step,
        "opportunity": opportunity,
        "skipped": skipped,
        "paper_trade": paper,
        "visible_skip_share": skipped / visible_exits if visible_exits else 0.0,
        "trade_rate": paper / opportunity if opportunity else 0.0,
        "note": note,
    }


def analyze(paths: list[Path] | None = None) -> dict[str, Any]:
    roots = list(paths or _DEFAULT_PATHS)
    rows = _load(roots)
    matches = [r for r in rows if _typ(r) == "MATCH_DIAGNOSTIC"]
    opportunities = [r for r in rows if _typ(r) == "OPPORTUNITY"]
    skipped = [r for r in rows if _typ(r) == "SKIPPED"]
    papers = [r for r in rows if _typ(r) == "PAPER_TRADE"]
    blends = [r for r in rows if _typ(r) == "BLEND_DECISION"]

    bprime_suppressed_keys = {_key(r) for r in matches if _bprime_suppresses(r)}
    retained_opps = [r for r in opportunities if _key(r) not in bprime_suppressed_keys]
    retained_papers = [r for r in papers if _key(r) not in bprime_suppressed_keys]
    retained_skipped = [r for r in skipped if _key(r) not in bprime_suppressed_keys]

    blend_index = _index_blends(blends)
    obs003_new_skips = 0
    reasons: Counter[str] = Counter()
    for opp in retained_opps:
        has_visible_exit = any(_key(r) == _key(opp) for r in retained_skipped + retained_papers)
        if has_visible_exit:
            continue
        blend = _nearest_blend(blend_index, opp)
        reason = str((blend or {}).get("trade_blocked_reason") or "")
        if reason:
            obs003_new_skips += 1
            reasons[reason] += 1

    exec002_paper_suppressed = _exec002_suppressed_papers(retained_papers)

    steps = [
        _row("baseline", len(opportunities), len(skipped), len(papers), "Observed archive."),
        _row("OBS-005", len(opportunities), len(skipped), len(papers), "Cooldown sentinel fix has no archive-visible counterfactual effect."),
        _row(
            "MATCH-001_B_prime",
            len(retained_opps),
            len(retained_skipped),
            len(retained_papers),
            f"Estimated by removing downstream records whose MATCH_DIAGNOSTIC would meet B' suppression ({len(bprime_suppressed_keys)} keys).",
        ),
        _row(
            "OBS-003",
            len(retained_opps),
            len(retained_skipped) + obs003_new_skips,
            len(retained_papers),
            f"Adds {obs003_new_skips} BlendTask blocked-reason SKIPPED records; top reasons {dict(reasons.most_common(5))}.",
        ),
        _row(
            "EXEC-002",
            len(retained_opps),
            len(retained_skipped) + obs003_new_skips + exec002_paper_suppressed,
            len(retained_papers) - exec002_paper_suppressed,
            f"Series-correlation guard suppresses {exec002_paper_suppressed} paper trades inside {_SERIES_WINDOW_SEC / 3600:.0f}h series windows.",
        ),
    ]
    return {
        "paths": [str(p) for p in roots],
        "steps": steps,
        "bprime_suppressed_match_keys": len(bprime_suppressed_keys),
        "obs003_added_skips": obs003_new_skips,
        "obs003_reason_counts": dict(reasons.most_common()),
        "exec002_paper_trades_suppressed": exec002_paper_suppressed,
        "limitations": [
            "MATCH-001 B' estimate uses archived MATCH_DIAGNOSTIC keys; it cannot model below-threshold matches that were never logged.",
            "OBS-003 estimate assumes every retained silent OPPORTUNITY with a nearby blocked BLEND_DECISION would emit exactly one SKIPPED.",
            "EXEC-002 estimate models only paper trades already present in the archive, not untraded candidates that would become newly visible after OBS-003.",
        ],
    }


def render(report: dict[str, Any]) -> str:
    lines = ["Post-soak landing simulation", "step | opportunity | skipped | paper_trade | trade_rate"]
    for row in report["steps"]:
        lines.append(f"{row['step']} | {row['opportunity']} | {row['skipped']} | {row['paper_trade']} | {row['trade_rate']:.1%}")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Post-Soak Landing Simulation",
        "",
        "Counterfactual replay over the 13-day MacBook archive.",
        "",
        "| step | OPPORTUNITY | SKIPPED | PAPER_TRADE | visible_skip_share | trade_rate | note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["steps"]:
        lines.append(
            f"| {row['step']} | {row['opportunity']} | {row['skipped']} | {row['paper_trade']} | "
            f"{row['visible_skip_share']:.1%} | {row['trade_rate']:.1%} | {row['note']} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
    ]
    for item in report["limitations"]:
        lines.append(f"- {item}")
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
