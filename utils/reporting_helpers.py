"""utils/reporting_helpers.py
Lightweight progress and profiling helpers for long-running reporting scripts.
All output goes to stderr; stdout is reserved for report content.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Generator

_PROGRESS_INTERVAL = 10_000


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
