import dataclasses
import io
import json
import math
import shutil
import sqlite3
import uuid
from argparse import Namespace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import main

from analysis.market_matcher import _compute_pre_llm_match_meta
from main import (
    _RuntimeInstanceGuard,
    _ensure_supported_python,
    _is_cli_only_command,
    _log_bankroll_summary,
    _log_polymarket_account_summary,
    _paper_open_exposure_drawdown_pct,
    _paper_open_exposure_drawdown_snapshot,
    _startup_probe_matched_tokens,
    _validate_startup_observability_probe_record,
    async_main,
    parse_args,
)
from config import cfg
from trading.settlement import (
    MarketOutcome,
    VoidRefundContract,
    build_settlement_observation,
)
from trading.paper_cohorts import PaperCohort
from trading.venue import MarketRef, Venue


def _tmp_root() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_main_startup" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _patch_legacy_cli_runtime_cohort(monkeypatch) -> PaperCohort:
    """Keep CLI-mode tests independent of the operator's active cohort config."""

    cohort = PaperCohort(
        cohort_id="legacy",
        db_path=main.DATA_DIR / "paper_trades.db",
        starting_bankroll=cfg.bankroll,
        writable=True,
        storage_root=main.DATA_DIR,
    )
    monkeypatch.setattr(
        main,
        "_runtime_paper_cohort_from_config",
        lambda: (cohort, (cohort,), None),
    )
    return cohort


def _cutover_eligible_legacy_db(path: Path) -> None:
    """Create the minimum reconciled legacy ledger needed by cutover validation."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE paper_trades (resolved INTEGER NOT NULL)")
        conn.execute("CREATE TABLE legacy_provenance (value TEXT)")


def _legacy_pending_state_db(path: Path) -> None:
    """Create the minimum unresolved legacy ledger for pending paper isolation."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO bot_state(key, value) VALUES ('notional_bankroll', '500.0')"
        )
        conn.execute("CREATE TABLE paper_trades (resolved INTEGER NOT NULL)")
        conn.executemany("INSERT INTO paper_trades(resolved) VALUES (?)", [(0,), (0,)])


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


def test_paper_open_exposure_drawdown_uses_mark_to_market_equity(monkeypatch):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    with patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value={"marked_value": 9.0},
    ) as compute_marks:
        drawdown = _paper_open_exposure_drawdown_pct(paper)

    compute_marks.assert_called_once_with(Path("paper.db"))
    assert drawdown == pytest.approx(0.21)


def test_paper_open_exposure_drawdown_fails_closed_when_marks_unavailable(monkeypatch):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    with patch("scripts.mark_open_positions.compute_open_position_marks", return_value=None):
        drawdown = _paper_open_exposure_drawdown_pct(paper)

    assert drawdown == pytest.approx(1.0)


def test_active_runtime_cohort_requires_manifest_before_paper_trader_bootstrap(
    monkeypatch,
    tmp_path: Path,
):
    from trading.paper_cohorts import (
        initialize_active_paper_cohort_manifest,
        resolve_runtime_paper_cohort,
    )

    active_cfg = SimpleNamespace(
        bankroll=500.0,
        paper_cohort_id="active-20260728",
        paper_active_cohort_starting_bankroll=125.0,
        paper_active_cohort_max_days_to_close=14.0,
    )
    monkeypatch.setattr(main, "cfg", active_cfg)

    with pytest.raises(FileNotFoundError, match="manifest"):
        main._runtime_paper_cohort_from_config(db_root=tmp_path)
    assert not (tmp_path / "paper_cohorts" / "active-20260728").exists()

    cohort = resolve_runtime_paper_cohort(
        active_cfg.paper_cohort_id,
        legacy_starting_bankroll=active_cfg.bankroll,
        active_starting_bankroll=active_cfg.paper_active_cohort_starting_bankroll,
        db_root=tmp_path,
    )
    legacy_path = tmp_path / "paper_trades.db"
    _cutover_eligible_legacy_db(legacy_path)
    initialize_active_paper_cohort_manifest(
        cohort,
        max_days_to_close=active_cfg.paper_active_cohort_max_days_to_close,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=active_cfg.bankroll,
    )

    runtime, risk_cohorts, block_reason = main._runtime_paper_cohort_from_config(
        db_root=tmp_path
    )
    assert runtime == cohort
    assert [item.cohort_id for item in risk_cohorts] == ["legacy", "active-20260728"]
    assert block_reason == "active paper cohort remains isolated from live trading"


