"""
scripts/ollama_error_audit.py

Read-only diagnostic: parses bot.log for Ollama HTTP error lines and
classifies them into failure buckets so the dominant failure mode is clear.

Buckets:
  model_not_found      -- HTTP 404 + body contains "not found"
  timeout_or_connection -- timeout wording or connection-related errors
  server_error         -- HTTP 5xx
  bad_request          -- HTTP 400
  other                -- any other HTTP error

Usage:
    python scripts/ollama_error_audit.py
    python scripts/ollama_error_audit.py --log logs/app/bot.log
    python scripts/ollama_error_audit.py --rotated        # also scan bot.log.* archives
    python scripts/ollama_error_audit.py --show-all       # show all examples per bucket
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from utils.app_log_reader import _parse_app_log_line, iter_app_log_records

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUCKET_MODEL_NOT_FOUND = "model_not_found"
BUCKET_TIMEOUT = "timeout_or_connection"
BUCKET_SERVER_ERROR = "server_error"
BUCKET_BAD_REQUEST = "bad_request"
BUCKET_OTHER = "other"

_BUCKET_ORDER = [
    BUCKET_MODEL_NOT_FOUND,
    BUCKET_TIMEOUT,
    BUCKET_SERVER_ERROR,
    BUCKET_BAD_REQUEST,
    BUCKET_OTHER,
]

# Matches: "Ollama HTTP 404 from /v1/... -- body: {...}"
#      or: "Ollama returned HTTP 404"
# Captures: status code and optional body snippet
_OLLAMA_HTTP_RE = re.compile(
    r"Ollama (?:returned )?HTTP (?P<status>\d{3})"
    r"(?:\s+from\s+\S+\s+--\s+body:\s+(?P<body>.*))?",
    re.IGNORECASE,
)

# Additional Ollama error patterns that do NOT produce HTTP status lines
_TIMEOUT_KEYWORDS = ("timed out", "timeout", "clientconnectorerror")
_CIRCUIT_KEYWORDS = ("circuit open",)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class OllamaErrorEvent(NamedTuple):
    ts: str             # raw timestamp string, e.g. "2026-04-15 12:22:47,529 UTC"
    status: int | None  # HTTP status code if present
    body: str           # first 200 chars of response body (may be empty)
    bucket: str         # one of the BUCKET_* constants
    raw: str            # first 300 chars of the original log line


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(status: int | None, body: str, line: str) -> str:
    """Return the failure bucket for one Ollama error event.

    Args:
        status: HTTP status code extracted from the log line (None if absent).
        body:   Response body snippet (lowercased for the check).
        line:   Full raw log line (lowercased for keyword checks).
    """
    body_lo = body.lower()
    line_lo = line.lower()

    # Timeout / connection check first -- can coexist with a status code
    if any(kw in body_lo or kw in line_lo for kw in _TIMEOUT_KEYWORDS):
        return BUCKET_TIMEOUT
    if "clientconnectorerror" in line_lo:
        return BUCKET_TIMEOUT

    if status is None:
        return BUCKET_OTHER

    if status == 404 and "not found" in body_lo:
        return BUCKET_MODEL_NOT_FOUND
    if status >= 500:
        return BUCKET_SERVER_ERROR
    if status == 400:
        return BUCKET_BAD_REQUEST

    return BUCKET_OTHER


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_log_line(line: str) -> OllamaErrorEvent | None:
    """Parse a single log line.  Returns None if it is not an Ollama error event."""
    return _event_from_record(_parse_app_log_line(line))


def _format_ts(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3] + " UTC"


def _event_from_record(record: dict[str, object]) -> OllamaErrorEvent | None:
    line = str(record.get("raw") or "")
    message = str(record.get("message") or "")

    # Fast pre-check before the heavier regex
    if "Ollama" not in line and "ollama" not in line:
        return None

    ts = _format_ts(record.get("ts") if isinstance(record.get("ts"), datetime) else None)

    # Primary: Ollama HTTP <status> lines (both old DEBUG and new WARNING formats)
    m_http = _OLLAMA_HTTP_RE.search(message or line)
    if m_http:
        status = int(m_http.group("status"))
        body = (m_http.group("body") or "")[:200].strip()
        bucket = classify(status, body, line)
        return OllamaErrorEvent(ts=ts, status=status, body=body, bucket=bucket, raw=line.strip()[:300])

    # Secondary: timeout / connection errors that emit without an HTTP status
    line_lo = line.lower()
    if (
        any(kw in line_lo for kw in _TIMEOUT_KEYWORDS)
        or any(kw in line_lo for kw in _CIRCUIT_KEYWORDS)
    ):
        # Only capture if clearly an Ollama-originating message
        if "ollama" not in line_lo:
            return None
        body = line.strip()[-150:]
        return OllamaErrorEvent(ts=ts, status=None, body=body, bucket=BUCKET_TIMEOUT, raw=line.strip()[:300])

    return None


def parse_log_file(path: Path) -> list[OllamaErrorEvent]:
    """Parse one log file and return all Ollama error events in order."""
    return [event for record in iter_app_log_records(path) if (event := _event_from_record(record)) is not None]


def collect(paths: list[Path]) -> list[OllamaErrorEvent]:
    """Collect events from multiple log files in given order."""
    all_events: list[OllamaErrorEvent] = []
    for p in paths:
        all_events.extend(parse_log_file(p))
    return all_events


# ---------------------------------------------------------------------------
# Time-gap analysis
# ---------------------------------------------------------------------------


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse "2026-04-15 12:22:47,529 UTC" -> datetime (naive, UTC)."""
    if not ts_str:
        return None
    clean = ts_str.replace(" UTC", "").replace(",", ".")
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return None


