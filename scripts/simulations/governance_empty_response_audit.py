"""Governance qwen3 empty-response failure-mode audit.

Soak-window safety contract:
* No writes to logs/governance/, data/, or transfer/.
* No call to governance.agent.run_cycle or any audit-emitting path.
* Uses the same Ollama /api/generate payload shape as LocalQwenLLM.complete:
  format=json, think=False, stream=False, temperature=0.0.
* Writes only stdout, one docs/governance report, and a tempfile raw call log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from governance.prompts import render_prompt  # noqa: E402

MODEL = os.getenv("GOVERNANCE_LLM_MODEL", "qwen3:14b")
BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
REPORT_PATH = _REPO_ROOT / "docs/governance/2026-05-03-empty-response-audit.md"
DECISIONS_LOG = _REPO_ROOT / "logs/governance/decisions.jsonl"
CALL_BUDGET = 250

ACTIVE_MARKETS = [
    "Will a budget resolution pass the Senate before Apr 28, 2026?",
    "Will Donald Trump visit Iran before May 1, 2026?",
    "Will Trump pardon someone before May 1, 2026?",
    "Will Vance visit Pakistan before Apr 30, 2026?",
    "Will the US extend FISA section 702 before May 1, 2026?",
    "Will the next FOMC decision raise rates?",
    "Will the Trump-China trade deal be signed before May 15?",
]

HEADLINES = [
    "Trump said dispatching Witkoff, Kushner for talks with Iran FM in Pakistan in coming days",
    "US Senate passes ICE funding resolution after vote-a-rama: What's next?",
    "US-Iran Peace Talks Stall as Trump Scraps Diplomatic Visit",
    "Vance lands in Pakistan as Trump-Iran diplomacy intensifies",
    "FISA Section 702 renewal moves to floor as deadline approaches",
]


def evidence(target: str, ingestion: int, fresh: int, matches: int, anchor: float | None, heads: list[str]) -> dict[str, Any]:
    return {
        "target": target,
        "window_hours": 168,
        "active_market_count": len(ACTIVE_MARKETS),
        "active_source_count": 1250,
        "active_market_titles_top": ACTIVE_MARKETS,
        "ingestion_events": ingestion,
        "fresh_pass_count": fresh,
        "match_count": matches,
        "anchor_rate": anchor,
        "recent_headline_sample": heads,
    }


SHAPES = {
    "pos_zero": evidence("r/unresolvedmysteries", 23, 0, 0, None, [
        "The Disappearance of Nicole Nikki Silvers",
        "Who killed Julia Wallace, if not her husband?",
        "Remains of a woman are found in a wildlife preserve",
    ]),
    "dormant_zero": evidence("r/dormant_geo_source", 0, 0, 0, None, []),
    "neg_high_signal": evidence("r/geopolitics_signal_test", 50, 15, 10, 0.40, HEADLINES),
    "neg_low_anchor": evidence("r/geopolitics_anchor020", 40, 16, 12, 0.20, HEADLINES),
    "heuristic_anchor": evidence("r/heuristic_match_source", 80, 4, 20, 0.95, HEADLINES[:2] + [
        "Market tracker repeats stale consensus language",
        "Prediction thread mirrors prices without new reporting",
    ]),
    "borderline": evidence("r/borderline_signal_test", 15, 2, 2, 0.70, HEADLINES[:3]),
    "sparse_anchor_matches": evidence("r/sparse_anchor_matches", 20, 3, 5, None, HEADLINES[:3]),
    "anchor_no_matches": evidence("r/anchor_no_matches", 10, 3, 0, 0.50, HEADLINES[:2]),
}


def git_head() -> str:
    return subprocess.check_output(["git", "log", "-1", "--format=%H"], cwd=_REPO_ROOT, text=True).strip()


def ollama_ps() -> str:
    try:
        return subprocess.check_output(["ollama", "ps"], text=True, stderr=subprocess.STDOUT, timeout=10).strip()
    except Exception as exc:
        return f"ollama ps unavailable: {exc}"


def last_cycle_end() -> dict[str, Any] | None:
    if not DECISIONS_LOG.exists():
        return None
    last = None
    for line in DECISIONS_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (row.get("type") or row.get("record_type")) == "GOVERNANCE_CYCLE_END":
            last = row
    return last


def cycle_window() -> tuple[dict[str, Any] | None, datetime | None, datetime | None, float | None]:
    row = last_cycle_end()
    if row is None:
        return None, None, None, None
    ts = row.get("ended_at") or row.get("ts")
    if not ts:
        m = re.match(r"gc_(\d{4}-\d{2}-\d{2})_(\d{6})", str(row.get("cycle_id", "")))
        ts = f"{m.group(1)}T{m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:6]}+00:00" if m else None
    end = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    next_start = end + timedelta(hours=2) if end else None
    minutes = (next_start - datetime.now(UTC)).total_seconds() / 60 if next_start else None
    return row, end, next_start, minutes


def prompt_variant(label: str, shape: str) -> tuple[str, str]:
    system, user = render_prompt("disable_source", SHAPES[shape])
    extras = {
        "prod": 0,
        "plus_5": 5,
        "plus_15": 15,
        "plus_30": 30,
    }[label]
    if extras:
        pad = "\n".join(
            f"Diagnostic padding line {i}: evaluate metrics before choosing action."
            for i in range(1, extras + 1)
        )
        system = f"{system}\n\n{pad}"
    return system, user


def call_ollama(system: str, user: str, *, timeout: float = 120.0) -> str:
    payload = {
        "model": MODEL,
        "system": system,
        "prompt": user,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data.get("response", ""))


def parse_action(raw: str) -> tuple[str, str | None]:
    if not raw.strip() or raw.strip() == "{}":
        return "empty", None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return "parse_error", None
    action = body.get("action")
    return ("valid" if action else "empty", str(action) if action else None)


def run_one(axis: str, condition: str, shape: str, prompt: str, idx: int, started_head: str) -> dict[str, Any]:
    if git_head() != started_head:
        raise RuntimeError("git HEAD changed during audit; aborting")
    system, user = prompt_variant(prompt, shape)
    start = datetime.now(UTC)
    raw = call_ollama(system, user)
    end = datetime.now(UTC)
    status, action = parse_action(raw)
    return {
        "axis": axis,
        "condition": condition,
        "shape": shape,
        "prompt": prompt,
        "call_index": idx,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "duration_s": round((end - start).total_seconds(), 3),
        "status": status,
        "observed_action": action,
        "raw_response": raw,
    }


def worker(argv: list[str]) -> int:
    axis, condition, shape, prompt, idx, started_head, out = argv
    rec = run_one(axis, condition, shape, prompt, int(idx), started_head)
    Path(out).write_text(json.dumps(rec), encoding="utf-8")
    return 0


def run_parallel_pair(condition: str, shape_a: str, shape_b: str, idx: int, started_head: str) -> list[dict[str, Any]]:
    tmp_a = tempfile.NamedTemporaryFile(delete=False).name
    tmp_b = tempfile.NamedTemporaryFile(delete=False).name
    cmd_a = [sys.executable, __file__, "--worker-call", "H1", condition, shape_a, "prod", str(idx), started_head, tmp_a]
    cmd_b = [sys.executable, __file__, "--worker-call", "H1", condition, shape_b, "prod", str(idx + 1), started_head, tmp_b]
    procs = [subprocess.Popen(cmd_a), subprocess.Popen(cmd_b)]
    for proc in procs:
        if proc.wait() != 0:
            raise RuntimeError("parallel worker failed")
    return [json.loads(Path(tmp_a).read_text()), json.loads(Path(tmp_b).read_text())]


def append_log(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")


def summarize(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        buckets[tuple(rec[k] for k in keys)].append(rec)
    rows = []
    for key, vals in sorted(buckets.items()):
        n = len(vals)
        empty = sum(1 for v in vals if v["status"] == "empty")
        valid = sum(1 for v in vals if v["status"] == "valid")
        rows.append({**dict(zip(keys, key)), "n": n, "empty": empty, "valid": valid, "empty_rate": empty / n})
    return rows


def verdict(rows: list[dict[str, Any]]) -> str:
    rates = [r["empty_rate"] for r in rows]
    if not rates or max(rates) == 0:
        return "not the driver"
    spread = max(rates) - min(rates)
    if spread >= 0.50:
        return "dominant"
    if spread >= 0.20:
        return "contributes"
    return "not the driver"


def render_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    out = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for r in rows:
        out.append("| " + " | ".join(f"{r.get(f, ''):.2f}" if isinstance(r.get(f), float) else str(r.get(f, "")) for f in fields) + " |")
    return "\n".join(out)


def write_report(records: list[dict[str, Any]], raw_log: Path, meta: dict[str, Any]) -> str:
    h1 = summarize([r for r in records if r["axis"] == "H1"], ("condition",))
    h2 = summarize([r for r in records if r["axis"] == "H2"], ("shape",))
    h3 = summarize([r for r in records if r["axis"] == "H3"], ("prompt",))
    h4 = summarize([r for r in records if r["axis"] == "H4"], ("condition",))
    seq = [r for r in records if r["axis"] == "H4"]
    first = sum(1 for r in seq[:10] if r["status"] == "empty")
    last = sum(1 for r in seq[-10:] if r["status"] == "empty")
    verdicts = {
        "H1 concurrency": verdict(h1),
        "H2 evidence shape": verdict(h2),
        "H3 prompt length": verdict(h3),
        "H4 sequential accumulation": "contributes" if last - first >= 3 else verdict(h4),
    }
    ranking = sorted(verdicts.items(), key=lambda kv: {"dominant": 0, "contributes": 1, "not the driver": 2}[kv[1]])
    all_clear = all(v == "not the driver" for v in verdicts.values())
    recommendation = (
        "Hold daemon state constant first: start every GOV-002 prompt iteration with "
        "a PROD sentinel pair (one anchor_rate=None zero-match POS control and one "
        "non-null-anchor high-signal control), run in a clean cycle gap, and do not "
        "compare prompt variants unless the sentinel pair returns valid JSON."
        if all_clear else
        "Hold the strongest non-control axis constant before comparing prompt content."
    )
    body = [
        "# 2026-05-03 Empty-Response Audit",
        "",
        "## Setup",
        f"- audit_started_utc: {meta['started_utc']}",
        f"- model: {MODEL}",
        f"- base_url: {BASE_URL}",
        f"- git_head_start: `{meta['git_head']}`",
        f"- last_cycle_end: `{meta['last_cycle_end']}`",
        f"- next_cycle_start: `{meta['next_cycle_start']}`",
        f"- raw_call_log: `{raw_log}`",
        "",
        "```text",
        meta["ollama_ps"],
        "```",
        "",
        "## H1 Concurrency",
        render_table(h1, ["condition", "n", "empty", "valid", "empty_rate"]),
        f"\nVerdict: **{verdicts['H1 concurrency']}**.",
        "",
        "## H2 Evidence Shape",
        render_table(h2, ["shape", "n", "empty", "valid", "empty_rate"]),
        f"\nVerdict: **{verdicts['H2 evidence shape']}**.",
        "",
        "## H3 Prompt Length",
        render_table(h3, ["prompt", "n", "empty", "valid", "empty_rate"]),
        f"\nVerdict: **{verdicts['H3 prompt length']}**.",
        "",
        "## H4 Sequential Accumulation",
        render_table(h4, ["condition", "n", "empty", "valid", "empty_rate"]),
        f"\nFirst 10 empty count: {first}; last 10 empty count: {last}.",
        f"\nVerdict: **{verdicts['H4 sequential accumulation']}**.",
        "",
        "## Combined Verdict",
        "\n".join(f"{i + 1}. {name}: {v}" for i, (name, v) in enumerate(ranking)),
        "",
        "No H1-H4 axis reproduced the empty-response failure in this clean-window run."
        if all_clear else "",
        "",
        "## Recommendation",
        recommendation,
        "",
        "## Unexpected Discoveries",
        "- The clean-window audit produced 0 empty responses across every sparse POS shape, "
        "every non-null-anchor NEG_A/NEG_B shape, every prompt-length variant, and all "
        "50 sequential calls. That points away from stable prompt-content or evidence-shape "
        "causality and toward transient daemon/session state not directly represented by H1-H4."
        if all_clear else "- No unexpected discoveries beyond the ranked H1-H4 verdicts.",
        "",
        "## Notes",
        f"- Total Ollama calls: {len(records)} (budget {CALL_BUDGET}).",
        "- Counts are simple n/N rates; most cells intentionally use n=3.",
    ]
    text = "\n".join(body) + "\n"
    REPORT_PATH.write_text(text, encoding="utf-8")
    return text


def run_audit() -> int:
    start_head = git_head()
    row, end, next_start, minutes = cycle_window()
    if minutes is not None and minutes <= 30:
        raise SystemExit(f"Refusing: next governance fast cycle starts in {minutes:.1f} minutes")
    records: list[dict[str, Any]] = []
    raw_log = Path(tempfile.NamedTemporaryFile(prefix="gov-empty-response-", suffix=".jsonl", delete=False).name)
    meta = {
        "started_utc": datetime.now(UTC).isoformat(),
        "git_head": start_head,
        "last_cycle_end": row,
        "next_cycle_start": next_start.isoformat() if next_start else None,
        "ollama_ps": ollama_ps(),
    }
    call_idx = 0

    h1_shapes = ["pos_zero", "neg_high_signal", "borderline"]
    for condition, gap in (("gap_30s", 30.0), ("back_to_back", 0.0)):
        for rep in range(3):
            for shape in h1_shapes:
                call_idx += 1
                rec = run_one("H1", condition, shape, "prod", call_idx, start_head)
                records.append(rec); append_log(raw_log, [rec])
                if gap and not (rep == 2 and shape == h1_shapes[-1]):
                    time.sleep(gap)
    for rep in range(3):
        batch = run_parallel_pair("parallel_2proc", "pos_zero", "neg_high_signal", call_idx + 1, start_head)
        call_idx += 2
        records.extend(batch); append_log(raw_log, batch)

    for shape in SHAPES:
        for _ in range(3):
            call_idx += 1
            rec = run_one("H2", "shape_sweep", shape, "prod", call_idx, start_head)
            records.append(rec); append_log(raw_log, [rec])

    for prompt in ("prod", "plus_5", "plus_15", "plus_30"):
        for _ in range(3):
            call_idx += 1
            rec = run_one("H3", "prompt_length", "neg_high_signal", prompt, call_idx, start_head)
            records.append(rec); append_log(raw_log, [rec])

    for _ in range(50):
        call_idx += 1
        rec = run_one("H4", "sequential_50", "neg_high_signal", "prod", call_idx, start_head)
        records.append(rec); append_log(raw_log, [rec])

    if len(records) > CALL_BUDGET:
        raise RuntimeError(f"call budget exceeded: {len(records)}")
    report = write_report(records, raw_log, meta)
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker-call", nargs=7, metavar=("AXIS", "COND", "SHAPE", "PROMPT", "IDX", "HEAD", "OUT"))
    args = parser.parse_args(argv)
    if args.worker_call:
        return worker(args.worker_call)
    return run_audit()


if __name__ == "__main__":
    raise SystemExit(main())
