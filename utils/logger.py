"""
Logging setup for the Kalshi bot.

Provides:
  - get_logger(name)          -- colored console + rotating file logger
  - rotate_logs()             -- force a safe rollover of app log files at runtime
  - emit_startup_banner(...)  -- write context line at startup and after each
                                 midnight rotation so every archived file is
                                 self-describing
  - trade_log                 -- structured JSON logger for paper/live records

File layout (logs/):
  app/bot.log                  -- active DEBUG+ log; rotated to bot.log.YYYY-MM-DD
  app/errors.log               -- active WARNING+ log; same rotation scheme
  app/bot.log.YYYY-MM-DD       -- 90-day archive
  app/errors.log.YYYY-MM-DD
  trades/live/trades.jsonl     -- active structured trade records
  trades/archive/              -- day-partitioned historical trade records
  trades/trades.jsonl          -- legacy monolithic file retained temporarily
                                  for validation / rollback confidence
  reports/                     -- report_YYYYMMDD.txt, analysis_*.txt

Architecture:
  _app_fh and _err_fh are module-level singletons.  get_logger() creates one
  console handler per named logger and attaches the shared file handlers --
  this ensures exactly one write per record regardless of how many loggers are
  active, and exactly one rotation attempt at midnight.
"""

import asyncio
import dataclasses
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar

import colorlog

from utils.output_paths import (
    HEALTH_REPORTS_DIR,
    PERFORMANCE_REPORTS_DIR,
    RAW_APP_DIR,
    RAW_TRADES_ARCHIVE_DIR,
    RAW_TRADES_DIR,
    RAW_TRADES_LIVE_DIR,
    RAW_TRADES_SHADOW_DIR,
)
from analysis.feedback_counterfactual import FeedbackDecisionRecord
from utils.log_records import SignalAnalysisDetail
from utils.lifecycle import strict_optional_bool


_RESEARCH_PROVIDER_ERROR_ATTRIBUTIONS = frozenset(
    {"timeout", "generic_search_unavailable", "provider_exception"}
)
_RESEARCH_GENERIC_SEARCH_CIRCUIT_STATES = frozenset(
    {"closed", "open", "half_open"}
)
_RESEARCH_GENERIC_SEARCH_FAILURE_CLASS_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.]*(?::(?:[A-Za-z_][A-Za-z0-9_.]*|\d{3}))?"
)


EVIDENCE_INGESTION_REQUIRED_FIELDS: tuple[str, ...] = (
    "market_ticker",
    "evidence_id",
    "source_class",
    "is_duplicate",
    "correlation_discount_applied",
    "update_type",
    "dossier_version_before",
    "dossier_version_after",
)

DOSSIER_UPDATE_REQUIRED_FIELDS: tuple[str, ...] = (
    "market_ticker",
    "dossier_version",
    "prior_estimate",
    "new_estimate",
    "update_delta",
    "confidence_before",
    "confidence_after",
    "evidence_ids_contributing",
    "llm_called",
    "drift_suspect",
    "in_recovery",
)

STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS: tuple[str, ...] = (
    "market_ticker",
    "prior_estimate",
    "new_estimate",
    "input_sources",
    "llm_called",
    "token_count",
)

BLEND_DECISION_REQUIRED_FIELDS: tuple[str, ...] = (
    "market_ticker",
    "fast_lane_p",
    "fast_lane_confidence",
    "accumulation_p",
    "accumulation_confidence",
    "structural_p",
    "structural_confidence",
    "regime_weights",
    "regime_confidence",
    "blended_p",
    "blended_confidence",
    "disagreement_score",
    "blend_mode",
    "trade_considered",
    "trade_blocked_reason",
    "evidence_ids_contributing",
)


def _promote_lifecycle_context(
    record: dict[str, Any],
    *,
    lifecycle_id: object = None,
    settlement_source_match: object = None,
    signal_meta: dict[str, Any] | None = None,
) -> None:
    """Promote only the explicit lifecycle transport keys to record scope."""
    meta = signal_meta if isinstance(signal_meta, dict) else {}
    raw_lifecycle_id = lifecycle_id or meta.get("lifecycle_id")
    if not isinstance(raw_lifecycle_id, str) or not raw_lifecycle_id.strip():
        return
    record["lifecycle_id"] = raw_lifecycle_id.strip()
    raw_settlement_match = (
        settlement_source_match if isinstance(settlement_source_match, bool) else meta.get("settlement_source_match")
    )
    record["settlement_source_match"] = strict_optional_bool(raw_settlement_match)


CALIBRATION_CHECK_REQUIRED_FIELDS: tuple[str, ...] = (
    "market_ticker",
    "lane",
    "lane_estimate",
    "final_resolution",
    "error",
)

# ── Subdirectory layout ───────────────────────────────────────────────────────
_LOG_APP_DIR = RAW_APP_DIR
_LOG_TRADES_DIR = RAW_TRADES_DIR
_LOG_TRADES_LIVE_DIR = RAW_TRADES_LIVE_DIR
_LOG_TRADES_SHADOW_DIR = RAW_TRADES_SHADOW_DIR
_LOG_REPORTS_DIR = PERFORMANCE_REPORTS_DIR
_LOG_HEALTH_REPORTS_DIR = HEALTH_REPORTS_DIR
_LOG_ARCHIVE_DIR = RAW_TRADES_ARCHIVE_DIR

for _d in (
    _LOG_APP_DIR,
    _LOG_TRADES_DIR,
    _LOG_TRADES_LIVE_DIR,
    _LOG_TRADES_SHADOW_DIR,
    _LOG_REPORTS_DIR,
    _LOG_HEALTH_REPORTS_DIR,
    _LOG_ARCHIVE_DIR,
):
    _d.mkdir(parents=True, exist_ok=True)

# ── Log file paths ────────────────────────────────────────────────────────────
APP_LOG_FILE = _LOG_APP_DIR / "bot.log"
ERROR_LOG_FILE = _LOG_APP_DIR / "errors.log"  # WARNING+ only -- quick triage
LEGACY_TRADE_LOG_FILE = _LOG_TRADES_DIR / "trades.jsonl"  # temporary legacy read path during cutover validation
TRADE_LOG_FILE = _LOG_TRADES_LIVE_DIR / "trades.jsonl"  # preferred active newline-delimited JSON target
SHADOW_ASSIGNMENT_LOG_FILE = _LOG_TRADES_SHADOW_DIR / "fresh_pass_assignment_shadow.jsonl"
LOG_REPORTS_DIR = _LOG_REPORTS_DIR  # for paper_trader report output

# ── Formatters ────────────────────────────────────────────────────────────────
_COLOR_FORMAT = "%(log_color)s%(asctime)s UTC %(levelname)-8s%(reset)s %(cyan)s%(name)-20s%(reset)s %(message)s"
_FILE_FORMAT = "%(asctime)s UTC %(levelname)-8s %(name)-20s %(message)s"

_COLOR_MAP = {
    "DEBUG": "white",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold_red",
}


def _utc_formatter(fmt: str) -> logging.Formatter:
    formatter = logging.Formatter(fmt)
    formatter.converter = time.gmtime
    return formatter


