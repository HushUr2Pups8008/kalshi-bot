from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from scripts.reconcile_kalshi_execution_ledger import main
from trading.kalshi_execution_ledger import KalshiExecutionLedger


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_order_receipt(self, order_id: str) -> dict[str, object]:
        self.calls.append(("GET_ORDER", order_id))
        return {
            "order_id": order_id,
            "user_id": "user-1",
            "client_order_id": "client-1",
            "ticker": "KXTEST-26JUL-T1",
            "outcome_side": "yes",
            "book_side": "bid",
            "type": "limit",
            "status": "executed",
            "yes_price_dollars": "0.500000",
            "no_price_dollars": "0.500000",
            "fill_count_fp": "1.00",
            "remaining_count_fp": "0.00",
            "initial_count_fp": "1.00",
            "taker_fees_dollars": "0.010000",
            "maker_fees_dollars": "0.000000",
            "taker_fill_cost_dollars": "0.010000",
            "maker_fill_cost_dollars": "0.000000",
            "subaccount_number": 0,
        }

    def get_fills_page(
        self,
        *,
        order_id: str,
        cursor: str | None = None,
        **_kwargs: object,
    ) -> tuple[list[dict[str, object]], str | None]:
        self.calls.append(("GET_FILLS", order_id, cursor))
        return (
            [
                {
                    "fill_id": "fill-1",
                    "trade_id": "fill-1",
                    "order_id": order_id,
                    "ticker": "KXTEST-26JUL-T1",
                    "market_ticker": "KXTEST-26JUL-T1",
                    "outcome_side": "yes",
                    "book_side": "bid",
                    "count_fp": "1.00",
                    "yes_price_dollars": "0.500000",
                    "no_price_dollars": "0.500000",
                    "fee_cost": "0.01",
                    "is_taker": True,
                    "ts": 1_753_356_600,
                    "subaccount_number": 0,
                }
            ],
            None,
        )


class QuarantinedFillClient(FakeClient):
    def get_fills_page(
        self,
        *,
        order_id: str,
        cursor: str | None = None,
        **kwargs: object,
    ) -> tuple[list[dict[str, object]], str | None]:
        fills, next_cursor = super().get_fills_page(
            order_id=order_id,
            cursor=cursor,
            **kwargs,
        )
        fills[0]["count_fp"] = "not-a-decimal"
        return fills, next_cursor


def test_default_cli_refuses_network_and_writes_without_constructing_dependencies(
    tmp_path: Path,
    capsys,
) -> None:
    calls: list[str] = []

    def fail_client() -> FakeClient:
        calls.append("client")
        raise AssertionError("client must not be constructed")

    def fail_ledger(_path: Path) -> KalshiExecutionLedger:
        calls.append("ledger")
        raise AssertionError("ledger must not be constructed")

    rc = main(
        ["--db-path", str(tmp_path / "ledger.db")],
        client_factory=fail_client,
        ledger_factory=fail_ledger,
    )

    assert rc == 2
    assert calls == []
    assert not (tmp_path / "ledger.db").exists()
    assert "refusing network or writes" in capsys.readouterr().err


def test_direct_script_execution_reaches_the_default_off_guard_from_any_directory(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/reconcile_kalshi_execution_ledger.py")],
        cwd=tmp_path,
        env={
            **os.environ,
            "CI": "1",
            "KALSHI_API_KEY_ID": "",
            "KALSHI_API_KEY_SECRET": "",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing network or writes" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_requires_order_id_even_when_network_and_write_are_explicit(capsys, tmp_path: Path) -> None:
    rc = main(
        ["--allow-network", "--write", "--db-path", str(tmp_path / "ledger.db")],
        client_factory=FakeClient,
    )

    assert rc == 2
    assert "--order-id" in capsys.readouterr().err
    assert not (tmp_path / "ledger.db").exists()


def test_cli_setup_failure_does_not_emit_traceback_or_create_a_ledger(tmp_path: Path, capsys) -> None:
    def fail_client() -> FakeClient:
        raise RuntimeError("invalid credentials")

    db_path = tmp_path / "ledger.db"
    rc = main(
        ["--allow-network", "--write", "--order-id", "order-1", "--db-path", str(db_path)],
        client_factory=fail_client,
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "RuntimeError" in captured.err
    assert "Traceback" not in captured.err
    assert not db_path.exists()


def test_cli_collects_only_explicit_order_after_both_irreversible_flags(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    db_path = tmp_path / "ledger.db"

    rc = main(
        [
            "--allow-network",
            "--write",
            "--order-id",
            "order-1",
            "--db-path",
            str(db_path),
        ],
        client_factory=lambda: client,
        now=lambda: "2026-07-24T10:50:00Z",
    )

    assert rc == 0
    assert client.calls == [("GET_ORDER", "order-1"), ("GET_FILLS", "order-1", None)]
    assert db_path.exists()
    out = capsys.readouterr().out
    assert '"complete_coverage": false' in out
    assert '"coverage_state": "historical_cutoff_unknown"' in out
    assert '"integrity_ok": true' in out
    assert '"source_kind": "unattributed_manual"' in out
    assert "pnl" not in out.lower()


def test_cli_returns_nonzero_and_emits_integrity_state_for_quarantined_receipt(
    tmp_path: Path,
    capsys,
) -> None:
    client = QuarantinedFillClient()
    rc = main(
        [
            "--allow-network",
            "--write",
            "--order-id",
            "order-1",
            "--db-path",
            str(tmp_path / "ledger.db"),
        ],
        client_factory=lambda: client,
        now=lambda: "2026-07-24T10:50:00Z",
    )

    assert rc == 1
    out = capsys.readouterr().out
    assert '"fill_statuses": ["quarantined"]' in out
    assert '"integrity_ok": false' in out
    assert "pnl" not in out.lower()
