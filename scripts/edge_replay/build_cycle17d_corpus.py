#!/usr/bin/env python3
"""Build the locked Cycle-17D merged replay corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUTS = {
    "cycle13_live": REPO_ROOT / "logs/edge_replay/cycle13_live/replay_dataset.jsonl",
    "cycle13_local": REPO_ROOT / "logs/edge_replay/cycle13_local/replay_dataset.jsonl",
    "cycle15b": REPO_ROOT / "logs/edge_replay/cycle15b/replay_dataset.jsonl",
    "cycle16d": REPO_ROOT / "logs/edge_replay/cycle16d/replay_dataset.jsonl",
}
DEFAULT_OUTPUT = REPO_ROOT / "logs/edge_replay/cycle17d/replay_dataset_merged.jsonl"
DEFAULT_MANIFEST = REPO_ROOT / "logs/edge_replay/cycle17d/build_manifest.json"

DEDUP_KEY = ("ticker", "decision_ts", "signal_source", "headline")
REQUIRED_DEDUP_FIELDS = ("ticker", "decision_ts", "signal_source")
SOURCE_ORDER = ("cycle13_live", "cycle13_local", "cycle15b", "cycle16d")
SOURCE_PRECEDENCE = {
    "cycle13_local": 0,
    "cycle13_live": 1,
    "cycle15b": 2,
    "cycle16d": 3,
}
DEDUP_POLICY = {
    "key": list(DEDUP_KEY),
    "cycle13_conflicts": "prefer cycle13_live over cycle13_local",
    "other_conflicts": (
        "prefer rows with non-null market_yes_price; "
        "if tied prefer source precedence cycle16d > cycle15b > cycle13_live > cycle13_local; "
        "if still tied keep the first input row"
    ),
}


@dataclass(frozen=True)
class SelectedRow:
    source: str
    row: dict[str, Any]
    input_index: int


def _market_family_for(row: dict[str, Any]) -> str:
    return str(row.get("market_family") or row.get("series_ticker") or row.get("ticker") or "unknown")


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        missing = [field for field in REQUIRED_DEDUP_FIELDS if not parsed.get(field)]
        if missing:
            raise ValueError(f"{path}:{line_number} missing required dedup key field(s): {', '.join(missing)}")
        if not parsed.get("headline"):
            parsed["headline"] = ""
        rows.append(parsed)
    return rows


def _dedup_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in DEDUP_KEY)


def _has_price(row: dict[str, Any]) -> bool:
    return row.get("market_yes_price") is not None


def _cohort_for(source: str, row: dict[str, Any]) -> str:
    if row.get("cohort") == "POST_FIX_NEW":
        return "POST_FIX_NEW"
    if source in {"cycle13_live", "cycle13_local"}:
        return "PRE_FIX"
    return "POST_FIX_REBUILT"


def _record_drop(
    dropped: dict[str, Counter[str]],
    source: str,
    reason: str,
) -> None:
    dropped[source][reason] += 1


def _prefer_candidate(
    current: SelectedRow,
    candidate: SelectedRow,
) -> tuple[SelectedRow, str, str]:
    sources = {current.source, candidate.source}
    if sources == {"cycle13_live", "cycle13_local"}:
        if current.source == "cycle13_live":
            return current, candidate.source, "cycle13_live_preferred"
        return candidate, current.source, "cycle13_live_preferred"

    current_has_price = _has_price(current.row)
    candidate_has_price = _has_price(candidate.row)
    if current_has_price != candidate_has_price:
        if candidate_has_price:
            return candidate, current.source, "non_cycle13_conflict_priced_row_preferred"
        return current, candidate.source, "non_cycle13_conflict_priced_row_preferred"

    current_precedence = SOURCE_PRECEDENCE[current.source]
    candidate_precedence = SOURCE_PRECEDENCE[candidate.source]
    if candidate_precedence > current_precedence:
        return candidate, current.source, "non_cycle13_conflict_source_precedence_preferred"
    if candidate_precedence < current_precedence:
        return current, candidate.source, "non_cycle13_conflict_source_precedence_preferred"

    if candidate.input_index < current.input_index:
        return candidate, current.source, "non_cycle13_conflict_first_input_order_preferred"
    return current, candidate.source, "non_cycle13_conflict_first_input_order_preferred"


def _sort_key(item: SelectedRow) -> tuple[Any, ...]:
    row = item.row
    return (
        row.get("decision_ts") or "",
        row.get("ticker") or "",
        row.get("signal_source") or "",
        row.get("headline") or "",
        item.source,
    )


def _sorted_manifest_counts(dropped: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {
        source: dict(sorted(counter.items()))
        for source, counter in ((source, dropped[source]) for source in SOURCE_ORDER)
        if counter
    }


def build_cycle17d_corpus(
    *,
    inputs: dict[str, Path],
    output: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    selected: dict[tuple[Any, ...], SelectedRow] = {}
    input_row_counts: dict[str, int] = {}
    blank_headline_counts: Counter[str] = Counter()
    dropped: dict[str, Counter[str]] = defaultdict(Counter)
    input_index = 0

    for source in SOURCE_ORDER:
        path = inputs[source]
        rows = load_jsonl(path)
        input_row_counts[source] = len(rows)
        for row in rows:
            if row.get("headline") == "":
                blank_headline_counts[source] += 1
            prepared = dict(row)
            prepared["cohort"] = _cohort_for(source, row)
            prepared["market_family"] = _market_family_for(row)
            candidate = SelectedRow(source=source, row=prepared, input_index=input_index)
            input_index += 1

            key = _dedup_key(candidate.row)
            current = selected.get(key)
            if current is None:
                selected[key] = candidate
                continue

            winner, dropped_source, reason = _prefer_candidate(current, candidate)
            selected[key] = winner
            _record_drop(dropped, dropped_source, reason)

    output_rows = [item.row for item in sorted(selected.values(), key=_sort_key)]

    output.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in output_rows
    )
    output.write_bytes(output_bytes)

    cohort_counts = dict(sorted(Counter(row["cohort"] for row in output_rows).items()))
    manifest = {
        "input_paths": {source: _display_path(inputs[source]) for source in SOURCE_ORDER},
        "input_row_counts": input_row_counts,
        "output_row_count": len(output_rows),
        "dropped_duplicate_count": sum(sum(counter.values()) for counter in dropped.values()),
        "dropped_counts_by_source_reason": _sorted_manifest_counts(dropped),
        "cohort_counts": cohort_counts,
        "dedup_key_blank_headline_count_by_source": dict(sorted(blank_headline_counts.items())),
        "dedup_policy": DEDUP_POLICY,
        "output_path": _display_path(output),
        "sha256": hashlib.sha256(output_bytes).hexdigest(),
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle13-live", type=Path, default=DEFAULT_INPUTS["cycle13_live"])
    parser.add_argument("--cycle13-local", type=Path, default=DEFAULT_INPUTS["cycle13_local"])
    parser.add_argument("--cycle15b", type=Path, default=DEFAULT_INPUTS["cycle15b"])
    parser.add_argument("--cycle16d", type=Path, default=DEFAULT_INPUTS["cycle16d"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json", action="store_true", help="Print the full build manifest to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_cycle17d_corpus(
        inputs={
            "cycle13_live": args.cycle13_live,
            "cycle13_local": args.cycle13_local,
            "cycle15b": args.cycle15b,
            "cycle16d": args.cycle16d,
        },
        output=args.output,
        manifest_path=args.manifest,
    )
    if args.json:
        print(json.dumps(manifest, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "output_row_count": manifest["output_row_count"],
                    "dropped_duplicate_count": manifest["dropped_duplicate_count"],
                    "output_path": manifest["output_path"],
                    "sha256": manifest["sha256"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