@pytest.fixture()
def legacy_pending_runtime(
    monkeypatch,
    tmp_path: Path,
):
    from trading.paper_cohorts import (
        initialize_legacy_pending_paper_cohort_manifest,
        legacy_open_exposure_fingerprint,
        resolve_runtime_paper_cohort,
    )

    pending_cfg = SimpleNamespace(
        bankroll=500.0,
        paper_cohort_id="pending-20260728",
        paper_cohort_kind="legacy_pending",
        paper_active_cohort_starting_bankroll=125.0,
        paper_active_cohort_max_days_to_close=14.0,
    )
    monkeypatch.setattr(main, "cfg", pending_cfg)

    with pytest.raises(FileNotFoundError, match="manifest"):
        main._runtime_paper_cohort_from_config(db_root=tmp_path)
    assert not (tmp_path / "legacy_pending_paper_cohorts" / "pending-20260728").exists()

    legacy_path = tmp_path / "paper_trades.db"
    _legacy_pending_state_db(legacy_path)
    cohort = resolve_runtime_paper_cohort(
        pending_cfg.paper_cohort_id,
        legacy_starting_bankroll=None,
        active_starting_bankroll=pending_cfg.paper_active_cohort_starting_bankroll,
        cohort_kind="legacy_pending",
        db_root=tmp_path,
    )
    exposure = legacy_open_exposure_fingerprint(legacy_path)
    initialize_legacy_pending_paper_cohort_manifest(
        cohort,
        max_days_to_close=pending_cfg.paper_active_cohort_max_days_to_close,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=pending_cfg.bankroll,
        expected_legacy_open_trade_count=exposure.unresolved_trade_count,
        expected_legacy_open_rows_sha256=exposure.rows_sha256,
    )

    runtime, risk_cohorts, block_reason = main._runtime_paper_cohort_from_config(
        db_root=tmp_path
    )
    return cohort, runtime, risk_cohorts, block_reason


def test_legacy_pending_runtime_binds_distinct_manifest_and_remains_paper_only(
    legacy_pending_runtime,
):
    cohort, runtime, risk_cohorts, block_reason = legacy_pending_runtime

    assert runtime == cohort
    assert [item.cohort_id for item in risk_cohorts] == [
        "legacy-pending-baseline",
        "pending-20260728",
    ]
    assert block_reason == (
        "legacy-pending paper cohort remains permanently isolated from live trading"
    )


def test_trading_bot_binds_global_trade_log_to_resolved_paper_cohort(
    monkeypatch,
    legacy_pending_runtime,
):
    cohort, _, _, _ = legacy_pending_runtime

    class StopAfterPaperTrader(RuntimeError):
        pass

    bound_trade_log = MagicMock()
    bound_shadow_trade_log = MagicMock()
    monkeypatch.setattr(main, "trade_log", bound_trade_log)
    monkeypatch.setattr(main, "shadow_trade_log", bound_shadow_trade_log, raising=False)
    monkeypatch.setattr(
        main,
        "_runtime_paper_cohort_from_config",
        lambda: (cohort, [], "legacy-pending paper cohort remains permanently isolated from live trading"),
    )
    monkeypatch.setattr(main, "_configured_paper_cohort_kind", lambda: "legacy_pending")
    for dependency in (
        "KalshiRestClient",
        "KalshiWebSocketClient",
        "MarketMatcher",
        "CalibrationTask",
    ):
        monkeypatch.setattr(main, dependency, MagicMock())
    monkeypatch.setattr(
        main,
        "PaperTrader",
        MagicMock(side_effect=StopAfterPaperTrader),
    )

    with pytest.raises(StopAfterPaperTrader):
        main.TradingBot()

    bound_trade_log.bind_runtime_context.assert_called_once_with(
        cohort_id=cohort.cohort_id,
        cohort_kind="legacy_pending",
    )
    bound_shadow_trade_log.bind_runtime_context.assert_called_once_with(
        cohort_id=cohort.cohort_id,
        cohort_kind="legacy_pending",
    )