def gap_analysis(events: list[OllamaErrorEvent]) -> dict:
    """Compute gap statistics between consecutive errors.

    Returns a dict with keys: times, gaps, avg_gap, min_gap, max_gap,
    burst_groups (list of burst sizes, where a burst = gap < 5s).
    Returns empty dict if fewer than 2 timestamped events.
    """
    times = [dt for e in events if (dt := _parse_ts(e.ts)) is not None]
    if len(times) < 2:
        return {}
    gaps = [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]
    # Group into bursts: consecutive errors within 5 seconds of each other
    burst_sizes: list[int] = [1]
    for g in gaps:
        if g < 5.0:
            burst_sizes[-1] += 1
        else:
            burst_sizes.append(1)
    return {
        "count": len(times),
        "gaps": gaps,
        "avg_gap": sum(gaps) / len(gaps),
        "min_gap": min(gaps),
        "max_gap": max(gaps),
        "burst_groups": burst_sizes,
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def build_report(events: list[OllamaErrorEvent], max_examples: int = 5) -> list[str]:
    """Build the full text report as a list of lines."""
    lines: list[str] = []
    total = len(events)

    lines.append("=" * 60)
    lines.append("OLLAMA ERROR AUDIT")
    lines.append("=" * 60)
    lines.append(f"Total error events scanned: {total}")
    lines.append("")

    if not events:
        lines.append("No Ollama error events found in log(s).")
        return lines

    bucket_counts: Counter[str] = Counter(e.bucket for e in events)
    status_counts: Counter[int] = Counter(e.status for e in events if e.status is not None)
    no_status_count = sum(1 for e in events if e.status is None)

    # --- Summary ---
    lines.append("By category:")
    for bucket in _BUCKET_ORDER:
        count = bucket_counts.get(bucket, 0)
        if count:
            pct = count / total * 100
            lines.append(f"  {bucket:<30} {count:>4}  ({pct:.0f}%)")
    lines.append("")

    lines.append("By HTTP status code:")
    for status in sorted(status_counts):
        lines.append(f"  HTTP {status:<5}  {status_counts[status]}")
    if no_status_count:
        lines.append(f"  (no status)    {no_status_count}")
    lines.append("")

    # --- Per-bucket examples ---
    for bucket in _BUCKET_ORDER:
        bucket_events = [e for e in events if e.bucket == bucket]
        if not bucket_events:
            continue
        header = f"--- {bucket.upper()} ({len(bucket_events)} events)"
        lines.append(header)
        shown = bucket_events[:max_examples]
        for e in shown:
            snippet = e.body[:100] if e.body else "(no body)"
            status_str = str(e.status) if e.status is not None else "n/a"
            lines.append(f"  {e.ts}  HTTP {status_str:<5}  {snippet}")
        if len(bucket_events) > max_examples:
            lines.append(f"  ... and {len(bucket_events) - max_examples} more")
        lines.append("")

    # --- Most recent 10 ---
    lines.append("--- MOST RECENT 10 ERRORS ---")
    for e in events[-10:]:
        snippet = e.body[:80] if e.body else "(no body)"
        status_str = str(e.status) if e.status is not None else "n/a"
        lines.append(f"  {e.ts}  [{e.bucket}]  HTTP {status_str:<5}  {snippet}")
    lines.append("")

    # --- Time gaps ---
    gaps = gap_analysis(events)
    if gaps:
        lines.append("--- TIME GAPS BETWEEN CONSECUTIVE ERRORS ---")
        lines.append(f"  Errors with timestamps : {gaps['count']}")
        lines.append(
            f"  Avg gap : {gaps['avg_gap']:.0f}s  "
            f"Min : {gaps['min_gap']:.0f}s  "
            f"Max : {gaps['max_gap']:.0f}s"
        )
        burst_groups = gaps["burst_groups"]
        multi_bursts = [s for s in burst_groups if s > 1]
        if multi_bursts:
            lines.append(
                f"  Burst groups (gap < 5s): {len(multi_bursts)}  "
                f"sizes: {multi_bursts}"
            )
        else:
            lines.append("  No burst groups detected (all gaps >= 5s)")
        lines.append("")

    # --- Interpretation ---
    lines.append("--- INTERPRETATION ---")
    dominant, dom_count = bucket_counts.most_common(1)[0]
    dom_pct = dom_count / total * 100

    if dominant == BUCKET_MODEL_NOT_FOUND:
        lines.append(
            f"  Dominant ({dom_pct:.0f}%): model_not_found -- configuration issue,"
        )
        lines.append(
            "  NOT CPU contention or timeout pressure. Ollama returned HTTP 404"
        )
        lines.append(
            "  because the configured model name does not match any pulled model."
        )
        lines.append(
            "  Fix: run `ollama pull <model>` or correct OLLAMA_MODEL in .env."
        )
    elif dominant == BUCKET_TIMEOUT:
        lines.append(
            f"  Dominant ({dom_pct:.0f}%): timeout/connection -- supports hypothesis"
        )
        lines.append(
            "  of CPU contention or slow inference under load. Consider increasing"
        )
        lines.append(
            "  OLLAMA_TIMEOUT or reducing concurrent inference load."
        )
    elif dominant == BUCKET_SERVER_ERROR:
        lines.append(
            f"  Dominant ({dom_pct:.0f}%): server_error -- Ollama runtime instability."
        )
        lines.append("  Check Ollama server logs for crash/OOM indicators.")
    elif dominant == BUCKET_BAD_REQUEST:
        lines.append(
            f"  Dominant ({dom_pct:.0f}%): bad_request -- request payload problem."
        )
        lines.append("  Inspect the request body format sent to /v1/chat/completions.")
    else:
        lines.append("  Mixed failure modes. No single dominant category (>50%).")
        lines.append("  Review individual examples above for patterns.")

    if len(bucket_counts) > 1:
        lines.append("")
        lines.append("  Secondary buckets:")
        for bucket, count in bucket_counts.most_common()[1:]:
            lines.append(f"    {bucket}: {count} ({count / total * 100:.0f}%)")

    return lines


def print_report(events: list[OllamaErrorEvent], max_examples: int = 5) -> None:
    for line in build_report(events, max_examples=max_examples):
        print(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Ollama HTTP error lines from bot.log. Read-only."
    )
    parser.add_argument(
        "--log",
        default="logs/app/bot.log",
        help="Path to bot.log (default: logs/app/bot.log)",
    )
    parser.add_argument(
        "--rotated",
        action="store_true",
        help="Also scan rotated log archives (bot.log.*) in the same directory",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all examples per bucket (default: top 5 each)",
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    if args.rotated:
        paths = [log_path.parent / f"{log_path.name}*"]
    else:
        paths = [log_path]

    events = collect(paths)
    max_ex = 9999 if args.show_all else 5
    print_report(events, max_examples=max_ex)


if __name__ == "__main__":
    main()
