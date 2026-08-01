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
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from collections.abc import Mapping

from dotenv import dotenv_values

REPO_ROOT_FOR_IMPORTS = Path(__file__).resolve().parent.parent
if str(REPO_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_FOR_IMPORTS))

from utils.research_prewarm_targets import (
    DEFAULT_TARGET_REASONS,
    DEFAULT_TARGET_RESEARCH_SKIP_REASONS,
    RESEARCH_PREWARM_EVENT_TYPES,
    record_targets_kalshi_research_prewarm,
)
from utils.research_evidence_quality import research_evidence_temporally_valid
from scripts.observability import paper_trade_journal_reconcile
from scripts.research_activation_status import evaluate_activation_profile
from trading.capital_guard_shadow import (
    capital_guard_shadow_schema_contract_matches,
)
from trading.runtime_paper_cohort_attestation import (
    RuntimePaperCohortAttestation,
    RuntimePaperCohortAttestationError,
    read_runtime_paper_cohort_attestation,
)
from trading.settlement_store import settlement_schema_contract_matches
from utils.diagnostics_script_helpers import is_replay_or_test_paper_trade


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
    "MATCH_NO_CANDIDATE",
    "POLYMARKET_MARKET_CACHE",
    "ANALYSIS_REJECTED",
    "OPPORTUNITY",
    "BLEND_DECISION",
    "SKIPPED",
    "RESEARCH_PREWARM_RESULT",
    "PAPER_TRADE",
    "LIVE_ORDER",
)
LIVE_SUBMISSION_UNKNOWN_EVENT = "LIVE_SUBMISSION_UNKNOWN"
LIVE_SUBMISSION_HOLD_FILENAME = "unknown_submission_holds.json"
LIVE_SUBMISSION_HOLD_SCHEMA_VERSION = 1
PENDING_PAPER_COHORTS_DIRNAME = "legacy_pending_paper_cohorts"
PENDING_PAPER_COHORT_MANIFEST_FILENAME = "cohort.json"
PENDING_PAPER_COHORT_MANIFEST_SCHEMA_VERSION = 1
PENDING_PAPER_COHORT_TYPE = "legacy_pending"
RESEARCH_DOSSIER_MAX_AGE_SECONDS = 6 * 60 * 60
RESEARCH_REQUIRED_SOURCE_CLASSES = {"resolution_source", "official_primary"}
WEATHER_SHADOW_ROW_COUNT_LIMIT = 10_000
WEATHER_SHADOW_TABLES = {
    "research_weather_shadow_snapshots": "snapshots",
    "research_weather_shadow_quotes": "quotes",
    "research_weather_shadow_outcomes": "outcomes",
    "research_weather_shadow_conflicts": "conflicts",
    "research_weather_shadow_outcome_checks": "checks",
}
CAPITAL_GUARD_SHADOW_ROW_COUNT_LIMIT = 10_000
CAPITAL_GUARD_SHADOW_TABLES = {
    "capital_guard_shadow_schema_meta": "meta",
    "capital_guard_shadow_capture_attempts": "attempts",
    "capital_guard_shadow_candidates": "candidates",
    "capital_guard_shadow_conflicts": "conflicts",
    "capital_guard_shadow_observations": "observations",
    "capital_guard_shadow_candidate_observations": "links",
    "capital_guard_shadow_settlement_attempts": "settlement_attempts",
    "capital_guard_shadow_settlement_quarantines": "quarantines",
    "capital_guard_shadow_settlements": "settlements",
    "capital_guard_shadow_evaluations": "evaluations",
}


def _research_proof_has_countercase(side: str, counter_directions: set[str]) -> bool:
    normalized_side = side.strip().lower()
    if normalized_side not in {"yes", "no"}:
        return False
    opposite = "no" if normalized_side == "yes" else "yes"
    return bool(counter_directions & {opposite, "neutral"})


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
    live_submission_unknown_count: int = 0
    latest_live_submission_unknown_ts: datetime | None = None
    paper_trade_admission_rows: int = 0
    paper_trade_replay_or_test_rows: int = 0
    paper_trade_replay_or_test_sources: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class LiveSubmissionHoldStats:
    path: Path
    status: str
    held_tickers: tuple[str, ...] = ()


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
    decision_grade_candidate_dossiers: int = 0
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
    live_submission_unknown_count = 0
    latest_live_submission_unknown_ts: datetime | None = None
    paper_trade_admission_rows = 0
    paper_trade_replay_or_test_rows = 0
    paper_trade_replay_or_test_sources: Counter[str] = Counter()
    research_records = 0
    records_kept = 0

    for record in _iter_trade_records(
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        event_type = str(record.get("type") or "").strip()
        if event_type not in SIGNAL_FLOW_EVENTS and event_type != LIVE_SUBMISSION_UNKNOWN_EVENT:
            continue
        ts = _parse_trade_ts(record.get("ts"))
        if ts is not None and ts < since:
            continue
        replay_or_test_paper_trade = is_replay_or_test_paper_trade(record)
        if event_type == "PAPER_TRADE":
            if replay_or_test_paper_trade:
                paper_trade_replay_or_test_rows += 1
                source = str(record.get("signal_source") or record.get("source") or "(unknown)").strip()
                paper_trade_replay_or_test_sources[source or "(unknown)"] += 1
            else:
                paper_trade_admission_rows += 1
        records_kept += 1
        if event_type == LIVE_SUBMISSION_UNKNOWN_EVENT:
            live_submission_unknown_count += 1
            if ts is not None and (
                latest_live_submission_unknown_ts is None
                or ts > latest_live_submission_unknown_ts
            ):
                latest_live_submission_unknown_ts = ts
            continue
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
        live_submission_unknown_count=live_submission_unknown_count,
        latest_live_submission_unknown_ts=latest_live_submission_unknown_ts,
        paper_trade_admission_rows=paper_trade_admission_rows,
        paper_trade_replay_or_test_rows=paper_trade_replay_or_test_rows,
        paper_trade_replay_or_test_sources=paper_trade_replay_or_test_sources,
    )


def _invalid_pending_paper_cohort_summary(root: Path, detail: str) -> dict[str, object]:
    return {"root": root, "status": "invalid", "detail": detail, "cohorts": ()}


def _pending_paper_cohort_regular_file_error(path: Path, *, label: str) -> str | None:
    try:
        file_mode = path.lstat().st_mode
    except FileNotFoundError:
        return f"legacy pending cohort {label} missing: {path}"
    except OSError:
        return f"legacy pending cohort {label} is unavailable: {path}"
    if stat.S_ISLNK(file_mode):
        return f"legacy pending cohort {label} is a symlink: {path}"
    if not stat.S_ISREG(file_mode):
        return f"legacy pending cohort {label} is not a regular file: {path}"
    return None


