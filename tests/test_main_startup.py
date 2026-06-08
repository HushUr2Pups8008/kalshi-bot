import dataclasses
import io
import shutil
import uuid
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from analysis.market_matcher import _compute_pre_llm_match_meta
from main import (
    _RuntimeInstanceGuard,
    _ensure_supported_python,
    _is_cli_only_command,
    _log_bankroll_summary,
    _log_polymarket_account_summary,
    _startup_probe_matched_tokens,
    _validate_startup_observability_probe_record,
    async_main,
)


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


def test_supported_python_guard_allows_python_311_and_newer():
    _ensure_supported_python((3, 11, 0))
    _ensure_supported_python((3, 12, 1))


def test_supported_python_guard_exits_with_actionable_message_for_older_versions():
    stderr = io.StringIO()

    with pytest.raises(SystemExit) as excinfo:
        _ensure_supported_python((3, 9, 6), stderr=stderr)

    assert excinfo.value.code == 1
    output = stderr.getvalue()
    assert "Python 3.11+ is required. Detected: 3.9.6" in output
    assert "python3.11 -m venv .venv" in output


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


def test_log_bankroll_summary_warns_when_persisted_paper_state_differs(monkeypatch, caplog):
    import config as _cfg_module

    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    monkeypatch.setattr(_cfg_module.cfg, "bankroll", 50.0)

    with caplog.at_level("INFO", logger="main"):
        _log_bankroll_summary(500.0)

    assert "Notional bankroll: $500.00" in caplog.text
    assert "Configured starting bankroll (.env BANKROLL): $50.00" in caplog.text
    assert "Persisted paper bankroll ($500.00) differs from .env BANKROLL ($50.00)" in caplog.text


def test_log_polymarket_account_summary_logs_balance_and_status(monkeypatch, caplog):
    import config as _cfg_module

    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_enabled", True)
    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_live_trading_enabled", False)

    class FakePolymarketAccountClient:
        def get_balance(self):
            return 850.0

        def get_positions(self, *, limit=100):
            return {"positions": [{"market": "m1"}, {"market": "m2"}]}

    with caplog.at_level("INFO", logger="main"):
        status = _log_polymarket_account_summary(
            client_factory=FakePolymarketAccountClient,
        )

    assert status.account_balance == 850.0
    assert status.positions_count == 2
    assert "Polymarket account balance: $850.00" in caplog.text
    assert (
        "Polymarket status: enabled=true live_trading=false "
        "paper_execution=blend account_balance=$850.00 positions=2"
    ) in caplog.text


def test_log_polymarket_account_summary_warns_when_auth_unavailable(monkeypatch, caplog):
    import config as _cfg_module

    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_enabled", True)
    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_live_trading_enabled", False)

    class FailingPolymarketAccountClient:
        def __init__(self):
            raise ValueError("invalid Polymarket US Ed25519 secret")

    with caplog.at_level("INFO", logger="main"):
        status = _log_polymarket_account_summary(
            client_factory=FailingPolymarketAccountClient,
        )

    assert status.account_balance is None
    assert status.account_error
    assert "Could not fetch Polymarket account summary" in caplog.text
    assert "Polymarket status: enabled=true live_trading=false" in caplog.text
    assert "account_balance=unavailable" in caplog.text


def test_log_polymarket_account_summary_keeps_balance_when_positions_fail(monkeypatch, caplog):
    import config as _cfg_module

    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_enabled", True)
    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_live_trading_enabled", False)

    class PartiallyFailingPolymarketAccountClient:
        def get_balance(self):
            return 850.0

        def get_positions(self, *, limit=100):
            raise RuntimeError("positions unavailable")

    with caplog.at_level("INFO", logger="main"):
        status = _log_polymarket_account_summary(
            client_factory=PartiallyFailingPolymarketAccountClient,
        )

    assert status.account_balance == 850.0
    assert status.positions_count is None
    assert status.account_error == "positions unavailable"
    assert "Polymarket account balance: $850.00" in caplog.text
    assert "Could not fetch Polymarket positions" in caplog.text
    assert "account_balance=$850.00" in caplog.text
    assert "account_error=true" in caplog.text


def test_validate_startup_observability_probe_record_reports_missing_fields():
    record = {
        "type": "SIGNAL_ANALYSIS_DETAIL",
        "is_startup_probe": True,
        "is_synthetic_probe": True,
        "pre_llm_quality_pass": False,
    }

    missing = _validate_startup_observability_probe_record(
        record,
        ("pre_llm_quality_pass", "pre_llm_semantic_overlap_count"),
    )

    assert missing == ["pre_llm_semantic_overlap_count"]


