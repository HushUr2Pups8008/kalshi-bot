"""Durable fail-closed holds for live submissions with unknown outcomes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl.
    fcntl = None

from utils.output_paths import STATE_ROOT


LIVE_SUBMISSION_HOLD_PATH = STATE_ROOT / "live_submission" / "unknown_submission_holds.json"
_SCHEMA_VERSION = 1
_PROCESS_LOCK = threading.RLock()


class LiveSubmissionHoldStore:
    """Persist fail-closed live-submission reservations and unknown-outcome holds."""

    def __init__(self, path: Path = LIVE_SUBMISSION_HOLD_PATH) -> None:
        self._path = path
        self._held_tickers: set[str] = set()
        self._available = True
        self._load()

    def can_submit(self, ticker: str) -> bool:
        return self._available and ticker not in self._held_tickers

    def reserve(self, ticker: str) -> bool:
        """Exclusively persist a ticker reservation before a live POST."""
        return self._claim(ticker, allow_existing=False)

    def hold(self, ticker: str) -> bool:
        """Idempotently persist a ticker hold after an unknown submission outcome."""
        return self._claim(ticker, allow_existing=True)

    def release(self, ticker: str) -> bool:
        """Release a reservation only after the accepted order is durably journaled."""
        try:
            with self._state_lock():
                if not self._available or not self._reload():
                    return False
                if ticker not in self._held_tickers:
                    next_held_tickers = self._held_tickers | {ticker}
                    if self._persist(next_held_tickers):
                        self._held_tickers = next_held_tickers
                    self._available = False
                    return False
                next_held_tickers = self._held_tickers - {ticker}
                if not self._persist(next_held_tickers):
                    return False
                self._held_tickers = next_held_tickers
        except (OSError, TypeError, ValueError):
            self._available = False
            return False
        return True

    def _load(self) -> None:
        try:
            self._held_tickers = self._read_held_tickers()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._available = False

    def _claim(self, ticker: str, *, allow_existing: bool) -> bool:
        try:
            with self._state_lock():
                if not self._available or not self._reload():
                    return False
                if ticker in self._held_tickers:
                    return allow_existing
                next_held_tickers = self._held_tickers | {ticker}
                if not self._persist(next_held_tickers):
                    return False
                self._held_tickers = next_held_tickers
        except (OSError, TypeError, ValueError):
            self._available = False
            return False
        return True

    def _reload(self) -> bool:
        try:
            self._held_tickers = self._read_held_tickers()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self._available = False
            return False
        return True

    def _read_held_tickers(self) -> set[str]:
        if self._path.parent.exists() and not self._path.parent.is_dir():
            raise OSError("hold parent is not a directory")
        if self._temp_path().exists():
            raise OSError("incomplete hold checkpoint")
        if not self._path.exists():
            return set()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid hold checkpoint")
        if type(payload.get("version")) is not int or payload["version"] != _SCHEMA_VERSION:
            raise ValueError("invalid hold checkpoint")
        held_tickers = payload.get("held_tickers")
        if not isinstance(held_tickers, list) or any(
            not isinstance(ticker, str) or not ticker for ticker in held_tickers
        ):
            raise ValueError("invalid hold checkpoint")
        return set(held_tickers)

    def _persist(self, held_tickers: set[str]) -> bool:
        try:
            self._write(held_tickers)
        except (OSError, TypeError, ValueError):
            self._available = False
            return False
        return True

    @contextmanager
    def _state_lock(self) -> Iterator[None]:
        """Serialize instances locally and reload under an advisory process lock when available."""
        with _PROCESS_LOCK:
            lock_path = self._lock_path()
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _write(self, held_tickers: set[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._temp_path()
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": _SCHEMA_VERSION,
                    "held_tickers": sorted(held_tickers),
                },
                handle,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self._path)

    def _temp_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".tmp")

    def _lock_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".lock")
