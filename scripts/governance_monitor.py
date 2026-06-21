"""Daily governance decisions.jsonl monitor.

Read-only aggregator for Phase 2 shadow-soak monitoring. It reads the single
``logs/governance/decisions.jsonl`` file, emits per-day counts, target/action
diversity, effective sample size, reasoning n-grams, and the §8.5 status header.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
# PROFIT-GOV-003: drop `KALSHI_HOME` as the implicit log-file root. The
# operator's shell exports `KALSHI_HOME=…/kalshi_bot` (underscore) while
# the active repo lives at `…/kalshi-bot` (hyphen), so the env override
# silently pointed the monitor at a non-existent path. The script lives
# inside the repo it monitors; `_REPO_ROOT` is unambiguous and survives
# `cd` from any working directory. Operators wanting a foreign file pass
# `--logfile` explicitly.
_DEFAULT_LOG = _REPO_ROOT / "logs/governance/decisions.jsonl"
_DEFAULT_OVERRIDES = _REPO_ROOT / "data/runtime_overrides.yaml"


def _record_type(r: dict[str, Any]) -> str:
    return str(r.get("type") or r.get("record_type") or "")


def _date_from_record(r: dict[str, Any]) -> str:
    if _record_type(r) == "GOVERNANCE_DECISION" and r.get("decision_id"):
        m = re.match(r"gd_(\d{4}-\d{2}-\d{2})_", str(r["decision_id"]))
        if m:
            return m.group(1)
    if r.get("cycle_id"):
        m = re.match(r"gc_(\d{4}-\d{2}-\d{2})_", str(r["cycle_id"]))
        if m:
            return m.group(1)
    if r.get("decision_id"):
        m = re.match(r"gd_(\d{4}-\d{2}-\d{2})_", str(r["decision_id"]))
        if m:
            return m.group(1)
    return "unknown"


def _load_records(path: Path, since: str | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        d = _date_from_record(r)
        if since and d != "unknown" and d < since:
            continue
        out.append(r)
    return out


def _has_applied_entries(path: Path) -> bool:
    if not path.exists():
        return False
    in_applied = False
    base_indent = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if re.match(r"^applied:\s*(\[\s*\])?\s*$", stripped):
            in_applied = True
            base_indent = indent
            if stripped not in {"applied:", "applied: []"}:
                return True
            continue
        if in_applied and indent <= base_indent and not stripped.startswith("-"):
            in_applied = False
        if in_applied:
            if stripped.startswith("-") or re.match(r"^[A-Za-z0-9_-]+:\s*.+", stripped):
                return True
    return False


def _fivegrams(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    return [" ".join(words[i:i + 5]) for i in range(max(0, len(words) - 4))]


def _first_decision_time(decisions: list[dict[str, Any]]) -> datetime | None:
    for r in sorted(decisions, key=lambda x: str(x.get("decision_id", ""))):
        ts = r.get("decided_at") or r.get("ts")
        if ts:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        m = re.match(r"gd_(\d{4}-\d{2}-\d{2})_", str(r.get("decision_id", "")))
        if m:
            return datetime.combine(date.fromisoformat(m.group(1)), datetime.min.time(), tzinfo=UTC)
    return None


def analyze(
    logfile: Path = _DEFAULT_LOG,
    *,
    since: str | None = None,
    overrides: Path = _DEFAULT_OVERRIDES,
    now: datetime | None = None,
) -> dict[str, Any]:
    records = _load_records(logfile, since)
    days: dict[str, Counter] = defaultdict(Counter)
    decisions = []
    actions: Counter = Counter()
    targets: Counter = Counter()
    tuples: Counter = Counter()
    grams: Counter = Counter()
    deep_cycles = set()

    for r in records:
        typ = _record_type(r)
        d = _date_from_record(r)
        if d != "unknown" or d in days:
            days[d]
        if typ == "GOVERNANCE_CYCLE_START":
            days[d]["cycles_started"] += 1
            if r.get("cadence") == "deep":
                deep_cycles.add(r.get("cycle_id") or f"{d}:{days[d]['cycles_started']}")
        elif typ == "GOVERNANCE_CYCLE_END":
            days[d]["cycles_ended"] += 1
            # PROFIT-GOV-003: `batch_aborted` rides as a boolean on the
            # cycle-end record, not as its own event type.
            if r.get("batch_aborted"):
                days[d]["batch_aborted"] += 1
        elif typ == "GOVERNANCE_DECISION":
            days[d]["decisions"] += 1
            decisions.append(r)
            action = str(r.get("action") or "")
            target = str(r.get("target") or "")
            reasoning = str(r.get("reasoning") or "")
            actions[action] += 1
            targets[target] += 1
            tuples[(target, reasoning)] += 1
            grams.update(_fivegrams(reasoning))
        # PROFIT-GOV-003: actual emitted event types are prefixed with
        # `GOVERNANCE_DECISION_` / `GOVERNANCE_`; the previous bare-name
        # set never matched anything in the live JSONL, so the per-day
        # counters silently stayed at zero. `batch_aborted` rides as a
        # boolean field on `GOVERNANCE_CYCLE_END`, not a separate event.
        elif typ in {
            "GOVERNANCE_DECISION_PARSE_ERROR",
            "GOVERNANCE_DECISION_VALIDATION_ERROR",
            "GOVERNANCE_KILL_SWITCH",
        }:
            key = typ.removeprefix("GOVERNANCE_DECISION_").removeprefix("GOVERNANCE_").lower()
            days[d][key] += 1

    for d in list(days):
        for k in ("cycles_started", "cycles_ended", "decisions", "parse_error",
                  "validation_error", "batch_aborted", "kill_switch"):
            days[d][k] += 0

    first_ts = _first_decision_time(decisions)
    now = now or datetime.now(UTC)
    elapsed_days = None if first_ts is None else (now - first_ts).total_seconds() / 86400
    raw = len(decisions)
    distinct = len(tuples)
    applied_ok = not _has_applied_entries(overrides)
    kill_count = sum(c["kill_switch"] for c in days.values())
    status = {
        "time": "PASS" if elapsed_days is not None and elapsed_days >= 14 else "IN_PROGRESS",
        "volume": "PASS" if raw >= 30 else "IN_PROGRESS",
        "cadence_coverage": "PASS" if len(deep_cycles) >= 7 else "IN_PROGRESS",
        "candidate_diversity": "PASS" if len(targets) >= 3 else "FAIL",
        "safety_applied_no_growth": "PASS" if applied_ok else "FAIL",
        "safety_kill_switch": "PASS" if kill_count == 0 else "FAIL",
        "quality": "HALTED - see PROFIT-GOV-002",
    }
    if not overrides.exists():
        status["safety_applied_no_growth"] = "PASS (vacuously satisfied)"
    return {
        "logfile": str(logfile),
        "since": since,
        "section_8_5": status,
        "elapsed_days_since_first_decision": elapsed_days,
        "per_day": {d: dict(c) for d, c in sorted(days.items()) if d != "unknown" or sum(c.values())},
        "actions": dict(actions),
        "target_count": len(targets),
        "targets": dict(targets.most_common()),
        "target_diversity_floor": "PASS" if len(targets) >= 3 else "FAIL",
        "raw_decision_count": raw,
        "effective_sample_size": distinct,
        "duplication_ratio": (raw / distinct if distinct else 0.0),
        "top_8_reasoning_5grams": dict(grams.most_common(8)),
    }


def render(report: dict[str, Any]) -> str:
    lines = ["§8.5 status"]
    for k, v in report["section_8_5"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "Per-day cycles", "date | cycles_started | cycles_ended | decisions | parse_errors | validation_errors | batch_aborted | kill_switch"]
    for d, c in report["per_day"].items():
        lines.append(
            f"{d} | {c['cycles_started']} | {c['cycles_ended']} | {c['decisions']} | "
            f"{c['parse_error']} | {c['validation_error']} | {c['batch_aborted']} | {c['kill_switch']}"
        )
    lines += ["", "Action distribution"]
    lines += [f"- {k}: {v}" for k, v in report["actions"].items()] or ["- none"]
    lines += ["", f"Target diversity: {report['target_count']} distinct targets ({report['target_diversity_floor']} >=3 floor)"]
    lines += [f"- {k}: {v}" for k, v in report["targets"].items()] or ["- none"]
    raw = report["raw_decision_count"]
    eff = report["effective_sample_size"]
    lines += [
        "",
        f"effective_sample_size = {eff} distinct of {raw} raw ({report['duplication_ratio']:.2f}x duplication)",
        "",
        "Top 8 reasoning 5-grams",
    ]
    lines += [f"- {k}: {v}" for k, v in report["top_8_reasoning_5grams"].items()] or ["- none"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--logfile", type=Path, default=_DEFAULT_LOG)
    p.add_argument("--since", help="YYYY-MM-DD; default all dates")
    p.add_argument("--json", action="store_true", help="emit JSON")
    args = p.parse_args(argv)
    report = analyze(args.logfile, since=args.since)
    if args.json:
        print(json.dumps(report, separators=(",", ":"), default=str))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