def test_pending_g7_snapshot_is_scoped_to_pending_runtime_db_not_legacy_baseline(
    legacy_pending_runtime,
):
    _, runtime, risk_cohorts, _ = legacy_pending_runtime

    assert [item.cohort_id for item in risk_cohorts] == [
        "legacy-pending-baseline",
        "pending-20260728",
    ]
    assert runtime.db_path == risk_cohorts[1].db_path
    assert runtime.db_path != risk_cohorts[0].db_path

    paper = SimpleNamespace(
        db_path=runtime.db_path,
        starting_bankroll=runtime.starting_bankroll,
        get_notional_bankroll=lambda: runtime.starting_bankroll,
    )

    def only_pending_runtime_db(db_path: Path) -> dict[str, float]:
        assert db_path == runtime.db_path
        return {"marked_value": 0.0}

    with patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        side_effect=only_pending_runtime_db,
    ) as compute_marks:
        snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    compute_marks.assert_called_once_with(runtime.db_path)
    assert snapshot.notional_bankroll == pytest.approx(runtime.starting_bankroll)
    assert snapshot.marked_value == pytest.approx(0.0)
    assert snapshot.drawdown_pct == pytest.approx(0.0)


def test_legacy_runtime_config_is_refused_after_active_cohort_provisioning(
    monkeypatch,
    tmp_path: Path,
):
    from trading.paper_cohorts import (
        initialize_active_paper_cohort_manifest,
        resolve_runtime_paper_cohort,
    )

    legacy_path = tmp_path / "paper_trades.db"
    _cutover_eligible_legacy_db(legacy_path)
    active = resolve_runtime_paper_cohort(
        "active-20260728",
        legacy_starting_bankroll=500.0,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        active,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    monkeypatch.setattr(
        main,
        "cfg",
        SimpleNamespace(
            bankroll=500.0,
            paper_cohort_id="legacy",
            paper_active_cohort_starting_bankroll=None,
        ),
    )

    with pytest.raises(RuntimeError, match="legacy paper runtime is blocked"):
        main._runtime_paper_cohort_from_config(db_root=tmp_path)


def test_active_runtime_uses_manifest_attested_legacy_baseline_not_cfg(
    monkeypatch,
    tmp_path: Path,
):
    from trading.paper_cohorts import (
        initialize_active_paper_cohort_manifest,
        resolve_runtime_paper_cohort,
    )

    legacy_path = tmp_path / "paper_trades.db"
    _cutover_eligible_legacy_db(legacy_path)
    active = resolve_runtime_paper_cohort(
        "active-attested-baseline",
        legacy_starting_bankroll=None,
        active_starting_bankroll=125.0,
        db_root=tmp_path,
    )
    initialize_active_paper_cohort_manifest(
        active,
        max_days_to_close=14.0,
        legacy_db_path=legacy_path,
        legacy_starting_bankroll=500.0,
    )
    monkeypatch.setattr(
        main,
        "cfg",
        SimpleNamespace(
            bankroll=999.0,
            paper_cohort_id=active.cohort_id,
            paper_active_cohort_starting_bankroll=125.0,
            paper_active_cohort_max_days_to_close=14.0,
        ),
    )

    runtime, risk_cohorts, _ = main._runtime_paper_cohort_from_config(db_root=tmp_path)

    assert runtime == active
    assert risk_cohorts[0].starting_bankroll == pytest.approx(500.0)


def test_broken_active_cohort_root_symlink_fails_live_risk_gate(tmp_path: Path):
    (tmp_path / "paper_cohorts").symlink_to(tmp_path / "missing-cohorts", target_is_directory=True)

    active, failures = main._provisioned_cohort_live_risk_gate_failures(db_root=tmp_path)

    assert active is True
    assert any("root is invalid" in failure for failure in failures)


def test_paper_open_exposure_drawdown_snapshot_preserves_mark_provenance(monkeypatch):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    with patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value={
            "marked_value": 9.0,
            "total_cost": 31.0,
            "unknown_cost": 0.0,
            "priced_count": 2,
            "unpriced_count": 0,
            "snapshot_fallback_count": 0,
            "as_of": "2026-07-24T00:00:00+00:00",
        },
    ):
        snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    assert snapshot.drawdown_pct == pytest.approx(0.21)
    assert snapshot.configured_bankroll == pytest.approx(100.0)
    assert snapshot.notional_bankroll == pytest.approx(70.0)
    assert snapshot.marked_value == pytest.approx(9.0)
    assert snapshot.total_entry_cost == pytest.approx(31.0)
    assert snapshot.unknown_entry_cost == pytest.approx(0.0)
    assert snapshot.priced_count == 2
    assert snapshot.unpriced_count == 0
    assert snapshot.snapshot_fallback_count == 0
    assert snapshot.valuation_basis == "legacy_marked_value_pre_exit_fees"
    assert snapshot.unpriced_positions_valued_at_zero is True
    assert snapshot.provider == "scripts.mark_open_positions"
    assert snapshot.fallback_status == "none"
    assert snapshot.valuation_as_of == "2026-07-24T00:00:00+00:00"