def summarize_pending_paper_cohorts(data_dir: Path) -> dict[str, object]:
    """Inspect pending-cohort manifests without opening their databases."""
    root = data_dir / PENDING_PAPER_COHORTS_DIRNAME
    try:
        root_mode = root.lstat().st_mode
    except FileNotFoundError:
        return {"root": root, "status": "absent", "cohorts": ()}
    except OSError:
        return _invalid_pending_paper_cohort_summary(
            root, "legacy pending cohort root is unavailable"
        )

    if stat.S_ISLNK(root_mode):
        return _invalid_pending_paper_cohort_summary(
            root, "legacy pending cohort root is a symlink"
        )
    if not stat.S_ISDIR(root_mode):
        return _invalid_pending_paper_cohort_summary(
            root, "legacy pending cohort root is not a directory"
        )

    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return _invalid_pending_paper_cohort_summary(
            root, "legacy pending cohort root cannot be listed"
        )

    cohorts: list[dict[str, object]] = []
    for entry in entries:
        try:
            entry_mode = entry.lstat().st_mode
        except OSError:
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort entry is unavailable: {entry}"
            )
        if stat.S_ISLNK(entry_mode):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort entry is a symlink: {entry}"
            )
        if not stat.S_ISDIR(entry_mode):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort entry is not a directory: {entry}"
            )

        manifest_path = entry / PENDING_PAPER_COHORT_MANIFEST_FILENAME
        try:
            manifest_mode = manifest_path.lstat().st_mode
        except OSError:
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort manifest is missing: {manifest_path}"
            )
        if stat.S_ISLNK(manifest_mode) or not stat.S_ISREG(manifest_mode):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort manifest is not a regular file: {manifest_path}"
            )

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort manifest is malformed: {manifest_path}"
            )
        if not isinstance(payload, dict):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort manifest is malformed: {manifest_path}"
            )

        cohort_id = payload.get("cohort_id")
        db_path_relative_to_storage_root = payload.get(
            "db_path_relative_to_storage_root"
        )
        legacy_db_path_relative_to_storage_root = payload.get(
            "legacy_db_path_relative_to_storage_root"
        )
        legacy_snapshot_path_relative_to_storage_root = payload.get(
            "legacy_snapshot_path_relative_to_storage_root"
        )
        if (
            type(payload.get("schema_version")) is not int
            or payload["schema_version"] != PENDING_PAPER_COHORT_MANIFEST_SCHEMA_VERSION
            or payload.get("cohort_type") != PENDING_PAPER_COHORT_TYPE
            or not isinstance(cohort_id, str)
            or not cohort_id
            or cohort_id != entry.name
            or not isinstance(db_path_relative_to_storage_root, str)
            or not db_path_relative_to_storage_root
            or not isinstance(legacy_db_path_relative_to_storage_root, str)
            or not legacy_db_path_relative_to_storage_root
            or not isinstance(legacy_snapshot_path_relative_to_storage_root, str)
            or not legacy_snapshot_path_relative_to_storage_root
        ):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort manifest is invalid: {manifest_path}"
            )

        relative_db_path = Path(db_path_relative_to_storage_root)
        database_path = data_dir / relative_db_path
        if (
            relative_db_path.is_absolute()
            or ".." in relative_db_path.parts
            or database_path.name != "paper_trades.db"
            or database_path.parent != entry
        ):
            return _invalid_pending_paper_cohort_summary(
                root, f"legacy pending cohort database path is invalid: {manifest_path}"
            )

        legacy_database_path = data_dir / Path(legacy_db_path_relative_to_storage_root)
        if (
            Path(legacy_db_path_relative_to_storage_root).is_absolute()
            or ".." in Path(legacy_db_path_relative_to_storage_root).parts
            or legacy_database_path != data_dir / "paper_trades.db"
        ):
            return _invalid_pending_paper_cohort_summary(
                root,
                f"legacy pending cohort legacy database path is invalid: {manifest_path}",
            )

        legacy_snapshot_path = data_dir / Path(
            legacy_snapshot_path_relative_to_storage_root
        )
        if (
            Path(legacy_snapshot_path_relative_to_storage_root).is_absolute()
            or ".." in Path(legacy_snapshot_path_relative_to_storage_root).parts
            or legacy_snapshot_path != entry / "legacy_cutover.db"
        ):
            return _invalid_pending_paper_cohort_summary(
                root,
                f"legacy pending cohort snapshot path is invalid: {manifest_path}",
            )

        for path, label in (
            (database_path, "database"),
            (legacy_database_path, "legacy database"),
            (legacy_snapshot_path, "snapshot"),
        ):
            file_error = _pending_paper_cohort_regular_file_error(path, label=label)
            if file_error is not None:
                return _invalid_pending_paper_cohort_summary(root, file_error)
        cohorts.append({"cohort_id": cohort_id, "database_path": database_path})

    return {
        "root": root,
        "status": "present_unverified" if cohorts else "empty",
        "cohorts": tuple(cohorts),
    }


def summarize_runtime_paper_cohort_attestation(
    data_dir: Path,
    receipt_path: Path,
    *,
    rows: list[ProcessInfo],
    launchd_pid: int | None,
    main_path: Path,
    now: datetime,
    now_epoch: float,
) -> dict[str, object]:
    """Validate a nonsecret startup receipt against the live Python process.

    The LaunchAgent PID may name a wrapper, so the receipt PID must be a real
    `main.py` process in that process tree before its binding is attested.
    """

    path = Path(receipt_path)
    try:
        path.lstat()
    except FileNotFoundError:
        return {
            "path": path,
            "status": "absent",
            "detail": "runtime cohort attestation is absent",
        }
    except OSError:
        return {
            "path": path,
            "status": "unverified",
            "detail": "runtime cohort attestation is unavailable",
        }
    try:
        receipt = read_runtime_paper_cohort_attestation(
            path,
            storage_root=data_dir,
        )
    except RuntimePaperCohortAttestationError as exc:
        return {"path": path, "status": "unverified", "detail": str(exc)}

    runtime_process = _attested_main_process(
        rows,
        pid=receipt.pid,
        main_path=main_path,
    )
    if runtime_process is None:
        return {
            "path": path,
            "status": "unverified",
            "detail": "attestation PID does not match a current main.py process",
        }
    if not _launchd_manages_attested_process(
        rows,
        launchd_pid=launchd_pid,
        attested_pid=receipt.pid,
    ):
        return {
            "path": path,
            "status": "unverified",
            "detail": "launchd PID does not manage the attested main.py process",
        }
    process_started_utc = process_start_utc(runtime_process, now_epoch)
    if receipt.started_utc < process_started_utc - timedelta(seconds=5):
        return {
            "path": path,
            "status": "unverified",
            "detail": "attestation predates the current main.py process",
        }
    if receipt.started_utc > now + timedelta(seconds=5):
        return {
            "path": path,
            "status": "unverified",
            "detail": "attestation start time is in the future",
        }

    try:
        database_path = _attested_cohort_database_path(data_dir, receipt)
    except RuntimePaperCohortAttestationError as exc:
        return {"path": path, "status": "unverified", "detail": str(exc)}
    return {
        "path": path,
        "status": "attested",
        "detail": "runtime binding attested",
        "pid": receipt.pid,
        "cohort_id": receipt.cohort_id,
        "cohort_kind": receipt.cohort_kind,
        "database_path": database_path,
    }


def _attested_main_process(
    rows: list[ProcessInfo],
    *,
    pid: int,
    main_path: Path,
) -> ProcessInfo | None:
    for process in rows:
        if process.pid != pid:
            continue
        if process.command.startswith("/usr/bin/caffeinate "):
            continue
        command_tokens = process.command.split()
        if len(command_tokens) < 2:
            continue
        if Path(command_tokens[-1]).name != main_path.name:
            continue
        if any(Path(token).name.lower().startswith("python") for token in command_tokens[:-1]):
            return process
    return None


def _launchd_manages_attested_process(
    rows: list[ProcessInfo],
    *,
    launchd_pid: int | None,
    attested_pid: int,
) -> bool:
    if launchd_pid is None:
        return False
    parent_by_pid = {process.pid: process.ppid for process in rows}

    def has_ancestor(pid: int, ancestor_pid: int) -> bool:
        seen: set[int] = set()
        current_pid = pid
        while current_pid not in seen:
            if current_pid == ancestor_pid:
                return True
            seen.add(current_pid)
            parent_pid = parent_by_pid.get(current_pid)
            if parent_pid is None:
                return False
            current_pid = parent_pid
        return False

    return has_ancestor(attested_pid, launchd_pid) or has_ancestor(
        launchd_pid,
        attested_pid,
    )


