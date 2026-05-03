"""Governance qwen3 cumulative-call empty-response audit.

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
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from governance.prompts import render_prompt  # noqa: E402
from scripts.simulations.governance_negative_control import _PROBES  # noqa: E402

MODEL = os.getenv("GOVERNANCE_LLM_MODEL", "qwen3:14b")
BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DECISIONS_LOG = _REPO_ROOT / "logs/governance/decisions.jsonl"
REPORT_PATH = _REPO_ROOT / "docs/governance/2026-05-03-cumulative-call-audit.md"
CALL_BUDGET = 250
LOAD_CALLS = 200
RECOVERY_CALLS = 10


def git_head() -> str:
    return subprocess.check_output(["git", "log", "-1", "--format=%H"], text=True).strip()


def run_cmd(cmd: list[str], timeout: float = 30.0) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return proc.returncode, sanitize_output(proc.stdout + proc.stderr)


def sanitize_output(value: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    text = re.sub(r"\x1b[()#][0-9A-Za-z]", "", text)
    return text.strip()


def ollama_ps() -> str:
    return run_cmd(["ollama", "ps"])[1]


def ollama_version() -> str:
    return run_cmd(["ollama", "--version"])[1]


def daemon_uptime() -> str:
    code, pids = run_cmd(["pgrep", "ollama"])
    if code != 0 or not pids.strip():
        return "unknown"
    pid = pids.splitlines()[0].strip()
    return run_cmd(["ps", "-o", "etime=", "-p", pid])[1].strip() or "unknown"


def stop_model() -> str:
    code, out = run_cmd(["ollama", "stop", MODEL], timeout=60.0)
    if code == 0:
        return f"ollama stop {MODEL}: {out or 'ok'}"
    code2, out2 = run_cmd(["ollama", "run", MODEL, "", "--keepalive", "0s"], timeout=120.0)
    if code2 == 0:
        return f"fallback ollama run {MODEL} '' --keepalive 0s: {out2 or 'ok'}"
    raise RuntimeError(f"unable to unload {MODEL}: stop={out}; fallback={out2}")


def wait_unloaded() -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if MODEL not in ollama_ps():
            time.sleep(30)
            return
        time.sleep(2)
    raise RuntimeError(f"{MODEL} still listed in ollama ps after unload attempt")


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


def reference_evidence() -> dict[str, Any]:
    for probe in _PROBES:
        if probe.probe_id == "POS_unresolvedmysteries":
            return dict(probe.evidence)
    raise RuntimeError("POS_unresolvedmysteries probe not found")


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


def parse_response(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped or stripped == "{}":
        return {"status": "empty", "action": "", "confidence": None, "reasoning_length": 0}
    try:
        body = json.loads(stripped)
    except json.JSONDecodeError:
        return {"status": "parse_error", "action": "", "confidence": None, "reasoning_length": 0}
    action = str(body.get("action") or "")
    reasoning = str(body.get("reasoning") or "")
    return {
        "status": "valid" if action else "empty",
        "action": action,
        "confidence": body.get("confidence"),
        "reasoning_length": len(reasoning),
    }


def run_one(phase: str, call_index: int, started_head: str, baseline_start: datetime, system: str, user: str) -> dict[str, Any]:
    if git_head() != started_head:
        raise RuntimeError("git HEAD changed during audit; aborting")
    start = datetime.now(UTC)
    raw = call_ollama(system, user)
    end = datetime.now(UTC)
    parsed = parse_response(raw)
    return {
        "phase": phase,
        "call_index": call_index,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "duration_sec": (end - start).total_seconds(),
        "elapsed_sec": (end - baseline_start).total_seconds(),
        "raw_response_length": len(raw),
        **parsed,
    }


def append_record(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")


def ten_call_windows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    load = [r for r in records if r["phase"] == "load"]
    for start in range(1, LOAD_CALLS + 1, 10):
        vals = [r for r in load if start <= r["call_index"] < start + 10]
        empty = sum(1 for r in vals if r["status"] != "valid")
        rows.append({
            "window": f"{start}-{start + len(vals) - 1}",
            "n": len(vals),
            "empty": empty,
            "empty_rate": empty / len(vals) if vals else 0.0,
        })
    return rows


def thresholds(records: list[dict[str, Any]]) -> dict[str, int | None]:
    out = {"25%": None, "50%": None, "75%": None}
    load = [r for r in records if r["phase"] == "load"]
    for i in range(10, len(load) + 1):
        window = load[i - 10:i]
        rate = sum(1 for r in window if r["status"] != "valid") / 10
        for label, threshold in (("25%", 0.25), ("50%", 0.50), ("75%", 0.75)):
            if out[label] is None and rate >= threshold:
                out[label] = load[i - 1]["call_index"]
    return out


def verdict(records: list[dict[str, Any]]) -> str:
    load = [r for r in records if r["phase"] == "load"]
    recovery = [r for r in records if r["phase"] == "recovery"]
    max_window = max((r["empty_rate"] for r in ten_call_windows(records)), default=0.0)
    recovery_empty = sum(1 for r in recovery if r["status"] != "valid")
    if max_window > 0.50 and recovery_empty == 0:
        return "confirmed"
    if max_window == 0.0:
        return "rejected"
    return "inconclusive"


def render_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(
            f"{row.get(f, ''):.2f}" if isinstance(row.get(f), float) else str(row.get(f, ""))
            for f in fields
        ) + " |")
    return "\n".join(lines)


def write_report(records: list[dict[str, Any]], raw_log: Path, meta: dict[str, Any]) -> str:
    load = [r for r in records if r["phase"] == "load"]
    recovery = [r for r in records if r["phase"] == "recovery"]
    status_counts = Counter(r["status"] for r in records)
    recovery_empty = sum(1 for r in recovery if r["status"] != "valid")
    rows = ten_call_windows(records)
    crit = thresholds(records)
    result = verdict(records)
    rec = (
        "File candidate PROFIT-GOV-003 at LOW if this run confirmed sustained post-threshold empties; "
        "otherwise close the cumulative-call question and keep prompt iteration gated by clean sentinels."
    )
    per_call = [
        {
            "call_index": r["call_index"],
            "empty": r["status"] != "valid",
            "action": r["action"],
            "confidence": r["confidence"],
            "elapsed_sec": r["elapsed_sec"],
        }
        for r in load
    ]
    body = f"""# Cumulative-Call Empty-Response Audit

