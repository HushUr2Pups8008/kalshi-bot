"""macOS status report for the Kalshi bot LaunchAgent workflow.

This is shell/status tooling only. It inspects launchd, process state, and
runtime log markers; it does not import or alter bot runtime behavior.

Process detection mirrors the known-good shell helpers in ~/.zshrc:
  _kalshi_bot_pids()       → _bot_pids()
  _kalshi_caffeinate_pids() → _caffeinate_pids()
  botstatus()              → print_bot_section()
  botcaff()                → print_caffeinate_section()

Keep the Python implementations in sync with those shell functions rather than
inventing a separate process model.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORTS))

from utils.research_prewarm_targets import (
    DEFAULT_TARGET_REASONS,
    DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    record_targets_research_prewarm,
)


LOG_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2}),(?P<ms>\d{3}) UTC "
)
BOOT_RE = re.compile(r"\[BOOT\]\s+version=(?P<version>\S+)\s+pid=(?P<pid>\d+)")
# New sigil banner: # ===== v0.29.59 | env=prod | model=qwen2.5:7b | py=3.14.4 =====
SIGIL_RE = re.compile(
    r"^#\s*={5,}\s+v(?P<version>\S+)\s+\|\s+env=\S+\s+\|\s+model=\S+\s+\|\s+py=\S+\s+=+\s*$"
)
SHUTDOWN_MARKERS = (
    "Shutdown signal received",
    "Bot shutting down...",
)
SIGNAL_FLOW_EVENTS = (
    "EARLY_FRESH_PASS",
    "MATCH_DIAGNOSTIC",
    "ANALYSIS_REJECTED",
    "OPPORTUNITY",
    "BLEND_DECISION",
    "SKIPPED",
    "PAPER_TRADE",
    "LIVE_ORDER",
)
RESEARCH_DOSSIER_MAX_AGE_SECONDS = 6 * 60 * 60
RESEARCH_REQUIRED_SOURCE_CLASSES = {"resolution_source", "official_primary"}


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    cpu: str
    mem: str
    etimes: int
    command: str


@dataclass(frozen=True)
class BotSession:
    boot_ts: datetime
    version: str
    pid: int | None
    shutdown_ts: datetime | None = None
    shutdown_marker: str | None = None
    next_boot_ts: datetime | None = None


@dataclass(frozen=True)
class SignalFlowStats:
    path: Path
    since: datetime
    records_kept: int
    lines_total: int
    lines_malformed: int
    counts: Counter[str]
    latest_ts_by_type: dict[str, datetime]
    research_records: int
    research_status_counts: Counter[str]
    latest_research_ts: datetime | None


@dataclass(frozen=True)
class ResearchDossierStats:
    db_path: Path
    exists: bool
    dossiers: int = 0
    evidence_rows: int = 0
    latest_researched_ts: datetime | None = None
    latest_evidence_ts: datetime | None = None
    verdict_counts: Counter[str] = field(default_factory=Counter)
    vetted_trade_candidate_dossiers: int = 0
    live_cache_eligible_dossiers: int = 0
    fresh_evidence_rows_24h: int = 0
    error: str | None = None


def human_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "n/a"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def parse_log_ts(line: str) -> datetime | None:
    match = LOG_TS_RE.match(line)
    if not match:
        return None
    raw = f"{match.group('date')} {match.group('time')}.{match.group('ms')}"
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def _parse_trade_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_trade_log_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return

    archive_root = path / "archive"
    if archive_root.exists():
        yield from sorted(
            candidate
            for candidate in archive_root.rglob("*")
            if candidate.name.endswith((".jsonl", ".jsonl.gz"))
        )

    legacy = path / "trades.jsonl"
    live = path / "live" / "trades.jsonl"
    if legacy.exists():
        yield legacy
    if live.exists():
        yield live


def _iter_trade_records(
    path: Path,
    *,
    lines_total: list[int],
    lines_malformed: list[int],
) -> Iterable[dict[str, Any]]:
    for file_path in _iter_trade_log_files(path):
        opener = gzip.open if file_path.name.endswith(".gz") else open
        try:
            with opener(file_path, "rt", encoding="utf-8") as fh:
                for raw_line in fh:
                    lines_total[0] += 1
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        lines_malformed[0] += 1
                        continue
                    if isinstance(record, dict):
                        yield record
                    else:
                        lines_malformed[0] += 1
        except OSError:
            continue


def summarize_signal_flow(
    path: Path,
    *,
    now: datetime,
    window_hours: float,
) -> SignalFlowStats:
    since = now - timedelta(hours=window_hours)
    lines_total = [0]
    lines_malformed = [0]
    counts: Counter[str] = Counter()
    latest_ts_by_type: dict[str, datetime] = {}
    research_status_counts: Counter[str] = Counter()
    latest_research_ts: datetime | None = None
    research_records = 0
    records_kept = 0

    for record in _iter_trade_records(
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        event_type = str(record.get("type") or "").strip()
        if event_type not in SIGNAL_FLOW_EVENTS:
            continue
        ts = _parse_trade_ts(record.get("ts"))
        if ts is not None and ts < since:
            continue
        records_kept += 1
        counts[event_type] += 1
        if record.get("research_attempted") is True or any(
            str(key).startswith("research_") for key in record
        ):
            research_records += 1
            status = str(record.get("research_status") or "unknown").strip() or "unknown"
            research_status_counts[status] += 1
            if ts is not None and (
                latest_research_ts is None or ts > latest_research_ts
            ):
                latest_research_ts = ts
        if ts is not None and (
            event_type not in latest_ts_by_type or ts > latest_ts_by_type[event_type]
        ):
            latest_ts_by_type[event_type] = ts

    return SignalFlowStats(
        path=path,
        since=since,
        records_kept=records_kept,
        lines_total=lines_total[0],
        lines_malformed=lines_malformed[0],
        counts=counts,
        latest_ts_by_type=latest_ts_by_type,
        research_records=research_records,
        research_status_counts=research_status_counts,
        latest_research_ts=latest_research_ts,
    )


def _research_dossier_db_path(repo_root: Path) -> Path:
    return repo_root / "data" / "evidence_store.db"


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _parse_research_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return _parse_trade_ts(value.replace("Z", "+00:00"))


def summarize_research_dossiers(
    repo_root: Path,
    *,
    now: datetime,
) -> ResearchDossierStats:
    db_path = _research_dossier_db_path(repo_root)
    if not db_path.exists():
        return ResearchDossierStats(db_path=db_path, exists=False)

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            missing_tables = [
                table_name
                for table_name in ("research_dossiers", "research_evidence")
                if not _sqlite_table_exists(conn, table_name)
            ]
            if missing_tables:
                return ResearchDossierStats(
                    db_path=db_path,
                    exists=True,
                    error="not_initialized",
                )

            dossiers = int(
                conn.execute("SELECT COUNT(*) AS count FROM research_dossiers").fetchone()[
                    "count"
                ]
            )
            evidence_rows = int(
                conn.execute("SELECT COUNT(*) AS count FROM research_evidence").fetchone()[
                    "count"
                ]
            )
            latest_researched_raw = conn.execute(
                "SELECT MAX(last_researched_ts) AS ts FROM research_dossiers"
            ).fetchone()["ts"]
            latest_evidence_raw = conn.execute(
                """
                SELECT MAX(COALESCE(retrieved_at, inserted_at)) AS ts
                FROM research_evidence
                """
            ).fetchone()["ts"]
            verdict_counts = Counter(
                {
                    str(row["last_verdict_status"] or "unknown"): int(row["count"])
                    for row in conn.execute(
                        """
                        SELECT last_verdict_status, COUNT(*) AS count
                        FROM research_dossiers
                        GROUP BY last_verdict_status
                        """
                    )
                }
            )
            vetted_trade_candidate_dossiers = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM research_dossiers
                    WHERE last_verdict_status = 'trade_candidate'
                      AND last_force_side IN ('yes', 'no')
                      AND last_estimated_probability IS NOT NULL
                      AND last_confidence IS NOT NULL
                    """
                ).fetchone()["count"]
            )
            live_cache_eligible_tickers: set[str] = set()
            fresh_since = now - timedelta(hours=24)
            live_cache_since = now - timedelta(seconds=RESEARCH_DOSSIER_MAX_AGE_SECONDS)
            fresh_evidence_rows_24h = 0
            evidence_by_ticker: dict[str, list[tuple[str, datetime]]] = {}
            for row in conn.execute(
                """
                SELECT
                    market_ticker,
                    source_class,
                    COALESCE(retrieved_at, inserted_at) AS ts
                FROM research_evidence
                """
            ):
                evidence_ts = _parse_research_ts(row["ts"])
                if evidence_ts is not None and evidence_ts >= fresh_since:
                    fresh_evidence_rows_24h += 1
                if evidence_ts is not None and evidence_ts >= live_cache_since:
                    ticker = str(row["market_ticker"] or "").strip()
                    if ticker:
                        evidence_by_ticker.setdefault(ticker, []).append(
                            (str(row["source_class"] or "").strip(), evidence_ts)
                        )
            vetted_tickers = {
                str(row["market_ticker"] or "").strip()
                for row in conn.execute(
                    """
                    SELECT market_ticker
                    FROM research_dossiers
                    WHERE last_verdict_status = 'trade_candidate'
                      AND last_force_side IN ('yes', 'no')
                      AND last_estimated_probability IS NOT NULL
                      AND last_confidence IS NOT NULL
                    """
                )
            }
            for ticker in vetted_tickers:
                ticker_evidence = evidence_by_ticker.get(ticker, [])
                if len(ticker_evidence) < 2:
                    continue
                if any(
                    source_class in RESEARCH_REQUIRED_SOURCE_CLASSES
                    for source_class, _ts in ticker_evidence
                ):
                    live_cache_eligible_tickers.add(ticker)
    except sqlite3.Error as exc:
        return ResearchDossierStats(
            db_path=db_path,
            exists=True,
            error=str(exc),
        )

    return ResearchDossierStats(
        db_path=db_path,
        exists=True,
        dossiers=dossiers,
        evidence_rows=evidence_rows,
        latest_researched_ts=_parse_research_ts(latest_researched_raw),
        latest_evidence_ts=_parse_research_ts(latest_evidence_raw),
        verdict_counts=verdict_counts,
        vetted_trade_candidate_dossiers=vetted_trade_candidate_dossiers,
        live_cache_eligible_dossiers=len(live_cache_eligible_tickers),
        fresh_evidence_rows_24h=fresh_evidence_rows_24h,
    )