def _attested_cohort_database_path(
    data_dir: Path,
    receipt: RuntimePaperCohortAttestation,
) -> Path:
    if receipt.cohort_kind == "legacy":
        if receipt.manifest_bound or receipt.db_path_relative_to_storage_root != "paper_trades.db":
            raise RuntimePaperCohortAttestationError(
                "legacy attestation database path is invalid"
            )
        return data_dir / "paper_trades.db"

    if receipt.cohort_kind == "legacy_pending":
        cohort_root = data_dir / PENDING_PAPER_COHORTS_DIRNAME
    elif receipt.cohort_kind == "active":
        cohort_root = data_dir / "paper_cohorts"
    else:
        raise RuntimePaperCohortAttestationError("attestation cohort kind is invalid")
    expected_database_path = cohort_root / receipt.cohort_id / "paper_trades.db"
    if (
        Path(receipt.db_path_relative_to_storage_root)
        != expected_database_path.relative_to(data_dir)
    ):
        raise RuntimePaperCohortAttestationError(
            "attestation database path does not match its cohort manifest"
        )

    manifest_path = expected_database_path.parent / PENDING_PAPER_COHORT_MANIFEST_FILENAME
    try:
        mode = manifest_path.lstat().st_mode
    except OSError as exc:
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest is unavailable"
        ) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest is not a regular file"
        )
    try:
        raw_manifest = manifest_path.read_bytes()
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest is malformed"
        ) from exc
    if not isinstance(manifest, dict):
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest is malformed"
        )
    if (
        manifest.get("cohort_id") != receipt.cohort_id
        or manifest.get("cohort_type") != receipt.cohort_kind
        or manifest.get("cohort_identity") != receipt.cohort_identity
    ):
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest identity does not match"
        )
    if hashlib.sha256(raw_manifest).hexdigest() != receipt.manifest_sha256:
        raise RuntimePaperCohortAttestationError(
            "attestation cohort manifest SHA-256 does not match"
        )
    return expected_database_path