class _DailyRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """TimedRotatingFileHandler using copy+truncate instead of rename.

    Overrides rotate() so the base log path never moves. This lets any process
    (VS Code, tail -f) that has the file open continue writing without
    interruption across midnight rotation.

    On Windows this is required -- os.rename() raises WinError 32 when
    another process holds the file open. On Mac/Linux it is harmless: slightly
    less efficient than an atomic rename but functionally identical.

    Rotated backups: bot.log.YYYY-MM-DD (UTC date), 90-day retention.

    Banner: a one-line context string (version/env/model) written at the top
    of every new file so any rotated archive is self-describing without
    needing to search back to the beginning of the bot run.

    Peer handlers: other _DailyRotatingFileHandler instances that should be
    rotated whenever this handler rotates. This ensures handlers with high
    log-level filters (e.g. WARNING-only) still rotate at midnight even if
    no qualifying messages arrive around that time.

    Python 3.14 compatibility: doRollover() in Python 3.14+ returns early
    without updating rolloverAt when the destination archive already exists
    (e.g. after a manual --rotate-logs call). _rollover_self() advances
    rolloverAt after every super().doRollover() call to prevent shouldRollover()
    from returning True forever and creating a hot loop on every emit().
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._banner: str = ""
        self._peers: list["_DailyRotatingFileHandler"] = []

    def _write_banner(self) -> None:
        if self._banner and self.stream:
            try:
                self.stream.write(self._banner + "\n")
                self.stream.flush()
            except Exception:
                pass

    def _rollover_self(self) -> None:
        super().doRollover()
        # Python 3.14+: doRollover() returns early without updating rolloverAt
        # when the destination archive already exists (e.g. after a prior
        # force_rollover() call). Advance rolloverAt here so shouldRollover()
        # does not return True forever, which would create a hot loop on every
        # emit() call.
        now = int(time.time())
        if self.rolloverAt <= now:
            self.rolloverAt = self.computeRollover(now)
        self._write_banner()

    def rotate(self, source: str, dest: str) -> None:
        import sys as _sys

        try:
            shutil.copy2(source, dest)
        except Exception as exc:
            print(
                f"[logger] WARN: log rotation copy failed {source!r} -> {dest!r}: {exc}",
                file=_sys.stderr,
            )
            return
        try:
            with open(source, "w", encoding="utf-8"):
                pass  # truncate in place -- path and inode unchanged
        except Exception as exc:
            print(
                f"[logger] WARN: log rotation truncate failed {source!r}: {exc}",
                file=_sys.stderr,
            )

    def doRollover(self) -> None:
        self._rollover_self()
        # Nudge peer handlers that may not have received any messages since
        # midnight (e.g. errors.log during a quiet period).
        for peer in self._peers:
            if peer.shouldRollover(logging.LogRecord("", 0, "", 0, None, None, None)):
                peer._rollover_self()

    def force_rollover(self) -> None:
        """Rotate this handler and its peers immediately."""
        self._rollover_self()
        for peer in self._peers:
            peer._rollover_self()


# ── Shared file handler singletons ────────────────────────────────────────────
# Created once on first get_logger() call; reused by every subsequent logger.
# This ensures exactly one rotation attempt at midnight and one write per record.
_app_fh: Optional[_DailyRotatingFileHandler] = None
_err_fh: Optional[_DailyRotatingFileHandler] = None


def _first_log_timestamp(path: Path) -> Optional[float]:
    """Return the UTC timestamp of the first non-comment log line in path, or None.

    Used as a fallback when mtime alone cannot determine whether the file belongs
    to a prior period (e.g. the bot ran continuously through midnight and the file
    was written after midnight, making its mtime appear current-period).
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # Expected prefix: "YYYY-MM-DD HH:MM:SS,mmm UTC ..."
                try:
                    dt = datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f")
                    return dt.replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    return None  # unrecognised format; stop here
    except OSError:
        pass
    return None


def _maybe_rotate_stale(handler: _DailyRotatingFileHandler) -> None:
    """Rotate the active log file on startup if it contains content from a prior period.

    Two-pass check:
      1. mtime — fast; catches the common start/stop-within-business-hours pattern.
      2. first-line timestamp — catches the run-through-midnight failure pattern,
         where mtime appears current-period because the bot wrote after midnight
         but emit-triggered rotation never fired.
    """
    path = Path(handler.baseFilename)
    if not path.exists() or path.stat().st_size == 0:
        return
    period_start = handler.rolloverAt - handler.interval
    if path.stat().st_mtime < period_start:
        handler.doRollover()
        return
    # Content-based fallback: check the timestamp on the first log line.
    first_ts = _first_log_timestamp(path)
    if first_ts is not None and first_ts < period_start:
        handler.doRollover()


def _ensure_file_handlers() -> tuple[_DailyRotatingFileHandler, _DailyRotatingFileHandler]:
    global _app_fh, _err_fh
    if _app_fh is None:
        _app_fh = _DailyRotatingFileHandler(APP_LOG_FILE, when="midnight", backupCount=90, encoding="utf-8", utc=True)
        _app_fh.setLevel(logging.DEBUG)
        _app_fh.setFormatter(_utc_formatter(_FILE_FORMAT))
        _maybe_rotate_stale(_app_fh)
    if _err_fh is None:
        _err_fh = _DailyRotatingFileHandler(ERROR_LOG_FILE, when="midnight", backupCount=90, encoding="utf-8", utc=True)
        _err_fh.setLevel(logging.WARNING)
        _err_fh.setFormatter(_utc_formatter(_FILE_FORMAT))
        # bot.log rotates on every DEBUG+ message at midnight; nudge errors.log
        # along with it so it rotates even during quiet (no-WARNING) periods.
        _app_fh._peers.append(_err_fh)
        _maybe_rotate_stale(_err_fh)
    return _app_fh, _err_fh


def emit_startup_banner(version: str, model: str, env: str) -> None:
    """Write a one-line context banner to the active log files.

    Call once near the top of main.py run() after logging is set up.
    The banner is stored in each handler and re-emitted automatically
    after every midnight rotation, so every archived log file begins
    with a self-describing header line.

    Example output at top of bot.log (and errors.log):
        # ===== v0.6.7 | env=demo | model=qwen2.5:7b | py=3.14.0 =====
    """
    import sys

    banner = f"# ===== v{version} | env={env} | model={model} | py={sys.version.split()[0]} ====="
    fh, eh = _ensure_file_handlers()
    for handler in (fh, eh):
        handler._banner = banner
        handler._write_banner()


def rotate_logs() -> list[Path]:
    """Force a safe rollover of the shared app log handlers.

    This is safe to call while the bot is running: the timed rotating handlers
    manage their own file handles, archive the current contents, truncate the
    active file in place, and keep logging to the same active path.
    """
    fh, eh = _ensure_file_handlers()
    fh.force_rollover()
    return [Path(fh.baseFilename), Path(eh.baseFilename)]


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger with console output and shared file output."""
    fh, eh = _ensure_file_handlers()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured
    logger.setLevel(level)

    # Console handler -- one per named logger (colored, INFO+)
    ch = colorlog.StreamHandler()
    ch.setLevel(logging.INFO)
    ch_formatter = colorlog.ColoredFormatter(_COLOR_FORMAT, log_colors=_COLOR_MAP)
    ch_formatter.converter = time.gmtime
    ch.setFormatter(ch_formatter)

    # Shared file handlers -- same objects attached to every logger
    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.addHandler(eh)
    return logger


# ── Structured trade logger ───────────────────────────────────────────────────

_T = TypeVar("_T")


async def write_trade_log_async(
    writer: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run a durable structured-log write without blocking the event loop.

    TradeLogStore intentionally fsyncs each JSONL event for audit durability.
    Async hot paths should await this helper instead of calling trade_log
    methods directly so the fsync happens in a worker thread while preserving
    caller-visible completion and exception semantics.
    """
    return await asyncio.to_thread(writer, *args, **kwargs)


def _parse_trade_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TradeLogStore:
    """Manages active trade-log writes and day-based archive rotation.

    Invariants:
      - `live/trades.jsonl` is the only active append target for in-order writes.
      - closed UTC days live under `archive/YYYY/MM/YYYY-MM-DD.jsonl`
      - older out-of-order records are appended to their archive partition rather
        than rotating the active live file backward in time
      - the live file is truncated only after archive data has been durably
        flushed, so a failed rotation cannot silently drop records
    """

    def __init__(
        self,
        root: Path = _LOG_TRADES_DIR,
        live_path: Path = TRADE_LOG_FILE,
        legacy_path: Path = LEGACY_TRADE_LOG_FILE,
    ) -> None:
        self._root = root
        self._live_path = live_path
        self._legacy_path = legacy_path
        self._archive_root = root / "archive"
        self._log = get_logger("trade_log_store")
        self._lock = threading.Lock()
        self._live_path.parent.mkdir(parents=True, exist_ok=True)
        self._archive_root.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            ts = _parse_trade_ts(record.get("ts"))
            if ts is None:
                ts = datetime.now(timezone.utc)
                record["ts"] = ts.isoformat()
            current_day = self._current_live_day()
            record_day = ts.date()

            if current_day is None or current_day == record_day:
                self._append_line(self._live_path, json.dumps(record) + "\n")
                return

            if record_day < current_day:
                self._log.warning(
                    "[TRADE_LOG] Out-of-order record for %s arrived while live file is %s; appending to archive",
                    record_day.isoformat(),
                    current_day.isoformat(),
                )
                self._append_line(self._archive_path_for_day(record_day), json.dumps(record) + "\n")
                return

            self._rotate_live_to_archive(current_day)
            self._append_line(self._live_path, json.dumps(record) + "\n")

    def _append_line(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _archive_path_for_day(self, day: datetime.date) -> Path:
        return self._archive_root / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.jsonl"

    def _rotate_live_to_archive(self, current_day: datetime.date) -> None:
        archive_path = self._archive_path_for_day(current_day)
        if archive_path.exists():
            self._append_file(self._live_path, archive_path)
            self._truncate_live()
            return

        archive_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(self._live_path, archive_path)
        except PermissionError:
            if sys.platform != "win32":
                # On macOS/Linux PermissionError is a real access-control problem,
                # not a transient file-lock; raise so the caller sees it clearly.
                raise
            # Windows: file may be transiently locked even after the write handle
            # is closed; copy+truncate is the safe fallback.
            self._append_file(self._live_path, archive_path)
            self._truncate_live()

    def _current_live_day(self) -> datetime.date | None:
        return self._infer_day_from_file(self._live_path)

    def _append_file(self, source: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "a", encoding="utf-8", newline="\n") as dst:
            with open(source, "r", encoding="utf-8") as src:
                shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())

    def _truncate_live(self) -> None:
        with open(self._live_path, "w", encoding="utf-8", newline="\n"):
            pass

    def _infer_day_from_file(self, path: Path) -> datetime.date | None:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    ts = _parse_trade_ts(record.get("ts"))
                    if ts is not None:
                        return ts.date()
        except OSError:
            return None
        return None


