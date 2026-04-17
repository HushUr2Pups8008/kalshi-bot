"""utils/reporting_helpers.py
Lightweight progress and profiling helpers for long-running reporting scripts.
All output goes to stderr; stdout is reserved for report content.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

_PROGRESS_INTERVAL = 10_000
DEFAULT_CURRENT_STATE_WINDOW_HOURS = 24


class ProgressTracker:
    """Counts records and prints a progress line every N records."""

    def __init__(self, *, interval: int = _PROGRESS_INTERVAL) -> None:
        self._interval = interval
        self._count = 0
        self._next = interval
        self._t0 = time.perf_counter()

    def tick(self) -> None:
        self._count += 1
        if self._count >= self._next:
            elapsed = time.perf_counter() - self._t0
            _eprint(f"[progress] scanned {self._count} records (elapsed: {elapsed:.1f}s)")
            self._next += self._interval

    @property
    def count(self) -> int:
        return self._count


@contextmanager
def stage_timer(label: str, *, enabled: bool = True) -> Generator[None, None, None]:
    """Context manager: prints stage start and elapsed time when enabled.
    When enabled=False, is a transparent no-op -- no contextlib.nullcontext needed."""
    if enabled:
        _eprint(f"[stage] starting: {label}")
    t0 = time.perf_counter()
    try:
        yield
    finally:
        if enabled:
            elapsed = time.perf_counter() - t0
            _eprint(f"[stage] {label} completed in {elapsed:.1f}s")


def _eprint(msg: str) -> None:
    """Write msg to stderr immediately. ASCII-only by design (Windows-safe)."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def resolve_recent_window(
    since: datetime | None,
    until: datetime | None,
    *,
    hours: int = DEFAULT_CURRENT_STATE_WINDOW_HOURS,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None, bool]:
    """Return an effective window for current-state scripts.

    If both bounds are omitted, default to the last ``hours`` hours in UTC.
    Explicit user-supplied bounds always win.
    """

    if since is not None or until is not None:
        return since, until, False

    ref = now or datetime.now(timezone.utc)
    effective_until = ref
    effective_since = ref - timedelta(hours=hours)
    return effective_since, effective_until, True


def is_trade_log_root_path(path: Path) -> bool:
    """True when path looks like the trade-log root directory."""

    path_str = os.fspath(path)
    if not os.path.isdir(path_str):
        return False
    archive_dir = os.path.join(path_str, "archive")
    live_file = os.path.join(path_str, "live", "trades.jsonl")
    legacy_file = os.path.join(path_str, "trades.jsonl")
    return os.path.exists(archive_dir) or os.path.exists(live_file) or os.path.exists(legacy_file)


def warn_if_full_trade_root_scan(
    path: Path,
    *,
    since: datetime | None,
    until: datetime | None,
) -> None:
    """Warn once when a command is about to scan the full trade-log root."""

    if since is not None or until is not None:
        return
    if not is_trade_log_root_path(path):
        return
    _eprint(
        "[warning] scanning full trade-log root with no time filter; "
        "this may read archive history and take longer than expected"
    )
