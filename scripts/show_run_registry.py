#!/usr/bin/env python3
"""Show recent local test runs from logs/tests/run_registry.jsonl."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT_DIR / "logs" / "tests" / "run_registry.jsonl"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_duration(start: str | None, end: str | None, status: str) -> str:
    start_dt = parse_time(start)
    end_dt = parse_time(end)
    if not start_dt:
        return "-"
    if not end_dt:
        if status == "running":
            end_dt = datetime.now(timezone.utc)
        else:
            return "-"
    seconds = max(0, int((end_dt - start_dt).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def compact_command(command: Any, max_len: int = 44) -> str:
    if isinstance(command, list):
        text = " ".join(str(part) for part in command)
    else:
        text = str(command or "")
    return fit_cell(text, max_len)


def fit_cell(value: Any, width: int) -> str:
    text = str(value)
    if len(text) > width:
        if width <= 3:
            text = text[:width]
        else:
            text = text[: width - 3] + "..."
    return text.ljust(width)


def load_runs(registry: Path) -> list[dict[str, Any]]:
    latest_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    if not registry.exists():
        return []

    with registry.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                row = {
                    "run_id": f"malformed:{line_no}",
                    "kind": "unknown",
                    "status": "malformed",
                    "start_time_utc": None,
                    "end_time_utc": None,
                    "exit_status": None,
                    "git_commit": "",
                    "command": [f"malformed registry row {line_no}"],
                    "log_file": str(registry),
                    "metadata_file": "",
                    "detached": False,
                }

            run_id = str(row.get("run_id") or f"missing:{line_no}")
            if run_id not in latest_by_id:
                order.append(run_id)
            previous = latest_by_id.get(run_id, {})
            merged = {**previous, **row}
            if previous.get("start_time_utc") and not row.get("start_time_utc"):
                merged["start_time_utc"] = previous["start_time_utc"]
            latest_by_id[run_id] = merged

    runs = [latest_by_id[run_id] for run_id in order]
    return sorted(runs, key=lambda row: row.get("start_time_utc") or "", reverse=True)


def print_runs(runs: list[dict[str, Any]]) -> None:
    headers = ["start", "dur", "status", "code", "kind", "commit", "cmd", "log"]
    widths = [20, 8, 12, 5, 8, 8, 44, 34]
    print(" ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print(" ".join("-" * width for width in widths))

    for run in runs:
        start = str(run.get("start_time_utc") or "-")
        duration = format_duration(
            run.get("start_time_utc"),
            run.get("end_time_utc"),
            str(run.get("status") or ""),
        )
        status = str(run.get("status") or "-")
        code = "-" if run.get("exit_status") is None else str(run.get("exit_status"))
        kind = str(run.get("kind") or "-")
        commit = str(run.get("git_commit") or "-")[:7]
        command = compact_command(run.get("command"))
        log_file = Path(str(run.get("log_file") or "-")).name

        values = [start, duration, status, code, kind, commit, command, log_file]
        print(" ".join(fit_cell(value, width) for value, width in zip(values, widths)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Show recent local run history.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--status")
    parser.add_argument("--failed", action="store_true", help="Shortcut for --status failed.")
    parser.add_argument("--running", action="store_true", help="Shortcut for --status running.")
    parser.add_argument("--kind")
    args = parser.parse_args()

    runs = load_runs(Path(args.registry))
    statuses = set()
    if args.status:
        statuses.add(args.status)
    if args.failed:
        statuses.add("failed")
    if args.running:
        statuses.add("running")
    if statuses:
        runs = [run for run in runs if run.get("status") in statuses]
    if args.kind:
        runs = [run for run in runs if run.get("kind") == args.kind]
    runs = runs[: max(args.limit, 0)]

    if not runs:
        print("No runs found.")
        return 0

    print_runs(runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