def summarize_live_submission_holds(path: Path) -> LiveSubmissionHoldStats:
    """Read the durable live-submission state without importing or mutating it."""
    try:
        if path.parent.exists() and not path.parent.is_dir():
            raise OSError("hold parent is not a directory")
        temp_path = path.with_suffix(path.suffix + ".tmp")
        if temp_path.exists():
            raise OSError("incomplete hold checkpoint")
        if not path.exists():
            return LiveSubmissionHoldStats(path=path, status="clear")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid hold checkpoint")
        if (
            type(payload.get("version")) is not int
            or payload["version"] != LIVE_SUBMISSION_HOLD_SCHEMA_VERSION
        ):
            raise ValueError("invalid hold checkpoint")
        held_tickers = payload.get("held_tickers")
        if not isinstance(held_tickers, list) or any(
            not isinstance(ticker, str) or not ticker for ticker in held_tickers
        ):
            raise ValueError("invalid hold checkpoint")
        return LiveSubmissionHoldStats(
            path=path,
            status="active" if held_tickers else "clear",
            held_tickers=tuple(sorted(set(held_tickers))),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return LiveSubmissionHoldStats(path=path, status="unavailable")


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
            evidence_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_evidence)").fetchall()
            }
            dossier_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(research_dossiers)").fetchall()
            }

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
            cache_evidence_ts_sql = """
                CASE
                    WHEN inserted_at IS NOT NULL
                         AND (retrieved_at IS NULL OR inserted_at > retrieved_at)
                    THEN inserted_at
                    ELSE retrieved_at
                END
            """
            latest_evidence_raw = conn.execute(
                f"""
                SELECT MAX({cache_evidence_ts_sql}) AS ts
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
            decision_grade_candidate_dossiers = 0
            if {"last_market_price", "last_estimated_edge"} <= dossier_columns:
                decision_grade_candidate_dossiers = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM research_dossiers
                        WHERE last_verdict_status = 'decision_grade_candidate'
                          AND last_force_side IN ('yes', 'no')
                          AND last_estimated_probability IS NOT NULL
                          AND last_confidence IS NOT NULL
                          AND last_market_price IS NOT NULL
                          AND last_estimated_edge IS NOT NULL
                        """
                    ).fetchone()["count"]
                )
            live_cache_eligible_tickers: set[str] = set()
            fresh_since = now - timedelta(hours=24)
            live_cache_since = now - timedelta(seconds=RESEARCH_DOSSIER_MAX_AGE_SECONDS)
            fresh_evidence_rows_24h = 0
            evidence_by_contract: dict[tuple[str, str], list[tuple[str, datetime]]] = {}
            evidence_by_proof: dict[
                tuple[str, str, str], list[tuple[str, datetime]]
            ] = {}
            counter_directions_by_contract: dict[tuple[str, str], set[str]] = {}
            counter_directions_by_proof: dict[tuple[str, str, str], set[str]] = {}
            fingerprint_select = (
                "contract_fingerprint"
                if "contract_fingerprint" in evidence_columns
                else "NULL AS contract_fingerprint"
            )
            run_id_select = (
                "research_run_id"
                if "research_run_id" in evidence_columns
                else "NULL AS research_run_id"
            )
            claim_type_select = "claim_type" if "claim_type" in evidence_columns else "NULL"
            direction_select = (
                "supports_direction" if "supports_direction" in evidence_columns else "NULL"
            )
            metric_name_select = (
                "metric_name" if "metric_name" in evidence_columns else "NULL"
            )
            published_at_select = (
                "published_at" if "published_at" in evidence_columns else "NULL"
            )
            retrieved_at_select = (
                "retrieved_at" if "retrieved_at" in evidence_columns else "NULL"
            )
            raw_payload_select = (
                "raw_payload_json"
                if "raw_payload_json" in evidence_columns
                else "NULL"
            )
            has_counter_columns = {"claim_type", "supports_direction"} <= evidence_columns
            has_research_runs = _sqlite_table_exists(conn, "research_runs")
            for row in conn.execute(
                f"""
                SELECT
                    market_ticker,
                    {run_id_select},
                    source_class,
                    {fingerprint_select},
                    {claim_type_select} AS claim_type,
                    {direction_select} AS supports_direction,
                    {metric_name_select} AS metric_name,
                    {published_at_select} AS published_at,
                    {retrieved_at_select} AS retrieved_at,
                    {raw_payload_select} AS raw_payload_json,
                    {cache_evidence_ts_sql} AS ts
                FROM research_evidence
                """
            ):
                evidence_ts = _parse_research_ts(row["ts"])
                if evidence_ts is not None and evidence_ts >= fresh_since:
                    fresh_evidence_rows_24h += 1
                if evidence_ts is not None and evidence_ts >= live_cache_since:
                    try:
                        raw_payload = json.loads(row["raw_payload_json"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        raw_payload = {}
                    if not research_evidence_temporally_valid(
                        {
                            "metric_name": row["metric_name"],
                            "published_at": row["published_at"],
                            "retrieved_at": row["retrieved_at"],
                            "available_at": (
                                raw_payload.get("available_at")
                                if isinstance(raw_payload, dict)
                                else None
                            ),
                        },
                        as_of=now,
                    ):
                        continue
                    ticker = str(row["market_ticker"] or "").strip()
                    fingerprint = str(row["contract_fingerprint"] or "").strip()
                    if ticker and fingerprint:
                        evidence_by_contract.setdefault((ticker, fingerprint), []).append(
                            (str(row["source_class"] or "").strip(), evidence_ts)
                        )
                        claim_type = str(row["claim_type"] or "").strip().lower()
                        direction = str(row["supports_direction"] or "").strip().lower()
                        if claim_type in {"disconfirming", "contradiction_check"}:
                            counter_directions_by_contract.setdefault(
                                (ticker, fingerprint),
                                set(),
                            ).add(direction)
                    run_id = str(row["research_run_id"] or "").strip()
                    if ticker and run_id and fingerprint:
                        evidence_by_proof.setdefault(
                            (ticker, run_id, fingerprint),
                            [],
                        ).append((str(row["source_class"] or "").strip(), evidence_ts))
                        if claim_type in {"disconfirming", "contradiction_check"}:
                            counter_directions_by_proof.setdefault(
                                (ticker, run_id, fingerprint),
                                set(),
                            ).add(direction)
            dossier_fingerprint_select = (
                "last_contract_fingerprint"
                if "last_contract_fingerprint" in dossier_columns
                else "NULL AS last_contract_fingerprint"
            )
            vetted_contracts: set[tuple[str, str]] = set()
            side_by_contract: dict[tuple[str, str], str] = {}
            if (
                not has_research_runs
                and {"last_market_price", "last_estimated_edge"} <= dossier_columns
            ):
                for row in conn.execute(
                    f"""
                        SELECT market_ticker, {dossier_fingerprint_select}
                        , last_force_side
                        FROM research_dossiers
                        WHERE last_verdict_status = 'decision_grade_candidate'
                          AND last_force_side IN ('yes', 'no')
                          AND last_estimated_probability IS NOT NULL
                          AND last_confidence IS NOT NULL
                          AND last_market_price IS NOT NULL
                          AND last_estimated_edge IS NOT NULL
                        """
                ):
                    contract = (
                        str(row["market_ticker"] or "").strip(),
                        str(row["last_contract_fingerprint"] or "").strip(),
                    )
                    vetted_contracts.add(contract)
                    side_by_contract[contract] = str(row["last_force_side"] or "")
            for ticker, fingerprint in vetted_contracts:
                if not ticker or not fingerprint:
                    continue
                contract_evidence = evidence_by_contract.get((ticker, fingerprint), [])
                if len(contract_evidence) < 2:
                    continue
                if any(
                    source_class in RESEARCH_REQUIRED_SOURCE_CLASSES
                    for source_class, _ts in contract_evidence
                ) and (
                    not has_counter_columns
                    or _research_proof_has_countercase(
                        side_by_contract.get((ticker, fingerprint), ""),
                        counter_directions_by_contract.get((ticker, fingerprint), set()),
                    )
                ):
                    live_cache_eligible_tickers.add(ticker)
            if has_research_runs:
                run_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(research_runs)").fetchall()
                }
                has_research_tasks = _sqlite_table_exists(conn, "research_tasks")
                task_join = (
                    "JOIN research_tasks t ON t.market_ticker = r.market_ticker"
                    if has_research_tasks
                    else ""
                )
                task_candidate_filter = (
                    "AND t.state = 'decision_grade_candidate'"
                    if has_research_tasks
                    else ""
                )
                current_fingerprint_select = (
                    "r.contract_fingerprint"
                    if "contract_fingerprint" in run_columns
                    else "NULL"
                )
                if {"market_price", "estimated_edge", "force_side"} <= run_columns:
                    run_created_expr = (
                        "r.created_ts" if "created_ts" in run_columns else "NULL"
                    )
                    run_has_unusable_timestamp_sql = (
                        "timestamp_check.created_ts IS NULL "
                        "OR datetime(timestamp_check.created_ts) IS NULL"
                        if "created_ts" in run_columns
                        else "1"
                    )
                    for row in conn.execute(
                        f"""
                        SELECT r.market_ticker, r.research_run_id, r.force_side,
                               {current_fingerprint_select} AS contract_fingerprint
                        FROM (
                            SELECT
                                r.*,
                                ROW_NUMBER() OVER (
                                    PARTITION BY r.market_ticker
                                    ORDER BY
                                        CASE WHEN EXISTS (
                                            SELECT 1
                                            FROM research_runs AS timestamp_check
                                            WHERE timestamp_check.market_ticker = r.market_ticker
                                              AND ({run_has_unusable_timestamp_sql})
                                        ) THEN r.rowid ELSE 0 END DESC,
                                        {run_created_expr} DESC,
                                        r.rowid DESC
                                ) AS current_run_rank
                            FROM research_runs AS r
                        ) AS r
                        JOIN research_dossiers d
                          ON d.market_ticker = r.market_ticker
                        {task_join}
                        WHERE r.current_run_rank = 1
                          AND r.verdict_status = 'decision_grade_candidate'
                          {task_candidate_filter}
                          AND r.force_side IN ('yes', 'no')
                          AND r.estimated_probability IS NOT NULL
                          AND r.confidence IS NOT NULL
                          AND r.market_price IS NOT NULL
                          AND r.estimated_edge IS NOT NULL
                        """
                    ):
                        ticker = str(row["market_ticker"] or "").strip()
                        run_id = str(row["research_run_id"] or "").strip()
                        current_fingerprint = str(
                            row["contract_fingerprint"] or ""
                        ).strip()
                        if not ticker or not run_id or not current_fingerprint:
                            continue
                        for proof_ticker, proof_run_id, _fingerprint in evidence_by_proof:
                            if proof_ticker != ticker or proof_run_id != run_id:
                                continue
                            if _fingerprint != current_fingerprint:
                                continue
                            proof = (proof_ticker, proof_run_id, _fingerprint)
                            proof_evidence = evidence_by_proof[
                                proof
                            ]
                            if len(proof_evidence) < 2:
                                continue
                            if any(
                                source_class in RESEARCH_REQUIRED_SOURCE_CLASSES
                                for source_class, _ts in proof_evidence
                            ) and (
                                not has_counter_columns
                                or _research_proof_has_countercase(
                                    str(row["force_side"] or ""),
                                    counter_directions_by_proof.get(proof, set()),
                                )
                            ):
                                live_cache_eligible_tickers.add(ticker)
                                break
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
        decision_grade_candidate_dossiers=decision_grade_candidate_dossiers,
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
        value = dotenv_values(path).get(key)
    except OSError:
        return None
    return value if isinstance(value, str) else None


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


@contextmanager
def _open_sqlite_snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    with tempfile.TemporaryDirectory(prefix="kalshi-botcheck-settlement-") as temp_dir:
        snapshot_path = Path(temp_dir) / db_path.name
        shutil.copyfile(db_path, snapshot_path)
        for suffix in ("-wal", "-shm"):
            try:
                shutil.copyfile(
                    Path(f"{db_path}{suffix}"),
                    Path(f"{snapshot_path}{suffix}"),
                )
            except FileNotFoundError:
                pass

        db_uri = f"{snapshot_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        try:
            yield conn
        finally:
            conn.close()


def print_g7_skip_evidence_status(
    repo_root: Path,
    *,
    current_proc: ProcessInfo | None = None,
) -> None:
    """Print default-off G7 evidence state without initializing its store."""
    flag_name = "ENABLE_G7_SKIP_EVIDENCE_CAPTURE"
    if current_proc is not None:
        runtime_value, runtime_readable = _running_process_env_value(
            current_proc,
            flag_name,
        )
        if not runtime_readable:
            print("g7_skip_evidence: unknown (runtime-process-env-unreadable)")
            return
        if runtime_value is not None:
            enabled_value, source = runtime_value, "runtime-process-env"
        else:
            fallback = _read_env_file_value(repo_root / ".env", flag_name)
            enabled_value = fallback or "false"
            source = ".env fallback" if fallback else "default fallback"
    else:
        enabled_value, source = _research_env_value(repo_root, flag_name, "false")

    enabled = _env_bool(enabled_value)
    state = ("configured-" if source.endswith(" fallback") else "") + (
        "on" if enabled else "off"
    )
    prefix = f"g7_skip_evidence: {state} ({source})"
    if not enabled:
        print(prefix)
        return

    db_path = repo_root / "data" / "g7_skip_evidence.db"
    if not db_path.is_file():
        print(f"{prefix} db=missing")
        return

    from trading.g7_skip_evidence import read_g7_skip_evidence_snapshot

    snapshot = read_g7_skip_evidence_snapshot(db_path)
    status_counts = ",".join(
        f"{status}:{count}" for status, count in snapshot.receipt_counts_by_status
    ) or "none"
    schema = "ok" if snapshot.schema_valid else "invalid"
    latest_capture = (
        snapshot.latest_captured_at.isoformat()
        if snapshot.latest_captured_at is not None
        else "n/a"
    )
    print(
        f"{prefix} integrity={snapshot.integrity_check} schema={schema} "
        f"rows={snapshot.record_count} statuses={status_counts} "
        f"last_capture={latest_capture} diagnostic_only=true"
    )


_FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT: dict[str, object] = {
    "transport_authentication": "upstream_attested_not_proven_by_ledger",
    "pagination_coverage": "unknown",
    "canonical_settlement_binding": "absent",
    "fee_net_pnl": "unscorable",
    "paper_trader_updated": False,
    "orders_changed": False,
    "promotion_eligible": False,
}
_FIX_SETTLEMENT_INGRESS_STATUS_VALUES = frozenset(
    {
        "captured_non_authoritative",
        "disabled_non_authoritative",
        "absent_non_authoritative",
        "invalid_non_authoritative",
    }
)


def _fix_settlement_ingress_status(
    status: str,
    *,
    raw_capture: str,
    session_provenance: str,
) -> dict[str, object]:
    return {
        "status": status,
        "transport_authentication": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "transport_authentication"
        ],
        "raw_capture": raw_capture,
        "session_provenance": session_provenance,
        "pagination_coverage": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "pagination_coverage"
        ],
        "canonical_settlement_binding": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "canonical_settlement_binding"
        ],
        "fee_net_pnl": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT["fee_net_pnl"],
        "paper_trader_updated": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "paper_trader_updated"
        ],
        "orders_changed": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "orders_changed"
        ],
        "promotion_eligible": _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT[
            "promotion_eligible"
        ],
    }