def test_paper_open_exposure_drawdown_snapshot_fails_closed_with_status(monkeypatch):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    with patch("scripts.mark_open_positions.compute_open_position_marks", return_value=None):
        snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    assert snapshot.drawdown_pct == pytest.approx(1.0)
    assert snapshot.provider == "scripts.mark_open_positions"
    assert snapshot.fallback_status == "marks_unavailable"
    assert snapshot.marked_value is None


def test_paper_open_exposure_drawdown_snapshot_reports_mark_error(monkeypatch):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    with patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        side_effect=RuntimeError("provider unavailable"),
    ):
        snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    assert snapshot.drawdown_pct == pytest.approx(1.0)
    assert snapshot.provider == "scripts.mark_open_positions"
    assert snapshot.fallback_status == "mark_error"


@pytest.mark.parametrize("configured_bankroll", (float("nan"), float("inf"), float("-inf")))
def test_paper_open_exposure_drawdown_snapshot_scrubs_nonfinite_configured_bankroll(
    monkeypatch,
    configured_bankroll: float,
):
    monkeypatch.setattr(cfg, "bankroll", configured_bankroll)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: 70.0,
    )

    snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    assert snapshot.drawdown_pct == pytest.approx(1.0)
    assert snapshot.fallback_status == "invalid_configured_bankroll"
    assert snapshot.configured_bankroll is None
    assert json.dumps(snapshot.as_log_record(threshold_pct=0.2), allow_nan=False)


@pytest.mark.parametrize(
    ("notional_bankroll", "marked_value"),
    ((float("nan"), 9.0), (float("inf"), 9.0), (70.0, float("nan")), (70.0, float("-inf"))),
)
def test_paper_open_exposure_drawdown_snapshot_scrubs_nonfinite_mark_values(
    monkeypatch,
    notional_bankroll: float,
    marked_value: float,
):
    monkeypatch.setattr(cfg, "bankroll", 100.0)
    paper = SimpleNamespace(
        db_path=Path("paper.db"),
        get_notional_bankroll=lambda: notional_bankroll,
    )

    with patch(
        "scripts.mark_open_positions.compute_open_position_marks",
        return_value={"marked_value": marked_value},
    ):
        snapshot = _paper_open_exposure_drawdown_snapshot(paper)

    assert snapshot.drawdown_pct == pytest.approx(1.0)
    assert snapshot.fallback_status == "invalid_mark"
    assert snapshot.notional_bankroll == (
        pytest.approx(notional_bankroll) if math.isfinite(notional_bankroll) else None
    )
    assert snapshot.marked_value == (
        pytest.approx(marked_value) if math.isfinite(marked_value) else None
    )
    assert json.dumps(snapshot.as_log_record(threshold_pct=0.2), allow_nan=False)


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