def parse_sessions(lines: Iterable[str]) -> list[BotSession]:
    """Parse bot.log lines into BotSession records.

    Recognises two boot-marker formats:
    1. Legacy [BOOT] marker (has timestamp on the same line):
           2026-05-09 12:00:00,000 UTC INFO ... [BOOT] version=X pid=Y ...
    2. Sigil banner (no timestamp on its own line; timestamp from the next
       timestamped log line):
           # ===== v0.29.59 | env=prod | model=qwen2.5:7b | py=3.14.4 =====
           2026-05-11 00:00:52,338 UTC INFO ...
    """
    sessions: list[BotSession] = []
    current: BotSession | None = None
    # When a sigil banner is seen we stash its version and wait for the next
    # timestamped line to provide the boot timestamp.
    pending_sigil_version: str | None = None

    for line in lines:
        # Check for sigil banner first (no timestamp on this line).
        sigil_match = SIGIL_RE.match(line)
        if sigil_match:
            pending_sigil_version = sigil_match.group("version")
            continue

        ts = parse_log_ts(line)
        if ts is None:
            continue

        # If a sigil banner is pending, this first timestamped line provides
        # the boot timestamp.
        if pending_sigil_version is not None:
            if current is not None:
                sessions.append(BotSession(
                    boot_ts=current.boot_ts,
                    version=current.version,
                    pid=current.pid,
                    shutdown_ts=current.shutdown_ts,
                    shutdown_marker=current.shutdown_marker,
                    next_boot_ts=ts,
                ))
            current = BotSession(
                boot_ts=ts,
                version=pending_sigil_version,
                pid=None,
            )
            pending_sigil_version = None
            # Still process this line for shutdown markers below.

        boot_match = BOOT_RE.search(line)
        if boot_match:
            if current is not None:
                sessions.append(BotSession(
                    boot_ts=current.boot_ts,
                    version=current.version,
                    pid=current.pid,
                    shutdown_ts=current.shutdown_ts,
                    shutdown_marker=current.shutdown_marker,
                    next_boot_ts=ts,
                ))
            current = BotSession(
                boot_ts=ts,
                version=boot_match.group("version"),
                pid=int(boot_match.group("pid")),
            )
            continue
        if current is not None and current.shutdown_ts is None:
            marker = next((m for m in SHUTDOWN_MARKERS if m in line), None)
            if marker is not None:
                current = BotSession(
                    boot_ts=current.boot_ts,
                    version=current.version,
                    pid=current.pid,
                    shutdown_ts=ts,
                    shutdown_marker=marker,
                    next_boot_ts=current.next_boot_ts,
                )
    if current is not None:
        sessions.append(current)
    return sessions