def test_startup_probe_matched_tokens_are_raw_and_downstream_meta_normalizes_them():
    raw_tokens = _startup_probe_matched_tokens()

    assert raw_tokens == [" Iran ", "Says"]

    match_meta = _compute_pre_llm_match_meta(
        "U.S. and Iran fail to agree on peace deal after marathon talks",
        "Will the U.S. and Iran agree to a peace deal this month?",
        raw_tokens,
    )

    assert match_meta["pre_llm_semantic_overlap_tokens"] == ["iran"]
    assert match_meta["pre_llm_semantic_overlap_count"] == 1


@pytest.mark.asyncio
async def test_startup_observability_probe_emits_one_tagged_detail_and_no_trade_artifacts(monkeypatch, caplog):
    import config as _cfg_module
    import main as main_module
    from main import TradingBot
    from utils.logger import trade_log

    bot = TradingBot.__new__(TradingBot)
    bot.keyword_stats = MagicMock()
    bot.keyword_stats.get_multiplier.return_value = 1.0

    monkeypatch.setattr(_cfg_module.cfg, "enable_startup_observability_probe", True)
    monkeypatch.setattr(_cfg_module.cfg, "startup_observability_probe_strict", False)
    monkeypatch.setattr(_cfg_module.cfg, "enable_pre_llm_match_gate", True)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_diagnostics_only", False)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_keyword_override_any_hit", True)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_keyword_override_mode", "any_hit")
    monkeypatch.setattr(
        _cfg_module.cfg,
        "startup_observability_probe_required_fields",
        (
            "pre_llm_quality_pass",
            "pre_llm_semantic_overlap_count",
            "pre_llm_semantic_overlap_ratio",
            "pre_llm_headline_token_count",
            "pre_llm_market_token_count",
            "pre_llm_would_block",
            "pre_llm_keyword_override",
            "pre_llm_gate_reason",
        ),
    )

    records: list[dict] = []
    original_write = trade_log._write

    def _capture(record):
        records.append(dict(record))
        original_write(record)

    with patch.object(trade_log, "_write", side_effect=_capture), \
         patch.object(trade_log, "log_signal") as signal_mock, \
         patch.object(trade_log, "log_opportunity") as opp_mock, \
         patch.object(trade_log, "log_analysis_rejected") as reject_mock, \
         caplog.at_level("INFO", logger="main"):
        await main_module.TradingBot._run_startup_observability_probe(bot)

    signal_detail = [
        r
        for r in records
        if r.get("type") == "SIGNAL_ANALYSIS_DETAIL" and r.get("is_synthetic_probe") is True
    ]
    assert len(signal_detail) == 1
    record = signal_detail[0]
    assert record["is_startup_probe"] is True
    assert record["is_synthetic_probe"] is True
    assert record["pre_llm_quality_pass"] is False
    assert record["pre_llm_semantic_overlap_count"] == 1
    assert record["pre_llm_keyword_override"] is True
    assert record["llm_probability_movement"] == pytest.approx(0.12)
    assert record["llm_useful"] is True
    assert record["pre_llm_would_block_and_useful"] is False
    signal_mock.assert_not_called()
    opp_mock.assert_not_called()
    reject_mock.assert_not_called()
    assert "[STARTUP_PROBE][PASS]" in caplog.text
    assert "method=llm" in caplog.text
    assert "ticker=KXSTARTUP-PROBE" in caplog.text


@pytest.mark.asyncio
async def test_startup_observability_probe_strict_fails_when_required_fields_missing(monkeypatch, caplog):
    import config as _cfg_module
    import main as main_module
    from main import TradingBot
    from utils.logger import trade_log

    bot = TradingBot.__new__(TradingBot)
    bot.keyword_stats = MagicMock()
    bot.keyword_stats.get_multiplier.return_value = 1.0

    monkeypatch.setattr(_cfg_module.cfg, "enable_startup_observability_probe", True)
    monkeypatch.setattr(_cfg_module.cfg, "startup_observability_probe_strict", True)
    monkeypatch.setattr(_cfg_module.cfg, "enable_pre_llm_match_gate", True)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_diagnostics_only", False)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_keyword_override_any_hit", True)
    monkeypatch.setattr(_cfg_module.cfg, "pre_llm_match_gate_keyword_override_mode", "any_hit")
    monkeypatch.setattr(
        _cfg_module.cfg,
        "startup_observability_probe_required_fields",
        ("pre_llm_quality_pass", "pre_llm_semantic_overlap_count"),
    )

    original_method = trade_log.log_signal_analysis_detail
    with patch.object(
        trade_log,
        "log_signal_analysis_detail",
        side_effect=lambda detail: original_method(
            dataclasses.replace(detail, pre_llm_semantic_overlap_count=None)
        ),
    ), caplog.at_level("ERROR", logger="main"):
        with pytest.raises(RuntimeError, match="missing required fields"):
            await main_module.TradingBot._run_startup_observability_probe(bot)

    assert "[STARTUP_PROBE][FAIL]" in caplog.text
    assert "missing SIGNAL_ANALYSIS_DETAIL fields=pre_llm_semantic_overlap_count" in caplog.text
    assert "strict=True" in caplog.text


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
