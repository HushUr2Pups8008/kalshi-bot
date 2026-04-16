import shutil
import uuid
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from main import _RuntimeInstanceGuard, _is_cli_only_command, async_main


def _tmp_root() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_main_startup" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_is_cli_only_command_distinguishes_short_lived_modes():
    assert _is_cli_only_command(
        Namespace(report=True, credibility=False, resolve=None, go_live=False, rotate_logs=False)
    ) is True
    assert _is_cli_only_command(
        Namespace(report=False, credibility=False, resolve=("TICKER", "YES"), go_live=False, rotate_logs=False)
    ) is True
    assert _is_cli_only_command(
        Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=True)
    ) is True
    assert _is_cli_only_command(
        Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=False)
    ) is False


def test_runtime_instance_guard_blocks_second_long_running_instance():
    root = _tmp_root()
    try:
        lock_path = root / "bot_runtime.lock"
        first = _RuntimeInstanceGuard(lock_path)
        second = _RuntimeInstanceGuard(lock_path)

        assert first.acquire() is True
        assert second.acquire() is False
        assert second.describe_owner()

        first.release()

        assert second.acquire() is True
        second.release()
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _FakeHandle:
    def __init__(self, *, fileno_value: int = 42, tell_value: int = 0):
        self._fileno_value = fileno_value
        self._tell_value = tell_value
        self.calls: list[tuple[str, object]] = []

    def seek(self, offset, whence=0):
        self.calls.append(("seek", (offset, whence)))

    def tell(self):
        self.calls.append(("tell", self._tell_value))
        return self._tell_value

    def write(self, text):
        self.calls.append(("write", text))

    def flush(self):
        self.calls.append(("flush", None))

    def fileno(self):
        self.calls.append(("fileno", self._fileno_value))
        return self._fileno_value


def test_runtime_instance_guard_recovers_after_handle_close_without_explicit_release():
    root = _tmp_root()
    try:
        lock_path = root / "bot_runtime.lock"
        first = _RuntimeInstanceGuard(lock_path)
        second = _RuntimeInstanceGuard(lock_path)

        assert first.acquire() is True
        assert first._handle is not None
        first._handle.close()
        first._handle = None

        assert second.acquire() is True
        second.release()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runtime_instance_guard_windows_lock_branch_uses_msvcrt_contract():
    handle = _FakeHandle()
    calls: list[tuple[int, int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda fd, mode, size: calls.append((fd, mode, size)),
    )

    _RuntimeInstanceGuard._lock_handle_windows(handle, fake_msvcrt)
    _RuntimeInstanceGuard._unlock_handle_windows(handle, fake_msvcrt)

    assert ("write", "0") in handle.calls
    assert calls == [(42, 1, 1), (42, 2, 1)]


def test_runtime_instance_guard_posix_lock_branch_uses_fcntl_contract():
    handle = _FakeHandle(tell_value=5)
    calls: list[tuple[int, int]] = []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=0x01,
        LOCK_NB=0x02,
        LOCK_UN=0x04,
        flock=lambda fd, mode: calls.append((fd, mode)),
    )

    _RuntimeInstanceGuard._lock_handle_posix(handle, fake_fcntl)
    _RuntimeInstanceGuard._unlock_handle_posix(handle, fake_fcntl)

    assert ("write", "0") not in handle.calls
    assert calls == [(42, 0x03), (42, 0x04)]


@pytest.mark.asyncio
async def test_async_main_cli_path_is_not_blocked_by_runtime_lock(caplog):
    root = _tmp_root()
    try:
        guard = _RuntimeInstanceGuard(root / "bot_runtime.lock")
        assert guard.acquire() is True

        paper = MagicMock()
        paper.generate_report.return_value = "report"
        with patch("main.parse_args", return_value=Namespace(report=True, credibility=False, resolve=None, go_live=False, rotate_logs=False)), \
             patch("main._BOT_RUNTIME_LOCK", root / "bot_runtime.lock"), \
             patch("main.PaperTrader", return_value=paper) as trader_cls, \
             patch("builtins.print") as print_mock, \
             caplog.at_level("DEBUG", logger="main"):
            await async_main()

        trader_cls.assert_called_once_with(startup_context="cli")
        print_mock.assert_called_once_with("report")
        assert "[BOOT]" in caplog.text
        assert "ctx=cli" in caplog.text
        guard.release()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_main_runtime_path_is_blocked_when_lock_is_held(caplog):
    root = _tmp_root()
    try:
        guard = _RuntimeInstanceGuard(root / "bot_runtime.lock")
        assert guard.acquire() is True

        with patch("main.parse_args", return_value=Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=False)), \
             patch("main._BOT_RUNTIME_LOCK", root / "bot_runtime.lock"), \
             patch("main.TradingBot") as bot_cls, \
             caplog.at_level("INFO", logger="main"):
            with pytest.raises(SystemExit) as excinfo:
                await async_main()

        assert excinfo.value.code == 1
        bot_cls.assert_not_called()
        assert "[BOOT]" in caplog.text
        assert "duplicate_runtime_blocked=true" in caplog.text
        guard.release()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_main_runtime_path_uses_runtime_context_for_boot_logging(caplog):
    with patch("main.parse_args", return_value=Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=False)), \
         patch("main._BOT_RUNTIME_LOCK") as lock_path, \
         patch("main._RuntimeInstanceGuard") as guard_cls, \
         patch("main.TradingBot") as bot_cls, \
         caplog.at_level("INFO", logger="main"):
        guard = MagicMock()
        guard.acquire.return_value = True
        guard_cls.return_value = guard

        bot = MagicMock()
        bot.run = MagicMock()
        bot_cls.return_value = bot

        async def _fake_run():
            return None

        bot.run = _fake_run
        await async_main()

    assert "[BOOT]" in caplog.text
    assert "ctx=runtime" in caplog.text
    guard_cls.assert_called_once_with(lock_path)


@pytest.mark.asyncio
async def test_async_main_rotate_logs_cli_exits_without_paper_trader(caplog):
    with patch("main.parse_args", return_value=Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=True)), \
         patch("main.rotate_logs", return_value=["bot.log", "errors.log"]) as rotate_mock, \
         patch("main.PaperTrader") as trader_cls, \
         caplog.at_level("DEBUG", logger="main"):
        await async_main()

    rotate_mock.assert_called_once_with()
    trader_cls.assert_not_called()
    assert "manual_log_rotation_complete=true" in caplog.text