## Setup

- audit_started_utc: {meta['audit_started_utc']}
- git_head: {meta['git_head']}
- model: {MODEL}
- base_url: {BASE_URL}
- ollama_version: {meta['ollama_version']}
- daemon_uptime_before: {meta['daemon_uptime_before']}
- unload_method: {meta['unload_method']}
- cycle_id: {meta['cycle_id']}
- last_cycle_end_utc: {meta['last_cycle_end_utc']}
- next_cycle_start_utc: {meta['next_cycle_start_utc']}
- minutes_until_next_cycle_at_start: {meta['minutes_until_next_cycle_at_start']:.1f}
- raw_call_log: {raw_log}
- status_counts: {dict(status_counts)}

## Verdict

H_CUMUL: **{result}**.

Empirical thresholds from 10-call trailing windows:

| threshold | N_crit |
| --- | --- |
| 25% | {crit['25%']} |
| 50% | {crit['50%']} |
| 75% | {crit['75%']} |

Recovery batch empty rate: {recovery_empty}/{len(recovery)} ({(recovery_empty / len(recovery) if recovery else 0):.1%}).

Recommendation: {rec}

## Empty Rate By 10-Call Window

{render_table(rows, ['window', 'n', 'empty', 'empty_rate'])}

## Per-Call Result Table

{render_table(per_call, ['call_index', 'empty', 'action', 'confidence', 'elapsed_sec'])}
"""
    REPORT_PATH.write_text(body, encoding="utf-8")
    return result


def run_audit() -> tuple[str, Path]:
    if 1 + LOAD_CALLS + RECOVERY_CALLS > CALL_BUDGET:
        raise RuntimeError("call budget exceeded before audit start")
    started_head = git_head()
    row, end, next_start, minutes = cycle_window()
    if minutes is not None and minutes < 30:
        raise RuntimeError(f"next governance fast cycle starts in {minutes:.1f} minutes; refusing to run")

    meta = {
        "audit_started_utc": datetime.now(UTC).isoformat(),
        "git_head": started_head,
        "ollama_version": ollama_version(),
        "daemon_uptime_before": daemon_uptime(),
        "cycle_id": row.get("cycle_id") if row else None,
        "last_cycle_end_utc": end.isoformat() if end else None,
        "next_cycle_start_utc": next_start.isoformat() if next_start else None,
        "minutes_until_next_cycle_at_start": minutes if minutes is not None else 9999.0,
    }
    meta["unload_method"] = stop_model()
    wait_unloaded()
    system, user = render_prompt("disable_source", reference_evidence())
    raw_log = Path(tempfile.NamedTemporaryFile(prefix="gov-cumulative-", suffix=".jsonl", delete=False).name)
    records: list[dict[str, Any]] = []
    baseline_start = datetime.now(UTC)
    warmup = run_one("warmup", 0, started_head, baseline_start, system, user)
    append_record(raw_log, warmup)
    records.append(warmup)
    if warmup["status"] != "valid":
        raise RuntimeError(f"cold-load warmup returned {warmup['status']}; aborting")
    for idx in range(1, LOAD_CALLS + 1):
        rec = run_one("load", idx, started_head, baseline_start, system, user)
        append_record(raw_log, rec)
        records.append(rec)
    meta["recovery_unload_method"] = stop_model()
    wait_unloaded()
    for idx in range(1, RECOVERY_CALLS + 1):
        rec = run_one("recovery", idx, started_head, baseline_start, system, user)
        append_record(raw_log, rec)
        records.append(rec)
    result = write_report(records, raw_log, meta)
    return result, raw_log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    result, raw_log = run_audit()
    print(f"verdict={result}")
    print(f"report={REPORT_PATH}")
    print(f"raw_log={raw_log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