def test_runtime_instance_guard_binds_runtime_paper_cohort():
    root = _tmp_root()
    try:
        lock_path = root / "bot_runtime.lock"
        guard = _RuntimeInstanceGuard(lock_path)

        assert guard.acquire() is True
        guard.bind_runtime_paper_cohort("legacy-pending-20260729", "legacy_pending")

        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["runtime_paper_cohort"] == {
            "schema_version": 1,
            "cohort_id": "legacy-pending-20260729",
            "cohort_kind": "legacy_pending",
            "owner_pid": metadata["pid"],
            "boot_started_utc": metadata["started_utc"],
        }
        assert metadata["started_utc"]

        guard.release()
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


def test_log_bankroll_summary_labels_an_isolated_cohort_baseline(monkeypatch, caplog):
    monkeypatch.setattr(cfg, "is_paper_trading", True)
    with caplog.at_level("INFO", logger="main"):
        _log_bankroll_summary(
            100.0,
            starting_bankroll=125.0,
            cohort_id="active-20260728",
            db_path=Path("data/paper_cohorts/active-20260728/paper_trades.db"),
        )

    assert "paper cohort active-20260728" in caplog.text
    assert "Configured starting bankroll" in caplog.text


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
async def test_async_main_cli_path_is_not_blocked_by_runtime_lock(monkeypatch, caplog):
    root = _tmp_root()
    try:
        guard = _RuntimeInstanceGuard(root / "bot_runtime.lock")
        assert guard.acquire() is True
        _patch_legacy_cli_runtime_cohort(monkeypatch)

        paper = MagicMock()
        paper.generate_report.return_value = "report"
        with patch("main.parse_args", return_value=Namespace(report=True, credibility=False, resolve=None, go_live=False, rotate_logs=False)), \
             patch("main._BOT_RUNTIME_LOCK", root / "bot_runtime.lock"), \
             patch("main.PaperTrader", return_value=paper) as trader_cls, \
             patch("builtins.print") as print_mock, \
             caplog.at_level("DEBUG", logger="main"):
            await async_main()

        trader_cls.assert_called_once()
        call_kwargs = trader_cls.call_args.kwargs
        assert call_kwargs["startup_context"] == "cli"
        assert call_kwargs["db_path"].name == "paper_trades.db"
        assert call_kwargs["starting_bankroll"] == pytest.approx(cfg.bankroll)
        assert call_kwargs["cohort_id"] == "legacy"
        assert call_kwargs["paper_cohort_storage_root"] == main.DATA_DIR
        print_mock.assert_called_once_with("report")
        assert "[BOOT]" in caplog.text
        assert "ctx=cli" in caplog.text
        guard.release()
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_async_main_resolve_is_blocked_before_shared_state_mutation(monkeypatch):
    root = _tmp_root()
    shared_db = sqlite3.connect(root / "shared-paper.db")
    guard = None
    try:
        shared_db.execute("CREATE TABLE runtime_positions (trade_id TEXT PRIMARY KEY)")
        shared_db.execute("INSERT INTO runtime_positions VALUES ('runtime-open')")
        shared_db.commit()
        runtime_portfolio = ["runtime-open"]
        guard = _RuntimeInstanceGuard(root / "bot_runtime.lock")
        assert guard.acquire() is True
        args = Namespace(
            report=False,
            credibility=False,
            resolve=("KXTEST", "YES"),
            go_live=False,
            rotate_logs=False,
        )

        def construct_cli_trader(*_args, **_kwargs):
            shared_db.execute("DELETE FROM runtime_positions")
            shared_db.commit()
            runtime_portfolio.clear()
            return MagicMock()

        with patch("main.parse_args", return_value=args), patch(
            "main._BOT_RUNTIME_LOCK", root / "bot_runtime.lock"
        ), patch(
            "main.PaperTrader", side_effect=construct_cli_trader
        ) as trader_cls, patch(
            "main.VenueRoutingAuthoritativeSettlementSource"
        ) as source_cls:
            with pytest.raises(SystemExit, match="[Ss]top.*restart"):
                await async_main()

        trader_cls.assert_not_called()
        source_cls.assert_not_called()
        assert runtime_portfolio == ["runtime-open"]
        assert shared_db.execute(
            "SELECT trade_id FROM runtime_positions"
        ).fetchall() == [("runtime-open",)]
    finally:
        if guard is not None:
            guard.release()
        shared_db.close()
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def available_cli_runtime_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "bot_runtime.lock"
    monkeypatch.setattr("main._BOT_RUNTIME_LOCK", lock_path)
    _patch_legacy_cli_runtime_cohort(monkeypatch)
    return lock_path