def session_duration(session: BotSession, now: datetime, active: bool) -> str:
    if session.shutdown_ts is not None:
        return human_duration((session.shutdown_ts - session.boot_ts).total_seconds())
    if active:
        return human_duration((now - session.boot_ts).total_seconds())
    return "n/a (shutdown not observed)"


def read_sessions(log_path: Path) -> list[BotSession]:
    try:
        return parse_sessions(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return []


def _read_env_file_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=\s*(?P<value>.*)\s*$")
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        return value
    return None


def _research_env_value(repo_root: Path, key: str, default: str) -> tuple[str, str]:
    if key in os.environ:
        return os.environ[key], "process-env"
    env_value = _read_env_file_value(repo_root / ".env", key)
    if env_value is not None:
        return env_value, ".env"
    return default, "default"


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _relative_display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _format_ts_age(ts: datetime | None, *, now: datetime) -> tuple[str, str]:
    if ts is None:
        return "n/a", "n/a"
    return ts.isoformat(), human_duration((now - ts).total_seconds())


def summarize_research_prewarm_backlog(path: Path, *, since: datetime) -> list[str]:
    return _target_tickers_from_trade_log(
        path,
        since=since,
        reasons=DEFAULT_TARGET_REASONS,
        research_skip_reasons=DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    )


def _target_tickers_from_trade_log(
    path: Path,
    *,
    since: datetime,
    reasons: list[str],
    research_skip_reasons: list[str],
) -> list[str]:
    reason_set = {reason.strip() for reason in reasons if reason.strip()}
    research_skip_reason_set = {
        reason.strip() for reason in research_skip_reasons if reason.strip()
    }
    last_index_by_ticker: dict[str, int] = {}
    lines_total = [0]
    lines_malformed = [0]
    for index, record in enumerate(
        _iter_trade_records(
            path,
            lines_total=lines_total,
            lines_malformed=lines_malformed,
        )
    ):
        event_type = str(record.get("type") or "").strip()
        if event_type not in {
            "ANALYSIS_REJECTED",
            "MATCH_LLM_REVIEW",
            "SIGNAL_ANALYSIS_DETAIL",
        }:
            continue
        ts = _parse_trade_ts(record.get("ts"))
        if ts is not None and ts < since:
            continue
        if not _record_targets_research_prewarm(
            record,
            reason_set=reason_set,
            research_skip_reason_set=research_skip_reason_set,
        ):
            continue
        ticker = str(record.get("ticker") or record.get("market_ticker") or "").strip()
        if ticker:
            last_index_by_ticker[ticker] = index
    return [
        ticker
        for ticker, _index in sorted(
            last_index_by_ticker.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def _record_targets_research_prewarm(
    record: dict[str, Any],
    *,
    reason_set: set[str],
    research_skip_reason_set: set[str],
) -> bool:
    return record_targets_research_prewarm(
        record,
        reason_set=reason_set,
        research_skip_reason_set=research_skip_reason_set,
    )


def print_research_gate_section(
    repo_root: Path,
    stats: SignalFlowStats,
    *,
    now: datetime,
    dossier_stats: ResearchDossierStats | None = None,
) -> None:
    mode, mode_source = _research_env_value(repo_root, "REAL_WEB_RESEARCH_MODE", "off")
    max_queries, max_queries_source = _research_env_value(
        repo_root,
        "REAL_WEB_RESEARCH_MAX_QUERIES",
        "6",
    )
    timeout, timeout_source = _research_env_value(
        repo_root,
        "REAL_WEB_RESEARCH_TIMEOUT_SECONDS",
        "12.0",
    )
    prewarm_enabled, prewarm_source = _research_env_value(
        repo_root,
        "ENABLE_RESEARCH_PREWARM_TASK",
        "false",
    )
    prewarm_interval, _prewarm_interval_source = _research_env_value(
        repo_root,
        "RESEARCH_PREWARM_INTERVAL_SECONDS",
        "900",
    )
    prewarm_max_markets, _prewarm_max_markets_source = _research_env_value(
        repo_root,
        "RESEARCH_PREWARM_MAX_MARKETS",
        "25",
    )
    prewarm_max_pages, _prewarm_max_pages_source = _research_env_value(
        repo_root,
        "RESEARCH_PREWARM_MAX_PAGES",
        "5",
    )
    mode = mode.strip().lower() or "off"
    print("=== Real web research gate ===")
    print(f"mode       : {mode} ({mode_source})")
    print(f"max_queries: {max_queries} ({max_queries_source})")
    print(f"timeout_s  : {timeout} ({timeout_source})")
    if _env_bool(prewarm_enabled):
        print(
            "prewarm   : "
            f"on ({prewarm_source}) interval={prewarm_interval}s "
            f"max_markets={prewarm_max_markets} max_pages={prewarm_max_pages}"
        )
    elif mode in {"shadow", "production"}:
        print(
            "prewarm   : "
            f"off ({prewarm_source}) RISK: cache misses can remain terminal"
        )
    else:
        print(f"prewarm   : off ({prewarm_source})")
    if mode == "off":
        print("status     : disabled; no-keyword candidates use legacy terminal skip")
    elif mode == "shadow":
        print("status     : shadow; evidence collected without promotion")
    elif mode == "production":
        print("status     : production; eligible researched candidates may promote")
    else:
        print("status     : INVALID; config validation should reject this at startup")

    latest_age = (
        human_duration((now - stats.latest_research_ts).total_seconds())
        if stats.latest_research_ts is not None
        else "n/a"
    )
    latest_text = (
        stats.latest_research_ts.isoformat()
        if stats.latest_research_ts is not None
        else "n/a"
    )
    print(
        "research_rows: "
        f"{stats.research_records} latest={latest_text} age={latest_age}"
    )
    if stats.research_status_counts:
        rendered = ", ".join(
            f"{status}={count}"
            for status, count in sorted(stats.research_status_counts.items())
        )
        print(f"statuses   : {rendered}")
    else:
        print("statuses   : none")
    prewarm_backlog = summarize_research_prewarm_backlog(stats.path, since=stats.since)
    sample = ",".join(prewarm_backlog[:5]) if prewarm_backlog else "none"
    print(
        "prewarm_backlog: "
        f"{len(prewarm_backlog)} targetable from logs sample={sample}"
    )
    if dossier_stats is not None:
        dossier_path = _relative_display_path(dossier_stats.db_path, repo_root)
        if not dossier_stats.exists:
            print(f"dossier_db : missing {dossier_path}")
        elif dossier_stats.error == "not_initialized":
            print(
                f"dossier_db : not_initialized {dossier_path}: "
                "research tables missing until first prewarm/research write"
            )
        elif dossier_stats.error is not None:
            print(f"dossier_db : error {dossier_path}: {dossier_stats.error}")
        else:
            researched_text, researched_age = _format_ts_age(
                dossier_stats.latest_researched_ts,
                now=now,
            )
            evidence_text, evidence_age = _format_ts_age(
                dossier_stats.latest_evidence_ts,
                now=now,
            )
            print(f"dossier_db : present {dossier_path}")
            print(
                "dossiers   : "
                f"{dossier_stats.dossiers} latest={researched_text} age={researched_age}"
            )
            print(
                "evidence   : "
                f"{dossier_stats.evidence_rows} latest={evidence_text} "
                f"age={evidence_age} fresh_24h={dossier_stats.fresh_evidence_rows_24h}"
            )
            if dossier_stats.verdict_counts:
                rendered_verdicts = ", ".join(
                    f"{status}={count}"
                    for status, count in sorted(dossier_stats.verdict_counts.items())
                )
                print(f"verdicts   : {rendered_verdicts}")
            else:
                print("verdicts   : none")
            print(
                "vetted     : "
                "historical_trade_candidate="
                f"{dossier_stats.vetted_trade_candidate_dossiers} "
                f"live_cache_eligible={dossier_stats.live_cache_eligible_dossiers}"
            )
    print()


def run_command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return ""


def launchd_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def launchd_print(label: str) -> str:
    return run_command(["launchctl", "print", launchd_target(label)])


def launchd_pid(launchd_output: str) -> int | None:
    match = re.search(r"^[ \t]*pid = (?P<pid>\d+)", launchd_output, re.MULTILINE)
    return int(match.group("pid")) if match else None


def launchd_state(launchd_output: str) -> str:
    match = re.search(r"^[ \t]*state = (?P<state>\S+)", launchd_output, re.MULTILINE)
    return match.group("state") if match else "not loaded"


def _parse_etime(etime: str) -> int:
    """Parse macOS ps etime format [[dd-]hh:]mm:ss to total seconds.

    macOS ps uses 'etime' (human format); Linux ps has 'etimes' (seconds).
    We use 'etime' to stay portable on macOS and convert to seconds here.
    """
    etime = etime.strip()
    days = 0
    if "-" in etime:
        day_part, etime = etime.split("-", 1)
        days = int(day_part)
    parts = etime.split(":")
    if len(parts) == 3:
        return days * 86_400 + int(parts[0]) * 3_600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return days * 86_400 + int(parts[0]) * 60 + int(parts[1])
    return days * 86_400 + int(parts[0])


def process_table() -> list[ProcessInfo]:
    # Use 'etime' (macOS) not 'etimes' (Linux-only); no spaces in etime value.
    output = run_command(["ps", "-ww", "-axo", "pid=,ppid=,pcpu=,pmem=,etime=,command="])
    rows: list[ProcessInfo] = []
    for raw in output.splitlines():
        parts = raw.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, cpu, mem, etime_str, command = parts
        try:
            rows.append(ProcessInfo(
                pid=int(pid),
                ppid=int(ppid),
                cpu=cpu,
                mem=mem,
                etimes=_parse_etime(etime_str),
                command=command,
            ))
        except ValueError:
            continue
    return rows


# ---------------------------------------------------------------------------
# Process detection — mirrors ~/.zshrc helper functions exactly.
# Do not add relationship inference here; keep these as independent detectors.
# ---------------------------------------------------------------------------

def _bot_pids(
    rows: list[ProcessInfo],
    *,
    launchd_pid: int | None,
    python_path: Path,
    main_path: Path,
) -> list[ProcessInfo]:
    """Mirror _kalshi_bot_pids() from ~/.zshrc.

    Priority 1 (launchd-managed): if launchd reports a live PID, return that
    process row and stop.  The shell equivalent is:
        if [ -n "$launchd_pid" ] && ps -p "$launchd_pid" -o pid= >/dev/null 2>&1
        then printf '%s\\n' "$launchd_pid"; return 0; fi

    Priority 2 (fallback / manual start): scan all processes for commands
    ending in ' {main}' while excluding the exact caffeinate command form.
    Shell equivalent:
        ps -ww -axo pid=,command= | awk '
          $0 != caffeinate_cmd && $0 ~ (" " main "$") { print $1 }'
    """
    main = str(main_path)
    python = str(python_path)
    caffeinate_cmd = f"/usr/bin/caffeinate -dimsu {python} {main}"

    # Priority 1: trust launchd's reported PID (same early-return as shell).
    if launchd_pid is not None:
        proc = next((p for p in rows if p.pid == launchd_pid), None)
        if proc is not None:
            return [proc]

    # Priority 2: scan for Python bot processes by command shape.
    return [
        p for p in rows
        if p.command != caffeinate_cmd and p.command.endswith(f" {main}")
    ]


def _caffeinate_pids(
    rows: list[ProcessInfo],
    *,
    python_path: Path,
    main_path: Path,
) -> list[ProcessInfo]:
    """Mirror _kalshi_caffeinate_pids() from ~/.zshrc.

    Shell equivalent:
        pgrep -f "^/usr/bin/caffeinate -dimsu $KALSHI_PYTHON $KALSHI_MAIN$"

    Exact command match only — no relationship inference with the bot process.
    Caffeinate may be a child of the bot, not a parent; relationship is not
    checked here (mirrors botcaff() behaviour).

    Path-form tolerance: launchd may report the python or main argument in
    relative form (e.g. 'main.py' instead of '/abs/path/main.py') depending on
    how the plist was authored.  We match structurally:
      - prefix '/usr/bin/caffeinate -dimsu ' is invariant
      - python basename appears somewhere in the command args
      - main basename is the last space-separated token
    This is equivalent to the shell pgrep -f match for any path-form variant.
    """
    prefix = "/usr/bin/caffeinate -dimsu "
    python_basename = Path(python_path).name
    main_basename = Path(main_path).name
    result = []
    for p in rows:
        if not p.command.startswith(prefix):
            continue
        args = p.command[len(prefix):]
        tokens = args.split()
        if len(tokens) < 2:
            continue
        # python token: any token whose basename matches
        # main token: last token whose basename matches
        if (
            any(Path(t).name == python_basename for t in tokens[:-1])
            and Path(tokens[-1]).name == main_basename
        ):
            result.append(p)
    return result


def process_start_utc(proc: ProcessInfo, now_epoch: float) -> datetime:
    return datetime.fromtimestamp(now_epoch - proc.etimes, tz=timezone.utc)


def short_command(command: str, width: int = 96) -> str:
    if len(command) <= width:
        return command
    return command[: width - 1] + "…"


def percent_value(value: str) -> str:
    return "n/a" if value == "n/a" else f"{value}%"


# ---------------------------------------------------------------------------
# Display sections — mirror botstatus() and botcaff() from ~/.zshrc.
# ---------------------------------------------------------------------------

def print_launchd(label: str, output: str, pid: int | None) -> None:
    print("=== LaunchAgent state ===")
    print(f"Label      : {label}")
    print(f"Target     : {launchd_target(label)}")
    print(f"State      : {launchd_state(output)}")
    print(f"Launchd PID: {pid if pid is not None else 'n/a'}")
    if not output:
        print("Detail     : launchd service not loaded or not readable")
    print()


def print_bot_section(
    bots: list[ProcessInfo],
    *,
    now_epoch: float,
    now: datetime,
) -> ProcessInfo | None:
    """Mirror botstatus() from ~/.zshrc.

    Header mirrors:
        echo "=== bot python process (main.py; launchd may report this PID) ==="
    """
    print("=== bot python process (main.py; launchd may report this PID) ===")
    if not bots:
        print("kalshi_bot is not running")
        print()
        return None
    for proc in bots:
        started = process_start_utc(proc, now_epoch)
        print(f"Bot PID    : {proc.pid}")
        print(f"Parent PID : {proc.ppid if proc.ppid else 'n/a'}")
        print(f"CPU / MEM  : {percent_value(proc.cpu)} / {percent_value(proc.mem)}")
        print(f"Started UTC: {started.isoformat()}")
        print(f"Elapsed    : {human_duration(proc.etimes)}")
        print(f"Command    : {short_command(proc.command)}")
    print()
    return bots[0]


def print_caffeinate_section(
    caffeinates: list[ProcessInfo],
    *,
    now_epoch: float,
) -> None:
    """Mirror botcaff() from ~/.zshrc.

    Header mirrors:
        echo "=== caffeinate assertion/helper process (may be child of bot, not parent) ==="

    Caffeinate is reported independently — no relationship check against the
    bot PID.  The shell helper never conditions caffeinate display on PPID.
    """
    print("=== caffeinate assertion/helper process (may be child of bot, not parent) ===")
    if not caffeinates:
        print("caffeinate helper is not running")
        print()
        return
    for proc in caffeinates:
        started = process_start_utc(proc, now_epoch)
        print(f"Caffeinate PID: {proc.pid}")
        print(f"Parent PID    : {proc.ppid if proc.ppid else 'n/a'}")
        print(f"CPU / MEM     : {percent_value(proc.cpu)} / {percent_value(proc.mem)}")
        print(f"Started UTC   : {started.isoformat()}")
        print(f"Elapsed       : {human_duration(proc.etimes)}")
        print(f"Command       : {short_command(proc.command)}")
    print()


def print_last_boot(log_path: Path, sessions: list[BotSession], now: datetime) -> None:
    print("=== Last bot boot seen in logs ===")
    print(f"Log file   : {log_path}")
    if not sessions:
        print("Result     : no boot markers found ([BOOT] or sigil banner)")
        print()
        return
    last = sessions[-1]
    print(f"Boot UTC   : {last.boot_ts.isoformat()}")
    print(f"Version    : {last.version}")
    print(f"Log PID    : {last.pid if last.pid is not None else 'n/a'}")
    print(f"Age        : {human_duration((now - last.boot_ts).total_seconds())}")
    print()


def print_signal_flow_section(stats: SignalFlowStats, *, now: datetime) -> None:
    print("=== Signal-flow heartbeat (structured trade log) ===")
    print(f"Trade log  : {stats.path}")
    print(f"Window UTC : since {stats.since.isoformat()}")
    print(f"Records    : {stats.records_kept} kept / {stats.lines_total} scanned")
    if stats.lines_malformed:
        print(f"Malformed  : {stats.lines_malformed}")
    if not stats.counts:
        print("Result     : no signal-flow records found in window")
        print()
        return

    for event_type in SIGNAL_FLOW_EVENTS:
        count = stats.counts.get(event_type, 0)
        latest = stats.latest_ts_by_type.get(event_type)
        age = human_duration((now - latest).total_seconds()) if latest is not None else "n/a"
        latest_text = latest.isoformat() if latest is not None else "n/a"
        print(f"{event_type:<17}: {count:>5} latest={latest_text} age={age}")
    print()


def print_kalshi_drift_section(now: datetime) -> None:
    """Additive Kalshi API drift heartbeat (P-9 / LD-14).

    Reads the DriftCounter halt sentinel at data/runtime/kalshi_drift_halt.json
    (if present) plus reads bot_state.p0_price_fix_deployed_ts for cohort
    boundary visibility. Strictly diagnostic; emits a single line for the
    operator and never mutates state.

    Operator-locked threshold (OQ-A -> LD-6): abs >= 1.
    """
    import sqlite3

    print("=== Kalshi API drift heartbeat (P0) ===")

    sentinel_path = _default_repo_root() / "data" / "runtime" / "kalshi_drift_halt.json"
    halt_state: dict[str, Any] = {
        "cycle_count": 0,
        "halt": False,
        "last_halt_at": None,
        "threshold_abs": 1,
    }
    if sentinel_path.is_file():
        try:
            payload = json.loads(sentinel_path.read_text(encoding="utf-8"))
            halt_state["cycle_count"] = int(payload.get("cycle_drift_count") or 0)
            halt_state["halt"] = True
            halt_state["last_halt_at"] = payload.get("halt_ts")
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            print(f"halt sentinel unreadable at {sentinel_path}: {exc}")
            halt_state["halt"] = True  # fail-closed if sentinel exists but bad

    cohort_ts: str | None = None
    db_path = _default_repo_root() / "data" / "paper_trades.db"
    if db_path.is_file():
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT value FROM bot_state WHERE key = ?",
                    ("p0_price_fix_deployed_ts",),
                ).fetchone()
                if row is not None:
                    cohort_ts = row[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(f"bot_state read failed: {exc}")

    print(
        f"kalshi_drift: cycle_count={halt_state['cycle_count']} "
        f"halt={halt_state['halt']} "
        f"last_halt_at={halt_state['last_halt_at'] or 'null'} "
        f"threshold_abs={halt_state['threshold_abs']}"
    )
    print(
        f"p0_cohort   : deployed_ts={cohort_ts or 'null'} "
        f"(post-P0 replay rows: decision_ts >= deployed_ts; LD-7 / CR-F)"
    )

    # P0 contract-version drift alarm (audit rec #7).
    # `paper_trades.p0_contract_version` defaults to 1; if a future Kalshi
    # contract change bumps it, this is the only forward-tracked audit
    # signal that catches the bump. Surface a heartbeat line so multi-
    # version drift is visible before it hides in replay corpora.
    contract_state = _summarize_p0_contract_version_drift(db_path)
    if contract_state["status"] == "no_recent_events":
        print(
            "p0_contract : status=no_recent_events "
            "(no paper_trades rows; expected if bot has not yet emitted "
            "a PAPER_TRADE post-deploy)"
        )
    elif contract_state["status"] == "single_version":
        print(
            f"p0_contract : status=ok version={contract_state['versions'][0]} "
            f"row_count={contract_state['recent_row_count']}"
        )
    elif contract_state["status"] == "drift":
        versions_text = ",".join(
            str(v) if v is not None else "null"
            for v in contract_state["versions"]
        )
        print(
            "p0_contract : status=DRIFT versions=["
            f"{versions_text}] row_count={contract_state['recent_row_count']} "
            "(multiple p0_contract_version values present; investigate)"
        )
    elif contract_state["status"] == "unknown":
        print(
            f"p0_contract : status=unknown reason={contract_state['reason']!r}"
        )

    print()


def _summarize_p0_contract_version_drift(
    db_path: Path,
    *,
    recent_limit: int = 200,
) -> dict[str, Any]:
    """Inspect recent paper_trades rows for ``p0_contract_version`` drift.

    Strictly read-only. Returns one of:

    - ``{"status": "unknown", "reason": ...}`` — DB unreadable, schema
      missing, or query failed.
    - ``{"status": "no_recent_events", "versions": [], "recent_row_count": 0}``
      — DB readable but no paper_trades rows.
    - ``{"status": "single_version", "versions": [v], "recent_row_count": n}``
      — all recent rows carry the same contract version (the common case
      when the bot is operating on a single P0 contract era).
    - ``{"status": "drift", "versions": [...], "recent_row_count": n}`` —
      multiple distinct contract versions present in the recent window;
      requires operator attention.

    The ``recent_limit`` window keeps the query cheap and bounds the alarm
    to current operational state — historical multi-version drift in
    long-archived rows is not the operator's concern at heartbeat time.
    """
    import sqlite3

    if not db_path.is_file():
        return {"status": "unknown", "reason": "paper_trades.db not found"}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            schema_row = conn.execute(
                "SELECT name FROM pragma_table_info('paper_trades') "
                "WHERE name = 'p0_contract_version'"
            ).fetchone()
            if schema_row is None:
                return {
                    "status": "unknown",
                    "reason": "p0_contract_version column missing",
                }
            rows = conn.execute(
                "SELECT p0_contract_version, count(*) AS n "
                "FROM (SELECT p0_contract_version FROM paper_trades "
                "ORDER BY ts DESC LIMIT ?) "
                "GROUP BY p0_contract_version "
                "ORDER BY p0_contract_version",
                (recent_limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"status": "unknown", "reason": f"sqlite error: {exc}"}

    if not rows:
        return {
            "status": "no_recent_events",
            "versions": [],
            "recent_row_count": 0,
        }
    versions = [r[0] for r in rows]
    total = sum(int(r[1]) for r in rows)
    if len(versions) == 1:
        return {
            "status": "single_version",
            "versions": versions,
            "recent_row_count": total,
        }
    return {
        "status": "drift",
        "versions": versions,
        "recent_row_count": total,
    }


def print_history(
    *,
    sessions: list[BotSession],
    current_proc: ProcessInfo | None,
    now: datetime,
) -> None:
    print("=== Recent runtime history (last 5) ===")
    if not sessions:
        print("No runtime sessions found in active app log.")
        return

    current_pid = current_proc.pid if current_proc is not None else None
    recent = sessions[-5:]
    for idx, session in enumerate(recent, start=1):
        active = (
            session.shutdown_ts is None
            and current_pid is not None
            and (session.pid == current_pid or session == sessions[-1])
        )
        shutdown = (
            session.shutdown_ts.isoformat()
            if session.shutdown_ts is not None
            else ("ACTIVE" if active else "not observed")
        )
        duration = session_duration(session, now, active)
        status = "current active" if active else "closed" if session.shutdown_ts else "unpaired"
        print(
            f"{idx}. version={session.version} pid={session.pid or 'n/a'} "
            f"boot={session.boot_ts.isoformat()} shutdown={shutdown} "
            f"runtime={duration} status={status}"
        )
        if session.shutdown_ts is None and session.next_boot_ts is not None and not active:
            print(
                f"   note: next boot at {session.next_boot_ts.isoformat()} "
                "but no shutdown marker was found before it"
            )


def _default_repo_root() -> Path:
    """Resolve the repo root for botcheck without a hardcoded user/repo path.

    Order: $KALSHI_HOME, then ~/vscode/kalshi-bot (Mac Studio), then
    ~/vscode/kalshi_bot (legacy MacBook), then ~/vscode/kalshi-bot as a final
    default if neither directory exists.
    """
    if (env := os.environ.get("KALSHI_HOME")):
        return Path(env)
    home = Path.home()
    for name in ("kalshi-bot", "kalshi_bot"):
        candidate = home / "vscode" / name
        if candidate.exists():
            return candidate
    return home / "vscode" / "kalshi-bot"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = _default_repo_root()
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument("--label", default=os.environ.get("KALSHI_LAUNCHD_LABEL", "com.jake.kalshi-bot"))
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("KALSHI_PYTHON", default_home / ".venv/bin/python")))
    parser.add_argument("--main", type=Path, default=Path(os.environ.get("KALSHI_MAIN", default_home / "main.py")))
    parser.add_argument("--log", type=Path, default=Path(os.environ.get("KALSHI_APP_LOG", default_home / "logs/app/bot.log")))
    parser.add_argument("--trades-log", type=Path, default=default_home / "logs/trades")
    parser.add_argument("--signal-window-hours", type=float, default=24.0)
    args = parser.parse_args()

    now_epoch = time.time()
    now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)

    launchd_output = launchd_print(args.label)
    wrapper_pid = launchd_pid(launchd_output)

    rows = process_table()
    bots = _bot_pids(rows, launchd_pid=wrapper_pid, python_path=args.python, main_path=args.main)
    caffeinates = _caffeinate_pids(rows, python_path=args.python, main_path=args.main)

    sessions = read_sessions(args.log)
    signal_flow = summarize_signal_flow(
        args.trades_log,
        now=now,
        window_hours=args.signal_window_hours,
    )
    research_dossiers = summarize_research_dossiers(default_home, now=now)

    print_launchd(args.label, launchd_output, wrapper_pid)
    current_proc = print_bot_section(bots, now_epoch=now_epoch, now=now)
    print_caffeinate_section(caffeinates, now_epoch=now_epoch)
    print_last_boot(args.log, sessions, now)
    print_signal_flow_section(signal_flow, now=now)
    print_research_gate_section(
        default_home,
        signal_flow,
        now=now,
        dossier_stats=research_dossiers,
    )
    print_kalshi_drift_section(now=now)
    print_history(sessions=sessions, current_proc=current_proc, now=now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