def _validated_fix_settlement_ingress_snapshot(snapshot: object) -> dict[str, object]:
    """Reject any diagnostic projection that could overstate receipt authority."""
    if not is_dataclass(snapshot) or isinstance(snapshot, type):
        raise ValueError("FIX settlement ingress snapshot must be a dataclass")

    status = getattr(snapshot, "status", None)
    raw_capture = getattr(snapshot, "raw_capture", None)
    session_provenance = getattr(snapshot, "session_provenance", None)
    if status not in _FIX_SETTLEMENT_INGRESS_STATUS_VALUES:
        raise ValueError("FIX settlement ingress snapshot has an invalid status")
    if raw_capture not in {"present", "absent"}:
        raise ValueError("FIX settlement ingress snapshot has an invalid raw-capture state")
    if session_provenance not in {"present", "absent"}:
        raise ValueError(
            "FIX settlement ingress snapshot has an invalid session-provenance state"
        )
    if status == "captured_non_authoritative" and (
        raw_capture != "present" or session_provenance != "present"
    ):
        raise ValueError("captured FIX ingress status requires raw capture and provenance")
    if status != "captured_non_authoritative" and (
        raw_capture != "absent" or session_provenance != "absent"
    ):
        raise ValueError("non-captured FIX ingress status cannot assert receipt material")

    for key, expected in _FIX_SETTLEMENT_INGRESS_STATIC_INVARIANT.items():
        if getattr(snapshot, key, None) != expected:
            raise ValueError(f"FIX settlement ingress snapshot violates {key}")

    return _fix_settlement_ingress_status(
        str(status),
        raw_capture=str(raw_capture),
        session_provenance=str(session_provenance),
    )


def _format_fix_settlement_ingress_status(status: dict[str, object]) -> str:
    return " ".join(
        f"{key}={str(value).lower()}" for key, value in status.items()
    )


def print_kalshi_fix_settlement_ingress_status(
    repo_root: Path,
    *,
    current_proc: ProcessInfo | None = None,
) -> None:
    """Print configured, read-only UMS ingress state without initializing a store."""
    flag_name = "ENABLE_KALSHI_FIX_SETTLEMENT_INGRESS"
    if current_proc is not None:
        runtime_value, runtime_readable = _running_process_env_value(
            current_proc,
            flag_name,
        )
        if not runtime_readable:
            status = _fix_settlement_ingress_status(
                "invalid_non_authoritative",
                raw_capture="absent",
                session_provenance="absent",
            )
            print(
                "kalshi_fix_settlement_ingress: configuration-unknown "
                "(runtime-process-env-unreadable) "
                f"{_format_fix_settlement_ingress_status(status)}"
            )
            return
        if runtime_value is not None:
            enabled_value, source = runtime_value, "runtime-process-env"
        else:
            fallback = _read_env_file_value(repo_root / ".env", flag_name)
            enabled_value = fallback or "false"
            source = ".env fallback" if fallback else "default fallback"
    else:
        enabled_value, source = _research_env_value(repo_root, flag_name, "false")

    enabled = _env_bool(enabled_value)
    prefix = (
        "kalshi_fix_settlement_ingress: "
        f"configured-{'on' if enabled else 'off'} ({source})"
    )
    if not enabled:
        status = _fix_settlement_ingress_status(
            "disabled_non_authoritative",
            raw_capture="absent",
            session_provenance="absent",
        )
        print(f"{prefix} {_format_fix_settlement_ingress_status(status)}")
        return

    db_path = repo_root / "data" / "kalshi_fix_settlement_ingress.db"
    if not db_path.is_file():
        status = _fix_settlement_ingress_status(
            "absent_non_authoritative",
            raw_capture="absent",
            session_provenance="absent",
        )
        print(
            f"{prefix} {_format_fix_settlement_ingress_status(status)} db=missing"
        )
        return

    try:
        from trading.kalshi_fix_settlement_ingress import (
            read_kalshi_fix_settlement_ingress_snapshot,
        )

        snapshot = read_kalshi_fix_settlement_ingress_snapshot(db_path)
        status = _validated_fix_settlement_ingress_snapshot(snapshot)
    except Exception as exc:
        status = _fix_settlement_ingress_status(
            "invalid_non_authoritative",
            raw_capture="absent",
            session_provenance="absent",
        )
        print(
            f"{prefix} {_format_fix_settlement_ingress_status(status)} "
            f"db=invalid error={type(exc).__name__}"
        )
        return

    print(f"{prefix} {_format_fix_settlement_ingress_status(status)} db=present")


