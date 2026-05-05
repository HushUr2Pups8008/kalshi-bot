"""§8.5.1 gate-6 operator review tool.

Streams `GOVERNANCE_DECISION` records from `logs/governance/decisions.jsonl`
in a structured CLI, captures operator y/n verdict per record, computes
the reasonable-rate, and emits a summary at end. The original §8.5 gate
calls for "≥ 85% reasonable on review of all 30+ decisions"; this tool
makes the review tractable when the decision count is in the hundreds.

Usage:

    python scripts/governance_decision_review.py
        # interactive review of every GOVERNANCE_DECISION

    python scripts/governance_decision_review.py --since 2026-05-03T15:28Z
        # only decisions after the GOV-002 SYSTEM_PROMPT change (regime 3)

    python scripts/governance_decision_review.py --output review_2026-05-08.jsonl
        # write per-decision verdicts to file for later aggregation

    python scripts/governance_decision_review.py --resume review_2026-05-08.jsonl
        # skip decisions already reviewed (decision_id matched)

    python scripts/governance_decision_review.py --aggregate review_2026-05-08.jsonl
        # no interactive review; just emit reasonable-rate summary from
        # an existing verdict file

The interactive prompt accepts:
    y / yes / 1   reasonable
    n / no / 0    not reasonable
    s / skip      defer (does not count toward the rate)
    q / quit      stop reviewing; emit summary up to this point
    ?             show the full record JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "logs" / "governance" / "decisions.jsonl"


def _load_decisions(path: Path, since: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") != "GOVERNANCE_DECISION":
            continue
        if since:
            ts = r.get("decided_at", "")
            if ts and ts < since:
                continue
        out.append(r)
    return out


def _load_resume(path: Path) -> set[str]:
    """Return the set of decision_ids already reviewed (i.e., to skip)."""
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            seen.add(r["decision_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def _format_decision(r: dict[str, Any], idx: int, total: int) -> str:
    es = r.get("evidence_summary", {}) or {}
    pe = r.get("predicted_effect", {}) or {}
    headlines = es.get("recent_headline_sample", []) or []
    headline_block = "\n".join(f"        - {h[:140]}" for h in headlines[:3])
    return (
        f"\n{'=' * 78}\n"
        f"[{idx}/{total}] {r.get('decision_id', '<no_id>')} — {r.get('action', '<no_action>')} "
        f"target={r.get('target', '<no_target>')!r}\n"
        f"{'=' * 78}\n"
        f"  decided_at: {r.get('decided_at', '<no_ts>')}\n"
        f"  cadence:    {r.get('cadence', '<no_cadence>')}\n"
        f"  confidence: {r.get('confidence', '<no_conf>')}\n"
        f"  reasoning:  {r.get('reasoning', '<no_reasoning>')}\n"
        f"  evidence:\n"
        f"    window_hours:     {es.get('window_hours', '?')}\n"
        f"    ingestion_events: {es.get('ingestion_events', '?')}\n"
        f"    fresh_pass_count: {es.get('fresh_pass_count', '?')}\n"
        f"    match_count:      {es.get('match_count', '?')}\n"
        f"    active_market_count: {es.get('active_market_count', '?')}\n"
        f"    recent_headlines:\n"
        f"{headline_block}\n"
        f"  predicted_effect:\n"
        f"    metric:               {pe.get('metric', '?')}\n"
        f"    baseline:             {pe.get('baseline', '?')}\n"
        f"    predicted_post_change: {pe.get('predicted_post_change', '?')}\n"
        f"    evaluate_at:          {pe.get('evaluate_at', '?')}\n"
        f"  shadow_mode: {r.get('shadow_mode', '?')}    "
        f"safety_checks: {r.get('safety_checks_passed', '?')}\n"
    )


def _aggregate(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(v["verdict"] for v in verdicts)
    reasonable = counts.get("y", 0)
    not_reasonable = counts.get("n", 0)
    skipped = counts.get("s", 0)
    rated = reasonable + not_reasonable
    rate_pct = round(100.0 * reasonable / rated, 1) if rated else 0.0
    return {
        "total_reviewed": len(verdicts),
        "reasonable": reasonable,
        "not_reasonable": not_reasonable,
        "skipped": skipped,
        "reasonable_rate_pct": rate_pct,
        "gate_6_pass": rate_pct >= 85.0 and rated >= 30,
    }


def _is_uniform_dead_source_disable(row: dict[str, Any]) -> bool:
    evidence = row.get("evidence_summary") or {}
    safety = row.get("safety_checks_passed") or {}
    return (
        row.get("action") == "disable_source"
        and all(safety.values())
        and (evidence.get("fresh_pass_count") or 0) == 0
        and (evidence.get("match_count") or 0) == 0
        and (evidence.get("active_market_count") or 0) == 0
    )


def _bulk_candidates(
    decisions: list[dict[str, Any]],
    resume_seen: set[str],
) -> list[dict[str, Any]]:
    return [
        row for row in decisions
        if row.get("decision_id", "<no_id>") not in resume_seen
        and _is_uniform_dead_source_disable(row)
    ]


def _print_summary(agg: dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print("§8.5.1 gate-6 review summary")
    print("=" * 78)
    print(f"  total reviewed:    {agg['total_reviewed']}")
    print(f"  reasonable:        {agg['reasonable']}")
    print(f"  not reasonable:    {agg['not_reasonable']}")
    print(f"  skipped:           {agg['skipped']}")
    print(f"  reasonable rate:   {agg['reasonable_rate_pct']:.1f}% (gate: ≥ 85.0%)")
    print(f"  GATE 6 STATUS:     {'PASS' if agg['gate_6_pass'] else 'FAIL'}")
    print()


def _interactive_loop(
    decisions: list[dict[str, Any]],
    resume_seen: set[str],
    output_path: Path | None,
) -> list[dict[str, Any]]:
    verdicts: list[dict[str, Any]] = []
    out_handle = output_path.open("a", encoding="utf-8") if output_path else None
    try:
        idx = 0
        for r in decisions:
            did = r.get("decision_id", "<no_id>")
            if did in resume_seen:
                continue
            idx += 1
            print(_format_decision(r, idx, len(decisions) - len(resume_seen)))
            while True:
                try:
                    response = input("verdict [y / n / s skip / q quit / ? show JSON]> ").strip().lower()
                except EOFError:
                    response = "q"
                if response in {"y", "yes", "1"}:
                    verdict = {"decision_id": did, "verdict": "y", "ts": datetime.utcnow().isoformat() + "Z"}
                    break
                if response in {"n", "no", "0"}:
                    verdict = {"decision_id": did, "verdict": "n", "ts": datetime.utcnow().isoformat() + "Z"}
                    break
                if response in {"s", "skip"}:
                    verdict = {"decision_id": did, "verdict": "s", "ts": datetime.utcnow().isoformat() + "Z"}
                    break
                if response in {"q", "quit"}:
                    print("[quitting; emitting summary up to this point]")
                    return verdicts
                if response == "?":
                    print(json.dumps(r, indent=2)[:4000])
                    continue
                print("  (please enter y / n / s / q / ?)")
            verdicts.append(verdict)
            if out_handle:
                out_handle.write(json.dumps(verdict) + "\n")
                out_handle.flush()
    finally:
        if out_handle:
            out_handle.close()
    return verdicts


def _bulk_loop(
    decisions: list[dict[str, Any]],
    resume_seen: set[str],
    output_path: Path | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    selected = _bulk_candidates(decisions, resume_seen)
    if not selected:
        print("Bulk mode: no unreviewed uniform dead-source disable decisions found.")
        return [], set()

    print()
    print("=" * 78)
    print("Bulk mode: uniform dead-source disable class")
    print("=" * 78)
    print(f"  candidate decisions: {len(selected)}")
    print("  predicate: action=disable_source, all safety checks true, fresh/match/active-market counts all zero")
    print("  verdict applies to every candidate in this mechanical class.")
    sample = ", ".join(str(row.get("decision_id", "<no_id>")) for row in selected[:5])
    if len(selected) > 5:
        sample += ", ..."
    print(f"  sample decision_ids: {sample}")

    while True:
        try:
            response = input("bulk verdict [y / n / s skip / q quit]> ").strip().lower()
        except EOFError:
            response = "q"
        if response in {"y", "yes", "1"}:
            verdict_value = "y"
            break
        if response in {"n", "no", "0"}:
            verdict_value = "n"
            break
        if response in {"s", "skip"}:
            verdict_value = "s"
            break
        if response in {"q", "quit"}:
            print("[quitting before bulk verdict; emitting summary up to this point]")
            return [], set()
        print("  (please enter y / n / s / q)")

    ts = datetime.utcnow().isoformat() + "Z"
    verdicts = [
        {
            "decision_id": row.get("decision_id", "<no_id>"),
            "verdict": verdict_value,
            "ts": ts,
            "bulk_mode": True,
            "bulk_class": "uniform_dead_source_disable",
        }
        for row in selected
    ]
    if output_path:
        with output_path.open("a", encoding="utf-8") as out_handle:
            for verdict in verdicts:
                out_handle.write(json.dumps(verdict) + "\n")
    return verdicts, {verdict["decision_id"] for verdict in verdicts}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", help="ISO timestamp; only review decisions decided_at >= this")
    p.add_argument("--output", help="path to write per-decision verdicts as JSONL")
    p.add_argument("--resume", help="path to existing verdicts file; skip already-reviewed decisions")
    p.add_argument("--aggregate", help="path to existing verdicts file; emit summary only, no interactive review")
    p.add_argument(
        "--bulk-mode",
        action="store_true",
        help="prompt once for the uniform dead-source disable class, then review remaining decisions",
    )
    args = p.parse_args(argv)

    if args.aggregate:
        path = Path(args.aggregate)
        if not path.exists():
            print(f"error: {args.aggregate} does not exist", file=sys.stderr)
            return 2
        verdicts = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                verdicts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        _print_summary(_aggregate(verdicts))
        return 0

    decisions = _load_decisions(_DEFAULT_PATH, since=args.since)
    if not decisions:
        print(f"No GOVERNANCE_DECISION records found at {_DEFAULT_PATH}", file=sys.stderr)
        return 2

    resume_seen = _load_resume(Path(args.resume)) if args.resume else set()
    output_path = Path(args.output) if args.output else None

    print(
        f"Loaded {len(decisions)} GOVERNANCE_DECISION records"
        + (f" (since={args.since})" if args.since else "")
        + (f"; {len(resume_seen)} already reviewed (resume mode)" if resume_seen else "")
    )

    verdicts: list[dict[str, Any]] = []
    if args.bulk_mode:
        bulk_verdicts, bulk_seen = _bulk_loop(decisions, resume_seen, output_path)
        verdicts.extend(bulk_verdicts)
        resume_seen = resume_seen | bulk_seen

    verdicts.extend(_interactive_loop(decisions, resume_seen, output_path))
    _print_summary(_aggregate(verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
