"""
P-1 RED tests for the DriftCounter + halt sentinel + manual-clearance
contract (LD-5, LD-6, LD-6b) and exchange-status fail-closed behavior
(LD-4, P-7).

Locked operator decisions encoded here:
  * OQ-A → LD-6: **absolute count ≥ 1** trips halt. No ratio threshold.
  * OQ-B → LD-6b: halt persists across cycles; **no auto-retry**. Operator
    must manually delete/clear the sentinel file.

Expected RED until `kalshi.normalizer.DriftCounter` and the orchestrator
gate function (`check_exchange_open`) ship.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest


# Module-under-test — RED on import until P-2/P-7 ship.
from kalshi.normalizer import (  # type: ignore[import-not-found]  # noqa: E402
    DriftCounter,
    ExchangeState,
    UnsupportedPayloadContractError,
    check_exchange_open,
    normalize_exchange_status,
    normalize_market_list_entry,
)


# ---------------------------------------------------------------------------
# DriftCounter basic state
# ---------------------------------------------------------------------------

def test_drift_counter_starts_at_zero(tmp_path: Path):
    counter = DriftCounter(runtime_dir=tmp_path)
    assert counter.count == 0
    assert counter.halt_tripped() is False


def test_drift_counter_increments_on_unsupported_payload_contract(
    tmp_path: Path,
):
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-X", payload_hash="abc123")
    assert counter.count == 1


# ---------------------------------------------------------------------------
# OQ-A → LD-6: strict absolute count >= 1 trips halt
# ---------------------------------------------------------------------------

def test_drift_counter_strict_abs_1_trips_halt(tmp_path: Path):
    """Operator-locked: a single drift is enough to halt the cycle."""
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-X", payload_hash="abc")
    assert counter.halt_tripped() is True


def test_drift_counter_halt_writes_sentinel_file(tmp_path: Path):
    """First drift writes data/runtime/kalshi_drift_halt.json with metadata."""
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-Y", payload_hash="deadbeef" * 8)
    sentinel = tmp_path / "data" / "runtime" / "kalshi_drift_halt.json"
    assert sentinel.exists(), f"sentinel not written at {sentinel}"
    payload = json.loads(sentinel.read_text(encoding="utf-8"))
    for required_key in (
        "halt_ts", "trigger_ticker", "trigger_payload_hash", "cycle_drift_count"
    ):
        assert required_key in payload, (
            f"sentinel JSON missing required key {required_key!r}; "
            f"got {sorted(payload.keys())}"
        )
    assert payload["trigger_ticker"] == "KXTEST-Y"
    assert payload["trigger_payload_hash"] == "deadbeef" * 8
    assert payload["cycle_drift_count"] >= 1
    # halt_ts must parse as ISO-8601.
    halt_ts = payload["halt_ts"]
    datetime.fromisoformat(halt_ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# OQ-B → LD-6b: halt persists, no auto-retry
# ---------------------------------------------------------------------------

def test_drift_counter_halt_persists_no_auto_retry(tmp_path: Path):
    """Re-instantiating the counter loads the existing sentinel — halt sticks."""
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-A", payload_hash="hash1")
    assert counter.halt_tripped() is True

    # Simulate next-cycle entry — fresh counter instance against same runtime.
    counter2 = DriftCounter(runtime_dir=tmp_path)
    assert counter2.halt_tripped() is True, (
        "halt did not persist across DriftCounter instances — auto-retry leak"
    )


def test_drift_counter_halt_clears_on_sentinel_delete(tmp_path: Path):
    """Operator manually clears the sentinel — halt releases."""
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-B", payload_hash="hash2")
    sentinel = tmp_path / "data" / "runtime" / "kalshi_drift_halt.json"
    assert sentinel.exists()

    sentinel.unlink()

    counter3 = DriftCounter(runtime_dir=tmp_path)
    assert counter3.halt_tripped() is False, (
        "deleting the sentinel did not release the halt"
    )


# ---------------------------------------------------------------------------
# Skip-log carries payload_hash + reason
# ---------------------------------------------------------------------------

def test_drift_counter_skip_log_carries_payload_hash_and_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    """Each drift event emits a structured log carrying both
    `reason="unsupported_payload_contract"` and the originating payload_hash."""
    caplog.set_level(logging.INFO)
    counter = DriftCounter(runtime_dir=tmp_path)
    counter.record_drift(ticker="KXTEST-C", payload_hash="hashXYZ")
    matching = [
        r for r in caplog.records
        if "unsupported_payload_contract" in r.getMessage()
        and "hashXYZ" in r.getMessage()
    ]
    if not matching:
        # Allow structured logs that put fields in record attrs rather than
        # the rendered message.
        matching = [
            r for r in caplog.records
            if getattr(r, "reason", None) == "unsupported_payload_contract"
            and getattr(r, "payload_hash", None) == "hashXYZ"
        ]
    assert matching, (
        "no log record carried both reason='unsupported_payload_contract' "
        "and payload_hash='hashXYZ'"
    )


# ---------------------------------------------------------------------------
# UnsupportedPayloadContractError contract
# ---------------------------------------------------------------------------

def test_unsupported_payload_contract_error_subclass():
    """Must subclass Exception. May also subclass ValueError — either is OK,
    but it must be catchable as Exception."""
    assert issubclass(UnsupportedPayloadContractError, Exception)


def test_normalizer_unknown_contract_raises_unsupported_payload_contract_error():
    """A payload carrying a *new unrecognized* price contract (e.g.,
    `yes_price_basis_points`) must raise UnsupportedPayloadContractError.

    Distinct from P0-REST-003 (all-fields-absent → price_available=False).
    The presence of an unrecognized price marker means "the payload IS
    asserting a price, in a shape we don't understand" — fail loudly.
    """
    payload = {
        "ticker": "X-UNKNOWN-CONTRACT",
        "title": "Unknown price contract",
        # No *_dollars, no legacy *_bid/*_ask cents — instead, a hypothetical
        # new contract that drift-introduces basis points.
        "yes_price_basis_points": 3800,
        "no_price_basis_points": 6200,
        "status": "active",
        "close_time": "2027-01-01T00:00:00Z",
    }
    with pytest.raises(UnsupportedPayloadContractError):
        normalize_market_list_entry(payload)


# ---------------------------------------------------------------------------
# LD-4 / P-7 — Exchange status fail-closed
# ---------------------------------------------------------------------------

def test_exchange_status_fail_closed_when_exchange_closed():
    """Orchestrator gate returns False when exchange is not open."""
    state = ExchangeState(
        exchange_active=False, trading_active=False, is_open=False
    )
    assert check_exchange_open(state) is False


def test_exchange_status_fail_closed_when_only_exchange_open_trading_closed():
    """Both flags must be True for is_open — partial-open is still closed."""
    state = ExchangeState(
        exchange_active=True, trading_active=False, is_open=False
    )
    assert check_exchange_open(state) is False


def test_exchange_status_fail_closed_on_fetch_failure():
    """Empty or None payload either raises UnsupportedPayloadContractError
    or returns an is_open=False ExchangeState. Either is acceptable as
    long as downstream cannot proceed."""
    for bad_payload in (None, {}):
        try:
            state = normalize_exchange_status(bad_payload)
        except (UnsupportedPayloadContractError, TypeError, AttributeError):
            continue  # acceptable: raised on bad input
        assert state.is_open is False, (
            f"empty/None payload yielded is_open=True (state={state}) — "
            "fail-closed contract broken"
        )


def test_exchange_status_open_payload_returns_open_true():
    """Sanity counterpart: real (active, active) payload yields is_open=True."""
    state = normalize_exchange_status({
        "exchange_active": True, "trading_active": True,
    })
    assert state.is_open is True