def print_canonical_settlement_status(
    repo_root: Path,
    *,
    runtime_paper_cohort_attestation: Mapping[str, object] | None = None,
) -> None:
    enabled_value, source = _research_env_value(
        repo_root,
        "ENABLE_CANONICAL_PERSISTED_SETTLEMENT_RECONCILIATION",
        "false",
    )
    enabled = "on" if _env_bool(enabled_value) else "off"
    prefix = f"canonical_settlement: {enabled} ({source})"
    unavailable = (
        "mapped_open=n/a identity_quarantined_open=n/a "
        "identity_unmapped_open=n/a settlement_quarantine=n/a "
        "pending_outbox=n/a last_observation=n/a"
    )

    if (
        runtime_paper_cohort_attestation is not None
        and runtime_paper_cohort_attestation.get("status") != "attested"
    ):
        print(
            f"{prefix} db=unavailable binding=runtime_unverified "
            f"schema=n/a contract=n/a {unavailable}"
        )
        return

    db_path = repo_root / "data" / "paper_trades.db"
    binding_suffix = ""
    if (
        runtime_paper_cohort_attestation is not None
        and runtime_paper_cohort_attestation.get("status") == "attested"
    ):
        runtime_db_path = runtime_paper_cohort_attestation.get("database_path")
        if not isinstance(runtime_db_path, Path):
            print(f"{prefix} db=invalid binding=runtime_attested schema=n/a contract=n/a {unavailable}")
            return
        try:
            runtime_db_path.resolve().relative_to((repo_root / "data").resolve())
        except ValueError:
            print(f"{prefix} db=invalid binding=runtime_attested schema=n/a contract=n/a {unavailable}")
            return
        db_path = runtime_db_path
        binding_suffix = " binding=runtime_attested"
    if not db_path.is_file():
        print(f"{prefix} db=missing{binding_suffix} schema=n/a contract=n/a {unavailable}")
        return

    try:
        with _open_sqlite_snapshot(db_path) as conn:
            schema_present = _sqlite_table_exists(
                conn,
                "paper_settlement_schema_meta",
            )
            if not schema_present:
                print(
                    f"{prefix} db=ok{binding_suffix} schema=absent contract=n/a {unavailable}"
                )
                return

            contract = (
                "ok" if settlement_schema_contract_matches(conn) else "mismatch"
            )
            paper_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(paper_trades)")
            }
            identity_columns = {"resolved", "identity_status", "venue_market_id"}
            if identity_columns <= paper_columns:
                backlog_row = conn.execute(
                    """
                    SELECT
                        SUM(CASE WHEN identity_status='mapped'
                                      AND venue_market_id IS NOT NULL
                                      AND TRIM(venue_market_id)<>''
                                 THEN 1 ELSE 0 END),
                        SUM(CASE WHEN identity_status='quarantined'
                                 THEN 1 ELSE 0 END),
                        SUM(CASE WHEN COALESCE(identity_status, '')<>'quarantined'
                                      AND (identity_status IS NULL
                                           OR identity_status<>'mapped'
                                           OR venue_market_id IS NULL
                                           OR TRIM(venue_market_id)='')
                                 THEN 1 ELSE 0 END)
                    FROM paper_trades
                    WHERE resolved=0
                    """
                ).fetchone()
                mapped_open = int(backlog_row[0] or 0)
                identity_quarantined_open = int(backlog_row[1] or 0)
                identity_unmapped_open = int(backlog_row[2] or 0)
            else:
                mapped_open = None
                identity_quarantined_open = None
                identity_unmapped_open = None

            if _sqlite_table_exists(conn, "paper_settlement_quarantine"):
                quarantine_row = conn.execute(
                    "SELECT COUNT(*) FROM paper_settlement_quarantine"
                ).fetchone()
                settlement_quarantine = int(quarantine_row[0] if quarantine_row else 0)
            else:
                settlement_quarantine = None

            outbox_tables = (
                "paper_settlement_outbox",
                "paper_settlement_outbox_requirements",
                "paper_settlement_consumer_receipts",
            )
            if all(_sqlite_table_exists(conn, table) for table in outbox_tables):
                pending_row = conn.execute(
                    """
                    SELECT COUNT(DISTINCT requirement.outbox_id)
                    FROM paper_settlement_outbox_requirements AS requirement
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM paper_settlement_consumer_receipts AS receipt
                        WHERE receipt.outbox_id=requirement.outbox_id
                          AND receipt.consumer_name=requirement.consumer_name
                    )
                    """
                ).fetchone()
                pending_outbox = int(pending_row[0] if pending_row else 0)
            else:
                pending_outbox = None

            if _sqlite_table_exists(conn, "paper_settlement_observations"):
                observation_row = conn.execute(
                    """
                    SELECT applied_at, venue, venue_market_id, outcome,
                           observation_sha256
                    FROM paper_settlement_observations
                    ORDER BY applied_at DESC, observation_sha256 DESC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                observation_row = None
    except (OSError, sqlite3.Error) as exc:
        print(f"{prefix} db=error{binding_suffix} error={exc}")
        return

    last_observation = (
        "|".join(str(value) for value in observation_row)
        if observation_row is not None
        else "n/a"
    )
    display = lambda value: "n/a" if value is None else str(value)
    print(
        f"{prefix} db=ok{binding_suffix} schema=present contract={contract} "
        f"mapped_open={display(mapped_open)} "
        f"identity_quarantined_open={display(identity_quarantined_open)} "
        f"identity_unmapped_open={display(identity_unmapped_open)} "
        f"settlement_quarantine={display(settlement_quarantine)} "
        f"pending_outbox={display(pending_outbox)} "
        f"last_observation={last_observation}"
    )


def _running_process_env_value(
    proc: ProcessInfo | None,
    key: str,
) -> tuple[str | None, bool]:
    if proc is None or re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None:
        return None, False
    output = run_command(["ps", "eww", "-p", str(proc.pid), "-o", "command="])
    if not output:
        return None, False
    match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]*)", output)
    return (match.group(1) if match else None), True


def print_weather_shadow_status(
    repo_root: Path,
    *,
    current_proc: ProcessInfo | None = None,
) -> None:
    runtime_value, runtime_readable = _running_process_env_value(
        current_proc,
        "ENABLE_WEATHER_SHADOW_CAPTURE",
    )
    if current_proc is not None and not runtime_readable:
        print("weather_shadow: unknown (runtime-process-env-unreadable)")
        return
    if runtime_readable:
        enabled_value, source = runtime_value or "false", "runtime-process-env"
    else:
        enabled_value, source = _research_env_value(
            repo_root,
            "ENABLE_WEATHER_SHADOW_CAPTURE",
            "false",
        )
    if not _env_bool(enabled_value):
        print(f"weather_shadow: off ({source})")
        return

    db_path = repo_root / "data" / "weather_shadow.db"
    if not db_path.is_file():
        print(f"weather_shadow: on ({source}) db=missing")
        return

    try:
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(db_uri, uri=True) as conn:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "unknown"
            application_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' "
                    "AND name GLOB 'research_weather_shadow_*'"
                )
            }
            expected_tables = set(WEATHER_SHADOW_TABLES)
            present_tables = application_tables & expected_tables
            missing_tables = expected_tables - application_tables
            unexpected_tables = application_tables - expected_tables
            counts: dict[str, int | None] = {}
            for table_name, label in WEATHER_SHADOW_TABLES.items():
                if table_name not in present_tables:
                    counts[label] = None
                    continue
                row = conn.execute(
                    f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table_name}" LIMIT ?)',
                    (WEATHER_SHADOW_ROW_COUNT_LIMIT + 1,),
                ).fetchone()
                counts[label] = int(row[0]) if row else 0

            last_capture = None
            snapshots_table = "research_weather_shadow_snapshots"
            if snapshots_table in present_tables:
                row = conn.execute(
                    f"SELECT capture_finished_at FROM {snapshots_table} "
                    "WHERE capture_finished_at IS NOT NULL "
                    "ORDER BY capture_finished_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    last_capture = str(row[0])
    except sqlite3.Error as exc:
        print(f"weather_shadow: on ({source}) db=error error={exc}")
        return

    row_summary = ",".join(
        f"{label}:{'missing' if count is None else f'{WEATHER_SHADOW_ROW_COUNT_LIMIT}+' if count > WEATHER_SHADOW_ROW_COUNT_LIMIT else count}"
        for label, count in counts.items()
    )
    missing_summary = ",".join(sorted(missing_tables)) or "none"
    unexpected_summary = ",".join(sorted(unexpected_tables)) or "none"
    schema_status = "ok" if not missing_tables and not unexpected_tables else "mismatch"
    print(
        f"weather_shadow: on ({source}) integrity={integrity} "
        f"tables={len(present_tables)}/{len(WEATHER_SHADOW_TABLES)} "
        f"schema={schema_status} missing={missing_summary} "
        f"unexpected={unexpected_summary} "
        f"rows={row_summary} last_capture={last_capture or 'n/a'}"
    )


def print_capital_guard_shadow_status(
    repo_root: Path,
    *,
    current_proc: ProcessInfo | None = None,
) -> None:
    flag_names = (
        "ENABLE_CAPITAL_GUARD_SHADOW_CAPTURE",
        "ENABLE_CAPITAL_GUARD_SHADOW_SETTLEMENT_COLLECTION",
    )
    if current_proc is not None:
        runtime_flags = tuple(
            _running_process_env_value(current_proc, flag_name)
            for flag_name in flag_names
        )
        if not all(readable for _, readable in runtime_flags):
            print("capital_guard_shadow: unknown (runtime-process-env-unreadable)")
            return
        resolved_flags: list[tuple[str, str]] = []
        for flag_name, (value, _) in zip(flag_names, runtime_flags):
            if value is not None:
                resolved_flags.append((value, "runtime-process-env"))
                continue
            fallback = _read_env_file_value(repo_root / ".env", flag_name)
            resolved_flags.append(
                (fallback or "false", ".env fallback" if fallback else "default fallback")
            )
        values = tuple(value for value, _ in resolved_flags)
        sources = tuple(source for _, source in resolved_flags)
    else:
        configured_flags = tuple(
            _research_env_value(repo_root, flag_name, "false")
            for flag_name in flag_names
        )
        values = tuple(value for value, _ in configured_flags)
        sources = tuple(source for _, source in configured_flags)

    capture_enabled, collection_enabled = map(_env_bool, values)
    capture_state = (
        "configured-" if sources[0].endswith(" fallback") else ""
    ) + ("on" if capture_enabled else "off")
    collection_state = (
        "configured-" if sources[1].endswith(" fallback") else ""
    ) + ("on" if collection_enabled else "off")
    capture_summary = f"capture={capture_state} ({sources[0]})"
    collection_summary = f"collection={collection_state} ({sources[1]})"
    prefix = f"capital_guard_shadow: {capture_summary} {collection_summary}"
    if not capture_enabled and not collection_enabled:
        print(prefix)
        return

    db_path = repo_root / "data" / "capital_guard_shadow.db"
    if not db_path.is_file():
        exact_source_status = (
            " exact-source=blocked-isolated-db-missing"
            if collection_enabled
            else ""
        )
        print(f"{prefix}{exact_source_status} db=missing")
        return

    try:
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(db_uri, uri=True) as conn:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "unknown"
            application_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' "
                    "AND name GLOB 'capital_guard_shadow_*'"
                )
            }
            expected_tables = set(CAPITAL_GUARD_SHADOW_TABLES)
            present_tables = application_tables & expected_tables
            missing_tables = expected_tables - application_tables
            unexpected_tables = application_tables - expected_tables
            counts: dict[str, int | None] = {}
            for table_name, label in CAPITAL_GUARD_SHADOW_TABLES.items():
                if table_name not in present_tables:
                    counts[label] = None
                    continue
                row = conn.execute(
                    f'SELECT COUNT(*) FROM (SELECT 1 FROM "{table_name}" LIMIT ?)',
                    (CAPITAL_GUARD_SHADOW_ROW_COUNT_LIMIT + 1,),
                ).fetchone()
                counts[label] = int(row[0]) if row else 0
            if {
                "capital_guard_shadow_candidates",
                "capital_guard_shadow_observations",
                "capital_guard_shadow_candidate_observations",
            } <= present_tables:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT 1
                        FROM capital_guard_shadow_candidates candidate
                        LEFT JOIN capital_guard_shadow_observations head
                          ON head.venue = candidate.venue
                         AND head.venue_market_id = candidate.venue_market_id
                         AND NOT EXISTS (
                             SELECT 1
                             FROM capital_guard_shadow_observations child
                             WHERE child.supersedes_observation_sha256 =
                                   head.observation_sha256
                         )
                        LEFT JOIN capital_guard_shadow_candidate_observations link
                          ON link.candidate_id = candidate.candidate_id
                         AND link.observation_sha256 = head.observation_sha256
                        WHERE head.observation_sha256 IS NULL OR link.link_id IS NULL
                        LIMIT ?
                    )
                    """,
                    (CAPITAL_GUARD_SHADOW_ROW_COUNT_LIMIT + 1,),
                ).fetchone()
                counts["link_backlog"] = int(row[0]) if row else 0
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT 1
                        FROM capital_guard_shadow_observations head
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM capital_guard_shadow_observations child
                            WHERE child.supersedes_observation_sha256 =
                                  head.observation_sha256
                        )
                        LIMIT ?
                    )
                    """,
                    (CAPITAL_GUARD_SHADOW_ROW_COUNT_LIMIT + 1,),
                ).fetchone()
                counts["current_heads"] = int(row[0]) if row else 0
            else:
                counts["link_backlog"] = None
                counts["current_heads"] = None
            exact_schema_contract = capital_guard_shadow_schema_contract_matches(conn)
    except (OSError, sqlite3.Error) as exc:
        exact_source_status = (
            " exact-source=blocked-isolated-db-invalid"
            if collection_enabled
            else ""
        )
        print(f"{prefix}{exact_source_status} db=error:{type(exc).__name__}")
        return

    row_summary = ",".join(
        f"{label}:{count if count is not None else 'missing'}"
        for label, count in counts.items()
    )
    missing_summary = ",".join(sorted(missing_tables)) or "none"
    unexpected_summary = ",".join(sorted(unexpected_tables)) or "none"
    schema_status = (
        "ok"
        if not missing_tables and not unexpected_tables and exact_schema_contract
        else "mismatch"
    )
    exact_source_status = ""
    if collection_enabled:
        exact_source_status = (
            " exact-source=runtime-eligible"
            if integrity == "ok" and schema_status == "ok"
            else " exact-source=blocked-isolated-db-invalid"
        )
    print(
        f"{prefix}{exact_source_status} integrity={integrity} "
        f"tables={len(present_tables)}/{len(CAPITAL_GUARD_SHADOW_TABLES)} "
        f"schema={schema_status} missing={missing_summary} "
        f"unexpected={unexpected_summary} rows={row_summary}"
    )


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
        if event_type not in RESEARCH_PREWARM_EVENT_TYPES:
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
    return record_targets_kalshi_research_prewarm(
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
    current_proc: ProcessInfo | None = None,
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
    runtime_search_circuit_mode, runtime_search_circuit_readable = (
        _running_process_env_value(current_proc, "GENERIC_SEARCH_CIRCUIT_MODE")
    )
    search_circuit_runtime_unreadable = (
        current_proc is not None and not runtime_search_circuit_readable
    )
    if runtime_search_circuit_readable:
        search_circuit_mode = runtime_search_circuit_mode or "shadow"
        search_circuit_mode_source = "runtime-process-env"
    else:
        search_circuit_mode, search_circuit_mode_source = _research_env_value(
            repo_root,
            "GENERIC_SEARCH_CIRCUIT_MODE",
            "shadow",
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
    if search_circuit_runtime_unreadable:
        print("search_cb : unknown (runtime-process-env-unreadable)")
    else:
        search_circuit_mode = search_circuit_mode.strip()
        search_circuit_suffix = (
            ""
            if search_circuit_mode in {"off", "shadow", "enforce"}
            else " INVALID"
        )
        print(
            f"search_cb : {search_circuit_mode} "
            f"({search_circuit_mode_source}){search_circuit_suffix}"
        )
    profile_path = Path("docs/governance/research-shadow.env.example")
    activation = evaluate_activation_profile(repo_root, profile_path)
    relative_profile = _relative_display_path(activation.profile_path, repo_root)
    print(
        "activation : "
        f"{'PASS' if activation.ok else 'FAIL'} profile={relative_profile}"
    )
    for key in activation.missing:
        expected = activation.profile_values.get(key, "")
        print(f"missing    : {key}={expected}")
    for key, expected, actual in activation.mismatched:
        print(f"mismatch   : {key} expected={expected} actual={actual}")
    for item in activation.unsafe:
        print(f"unsafe     : {item}")
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
                "snapshot_decision_grade_candidate="
                f"{dossier_stats.decision_grade_candidate_dossiers} "
                "current_live_cache_eligible="
                f"{dossier_stats.live_cache_eligible_dossiers}"
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


def print_last_boot(
    log_path: Path,
    sessions: list[BotSession],
    now: datetime,
    *,
    current_proc: ProcessInfo | None = None,
    now_epoch: float | None = None,
) -> None:
    print("=== Last bot log boot marker ===")
    print(f"Log file   : {log_path}")
    if current_proc is not None and now_epoch is not None:
        process_started = process_start_utc(current_proc, now_epoch)
        print(f"Process started UTC: {process_started.isoformat()}")
        print("Boundary note: Use process start for since-restart P&L when it predates the latest log boot marker.")
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
    if not stats.counts and not stats.live_submission_unknown_count:
        print("Result     : no signal-flow records found in window")
        print()
        return

    if stats.counts:
        for event_type in SIGNAL_FLOW_EVENTS:
            count = stats.counts.get(event_type, 0)
            latest = stats.latest_ts_by_type.get(event_type)
            age = human_duration((now - latest).total_seconds()) if latest is not None else "n/a"
            latest_text = latest.isoformat() if latest is not None else "n/a"
            if event_type == "PAPER_TRADE":
                print(f"PAPER_TRADE raw              : {count} latest={latest_text} age={age}")
            else:
                print(f"{event_type:<17}: {count:>5} latest={latest_text} age={age}")
        paper_trade_raw_rows = stats.counts.get("PAPER_TRADE", 0)
        if paper_trade_raw_rows or stats.paper_trade_replay_or_test_rows:
            print(f"PAPER_TRADE admissions       : {stats.paper_trade_admission_rows}")
            print(f"PAPER_TRADE replay/test      : {stats.paper_trade_replay_or_test_rows}")
            if stats.paper_trade_replay_or_test_sources:
                source_summary = ", ".join(
                    f"{source}: {count}"
                    for source, count in stats.paper_trade_replay_or_test_sources.most_common()
                )
                print(f"PAPER_TRADE replay/test srcs : {source_summary}")
    if stats.live_submission_unknown_count:
        latest = stats.latest_live_submission_unknown_ts
        age = human_duration((now - latest).total_seconds()) if latest is not None else "n/a"
        latest_text = latest.isoformat() if latest is not None else "n/a"
        print(
            "WARNING    : LIVE_SUBMISSION_UNKNOWN="
            f"{stats.live_submission_unknown_count} latest={latest_text} age={age}; "
            "reconciliation required"
        )
    print()


def print_pending_paper_cohort_section(summary: dict[str, object]) -> None:
    print("=== Pending paper cohorts (read-only manifest status) ===")
    print(f"Root       : {summary.get('root', 'n/a')}")
    status = summary.get("status")
    if status == "absent":
        print("Result     : legacy pending cohort root absent")
    elif status == "invalid":
        print(f"WARNING    : {summary.get('detail', 'manifest status unavailable')}")
    elif status == "empty":
        print("Result     : no pending paper cohort manifests found")
    else:
        for cohort in summary.get("cohorts", ()):
            if not isinstance(cohort, dict):
                continue
            print(f"Cohort     : {cohort.get('cohort_id', 'n/a')}")
            print(f"Database   : {cohort.get('database_path', 'n/a')}")
        if status == "present_unverified":
            print("Result     : topology present; runtime binding/identity remains unverified")
    print()


def print_runtime_paper_cohort_attestation_section(summary: dict[str, object]) -> None:
    print("=== Runtime paper cohort binding ===")
    print(f"Receipt    : {summary.get('path', 'n/a')}")
    if summary.get("status") == "attested":
        print(f"PID        : {summary.get('pid', 'n/a')}")
        print(f"Cohort     : {summary.get('cohort_id', 'n/a')}")
        print(f"Kind       : {summary.get('cohort_kind', 'n/a')}")
        print(f"Database   : {summary.get('database_path', 'n/a')}")
        print("Result     : runtime binding attested")
    else:
        print(f"Result     : {summary.get('detail', 'runtime binding remains unverified')}")
    print()


def summarize_runtime_paper_trade_journal_parity(
    home: Path,
    trade_log_root: Path,
    runtime_paper_cohort_attestation: Mapping[str, object],
) -> dict[str, object]:
    """Read current attested cohort parity without letting botcheck fail open."""

    if runtime_paper_cohort_attestation.get("status") != "attested":
        return {
            "status": "unavailable",
            "detail": "runtime paper cohort is not attested",
        }
    cohort_id = runtime_paper_cohort_attestation.get("cohort_id")
    cohort_kind = runtime_paper_cohort_attestation.get("cohort_kind")
    database_path = runtime_paper_cohort_attestation.get("database_path")
    if type(cohort_id) is not str or type(cohort_kind) is not str or not isinstance(database_path, Path):
        return {
            "status": "unavailable",
            "detail": "attested runtime paper cohort metadata is incomplete",
        }
    try:
        result = paper_trade_journal_reconcile.reconcile_paper_trade_journal(
            trade_log_root=trade_log_root,
            paper_db_path=database_path,
            repo_root=home,
            runtime_paper_cohort_id=cohort_id,
            runtime_paper_cohort_kind=cohort_kind,
        )
    except Exception as exc:  # Diagnostic boundary: botcheck must keep reporting.
        return {
            "status": "unavailable",
            "detail": f"{exc.__class__.__name__}: {exc}",
        }
    return {"status": "available", "result": result}


def print_runtime_paper_trade_journal_parity(summary: Mapping[str, object]) -> None:
    """Print one scoped parity heartbeat plus an explicit trust alarm when needed."""

    result = summary.get("result")
    if (
        summary.get("status") != "available"
        or not isinstance(result, paper_trade_journal_reconcile.PaperTradeJournalParityResult)
    ):
        print(
            "paper_trade_journal_parity: unavailable "
            f"detail={summary.get('detail', 'result missing')}"
        )
        print("WARNING: current canonical paper-trade journal parity is unavailable")
        return

    print(paper_trade_journal_reconcile.format_botcheck_parity_line(result))
    if result.verdict != "OK":
        level = "ALARM" if result.verdict.startswith("ALARM_") else "WARNING"
        print(
            f"{level}: current canonical paper-trade journal/DB parity is not trustworthy; "
            "reconcile before relying on paper-trade counts or profit attribution"
        )


def print_live_submission_hold_section(stats: LiveSubmissionHoldStats) -> None:
    print("=== Live submission reservations ===")
    print(f"State file : {stats.path}")
    if stats.status == "active":
        print(
            "WARNING    : active ticker reservations="
            f"{','.join(stats.held_tickers)}; reconciliation required"
        )
    elif stats.status == "unavailable":
        print(
            "WARNING    : reservation state unavailable or incomplete; "
            "reconciliation required"
        )
    else:
        print("Result     : no active live submission reservations")
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


def _default_live_submission_hold_path(repo_root: Path) -> Path:
    output_root = Path(
        os.environ.get("KALSHI_OUTPUT_ROOT")
        or os.environ.get("KALSHI_LOG_ROOT")
        or repo_root / "logs"
    ).expanduser()
    return output_root / "state" / "live_submission" / LIVE_SUBMISSION_HOLD_FILENAME


def _default_runtime_paper_cohort_attestation_path(repo_root: Path) -> Path:
    output_root = Path(
        os.environ.get("KALSHI_OUTPUT_ROOT")
        or os.environ.get("KALSHI_LOG_ROOT")
        or repo_root / "logs"
    ).expanduser()
    return output_root / "state" / "runtime_paper_cohort_attestation.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = _default_repo_root()
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument("--label", default=os.environ.get("KALSHI_LAUNCHD_LABEL", "com.jake.kalshi-bot"))
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("KALSHI_PYTHON", default_home / ".venv/bin/python")))
    parser.add_argument("--main", type=Path, default=Path(os.environ.get("KALSHI_MAIN", default_home / "main.py")))
    parser.add_argument("--log", type=Path, default=Path(os.environ.get("KALSHI_APP_LOG", default_home / "logs/app/bot.log")))
    parser.add_argument("--trades-log", type=Path, default=default_home / "logs/trades")
    parser.add_argument(
        "--live-submission-hold",
        type=Path,
        default=_default_live_submission_hold_path(default_home),
    )
    parser.add_argument(
        "--runtime-paper-cohort-attestation",
        type=Path,
        default=_default_runtime_paper_cohort_attestation_path(default_home),
    )
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
    pending_paper_cohorts = summarize_pending_paper_cohorts(args.home / "data")
    runtime_paper_cohort_attestation = summarize_runtime_paper_cohort_attestation(
        args.home / "data",
        args.runtime_paper_cohort_attestation,
        rows=rows,
        launchd_pid=wrapper_pid,
        main_path=args.main,
        now=now,
        now_epoch=now_epoch,
    )
    runtime_paper_trade_journal_parity = summarize_runtime_paper_trade_journal_parity(
        args.home,
        args.trades_log,
        runtime_paper_cohort_attestation,
    )
    live_submission_holds = summarize_live_submission_holds(args.live_submission_hold)
    research_dossiers = summarize_research_dossiers(default_home, now=now)

    print_launchd(args.label, launchd_output, wrapper_pid)
    current_proc = print_bot_section(bots, now_epoch=now_epoch, now=now)
    print_caffeinate_section(caffeinates, now_epoch=now_epoch)
    print_last_boot(args.log, sessions, now, current_proc=current_proc, now_epoch=now_epoch)
    print_signal_flow_section(signal_flow, now=now)
    print_pending_paper_cohort_section(pending_paper_cohorts)
    print_runtime_paper_cohort_attestation_section(runtime_paper_cohort_attestation)
    print_runtime_paper_trade_journal_parity(runtime_paper_trade_journal_parity)
    print_live_submission_hold_section(live_submission_holds)
    print_research_gate_section(
        default_home,
        signal_flow,
        now=now,
        dossier_stats=research_dossiers,
        current_proc=current_proc,
    )
    print_weather_shadow_status(args.home, current_proc=current_proc)
    print_capital_guard_shadow_status(args.home, current_proc=current_proc)
    print_g7_skip_evidence_status(args.home, current_proc=current_proc)
    print_kalshi_fix_settlement_ingress_status(args.home, current_proc=current_proc)
    print_canonical_settlement_status(
        args.home,
        runtime_paper_cohort_attestation=runtime_paper_cohort_attestation,
    )
    print_kalshi_drift_section(now=now)
    print_history(sessions=sessions, current_proc=current_proc, now=now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