def _settlement_observation(
    market_ref: MarketRef,
    outcome: MarketOutcome,
    *,
    void_refund: VoidRefundContract | None = None,
):
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return build_settlement_observation(
        market_ref=market_ref,
        outcome=outcome,
        authoritative_outcome={"result": outcome.value},
        authoritative_payload={"result": outcome.value},
        observed_at=now,
        effective_at=now,
        rules_version="test-v1",
        source_id="test-authority",
        void_refund=void_refund,
    )


@pytest.mark.asyncio
async def test_async_main_resolve_false_mode_rejects_non_yes_no(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        False,
    )
    paper = MagicMock()
    paper.resolve_market = AsyncMock()
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXTEST", "MAYBE"),
        go_live=False,
        rotate_logs=False,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ):
        with pytest.raises(SystemExit, match="exactly YES or NO"):
            await async_main()

    paper.resolve_market.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_main_resolve_false_mode_rejects_void_even_with_refund(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        False,
    )
    paper = MagicMock()
    paper.resolve_market = AsyncMock()
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXTEST", "VOID"),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("50"),
        void_refunds_entry_fee="YES",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ):
        with pytest.raises(SystemExit, match="exactly YES or NO"):
            await async_main()

    paper.resolve_market.assert_not_awaited()
    paper.resolve_observation.assert_not_called()


@pytest.mark.asyncio
async def test_async_main_resolve_false_mode_preserves_legacy_resolution(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        False,
    )
    paper = MagicMock()
    paper.resolve_market = AsyncMock()
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXTEST", "YES"),
        go_live=False,
        rotate_logs=False,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch("main.VenueRoutingAuthoritativeSettlementSource") as source_cls:
        await async_main()

    paper.resolve_market.assert_awaited_once_with("KXTEST", True)
    paper.resolve_observation.assert_not_called()
    source_cls.assert_not_called()


def test_parse_args_preserves_exact_void_refund_economics(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--resolve",
            "KXVOID",
            "VOID",
            "--void-refund-cents-per-contract",
            "37.25",
            "--void-refunds-entry-fee",
            "NO",
        ],
    )

    args = parse_args()

    assert args.void_refund_cents_per_contract == Decimal("37.25")
    assert args.void_refunds_entry_fee == "NO"


@pytest.mark.asyncio
async def test_async_main_rejects_void_refund_args_without_resolve(monkeypatch):
    args = Namespace(
        report=False,
        credibility=False,
        resolve=None,
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("50"),
        void_refunds_entry_fee="YES",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader"
    ) as trader_cls, patch("main.TradingBot") as bot_cls:
        with pytest.raises(SystemExit, match="require --resolve .* VOID"):
            await async_main()

    trader_cls.assert_not_called()
    bot_cls.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["YES", "NO"])
async def test_async_main_rejects_void_refund_args_for_nonvoid_result(
    monkeypatch,
    available_cli_runtime_lock,
    result,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    paper = MagicMock()
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXTEST", result),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("50"),
        void_refunds_entry_fee="YES",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ):
        with pytest.raises(SystemExit, match="only valid with --resolve .* VOID"):
            await async_main()

    paper.mapped_open_market_refs.assert_not_called()
    paper.resolve_observation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refund_cents", "refunds_entry_fee"),
    [
        (None, None),
        (Decimal("50"), None),
        (None, "YES"),
        ("50", "YES"),
        (Decimal("101"), "YES"),
        (Decimal("50"), "MAYBE"),
    ],
)
async def test_async_main_resolve_void_rejects_missing_or_invalid_refund_args(
    monkeypatch,
    available_cli_runtime_lock,
    refund_cents,
    refunds_entry_fee,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    paper = MagicMock()
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXVOID", "VOID"),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=refund_cents,
        void_refunds_entry_fee=refunds_entry_fee,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ):
        with pytest.raises(SystemExit, match="VOID"):
            await async_main()

    paper.mapped_open_market_refs.assert_not_called()
    paper.resolve_observation.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_ref_count", [0, 2])
