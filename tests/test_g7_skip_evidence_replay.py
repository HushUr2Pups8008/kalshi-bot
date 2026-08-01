from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

from scripts.g7_skip_evidence_replay import build_report
from trading.g7_skip_evidence import (
    G7SkipEvidenceRecord,
    G7SkipEvidenceStore,
    read_g7_skip_evidence_records,
)


NOW = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)


def _record(
    *,
    decision_key: str,
    status: str,
    executable_notional: float | None = None,
    failures: tuple[str, ...] = ("G7_zero_liquidity",),
) -> G7SkipEvidenceRecord:
    if status == "observed":
        assert executable_notional is not None
        execution_liquidity: dict[str, object] = {
            "source": "kalshi_orderbook",
            "side": "yes",
            "limit_price": 0.50,
            "best_price": 0.50,
            "executable_quantity": executable_notional / 0.50,
            "executable_notional": executable_notional,
            "as_of": "2026-07-31T14:59:00Z",
            "raw_payload_hash": "a" * 64,
        }
    elif status == "unavailable":
        execution_liquidity = {
            "source": "kalshi_orderbook",
            "status": "unavailable",
            "reason": "RuntimeError",
        }
    else:
        execution_liquidity = {
            "status": "not_queried",
            "reason": "execution_liquidity_not_queried",
        }

    return G7SkipEvidenceRecord(
        decision_key=decision_key,
        lifecycle_id=decision_key,
        decision_at=NOW,
        captured_at=NOW,
        venue="kalshi",
        market_ticker="KXG7-26JUL31-T50",
        intended_side="yes",
        market_family="KXG7",
        runtime_paper_cohort_id="legacy-pending-20260729",
        runtime_paper_cohort_kind="legacy_pending",
        ordered_failures=failures,
        g7_failures=failures,
        trade_blocked_reason=failures[0],
        g7_inputs={
            "minimum_market_liquidity_dollars": 1.0,
            "maximum_open_exposure_drawdown_pct": 0.20,
            "market_liquidity_dollars": executable_notional,
            "market_price_momentum_cents": -1.0
            if "G7_adverse_price_momentum" in failures
            else 0.0,
            "intended_side": "yes",
            "open_exposure_drawdown_pct": 0.0,
        },
        g7_results={
            "ordered_failures": list(failures),
            "g7_failures": list(failures),
            "non_drawdown_g7_failures": list(failures),
            "trade_blocked_reason": failures[0],
        },
        liquidity_evidence_status=status,  # type: ignore[arg-type]
        execution_liquidity=execution_liquidity,
    )


def test_read_only_report_classifies_evidence_without_financial_inference(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "g7_skip_evidence.db"
    store = G7SkipEvidenceStore(db_path)
    store.initialize(applied_at=NOW)
    records = (
        _record(decision_key="observed-insufficient", status="observed", executable_notional=0.0),
        _record(
            decision_key="observed-sufficient",
            status="observed",
            executable_notional=1.0,
            failures=("G7_adverse_price_momentum",),
        ),
        _record(decision_key="unavailable", status="unavailable"),
        _record(
            decision_key="not-queried",
            status="not_queried",
            failures=("G7_adverse_price_momentum",),
        ),
    )
    for record in records:
        assert store.append_record(record).status == "inserted"

    report = build_report(read_g7_skip_evidence_records(db_path))

    assert report["coverage"]["receipt_rows"] == 4
    assert report["coverage"]["classification_counts"] == {
        "not_queried_liquidity_evidence": 1,
        "observed_insufficient_liquidity": 1,
        "observed_sufficient_liquidity": 1,
        "unavailable_liquidity_evidence": 1,
    }
    assert report["manifest"]["financial_evaluation"] == "not_performed"
    assert report["manifest"]["not_trade_or_pnl_evidence"] is True
    assert report["promotion"]["eligible"] is False
    assert report["report_sha256"]


def test_empty_report_remains_ineligible_without_receipts() -> None:
    report = build_report(())

    assert report["coverage"]["receipt_rows"] == 0
    assert report["promotion"] == {
        "eligible": False,
        "failure_reasons": [
            "diagnostic_only_scope",
            "financial_evidence_not_evaluated_by_g7_receipt_report",
            "repeatable_profitability_not_established_by_g7_receipt_report",
        ],
    }


def test_replay_script_runs_directly_outside_repository_root(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "g7_skip_evidence_replay.py"

    completed = subprocess.run(
        [sys.executable, "-E", str(script), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Read-only G7 skip evidence classification report" in completed.stdout
