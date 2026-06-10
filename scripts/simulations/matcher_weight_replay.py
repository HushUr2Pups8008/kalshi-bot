#!/usr/bin/env python3
"""Replay pre-threshold matcher weight logs against HEAD and dirty weights.

Reads MATCH_WEIGHT_APPLIED events from recent trade JSONL files. These events
already contain the pre-weight score and overlap tokens, so the replay can
compare two matcher_token_weights snapshots without invoking runtime services,
writing DBs, or resetting the working tree.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analysis.match_feedback import is_market_defining_token

try:
    from config import PAPER_MIN_MATCH_SCORE
except Exception:  # pragma: no cover - CLI fallback for isolated use
    PAPER_MIN_MATCH_SCORE = 0.06


EVENT_TYPES = {
    "EARLY_FRESH_PASS",
    "MATCH_WEIGHT_APPLIED",
    "MATCH_DIAGNOSTIC",
    "OPPORTUNITY",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_head_weights(repo_root: Path) -> dict[str, Any]:
    output = subprocess.check_output(
        ["git", "show", "HEAD:data/matcher_token_weights.json"],
        cwd=repo_root,
        text=True,
    )
    return json.loads(output)


def _coerce_weight(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def score_multiplier(tokens: list[str], prefix: str, weights: dict[str, Any]) -> float:
    """Mirror production mean composition for overlap-token downweights.

    Keeps parity with analysis.market_matcher._combined_token_downweight,
    including the defining-token guard: a market's own ticker-defining token is
    never downweighted (kept at 1.0) regardless of the stored weight.
    """
    values: list[float] = []
    for token in tokens:
        if is_market_defining_token(token, prefix):
            values.append(1.0)
            continue
        entry = weights.get(f"{prefix}:{token}", {})
        values.append(_coerce_weight(entry.get("weight", 1.0)))
    return sum(values) / len(values) if values else 1.0


def _prefix_from_ticker(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def _recent_jsonl_files(log_root: Path, recent_files: int) -> list[Path]:
    files = [p for p in log_root.rglob("*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:recent_files]


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                continue


def _score_record(
    *,
    record: dict[str, Any],
    path: Path,
    line_no: int,
    threshold: float,
    head_weights: dict[str, Any],
    dirty_weights: dict[str, Any],
) -> dict[str, Any] | None:
    ticker = str(record.get("ticker") or "")
    tokens = record.get("tokens") or []
    if not ticker or not isinstance(tokens, list):
        return None
    try:
        pre_weight_score = float(record.get("pre_weight_score"))
    except (TypeError, ValueError):
        return None

    prefix = str(record.get("market_prefix") or _prefix_from_ticker(ticker))
    token_strings = [str(token) for token in tokens]
    head_multiplier = score_multiplier(token_strings, prefix, head_weights)
    dirty_multiplier = score_multiplier(token_strings, prefix, dirty_weights)
    head_score = round(pre_weight_score * head_multiplier, 6)
    dirty_score = round(pre_weight_score * dirty_multiplier, 6)
    head_clears = head_score >= threshold
    dirty_clears = dirty_score >= threshold

    if head_clears and dirty_clears:
        classification = "both_clear"
    elif head_clears:
        classification = "head_only"
    elif dirty_clears:
        classification = "dirty_only"
    else:
        classification = "neither_clear"

    return {
        "file": str(path),
        "line": line_no,
        "ts": record.get("ts"),
        "source": record.get("source"),
        "headline": record.get("headline"),
        "ticker": ticker,
        "prefix": prefix,
        "market_title": record.get("market_title"),
        "tokens": token_strings,
        "pre_weight_score": pre_weight_score,
        "head_multiplier": round(head_multiplier, 6),
        "dirty_multiplier": round(dirty_multiplier, 6),
        "head_score": head_score,
        "dirty_score": dirty_score,
        "head_clears": head_clears,
        "dirty_clears": dirty_clears,
        "classification": classification,
    }


def _counter_to_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def replay(
    *,
    log_root: Path,
    dirty_weights_path: Path,
    head_weights_path: Path | None = None,
    recent_files: int = 3,
    threshold: float = PAPER_MIN_MATCH_SCORE,
    example_limit: int = 20,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    dirty_weights = _load_json(dirty_weights_path)
    head_weights = _load_json(head_weights_path) if head_weights_path else _load_head_weights(repo_root)
    files = _recent_jsonl_files(log_root, recent_files)

    summary = Counter()
    by_ticker: dict[str, Counter[str]] = defaultdict(Counter)
    by_prefix: dict[str, Counter[str]] = defaultdict(Counter)
    file_counts: dict[str, Counter[str]] = {str(path): Counter() for path in files}
    over_suppressed: list[dict[str, Any]] = []

    for path in files:
        for line_no, record in _iter_jsonl(path):
            event_type = record.get("type")
            if event_type in EVENT_TYPES:
                file_counts[str(path)][str(event_type)] += 1
            if event_type != "MATCH_WEIGHT_APPLIED":
                continue
            scored = _score_record(
                record=record,
                path=path,
                line_no=line_no,
                threshold=threshold,
                head_weights=head_weights,
                dirty_weights=dirty_weights,
            )
            if scored is None:
                continue
            classification = scored["classification"]
            summary["candidate_events"] += 1
            summary[classification] += 1
            if scored["head_clears"]:
                summary["head_clears"] += 1
            if scored["dirty_clears"]:
                summary["dirty_clears"] += 1
            by_ticker[scored["ticker"]][classification] += 1
            by_ticker[scored["ticker"]]["candidate_events"] += 1
            by_prefix[scored["prefix"]][classification] += 1
            by_prefix[scored["prefix"]]["candidate_events"] += 1
            if classification == "head_only":
                over_suppressed.append(scored)

    over_suppressed.sort(
        key=lambda row: (row["head_score"] - row["dirty_score"], row["head_score"]),
        reverse=True,
    )
    summary_payload = {
        "threshold": threshold,
        "log_files": len(files),
        "candidate_events": summary["candidate_events"],
        "head_clears": summary["head_clears"],
        "dirty_clears": summary["dirty_clears"],
        "head_only": summary["head_only"],
        "dirty_only": summary["dirty_only"],
        "both_clear": summary["both_clear"],
        "neither_clear": summary["neither_clear"],
    }
    return {
        "summary": summary_payload,
        "files": [str(path) for path in files],
        "file_counts": {path: _counter_to_dict(counts) for path, counts in file_counts.items()},
        "by_prefix": {key: _counter_to_dict(value) for key, value in sorted(by_prefix.items())},
        "by_ticker": {key: _counter_to_dict(value) for key, value in sorted(by_ticker.items())},
        "over_suppressed_examples": over_suppressed[:example_limit],
    }


def _print_text(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print(
        "Matcher weight replay: "
        f"threshold={summary['threshold']:.4f} files={summary['log_files']} "
        f"candidate_events={summary['candidate_events']}"
    )
    print(
        "Clears: "
        f"HEAD={summary['head_clears']} dirty={summary['dirty_clears']} "
        f"head_only={summary['head_only']} dirty_only={summary['dirty_only']} "
        f"both={summary['both_clear']} neither={summary['neither_clear']}"
    )
    print("\nFiles:")
    for path, counts in result["file_counts"].items():
        print(f"  {path}: {counts}")

    print("\nPrefixes with head_only:")
    prefix_rows = [
        (prefix, counts)
        for prefix, counts in result["by_prefix"].items()
        if counts.get("head_only", 0) > 0
    ]
    prefix_rows.sort(key=lambda row: row[1]["head_only"], reverse=True)
    for prefix, counts in prefix_rows[:20]:
        print(
            f"  {prefix}: head_only={counts.get('head_only', 0)} "
            f"candidates={counts.get('candidate_events', 0)}"
        )

    print("\nOver-suppressed examples:")
    for row in result["over_suppressed_examples"]:
        print(
            f"  {row['ticker']} head={row['head_score']:.4f} dirty={row['dirty_score']:.4f} "
            f"pre={row['pre_weight_score']:.4f} tokens={','.join(row['tokens'])} "
            f"headline={row.get('headline')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-root", type=Path, default=Path("logs/trades"))
    parser.add_argument("--dirty-weights-path", type=Path, default=Path("data/matcher_token_weights.json"))
    parser.add_argument("--head-weights-path", type=Path)
    parser.add_argument("--recent-files", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=PAPER_MIN_MATCH_SCORE)
    parser.add_argument("--example-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = replay(
        log_root=args.log_root,
        dirty_weights_path=args.dirty_weights_path,
        head_weights_path=args.head_weights_path,
        recent_files=args.recent_files,
        threshold=args.threshold,
        example_limit=args.example_limit,
        repo_root=Path.cwd(),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_text(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