async def test_async_main_resolve_true_mode_requires_exactly_one_mapped_ref(
    monkeypatch,
    available_cli_runtime_lock,
    matching_ref_count,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST")
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = tuple(
        market_ref for _ in range(matching_ref_count)
    )
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXTEST", "YES"),
        go_live=False,
        rotate_logs=False,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch("main.VenueRoutingAuthoritativeSettlementSource") as source_cls:
        with pytest.raises(SystemExit, match="exactly one mapped market"):
            await async_main()

    paper.resolve_observation.assert_not_called()
    source_cls.assert_not_called()


@pytest.mark.asyncio
async def test_async_main_resolve_true_mode_rejects_authority_disagreement(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.POLYMARKET_US, "pm-1", "SHARED")
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = (market_ref,)
    source = MagicMock()
    source.get_settlement.return_value = _settlement_observation(
        market_ref,
        MarketOutcome.NO,
    )
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("SHARED", "YES"),
        go_live=False,
        rotate_logs=False,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch(
        "main.VenueRoutingAuthoritativeSettlementSource", return_value=source
    ), patch("main.KalshiRestClient"), patch("main.PolymarketPublicSettlementSource"):
        with pytest.raises(SystemExit, match="disagrees with authoritative NO"):
            await async_main()

    source.get_settlement.assert_called_once_with(market_ref)
    paper.resolve_observation.assert_not_called()
    paper.resolve_market.assert_not_called()
    guard = _RuntimeInstanceGuard(available_cli_runtime_lock)
    assert guard.acquire() is True
    guard.release()


@pytest.mark.asyncio
async def test_async_main_resolve_void_rejects_authority_disagreement(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.KALSHI, "KXVOID-1", "KXVOID")
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = (market_ref,)
    source = MagicMock()
    source.get_settlement.return_value = _settlement_observation(
        market_ref,
        MarketOutcome.YES,
    )
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXVOID", "VOID"),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("37.25"),
        void_refunds_entry_fee="NO",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch(
        "main.VenueRoutingAuthoritativeSettlementSource", return_value=source
    ), patch("main.KalshiRestClient"), patch("main.PolymarketPublicSettlementSource"):
        with pytest.raises(SystemExit, match="disagrees with authoritative YES"):
            await async_main()

    paper.resolve_observation.assert_not_called()
    paper.resolve_market.assert_not_called()


@pytest.mark.asyncio
async def test_async_main_resolve_void_rejects_unavailable_authority(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.KALSHI, "KXVOID-1", "KXVOID")
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = (market_ref,)
    source = MagicMock()
    source.get_settlement.return_value = None
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("KXVOID", "VOID"),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("37.25"),
        void_refunds_entry_fee="NO",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch(
        "main.VenueRoutingAuthoritativeSettlementSource", return_value=source
    ), patch("main.KalshiRestClient"), patch("main.PolymarketPublicSettlementSource"):
        with pytest.raises(SystemExit, match="no authoritative terminal settlement"):
            await async_main()

    paper.resolve_observation.assert_not_called()
    paper.resolve_market.assert_not_called()


@pytest.mark.asyncio
async def test_async_main_resolve_void_applies_authoritative_observation(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.POLYMARKET_US, "pm-void-1", "PMVOID")
    refund = VoidRefundContract(
        refund_cents_per_contract=Decimal("37.25"),
        refunds_entry_fee=False,
    )
    observation = _settlement_observation(
        market_ref,
        MarketOutcome.VOID,
        void_refund=refund,
    )
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = (market_ref,)
    paper.resolve_observation.return_value = True
    source = MagicMock()
    source.get_settlement.return_value = observation
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("PMVOID", "VOID"),
        go_live=False,
        rotate_logs=False,
        void_refund_cents_per_contract=Decimal("37.25"),
        void_refunds_entry_fee="NO",
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch(
        "main.VenueRoutingAuthoritativeSettlementSource", return_value=source
    ) as source_cls, patch("main.KalshiRestClient") as kalshi_cls, patch(
        "main.PolymarketPublicSettlementSource"
    ) as polymarket_cls:
        await async_main()

    source_cls.assert_called_once_with(
        kalshi_source=kalshi_cls.return_value,
        polymarket_source=polymarket_cls.return_value,
        void_refunds_by_market_ref={market_ref: refund},
    )
    source.get_settlement.assert_called_once_with(market_ref)
    paper.resolve_observation.assert_called_once_with(observation)
    paper.resolve_market.assert_not_called()


@pytest.mark.asyncio
async def test_async_main_resolve_true_mode_applies_authoritative_observation(
    monkeypatch,
    available_cli_runtime_lock,
):
    monkeypatch.setattr(
        cfg,
        "enable_canonical_persisted_settlement_reconciliation",
        True,
    )
    market_ref = MarketRef(Venue.POLYMARKET_US, "pm-1", "SHARED")
    observation = _settlement_observation(market_ref, MarketOutcome.YES)
    paper = MagicMock()
    paper.mapped_open_market_refs.return_value = (market_ref,)
    paper.resolve_observation.return_value = True
    source = MagicMock()
    source.get_settlement.return_value = observation
    args = Namespace(
        report=False,
        credibility=False,
        resolve=("SHARED", "YES"),
        go_live=False,
        rotate_logs=False,
    )

    with patch("main.parse_args", return_value=args), patch(
        "main.PaperTrader", return_value=paper
    ), patch(
        "main.VenueRoutingAuthoritativeSettlementSource", return_value=source
    ) as source_cls, patch("main.KalshiRestClient") as kalshi_cls, patch(
        "main.PolymarketPublicSettlementSource"
    ) as polymarket_cls:
        await async_main()

    source_cls.assert_called_once_with(
        kalshi_source=kalshi_cls.return_value,
        polymarket_source=polymarket_cls.return_value,
    )
    source.get_settlement.assert_called_once_with(market_ref)
    paper.resolve_observation.assert_called_once_with(observation)
    paper.resolve_market.assert_not_called()


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
         patch("main._configured_paper_cohort_kind", return_value="legacy_pending"), \
         caplog.at_level("INFO", logger="main"):
        guard = MagicMock()
        guard.acquire.return_value = True
        guard_cls.return_value = guard

        bot = MagicMock()
        bot.run = MagicMock()
        bot.paper_cohort.cohort_id = "legacy-pending-20260729"
        bot_cls.return_value = bot

        async def _fake_run():
            return None

        bot.run = _fake_run
        await async_main()

    assert "[BOOT]" in caplog.text
    assert "ctx=runtime" in caplog.text
    guard_cls.assert_called_once_with(lock_path)
    guard.bind_runtime_paper_cohort.assert_called_once_with(
        "legacy-pending-20260729",
        "legacy_pending",
    )


@pytest.mark.asyncio
async def test_async_main_stops_before_runtime_when_cohort_lock_binding_fails(caplog):
    with patch("main.parse_args", return_value=Namespace(report=False, credibility=False, resolve=None, go_live=False, rotate_logs=False)), \
         patch("main._BOT_RUNTIME_LOCK") as lock_path, \
         patch("main._RuntimeInstanceGuard") as guard_cls, \
         patch("main.TradingBot") as bot_cls, \
         patch("main._configured_paper_cohort_kind", return_value="legacy_pending"), \
         caplog.at_level("ERROR", logger="main"):
        guard = MagicMock()
        guard.acquire.return_value = True
        guard.bind_runtime_paper_cohort.side_effect = OSError("lock is read-only")
        guard_cls.return_value = guard

        bot = MagicMock()
        bot.paper_cohort.cohort_id = "legacy-pending-20260729"
        bot.run = AsyncMock()
        bot_cls.return_value = bot

        with pytest.raises(SystemExit) as excinfo:
            await async_main()

    assert excinfo.value.code == 1
    bot.run.assert_not_awaited()
    guard.release.assert_called_once_with()
    assert "runtime_paper_cohort_lock_binding=unavailable" in caplog.text


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