class TradeLogger:
    """Appends structured JSON records to the preferred live trade log for easy analysis."""

    def __init__(self, path: Path = TRADE_LOG_FILE):
        self._path = path
        self._store = TradeLogStore(live_path=path)
        self._log = get_logger("trade_logger")
        self._runtime_paper_context: dict[str, str] = {}

    def bind_runtime_context(self, *, cohort_id: str, cohort_kind: str) -> None:
        """Attach immutable runtime lineage to future primary durable records."""
        runtime_paper_context = {
            "runtime_paper_cohort_id": cohort_id,
            "runtime_paper_cohort_kind": cohort_kind,
        }
        if not self._runtime_paper_context:
            self._runtime_paper_context = runtime_paper_context
            return
        if self._runtime_paper_context != runtime_paper_context:
            existing_id = self._runtime_paper_context["runtime_paper_cohort_id"]
            existing_kind = self._runtime_paper_context["runtime_paper_cohort_kind"]
            raise RuntimeError(
                "runtime paper cohort is already bound to "
                f"({existing_id!r}, {existing_kind!r}); refusing to rebind to "
                f"({cohort_id!r}, {cohort_kind!r})"
            )

    def _write(self, record: dict[str, Any]) -> None:
        record = {**record, **getattr(self, "_runtime_paper_context", {})}
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._store.append(record)

    def log_signal(
        self,
        *,
        source: str,
        headline: str,
        url: str,
        signal_strength: float,
        keywords_matched: list[str],
    ) -> None:
        self._write(
            {
                "type": "SIGNAL",
                "source": source,
                "headline": headline,
                "url": url,
                "signal_strength": round(signal_strength, 4),
                "keywords_matched": keywords_matched,
            }
        )

    def log_research_prewarm_result(
        self,
        *,
        ticker: str,
        status: str,
        attempted: bool,
        query_count: int = 0,
        evidence_count: int = 0,
        skip_reason: str | None = None,
        error: str | None = None,
        research_run_id: str | None = None,
        research_contract_fingerprint: str | None = None,
        research_persisted: bool | None = None,
        research_persistence_error: str | None = None,
        research_direct_fetch_failures: list[str] | None = None,
        research_direct_fetch_failure_count: int | None = None,
        research_timeout_stage: str | None = None,
        research_provider_error_count: int = 0,
        research_provider_error_attributions: list[str] | None = None,
        research_generic_search_circuit_state: str | None = None,
        research_generic_search_failure_classes: list[str] | None = None,
        research_generic_search_attempt_delta: int = 0,
        research_generic_search_blocked_call_delta: int = 0,
    ) -> None:
        record: dict[str, Any] = {
            "type": "RESEARCH_PREWARM_RESULT",
            "ticker": ticker,
            "research_prewarm": True,
            "research_attempted": bool(attempted),
            "research_status": status,
            "research_queries_count": int(query_count),
            "research_hit_count": int(evidence_count),
        }
        if skip_reason:
            record["research_skip_reason"] = skip_reason
        if error:
            record["research_error"] = error
        if research_run_id:
            record["research_run_id"] = research_run_id
        if research_contract_fingerprint:
            record["research_contract_fingerprint"] = research_contract_fingerprint
        if research_persisted is not None:
            record["research_persisted"] = bool(research_persisted)
        if research_persistence_error:
            record["research_persistence_error"] = research_persistence_error
        if research_direct_fetch_failures:
            record["research_direct_fetch_failures"] = list(research_direct_fetch_failures)
        if research_direct_fetch_failure_count is not None:
            record["research_direct_fetch_failure_count"] = int(research_direct_fetch_failure_count)
        if research_timeout_stage:
            record["research_timeout_stage"] = research_timeout_stage
        record["research_provider_error_count"] = max(
            0,
            int(research_provider_error_count),
        )
        record["research_provider_error_attributions"] = [
            attribution
            for attribution in (research_provider_error_attributions or [])
            if attribution in _RESEARCH_PROVIDER_ERROR_ATTRIBUTIONS
        ]
        if (
            research_generic_search_circuit_state
            in _RESEARCH_GENERIC_SEARCH_CIRCUIT_STATES
        ):
            record["research_generic_search_circuit_state"] = (
                research_generic_search_circuit_state
            )
        if research_generic_search_failure_classes:
            record["research_generic_search_failure_classes"] = [
                failure_class
                for failure_class in research_generic_search_failure_classes
                if isinstance(failure_class, str)
                and _RESEARCH_GENERIC_SEARCH_FAILURE_CLASS_RE.fullmatch(failure_class)
            ]
        if research_generic_search_attempt_delta:
            record["research_generic_search_attempt_delta"] = max(
                0,
                int(research_generic_search_attempt_delta),
            )
        if research_generic_search_blocked_call_delta:
            record["research_generic_search_blocked_call_delta"] = max(
                0,
                int(research_generic_search_blocked_call_delta),
            )
        self._write(record)

    def log_opportunity(
        self,
        *,
        ticker: str,
        market_title: str,
        entry_price_cents: float,
        estimated_probability: float,
        edge: float,
        kelly_fraction: float,
        kelly_dollars: float,
        capped_dollars: float,
        side: str,  # "yes" or "no"
        reasoning: str,
        source: str | None = None,
        headline: str | None = None,
        method: str | None = None,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
        venue: str | None = None,
        keywords: list[str] | None = None,
        source_class: str | None = None,
        retrieval_mode: str | None = None,
        source_hint_domain: str | None = None,
        source_hint_query: str | None = None,
        evidence_id: str | None = None,
        settlement_source_match: bool | None = None,
        research_status: str | None = None,
        research_run_id: str | None = None,
        signal_type: str | None = None,
        lifecycle_id: str | None = None,
    ) -> None:
        record = {
            "type": "OPPORTUNITY",
            "ticker": ticker,
            "market_title": market_title,
            "entry_price_cents": round(entry_price_cents, 4),
            "estimated_probability": round(estimated_probability, 4),
            "edge": round(edge, 4),
            "kelly_fraction": round(kelly_fraction, 4),
            "kelly_dollars": round(kelly_dollars, 2),
            "capped_dollars": round(capped_dollars, 2),
            "side": side,
            "reasoning": reasoning,
        }
        if source is not None:
            record["source"] = source
        if headline is not None:
            record["headline"] = headline
        if method is not None:
            record["method"] = method
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        if venue is not None:
            record["venue"] = venue
        if keywords is not None:
            record["keywords"] = list(keywords)
            record["keyword_count"] = len(keywords)
        if source_class is not None:
            record["source_class"] = source_class
        if retrieval_mode:
            record["retrieval_mode"] = retrieval_mode
        if source_hint_domain:
            record["source_hint_domain"] = source_hint_domain
        if source_hint_query:
            record["source_hint_query"] = source_hint_query
        if evidence_id:
            record["evidence_id"] = evidence_id
        if settlement_source_match is not None:
            record["settlement_source_match"] = strict_optional_bool(settlement_source_match)
        if research_status is not None:
            record["research_status"] = research_status
        if research_run_id is not None:
            record["research_run_id"] = research_run_id
        if signal_type is not None:
            record["signal_type"] = signal_type
        _promote_lifecycle_context(
            record,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )
        self._write(record)

    def log_paper_trade(
        self,
        *,
        trade_id: str,
        ticker: str,
        market_title: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        estimated_probability: float,
        entry_price_cents: float,
        edge: float,
        kelly_dollars: float,
        reasoning: str,
        signal_headline: str,
        signal_source: str,
        keywords_matched: list[str] | None = None,
        signal_meta: dict[str, Any] | None = None,
        bankroll_delta_dollars: float | None = None,
        venue: str = "kalshi",
    ) -> None:
        record = {
            "type": "PAPER_TRADE",
            "trade_id": trade_id,
            "ticker": ticker,
            "venue": venue,
            "market_title": market_title,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "estimated_probability": round(estimated_probability, 4),
            "entry_price_cents": round(entry_price_cents, 4),
            "edge": round(edge, 4),
            "kelly_dollars": round(kelly_dollars, 2),
            "reasoning": reasoning,
            "signal_headline": signal_headline,
            "signal_source": signal_source,
        }
        if keywords_matched is not None:
            record["keywords_matched"] = keywords_matched
        if signal_meta:
            record["signal_meta"] = signal_meta
        _promote_lifecycle_context(record, signal_meta=signal_meta)
        if bankroll_delta_dollars is not None:
            record["bankroll_delta_dollars"] = round(bankroll_delta_dollars, 2)
        self._write(record)

    def log_paper_resolution(
        self,
        *,
        trade_id: str,
        ticker: str,
        resolved_yes: bool | None,
        pnl_dollars: float,
        bankroll_delta_dollars: float | None = None,
        venue: str | None = None,
        terminal_state: str | None = None,
        outbox_id: str | None = None,
        ts: str | None = None,
    ) -> None:
        record = {
            "type": "PAPER_RESOLUTION",
            "trade_id": trade_id,
            "ticker": ticker,
            "resolved_yes": resolved_yes,
            "pnl_dollars": round(pnl_dollars, 2),
        }
        if venue:
            record["venue"] = venue
        if terminal_state is not None:
            record["terminal_state"] = terminal_state
        if outbox_id is not None:
            record["outbox_id"] = outbox_id
        if ts is not None:
            record["ts"] = ts
        if bankroll_delta_dollars is not None:
            record["bankroll_delta_dollars"] = round(bankroll_delta_dollars, 2)
        self._write(record)

    def log_live_submission_intent(
        self,
        *,
        submission_id: str,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        venue: str | None = None,
        signal_meta: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "type": "LIVE_SUBMISSION_INTENT",
            "submission_id": submission_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
        }
        if venue:
            record["venue"] = venue
        if signal_meta:
            record["signal_meta"] = signal_meta
        _promote_lifecycle_context(record, signal_meta=signal_meta)
        self._write(record)

    def log_live_submission_unknown(
        self,
        *,
        submission_id: str,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        outcome: str,
        venue_order_id: str | None = None,
        venue: str | None = None,
        signal_meta: dict[str, Any] | None = None,
    ) -> None:
        if outcome not in {
            "exception",
            "error_result",
            "cancelled",
            "live_order_journal_failure",
            "unverified_receipt",
        }:
            raise ValueError("unsupported live submission outcome")
        record = {
            "type": "LIVE_SUBMISSION_UNKNOWN",
            "submission_id": submission_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "outcome": outcome,
        }
        if venue_order_id is not None:
            if outcome != "live_order_journal_failure":
                raise ValueError("venue order ID requires a journal failure outcome")
            if not isinstance(venue_order_id, str) or not venue_order_id.strip():
                raise ValueError("venue order ID must be a non-empty string")
            record["venue_order_id"] = venue_order_id.strip()
        if venue:
            record["venue"] = venue
        if signal_meta:
            record["signal_meta"] = signal_meta
        _promote_lifecycle_context(record, signal_meta=signal_meta)
        self._write(record)

    def log_live_order(
        self,
        *,
        order_id: str,
        ticker: str,
        side: str,
        contracts: int,
        price_cents: int,
        cost_dollars: float,
        status: str,
        venue: str | None = None,
        model_probability: float | None = None,
        market_price: float | None = None,
        edge: float | None = None,
        min_edge_threshold: float | None = None,
        signal_meta: dict[str, Any] | None = None,
        submission_id: str | None = None,
    ) -> None:
        record = {
            "type": "LIVE_ORDER",
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost_dollars": round(cost_dollars, 2),
            "status": status,
        }
        if venue:
            record["venue"] = venue
        if submission_id is not None:
            record["submission_id"] = submission_id
        if model_probability is not None:
            record["model_probability"] = round(model_probability, 4)
        if market_price is not None:
            record["market_price"] = round(market_price, 4)
        if edge is not None:
            record["edge"] = round(edge, 4)
        if min_edge_threshold is not None:
            record["min_edge_threshold"] = round(min_edge_threshold, 4)
        if signal_meta:
            record["signal_meta"] = signal_meta
        _promote_lifecycle_context(record, signal_meta=signal_meta)
        self._write(record)

    def log_skipped(
        self,
        *,
        reason: str,
        skip_category: str | None = None,
        ticker: str | None = None,
        headline: str | None = None,
        source: str | None = None,
        method: str | None = None,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
        model_probability: float | None = None,
        market_price: float | None = None,
        edge: float | None = None,
        min_edge_threshold: float | None = None,
        venue: str | None = None,
        side: str | None = None,
        signal_meta: dict[str, Any] | None = None,
        recency_score: float | None = None,
        recency_threshold: float | None = None,
        recency_distance: float | None = None,
        g7_mark_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Emit a SKIPPED trade-log record.

        Two emission origins share this method:

        - `trading/executor.py:152` — emits when the executor's `_validate()`
          rejects a candidate (cooldown, opposing-position guard, capped_dollars,
          insufficient balance, etc.). Reason values come from the executor's
          internal skip-reason vocabulary (e.g.
          ``"edge +0.0000 below min_edge 0.02"``, ``"paper cooldown active"``).
          `model_probability` and `edge` are the executor's pre-trade values
          (= `analysis.estimated_probability` and `analysis.edge`, the fast-lane
          raw values).

        - `tasks/blend_task.py` — emits when the blender or readiness gate
          produces a non-None `trade_blocked_reason` and the candidate is
          dropped before reaching the executor. Reason values are the G1-G6
          readiness-gate enum (``"G1_blended_confidence"`` ...
          ``"G6_recency_score"``) or a blender-side reason. `model_probability`
          is the *post-blend* value (`blend_result.blended_p`), and `edge` is
          the post-blend edge (`blended_p - market_yes_price/100.0`). The
          headline is truncated to 80 chars to match the executor convention.

        Per PROFIT-OBS-003: BlendTask-emitted SKIPPED records were added on
        2026-05-09 to close the OPPORTUNITY -> SKIPPED accounting gap. Before
        this change, ~92% of OPPORTUNITY events exited silently with no
        matching SKIPPED record because the BlendTask blocked-reason path
        bypassed this emitter entirely.
        """
        record = {
            "type": "SKIPPED",
            "reason": reason,
            "ticker": ticker,
            "headline": headline,
        }
        if skip_category is not None:
            record["skip_category"] = skip_category
        if source is not None:
            record["source"] = source
        if method is not None:
            record["method"] = method
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        if model_probability is not None:
            record["model_probability"] = round(model_probability, 4)
        if market_price is not None:
            record["market_price"] = round(market_price, 4)
        if model_probability is not None and market_price is not None:
            signed_diff = model_probability - market_price
            record["signed_diff"] = round(signed_diff, 4)
            record["absolute_diff"] = round(abs(signed_diff), 4)
        if edge is not None:
            record["edge"] = round(edge, 4)
        if min_edge_threshold is not None:
            record["min_edge_threshold"] = round(min_edge_threshold, 4)
        if venue:
            record["venue"] = venue
        if side:
            record["side"] = side
        if recency_score is not None:
            record["recency_score"] = round(float(recency_score), 4)
        if recency_threshold is not None:
            record["recency_threshold"] = round(float(recency_threshold), 4)
        if recency_distance is not None:
            record["recency_distance"] = round(float(recency_distance), 4)
        if signal_meta:
            record["signal_meta"] = signal_meta
        if g7_mark_snapshot is not None:
            record["g7_mark_snapshot"] = dict(g7_mark_snapshot)
        _promote_lifecycle_context(record, signal_meta=signal_meta)
        self._write(record)

    def log_analysis_rejected(
        self,
        *,
        reason: str,
        ticker: str,
        source: str,
        headline: str,
        match_score: float,
        rejection_category: str | None = None,
        signal_branch: str | None = None,
        method: str | None = None,
        llm_direction: str | None = None,
        llm_magnitude: str | None = None,
        llm_confidence: float | None = None,
        keywords: list[str] | None = None,
        age_seconds: float | None = None,
        threshold_seconds: int | None = None,
        retrieval_mode: str | None = None,
        source_hint_domain: str | None = None,
        source_hint_query: str | None = None,
        source_class: str | None = None,
        rules_primary: str | None = None,
        rules_secondary: str | None = None,
        settlement_source_names: list[str] | None = None,
        settlement_source_urls: list[str] | None = None,
        contract_terms_url: str | None = None,
        research_attempted: bool | None = None,
        research_status: str | None = None,
        research_queries: list[str] | None = None,
        research_sources_consulted: list[str] | None = None,
        research_hit_count: int | None = None,
        research_settlement_source_hits: int | None = None,
        research_urls: list[str] | None = None,
        research_summary: str | None = None,
        research_model_direction: str | None = None,
        research_model_confidence: float | None = None,
        research_model_probability_yes: float | None = None,
        research_market_price: float | None = None,
        research_estimated_edge: float | None = None,
        research_decision_grade_reasons: list[str] | None = None,
        research_open_questions: list[str] | None = None,
        research_counterclaims: list[str] | None = None,
        research_skip_reason: str | None = None,
        research_started_ts: str | None = None,
        research_completed_ts: str | None = None,
        research_duration_ms: float | None = None,
        research_min_published_at: str | None = None,
        research_max_published_at: str | None = None,
        research_min_retrieved_at: str | None = None,
        research_max_retrieved_at: str | None = None,
        research_run_id: str | None = None,
        research_contract_fingerprint: str | None = None,
        research_persisted: bool | None = None,
        research_persistence_error: str | None = None,
        research_direct_fetch_failures: list[str] | None = None,
        research_direct_fetch_failure_count: int | None = None,
        research_timeout_stage: str | None = None,
        research_provider_error_count: int | None = None,
        research_provider_error_attributions: list[str] | None = None,
        research_generic_search_circuit_state: str | None = None,
        research_generic_search_failure_classes: list[str] | None = None,
        research_generic_search_attempt_delta: int = 0,
        research_generic_search_blocked_call_delta: int = 0,
    ) -> None:
        record = {
            "type": "ANALYSIS_REJECTED",
            "reason": reason,
            "ticker": ticker,
            "source": source,
            "headline": headline,
            "match_score": round(match_score, 4),
        }
        if rejection_category is not None:
            record["rejection_category"] = rejection_category
        if signal_branch is not None:
            record["signal_branch"] = signal_branch
        if method is not None:
            record["method"] = method
        if llm_direction is not None:
            record["llm_direction"] = llm_direction
        if llm_magnitude is not None:
            record["llm_magnitude"] = llm_magnitude
        if llm_confidence is not None:
            record["llm_confidence"] = round(float(llm_confidence), 4)
        if keywords is not None:
            record["keywords"] = list(keywords)
            record["keyword_count"] = len(keywords)
        if age_seconds is not None:
            record["age_seconds"] = round(age_seconds, 2)
        if threshold_seconds is not None:
            record["threshold_seconds"] = int(threshold_seconds)
        if retrieval_mode:
            record["retrieval_mode"] = retrieval_mode
        if source_hint_domain:
            record["source_hint_domain"] = source_hint_domain
        if source_hint_query:
            record["source_hint_query"] = source_hint_query
        if source_class:
            record["source_class"] = source_class
        if rules_primary:
            record["rules_primary"] = rules_primary
        if rules_secondary:
            record["rules_secondary"] = rules_secondary
        if settlement_source_names:
            record["settlement_source_names"] = list(settlement_source_names)
        if settlement_source_urls:
            record["settlement_source_urls"] = list(settlement_source_urls)
        if contract_terms_url:
            record["contract_terms_url"] = contract_terms_url
        if research_attempted is not None:
            record["research_attempted"] = bool(research_attempted)
        if research_status:
            record["research_status"] = research_status
        if research_queries:
            record["research_queries"] = list(research_queries)
        if research_sources_consulted:
            record["research_sources_consulted"] = list(research_sources_consulted)
        if research_hit_count is not None:
            record["research_hit_count"] = int(research_hit_count)
        if research_settlement_source_hits is not None:
            record["research_settlement_source_hits"] = int(research_settlement_source_hits)
        if research_urls:
            record["research_urls"] = list(research_urls)
        if research_summary:
            record["research_summary"] = research_summary
        if research_model_direction:
            record["research_model_direction"] = research_model_direction
        if research_model_confidence is not None:
            record["research_model_confidence"] = round(float(research_model_confidence), 4)
        if research_model_probability_yes is not None:
            record["research_model_probability_yes"] = round(
                float(research_model_probability_yes),
                4,
            )
        if research_market_price is not None:
            record["research_market_price"] = round(float(research_market_price), 4)
        if research_estimated_edge is not None:
            record["research_estimated_edge"] = round(float(research_estimated_edge), 4)
        if research_decision_grade_reasons:
            record["research_decision_grade_reasons"] = list(research_decision_grade_reasons)
        if research_open_questions:
            record["research_open_questions"] = list(research_open_questions)
        if research_counterclaims:
            record["research_counterclaims"] = list(research_counterclaims)
        if research_skip_reason:
            record["research_skip_reason"] = research_skip_reason
        if research_started_ts:
            record["research_started_ts"] = research_started_ts
        if research_completed_ts:
            record["research_completed_ts"] = research_completed_ts
        if research_duration_ms is not None:
            record["research_duration_ms"] = round(float(research_duration_ms), 2)
        if research_min_published_at:
            record["research_min_published_at"] = research_min_published_at
        if research_max_published_at:
            record["research_max_published_at"] = research_max_published_at
        if research_min_retrieved_at:
            record["research_min_retrieved_at"] = research_min_retrieved_at
        if research_max_retrieved_at:
            record["research_max_retrieved_at"] = research_max_retrieved_at
        if research_run_id:
            record["research_run_id"] = research_run_id
        if research_contract_fingerprint:
            record["research_contract_fingerprint"] = research_contract_fingerprint
        if research_persisted is not None:
            record["research_persisted"] = bool(research_persisted)
        if research_persistence_error:
            record["research_persistence_error"] = research_persistence_error
        if research_direct_fetch_failures:
            record["research_direct_fetch_failures"] = list(research_direct_fetch_failures)
        if research_direct_fetch_failure_count is not None:
            record["research_direct_fetch_failure_count"] = int(research_direct_fetch_failure_count)
        if research_timeout_stage:
            record["research_timeout_stage"] = research_timeout_stage
        if research_provider_error_count is not None:
            record["research_provider_error_count"] = max(
                0,
                int(research_provider_error_count),
            )
        if research_provider_error_attributions is not None:
            record["research_provider_error_attributions"] = [
                attribution
                for attribution in research_provider_error_attributions
                if attribution in _RESEARCH_PROVIDER_ERROR_ATTRIBUTIONS
            ]
        if (
            research_generic_search_circuit_state
            in _RESEARCH_GENERIC_SEARCH_CIRCUIT_STATES
        ):
            record["research_generic_search_circuit_state"] = (
                research_generic_search_circuit_state
            )
        if research_generic_search_failure_classes:
            record["research_generic_search_failure_classes"] = [
                failure_class
                for failure_class in research_generic_search_failure_classes
                if isinstance(failure_class, str)
                and _RESEARCH_GENERIC_SEARCH_FAILURE_CLASS_RE.fullmatch(failure_class)
            ]
        if research_generic_search_attempt_delta:
            record["research_generic_search_attempt_delta"] = max(
                0,
                int(research_generic_search_attempt_delta),
            )
        if research_generic_search_blocked_call_delta:
            record["research_generic_search_blocked_call_delta"] = max(
                0,
                int(research_generic_search_blocked_call_delta),
            )
        self._write(record)

    def log_early_stale_drop(
        self,
        *,
        reason: str,
        source: str,
        headline: str,
        age_seconds: float | None = None,
        threshold_seconds: float | None = None,
    ) -> None:
        record = {
            "type": "EARLY_STALE_DROP",
            "reason": reason,
            "source": source,
            "headline": headline,
        }
        if age_seconds is not None:
            record["age_seconds"] = round(age_seconds, 2)
        if threshold_seconds is not None:
            record["threshold_seconds"] = round(threshold_seconds, 2)
        self._write(record)

    def log_early_fresh_pass(
        self,
        *,
        source: str,
        headline: str,
        age_seconds: float,
    ) -> None:
        self._write(
            {
                "type": "EARLY_FRESH_PASS",
                "source": source,
                "headline": headline,
                "age_seconds": round(age_seconds, 2),
            }
        )

    # Fields whose value is rounded to 4 decimals when included in the record.
    # Required-float fields are always rounded; optional-float fields are
    # rounded only when not None.
    _SAD_REQUIRED_ROUND = frozenset(
        {
            "base_probability",
            "final_probability",
            "market_price",
        }
    )
    _SAD_OPTIONAL_ROUND = frozenset(
        {
            "llm_confidence",
            "pre_llm_semantic_overlap_ratio",
            "pre_llm_keyword_signal_strength",
            "llm_probability_movement",
            "age_at_analysis_seconds",
        }
    )

    def log_signal_analysis_detail(self, detail: SignalAnalysisDetail) -> None:
        """Write one SIGNAL_ANALYSIS_DETAIL record from a typed struct.

        Emission contract preserved from the prior 46-kwarg signature:
          - Required fields always emitted.
          - `keyword_contributions` emitted only when truthy (matches prior
            `if keyword_contributions:` guard).
          - All other optional fields emitted only when not None.
          - Float fields rounded to 4 decimals in the listed sets.
          - JSON key order matches dataclass field declaration order, which
            matches the prior emission cascade.
        """
        record: dict[str, Any] = {"type": "SIGNAL_ANALYSIS_DETAIL"}
        for field in dataclasses.fields(detail):
            value = getattr(detail, field.name)
            if field.name == "keyword_contributions":
                if not value:
                    continue
            elif field.name in self._SAD_REQUIRED_ROUND:
                value = round(value, 4)
            elif value is None:
                continue
            elif field.name in self._SAD_OPTIONAL_ROUND:
                value = round(value, 4)
            record[field.name] = value
        self._write(record)

    def log_feedback_decision(self, decision: FeedbackDecisionRecord) -> None:
        """Append one immutable feedback counterfactual record."""
        self._write({"type": "FEEDBACK_DECISION", **decision.as_dict()})

    def log_llm_skipped_routing(
        self,
        *,
        ticker: str,
        source: str,
        headline: str,
        reason: str,
        market_price: float,
    ) -> None:
        self._write(
            {
                "type": "LLM_SKIPPED_ROUTING",
                "ticker": ticker,
                "source": source,
                "headline": headline,
                "reason": reason,
                "market_price": round(market_price, 4),
            }
        )

    def log_match_no_candidate(
        self,
        *,
        source: str,
        headline: str,
        venue: str,
        eligible_market_count: int,
        reason: Literal["no_eligible_markets", "no_match", "market_fetch_failed"],
    ) -> None:
        self._write(
            {
                "type": "MATCH_NO_CANDIDATE",
                "source": source,
                "headline": headline,
                "venue": venue,
                "eligible_market_count": eligible_market_count,
                "reason": reason,
            }
        )

    def log_polymarket_market_cache(
        self,
        *,
        raw_fetched: int,
        cursor_present: bool,
        eligible_30d: int,
        candidate_within_admission_horizon: int,
        admission_horizon_days: float,
        market_limit: int,
    ) -> None:
        self._write(
            {
                "type": "POLYMARKET_MARKET_CACHE",
                "raw_fetched": raw_fetched,
                "cursor_present": cursor_present,
                "eligible_30d": eligible_30d,
                "candidate_within_admission_horizon": candidate_within_admission_horizon,
                "admission_horizon_days": admission_horizon_days,
                "market_limit": market_limit,
            }
        )

    def log_match_diagnostic(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        market_title: str,
        match_score: float,
        matched_tokens: list[str],
        token_overlap_count: int,
        geo_overlap_count: int,
        generic_overlap_count: int,
        headline_token_count: int,
        market_title_token_count: int,
        overlap_ratio: float,
        low_match_quality: bool,
        heuristic_flags: list[str] | None = None,
        pre_llm_semantic_overlap_count: int | None = None,
        pre_llm_semantic_overlap_ratio: float | None = None,
        would_fail_pre_llm_gate: bool | None = None,
        pre_llm_headline_token_count: int | None = None,
        pre_llm_market_token_count: int | None = None,
        pre_llm_filtered_stopword_count: int | None = None,
        pre_llm_filtered_generic_count: int | None = None,
        pre_llm_semantic_token_types: dict[str, int] | None = None,
        publish_ts: str | None = None,
        age_at_match_seconds: float | None = None,
        market_specificity_score: float | None = None,
        venue: str | None = None,
        lifecycle_id: str | None = None,
        settlement_source_match: bool | None = None,
    ) -> None:
        record = {
            "type": "MATCH_DIAGNOSTIC",
            "source": source,
            "headline": headline,
            "ticker": ticker,
            "market_title": market_title,
            "match_score": round(match_score, 4),
            "matched_tokens": matched_tokens,
            "token_overlap_count": token_overlap_count,
            "geo_overlap_count": geo_overlap_count,
            "generic_overlap_count": generic_overlap_count,
            "headline_token_count": headline_token_count,
            "market_title_token_count": market_title_token_count,
            "overlap_ratio": round(overlap_ratio, 4),
            "low_match_quality": low_match_quality,
        }
        if heuristic_flags:
            record["heuristic_flags"] = heuristic_flags
        if pre_llm_semantic_overlap_count is not None:
            record["pre_llm_semantic_overlap_count"] = pre_llm_semantic_overlap_count
        if pre_llm_semantic_overlap_ratio is not None:
            record["pre_llm_semantic_overlap_ratio"] = round(pre_llm_semantic_overlap_ratio, 4)
        if would_fail_pre_llm_gate is not None:
            record["would_fail_pre_llm_gate"] = would_fail_pre_llm_gate
        if pre_llm_headline_token_count is not None:
            record["pre_llm_headline_token_count"] = pre_llm_headline_token_count
        if pre_llm_market_token_count is not None:
            record["pre_llm_market_token_count"] = pre_llm_market_token_count
        if pre_llm_filtered_stopword_count is not None:
            record["pre_llm_filtered_stopword_count"] = pre_llm_filtered_stopword_count
        if pre_llm_filtered_generic_count is not None:
            record["pre_llm_filtered_generic_count"] = pre_llm_filtered_generic_count
        if pre_llm_semantic_token_types is not None:
            record["pre_llm_semantic_token_types"] = pre_llm_semantic_token_types
        if publish_ts is not None:
            record["publish_ts"] = publish_ts
        if age_at_match_seconds is not None:
            record["age_at_match_seconds"] = round(age_at_match_seconds, 1)
        if market_specificity_score is not None:
            record["market_specificity_score"] = round(market_specificity_score, 4)
        if venue:
            record["venue"] = venue
        _promote_lifecycle_context(
            record,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )
        self._write(record)

    def log_match_weight_applied(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        market_title: str,
        market_prefix: str,
        tokens: list[str],
        token_weights: dict[str, dict[str, Any]],
        composition_rule: str,
        final_multiplier: float,
        pre_weight_score: float,
        post_weight_score: float,
    ) -> None:
        self._write(
            {
                "type": "MATCH_WEIGHT_APPLIED",
                "source": source,
                "headline": headline,
                "ticker": ticker,
                "market_title": market_title,
                "market_prefix": market_prefix,
                "tokens": tokens,
                "token_weights": token_weights,
                "composition_rule": composition_rule,
                "final_multiplier": round(final_multiplier, 4),
                "pre_weight_score": round(pre_weight_score, 4),
                "post_weight_score": round(post_weight_score, 4),
            }
        )

    def log_market_source_hint_diagnostic(
        self,
        *,
        ticker: str,
        mode: str,
        shadow_only: bool,
        targets: list[dict[str, Any]],
        counters: dict[str, dict[str, int | None]],
        rejected_labels: dict[str, str],
        log_records: list[dict[str, object]],
    ) -> None:
        self._write(
            {
                "type": "MARKET_SOURCE_HINT_DIAGNOSTIC",
                "ticker": ticker,
                "mode": mode,
                "shadow_only": shadow_only,
                "targets": targets,
                "counters": counters,
                "rejected_labels": rejected_labels,
                "log_records": log_records,
            }
        )

    def log_blend_decision(
        self,
        *,
        market_ticker: str,
        fast_lane_p: float | None,
        fast_lane_confidence: float | None,
        accumulation_p: float | None,
        accumulation_confidence: float | None,
        structural_p: float | None,
        structural_confidence: float | None,
        regime_weights: dict[str, float],
        regime_confidence: float,
        blended_p: float,
        blended_confidence: float,
        disagreement_score: float,
        blend_mode: str,
        trade_considered: bool,
        trade_blocked_reason: str | None,
        evidence_ids_contributing: list[str],
        venue: str | None = None,
        recency_score: float | None = None,
        recency_threshold: float | None = None,
        recency_distance: float | None = None,
        lifecycle_id: str | None = None,
        settlement_source_match: bool | None = None,
        g7_mark_snapshot: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "type": "BLEND_DECISION",
            "market_ticker": market_ticker,
            "fast_lane_p": round(fast_lane_p, 4) if fast_lane_p is not None else None,
            "fast_lane_confidence": round(fast_lane_confidence, 4) if fast_lane_confidence is not None else None,
            "accumulation_p": round(accumulation_p, 4) if accumulation_p is not None else None,
            "accumulation_confidence": round(accumulation_confidence, 4)
            if accumulation_confidence is not None
            else None,
            "structural_p": round(structural_p, 4) if structural_p is not None else None,
            "structural_confidence": round(structural_confidence, 4) if structural_confidence is not None else None,
            "regime_weights": regime_weights,
            "regime_confidence": round(regime_confidence, 4),
            "blended_p": round(blended_p, 4),
            "blended_confidence": round(blended_confidence, 4),
            "disagreement_score": round(disagreement_score, 4),
            "blend_mode": blend_mode,
            "trade_considered": trade_considered,
            "trade_blocked_reason": trade_blocked_reason,
            "evidence_ids_contributing": evidence_ids_contributing,
        }
        if venue:
            record["venue"] = venue
        if recency_score is not None:
            record["recency_score"] = round(float(recency_score), 4)
        if recency_threshold is not None:
            record["recency_threshold"] = round(float(recency_threshold), 4)
        if recency_distance is not None:
            record["recency_distance"] = round(float(recency_distance), 4)
        if g7_mark_snapshot is not None:
            record["g7_mark_snapshot"] = dict(g7_mark_snapshot)
        _promote_lifecycle_context(
            record,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )
        self._write(record)

    def log_evidence_ingestion(
        self,
        *,
        market_ticker: str,
        evidence_id: str,
        source_class: str,
        is_duplicate: bool,
        correlation_discount_applied: bool,
        update_type: str,
        dossier_version_before: Any,
        dossier_version_after: Any,
    ) -> None:
        record = {
            "type": "EVIDENCE_INGESTION",
            "market_ticker": market_ticker,
            "evidence_id": evidence_id,
            "source_class": source_class,
            "is_duplicate": is_duplicate,
            "correlation_discount_applied": correlation_discount_applied,
            "update_type": update_type,
            "dossier_version_before": dossier_version_before,
            "dossier_version_after": dossier_version_after,
        }
        self._write(record)

    def log_dossier_update(
        self,
        *,
        market_ticker: str,
        dossier_version: Any,
        prior_estimate: float,
        new_estimate: float,
        update_delta: float,
        confidence_before: float,
        confidence_after: float,
        evidence_ids_contributing: list[str],
        llm_called: bool,
        drift_suspect: bool,
        in_recovery: bool,
    ) -> None:
        record = {
            "type": "DOSSIER_UPDATE",
            "market_ticker": market_ticker,
            "dossier_version": dossier_version,
            "prior_estimate": round(prior_estimate, 4),
            "new_estimate": round(new_estimate, 4),
            "update_delta": round(update_delta, 4),
            "confidence_before": round(confidence_before, 4),
            "confidence_after": round(confidence_after, 4),
            "evidence_ids_contributing": evidence_ids_contributing,
            "llm_called": llm_called,
            "drift_suspect": drift_suspect,
            "in_recovery": in_recovery,
        }
        self._write(record)

    def log_lane_skipped(
        self,
        *,
        market_ticker: str,
        lane_id: str,
        reason: str,
    ) -> None:
        self._write(
            {
                "type": "LANE_SKIPPED",
                "market_ticker": market_ticker,
                "lane_id": lane_id,
                "reason": reason,
            }
        )

    def log_structural_prior_recompute(
        self,
        *,
        market_ticker: str,
        prior_estimate: float,
        new_estimate: float,
        input_sources: Any,
        llm_called: bool,
        token_count: int,
    ) -> None:
        record = {
            "type": "STRUCTURAL_PRIOR_RECOMPUTE",
            "market_ticker": market_ticker,
            "prior_estimate": round(prior_estimate, 4),
            "new_estimate": round(new_estimate, 4),
            "input_sources": input_sources,
            "llm_called": llm_called,
            "token_count": token_count,
        }
        self._write(record)

    def log_budget_pressure(
        self,
        *,
        market_ticker: str,
        reason: str,
        queue_depth: int,
        circuit_breaker_threshold: int,
        per_market_limit: int,
        global_limit: int,
        per_market_calls_last_hour: int,
        global_calls_last_hour: int,
    ) -> None:
        record = {
            "type": "BUDGET_PRESSURE",
            "market_ticker": market_ticker,
            "reason": reason,
            "queue_depth": queue_depth,
            "circuit_breaker_threshold": circuit_breaker_threshold,
            "per_market_limit": per_market_limit,
            "global_limit": global_limit,
            "per_market_calls_last_hour": per_market_calls_last_hour,
            "global_calls_last_hour": global_calls_last_hour,
        }
        self._write(record)

    def log_match_suppressed(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        market_title: str,
        match_score: float,
        matched_tokens: list[str],
        heuristic_flags: list[str],
        reason: str,
        raw_score: float | None = None,
        adjusted_score: float | None = None,
        threshold: float | None = None,
        token_weight_multiplier: float | None = None,
        venue: str | None = None,
        market_prefix: str | None = None,
    ) -> None:
        record = {
            "type": "MATCH_SUPPRESSED",
            "source": source,
            "headline": headline,
            "ticker": ticker,
            "market_title": market_title,
            "match_score": round(match_score, 4),
            "matched_tokens": matched_tokens,
            "heuristic_flags": heuristic_flags,
            "reason": reason,
        }
        if raw_score is not None:
            record["raw_score"] = round(raw_score, 4)
        if adjusted_score is not None:
            record["adjusted_score"] = round(adjusted_score, 4)
        if threshold is not None:
            record["threshold"] = round(threshold, 4)
        if token_weight_multiplier is not None:
            record["token_weight_multiplier"] = round(token_weight_multiplier, 4)
        if venue:
            record["venue"] = venue
        if market_prefix:
            record["market_prefix"] = market_prefix
        self._write(record)

    def log_match_suppression_candidate(
        self,
        *,
        source: str,
        headline: str,
        ticker: str,
        match_score: float,
        overlap_count: int,
        overlap_ratio: float,
        heuristic_flags: list[str],
        matched_tokens: list[str] | None = None,
    ) -> None:
        record: dict = {
            "type": "MATCH_SUPPRESSION_CANDIDATE",
            "source": source,
            "headline": headline[:200],
            "ticker": ticker,
            "match_score": round(match_score, 4),
            "overlap_count": overlap_count,
            "overlap_ratio": round(overlap_ratio, 4),
            "heuristic_flags": heuristic_flags,
        }
        if matched_tokens is not None:
            record["matched_tokens"] = matched_tokens
        self._write(record)

    def log_calibration_observation(
        self,
        *,
        trade_id: str,
        ticker: str,
        market_prefix: str,
        side: str,
        estimated_probability: float,
        realized_outcome: int,
        entry_price_cents: float,
        pnl_dollars: float,
        cost_dollars: float,
        llm_magnitude: str | None,
        llm_confidence: float | None,
        signal_source: str,
        ts_entry: str,
        ts_resolved: str,
        outbox_id: str | None = None,
        ts: str | None = None,
    ) -> None:
        """PROFIT-ALIGN-002 (2026-05-25): per-resolved-trade calibration observation.

        Emitted once per resolved paper trade as the primary feedback signal
        for the calibration tracker (the largest missing piece flagged in the
        2026-05-25 architecture review). Downstream aggregator computes
        Brier score / calibration curve per (market_prefix, magnitude_bucket)
        archetype; surfaces in daily_review.

        ``realized_outcome`` is 1 if the bot's chosen side won (got paid),
        0 otherwise — i.e. it's the probability the BOT'S BET realizes, not
        the YES probability. This makes the Brier score `(p̂ - 1)²` on wins
        and `(p̂ - 0)²` on losses where p̂ is the bot's stated probability
        for its chosen side. Calibration target: mean(p̂) ≈ mean(realized).

        Schema is forward-compatible — the aggregator tolerates absent
        magnitude/confidence fields by bucketing them as "unknown".
        """
        record = {
            "type": "CALIBRATION_OBSERVATION",
            "trade_id": trade_id,
            "ticker": ticker,
            "market_prefix": market_prefix,
            "side": side,
            "estimated_probability": round(float(estimated_probability), 4),
            "realized_outcome": int(realized_outcome),
            "entry_price_cents": round(float(entry_price_cents), 2),
            "pnl_dollars": round(float(pnl_dollars), 2),
            "cost_dollars": round(float(cost_dollars), 2),
            "llm_magnitude": llm_magnitude,
            "llm_confidence": (round(float(llm_confidence), 4) if llm_confidence is not None else None),
            "signal_source": signal_source,
            "ts_entry": ts_entry,
            "ts_resolved": ts_resolved,
        }
        if outbox_id is not None:
            record["outbox_id"] = outbox_id
        if ts is not None:
            record["ts"] = ts
        self._write(record)

    def log_gate_summary(
        self,
        *,
        ticker: str,
        market_prefix: str,
        binding_constraint: str,
        scaled_confidence: float,
        regime_confidence: float,
        blended_confidence: float,
        g1_threshold: float,
        g4_threshold: float,
        gate_chain: list[str],
        recency_score: float | None = None,
        recency_threshold: float | None = None,
        recency_distance: float | None = None,
        g7_mark_snapshot: dict[str, Any] | None = None,
        lifecycle_id: str | None = None,
        settlement_source_match: bool | None = None,
    ) -> None:
        """PROFIT-ALIGN-008 (2026-05-25): consolidated gate-decision diagnostic.

        Per CLAUDE.md gotcha: G1 reads `scaled_confidence = blended * regime`,
        but the binding constraint when fail-safe mode fires is G4
        (`regime_confidence < 0.20`). Operators reading SKIPPED logs see
        G1 first and miss that G4 is the upstream cause.

        This event makes the actual binding constraint explicit:

          binding_constraint ∈ {
            "G4_regime_low",       # rc < 0.20 → fail-safe G1=0.10
            "G1_blended_confidence",  # rc ≥ 0.20 but sc < 0.05
            "G1_fail_safe",        # in fail-safe mode AND sc < 0.10
            "passed",              # both G1 and G4 OK
          }

        gate_chain enumerates which gates were checked + passed/failed in
        order. Surfaces in daily_review.
        """
        record = {
            "type": "GATE_SUMMARY",
            "ticker": ticker,
            "market_prefix": market_prefix,
            "binding_constraint": binding_constraint,
            "scaled_confidence": round(float(scaled_confidence), 4),
            "regime_confidence": round(float(regime_confidence), 4),
            "blended_confidence": round(float(blended_confidence), 4),
            "g1_threshold": round(float(g1_threshold), 4),
            "g4_threshold": round(float(g4_threshold), 4),
            "gate_chain": gate_chain,
        }
        if recency_score is not None:
            record["recency_score"] = round(float(recency_score), 4)
        if recency_threshold is not None:
            record["recency_threshold"] = round(float(recency_threshold), 4)
        if recency_distance is not None:
            record["recency_distance"] = round(float(recency_distance), 4)
        if g7_mark_snapshot is not None:
            record["g7_mark_snapshot"] = dict(g7_mark_snapshot)
        _promote_lifecycle_context(
            record,
            lifecycle_id=lifecycle_id,
            settlement_source_match=settlement_source_match,
        )
        self._write(record)

    def log_match_llm_review(
        self,
        *,
        ticker: str,
        market_title: str,
        market_prefix: str,
        headline: str,
        source: str,
        matched_tokens: list[str],
        llm_relevant: bool,
        llm_direction: str,
        llm_magnitude: str,
        llm_confidence: float,
        verdict: str,
        venue: str | None = None,
        match_score: float | None = None,
        keywords: list[str] | None = None,
        source_class: str | None = None,
    ) -> None:
        """PROFIT-MATCH-DYNAMIC (2026-05-24): per-LLM-call feedback signal.

        Emitted ONCE per signal-analyzer LLM call after parse, regardless of
        verdict. Downstream aggregator (`scripts/match_feedback_aggregator.py`)
        rolls these into per-(token × market_prefix) FP-rate counters that
        feed the runtime downweight list at `data/matcher_token_weights.json`.

        `verdict` is one of:
          - "false_positive_relevance"  — LLM said relevant=False
          - "false_positive_neutral"    — relevant=True but dir=neutral + mag=none
                                          + conf>=0.7 (LLM judged not actionable)
          - "true_positive"             — relevant=True with directional signal
        """
        record = {
            "type": "MATCH_LLM_REVIEW",
            "ticker": ticker,
            "market_title": market_title[:200],
            "market_prefix": market_prefix,
            "headline": headline[:200],
            "source": source,
            "matched_tokens": matched_tokens,
            "llm_relevant": llm_relevant,
            "llm_direction": llm_direction,
            "llm_magnitude": llm_magnitude,
            "llm_confidence": round(float(llm_confidence), 4),
            "verdict": verdict,
        }
        if venue:
            record["venue"] = venue
        if keywords is not None:
            record["keywords"] = list(keywords)
            record["keyword_count"] = len(keywords)
        if source_class:
            record["source_class"] = source_class
        # PROFIT-MATCH-003 (L2-a): carry the matcher score so the feedback loop
        # can score-gate the false_positive_neutral signal. Omitted when None so
        # the consumer treats it as marginal (prior behaviour).
        if match_score is not None:
            record["match_score"] = round(float(match_score), 4)
        self._write(record)

    def log_position_drift(
        self,
        *,
        ticker: str,
        entry_price: float,
        current_price: float,
        drift_cents: float,
        side: str,
        open_since: str,
    ) -> None:
        """Loop C: open position price has drifted significantly from entry."""
        self._write(
            {
                "type": "POSITION_DRIFT",
                "ticker": ticker,
                "entry_price": round(entry_price, 2),
                "current_price": round(current_price, 2),
                "drift_cents": round(drift_cents, 2),
                "side": side,
                "open_since": open_since,
            }
        )

    def log_new_market(
        self,
        *,
        ticker: str,
        title: str,
        series_ticker: str,
    ) -> None:
        """Loop D: a market not seen in the previous cache refresh has appeared."""
        self._write(
            {
                "type": "NEW_MARKET",
                "ticker": ticker,
                "title": title,
                "series_ticker": series_ticker,
            }
        )

    def log_calibration_check(
        self,
        *,
        market_ticker: str,
        lane: str,
        lane_estimate: float,
        final_resolution: float,
        error: float,
        venue: str | None = None,
        outbox_id: str | None = None,
        ts: str | None = None,
    ) -> None:
        record = {
            "type": "CALIBRATION_CHECK",
            "market_ticker": market_ticker,
            "lane": lane,
            "lane_estimate": round(lane_estimate, 4),
            "final_resolution": round(final_resolution, 4),
            "error": round(error, 4),
        }
        if venue:
            record["venue"] = venue
        if outbox_id is not None:
            record["outbox_id"] = outbox_id
        if ts is not None:
            record["ts"] = ts
        self._write(record)


class ShadowTradeLogger:
    """Partitioned writer for diagnostic-only shadow rows."""

    def __init__(self, path: Path = SHADOW_ASSIGNMENT_LOG_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        record.setdefault("shadow_only", True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def log_fresh_pass_assignment_shadow(self, record: dict[str, Any]) -> None:
        if record.get("type") != "FRESH_PASS_ASSIGNMENT_SHADOW":
            raise ValueError("unexpected shadow record type")
        self._write(record)


# Module-level singletons
trade_log = TradeLogger()
shadow_trade_log = ShadowTradeLogger()


def emit_opportunity(
    *,
    market: Any,
    blended_yes_prob: float,
    side: str,
    executed_price_cents: int,
    skip_reason: Optional[str] = None,
) -> None:
    """Emit a structured OPPORTUNITY JSONL event with post-P0 provenance.

    P-6 / P0-PROV-020: opt-in surface gated on the ``KALSHI_OPPORTUNITY_LOG``
    env var. When set, appends a line-delimited JSON event carrying
    ``executed_price_cents``, ``price_source``, ``price_method``,
    ``price_retrieved_at``, ``raw_payload_hash``, and
    ``p0_contract_version: 1``. A 50¢ executable price triggers an explicit
    ``_fixture_encodes_real_50c=true`` marker so downstream tooling can
    distinguish a real 50¢ ask from the retired silent-midpoint fallback.

    Production hot path continues to use ``Logger.log_opportunity()`` — this
    module-level function is for P0 provenance testing and structured
    instrumentation. No-ops when ``KALSHI_OPPORTUNITY_LOG`` is unset so it is
    safe to call unconditionally from analysis paths.
    """
    path_str = os.environ.get("KALSHI_OPPORTUNITY_LOG")
    if not path_str:
        return

    out_path = Path(path_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    retrieved = getattr(market, "price_retrieved_at", None)
    if isinstance(retrieved, datetime):
        retrieved_str: Optional[str] = retrieved.isoformat()
    elif isinstance(retrieved, str):
        retrieved_str = retrieved
    else:
        retrieved_str = None

    event: dict[str, Any] = {
        "type": "OPPORTUNITY",
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": getattr(market, "ticker", None),
        "side": side,
        "estimated_probability": round(float(blended_yes_prob), 4),
        "price_cents": int(executed_price_cents),
        "price_source": getattr(market, "price_source", None) or "unavailable",
        "price_method": getattr(market, "price_method", None) or "none",
        "price_retrieved_at": retrieved_str,
        "raw_payload_hash": getattr(market, "raw_payload_hash", None),
        "p0_contract_version": 1,
    }
    if skip_reason is not None:
        event["skip_reason"] = skip_reason

    # P0-PROV-020: an executable 50¢ price needs an explicit marker so the
    # event is not mistakable for the retired silent-midpoint fallback.
    if int(executed_price_cents) == 50 and skip_reason is None:
        event["_fixture_encodes_real_50c"] = True

    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
