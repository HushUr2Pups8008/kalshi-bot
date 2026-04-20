"""MAC-TEST-003: _RuntimeInstanceGuard stale-lock-file regression tests.

Verifies that the guard correctly acquires the lock when a lock file
from a previous (now-dead) process already exists with stale PID content.
"""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from main import _RuntimeInstanceGuard


def _tmp_lock_path() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_instance_guard" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root / "instance.lock"


def test_guard_acquires_when_lock_file_absent():
    lock_path = _tmp_lock_path()
    try:
        guard = _RuntimeInstanceGuard(lock_path)
        result = guard.acquire()
        assert result is True, "acquire() must succeed when no lock file exists"
        guard.release()
    finally:
        shutil.rmtree(lock_path.parent, ignore_errors=True)


def test_guard_acquires_over_stale_pid_content():
    """MAC-TEST-003: stale lock file (dead PID) must not block a new acquire.

    Simulates SIGKILL scenario: a lock file exists with a dead process's PID
    written to it, but the flock has been released by the OS when the process
    died.  A new _RuntimeInstanceGuard must acquire the lock and overwrite the
    stale content with its own PID.
    """
    lock_path = _tmp_lock_path()
    try:
        # Write stale content — PID 999999999 is virtually guaranteed not to exist
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        stale_metadata = {
            "pid": 999_999_999,
            "cwd": "/tmp/stale",
            "started_utc": "2000-01-01T00:00:00+00:00",
            "argv": ["main.py"],
        }
        lock_path.write_text(
            json.dumps(stale_metadata, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        guard = _RuntimeInstanceGuard(lock_path)
        result = guard.acquire()
        assert result is True, "acquire() must succeed when existing lock file has a stale (dead) PID"

        # New content must reflect the current process, not the stale one
        content = json.loads(lock_path.read_text(encoding="utf-8").strip())
        import os
        assert content["pid"] == os.getpid(), "Lock file must be rewritten with the current PID"

        guard.release()
    finally:
        shutil.rmtree(lock_path.parent, ignore_errors=True)


def test_guard_describe_owner_returns_stale_info_before_acquire():
    """describe_owner() reads the raw lock file content before any acquire."""
    lock_path = _tmp_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        stale_metadata = {
            "pid": 999_999_999,
            "cwd": "/tmp/stale",
            "started_utc": "2000-01-01T00:00:00+00:00",
            "argv": ["main.py"],
        }
        lock_path.write_text(
            json.dumps(stale_metadata, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        guard = _RuntimeInstanceGuard(lock_path)
        owner = guard.describe_owner()
        assert "999999999" in owner, "describe_owner() must include the stale PID"
    finally:
        shutil.rmtree(lock_path.parent, ignore_errors=True)
