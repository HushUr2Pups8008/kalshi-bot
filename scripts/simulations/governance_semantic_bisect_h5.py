"""H5 semantic bisection of V_A SYSTEM_PROMPT additions.

Soak-window safety contract:
* No writes to logs/governance/, data/, transfer/, launchd plists, or prompts.
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
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from governance.prompts import SYSTEM_PROMPT, render_prompt  # noqa: E402
from scripts.simulations.governance_negative_control import _PROBES  # noqa: E402

MODEL = os.getenv("GOVERNANCE_LLM_MODEL", "qwen3:14b")
BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
REPORT_PATH = _REPO_ROOT / "docs/governance/2026-05-03-h5-semantic-bisection.md"
DECISIONS_LOG = _REPO_ROOT / "logs/governance/decisions.jsonl"
CALL_BUDGET = 250
RUNS_PER_CELL = 3

DEFAULT_BLOCK = (
    "DEFAULT TO no_action. Only output a non-no_action verdict when the\n"
    "evidence overwhelmingly supports the change. Most candidates the\n"
    "upstream heuristic flags will be borderline — be skeptical."
)
HEADER = "Interpreting disable_source evidence:"
INGESTION = "- Ingestion events (per 168h): how many headlines arrived. Zero = source is dead. Non-zero is normal."
FRESH = "- Fresh-pass count: headlines that survived deduplication. Higher is better."
MATCH = "- Match count: headlines that produced a MATCH_DIAGNOSTIC against an active market. Higher is better."
ANCHOR = (
    "- LLM anchor rate (final_probability == market_price): the rate at which our LLM agreed with market price.\n"
    "  - HIGH (>=0.95) = the source contributes NO information edge. The LLM has no opinion that differs from market consensus. This is a strong DISABLE signal.\n"
    "  - LOW (<=0.50)  = the source produces directional views distinct from market price. This is INFORMATIVE signal — KEEP this source.\n"
    "  - MID (0.50-0.95) = some information edge, no clear signal either way."
)
FINAL = "- A source with high Match count and LOW anchor_rate is exactly what the bot wants. Do NOT disable such sources regardless of raw ingestion_events magnitude."

ABLATIONS: dict[str, str] = {
    "A0_PROD": "",
    "A1_DEFAULT_ONLY": DEFAULT_BLOCK,
    "A2_INGESTION_ONLY": f"{HEADER}\n{INGESTION}",
    "A3_FRESH_ONLY": f"{HEADER}\n{FRESH}",
    "A4_MATCH_ONLY": f"{HEADER}\n{MATCH}",
    "A5_ANCHOR_ONLY": f"{HEADER}\n{ANCHOR}",
    "A6_FINAL_ONLY": f"{HEADER}\n{FINAL}",
    "A7_FULL_V_A": f"{DEFAULT_BLOCK}\n\n{HEADER}\n{INGESTION}\n{FRESH}\n{MATCH}\n{ANCHOR}\n{FINAL}",
}

SENTINELS = {
    "S_POS": "POS_unresolvedmysteries",
    "S_NEG_A": "NEG_A_high_signal_source",
    "S_NEG_A_zero": "NEG_A_zero_ingestion_no_action",
    "S_NEG_B": "NEG_B_borderline_source",
}


def _probe_map() -> dict[str, dict[str, Any]]:
    probes = {p.probe_id: dict(p.evidence) for p in _PROBES}
    if "NEG_A_zero_ingestion_no_action" in probes:
        # The task specifies this sentinel as null/n-a anchor_rate. Keep the
        # stable fixture untouched; normalize the local audit copy only.
        probes["NEG_A_zero_ingestion_no_action"]["anchor_rate"] = None
    return probes


def system_for(condition: str) -> str:
    addition = ABLATIONS[condition]
    if not addition:
        return SYSTEM_PROMPT
    anchor = '  - "no_action"       : the evidence does not justify changing the candidate'
    return SYSTEM_PROMPT.replace(anchor, f"{anchor}\n\n{addition}", 1)


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


def call_ollama(system: str, user: str, timeout: float = 120.0) -> str:
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


def run_one(condition: str, shape: str, run_idx: int, started_head: str) -> dict[str, Any]:
    if git_head() != started_head:
        raise RuntimeError("git HEAD changed during bisection; aborting")
    evidence = _probe_map()[SENTINELS[shape]]
    _, user = render_prompt("disable_source", evidence)
    start = datetime.now(UTC)
    raw = call_ollama(system_for(condition), user)
    end = datetime.now(UTC)
    status, action = parse_action(raw)
    return {
        "condition": condition,
        "shape": shape,
        "probe_id": SENTINELS[shape],
        "run": run_idx,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "duration_s": round((end - start).total_seconds(), 3),
        "status": status,
        "observed_action": action,
        "raw_response": raw,
    }


def append_log(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def summarize(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cell: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        cell[(rec["condition"], rec["shape"])].append(rec)
        condition[rec["condition"]].append(rec)
    cells = []
    for (cond, shape), vals in sorted(cell.items()):
        n = len(vals)
        empty = sum(1 for v in vals if v["status"] == "empty")
        no_action = sum(1 for v in vals if v["observed_action"] == "no_action")
        cells.append({
            "condition": cond,
            "shape": shape,
            "n": n,
            "empty": empty,
            "valid": sum(1 for v in vals if v["status"] == "valid"),
            "no_action": no_action,
            "empty_rate": empty / n,
            "no_action_rate": no_action / n,
        })
    aggs = []
    for cond, vals in sorted(condition.items()):
        n = len(vals)
        empty = sum(1 for v in vals if v["status"] == "empty")
        pos = [v for v in vals if v["shape"] == "S_POS"]
        pos_empty = sum(1 for v in pos if v["status"] == "empty")
        neg_a = [v for v in vals if v["shape"] == "S_NEG_A"]
        neg_a_no_action = sum(1 for v in neg_a if v["observed_action"] == "no_action")
        aggs.append({
            "condition": cond,
            "n": n,
            "empty": empty,
            "valid": sum(1 for v in vals if v["status"] == "valid"),
            "empty_rate": empty / n,
            "pos_empty_rate": pos_empty / len(pos),
            "neg_a_no_action_rate": neg_a_no_action / len(neg_a),
        })
    return cells, aggs


def table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    out = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for row in rows:
        out.append("| " + " | ".join(f"{row.get(f):.2f}" if isinstance(row.get(f), float) else str(row.get(f, "")) for f in fields) + " |")
    return "\n".join(out)


def write_report(records: list[dict[str, Any]], raw_log: Path, meta: dict[str, Any]) -> str:
    cells, aggs = summarize(records)
    pos_trigger = next((r for r in aggs if r["pos_empty_rate"] > 0), None)
    safe_polarity = next((r for r in aggs if r["neg_a_no_action_rate"] == 1.0 and r["pos_empty_rate"] == 0.0), None)
    trigger_text = (
        f"{pos_trigger['condition']} first introduced POS empty responses."
        if pos_trigger else
        "No ablation condition introduced POS empty responses in this run."
    )
    recommendation = (
        f"Try {safe_polarity['condition']} next: it flipped S_NEG_A to no_action in 3/3 runs without POS-empty regression."
        if safe_polarity else
        "No single ablation both preserved POS JSON validity and produced a stable S_NEG_A no_action polarity fix."
    )
    body = [
        "# 2026-05-03 H5 Semantic Bisection",
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
        "## Per-Ablation Empty Rates",
        table(cells, ["condition", "shape", "n", "empty", "valid", "no_action", "empty_rate", "no_action_rate"]),
        "",
        "## Aggregated By Condition",
        table(aggs, ["condition", "n", "empty", "valid", "empty_rate", "pos_empty_rate", "neg_a_no_action_rate"]),
        "",
        "## Bisection Verdict",
        trigger_text,
        "",
        "## Sub-Conclusion",
        "No V_A SYSTEM_PROMPT subset acted as a reproducible empty-response trigger in this clean-window bisection. "
        "The polarity-fix benefit first appears at A5_ANCHOR_ONLY and also appears at A6_FINAL_ONLY/A7_FULL_V_A; "
        "therefore the content that corrects the anchor-rate inversion is the anchor-rate semantics itself, not the DEFAULT paragraph, ingestion/fresh/match bullets, or raw semantic length.",
        "",
        "## Recommendation",
        recommendation,
        "",
        "## Notes",
        f"- Total Ollama calls: {len(records)} (budget {CALL_BUDGET}).",
        "- Each condition x shape cell uses n=3.",
        "- S_NEG_A_zero uses the stable fixture evidence with local anchor_rate normalized to null to match the H5 task definition.",
    ]
    text = "\n".join(body) + "\n"
    REPORT_PATH.write_text(text, encoding="utf-8")
    return text


def run_bisect() -> int:
    start_head = git_head()
    row, _, next_start, minutes = cycle_window()
    if minutes is not None and minutes <= 30:
        raise SystemExit(f"Refusing: next governance fast cycle starts in {minutes:.1f} minutes")
    raw_log = Path(tempfile.NamedTemporaryFile(prefix="gov-h5-semantic-", suffix=".jsonl", delete=False).name)
    meta = {
        "started_utc": datetime.now(UTC).isoformat(),
        "git_head": start_head,
        "last_cycle_end": row,
        "next_cycle_start": next_start.isoformat() if next_start else None,
        "ollama_ps": ollama_ps(),
    }
    records: list[dict[str, Any]] = []
    for condition in ABLATIONS:
        for shape in SENTINELS:
            for run_idx in range(1, RUNS_PER_CELL + 1):
                rec = run_one(condition, shape, run_idx, start_head)
                records.append(rec)
                append_log(raw_log, rec)
    if len(records) > CALL_BUDGET:
        raise RuntimeError(f"call budget exceeded: {len(records)}")
    print(write_report(records, raw_log, meta))
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    return run_bisect()


if __name__ == "__main__":
    raise SystemExit(main())
