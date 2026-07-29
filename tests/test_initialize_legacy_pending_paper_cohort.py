"""Operator-confirmation contracts for legacy-pending paper provisioning."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import initialize_legacy_pending_paper_cohort as initializer


def test_initializer_direct_script_help_resolves_project_imports():
    completed = subprocess.run(
        [sys.executable, str(Path(initializer.__file__)), "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--expected-legacy-open-trade-count" in completed.stdout
    assert "--expected-legacy-open-rows-sha256" in completed.stdout
    assert "--confirm-paper-only" in completed.stdout


def test_parse_args_requires_pending_open_exposure_and_paper_only_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "initialize_legacy_pending_paper_cohort.py",
            "--cohort-id",
            "pending-20260728",
            "--starting-bankroll",
            "125",
            "--legacy-starting-bankroll",
            "777",
            "--confirm-legacy-starting-bankroll",
            "777",
            "--expected-legacy-open-trade-count",
            "11",
            "--expected-legacy-open-rows-sha256",
            "a" * 64,
            "--confirm-cohort",
            "pending-20260728",
            "--confirm-paper-only",
            "PAPER_ONLY",
        ],
    )

    args = initializer.parse_args()

    assert args.expected_legacy_open_trade_count == 11
    assert args.expected_legacy_open_rows_sha256 == "a" * 64
    assert args.confirm_paper_only == "PAPER_ONLY"


def _args(
    *,
    legacy_starting_bankroll: float = 777.0,
    confirmation: float = 777.0,
    confirm_cohort: str = "pending-20260728",
    confirm_paper_only: str = "PAPER_ONLY",
    expected_trade_count: int = 11,
    expected_rows_sha256: str = "a" * 64,
) -> SimpleNamespace:
    return SimpleNamespace(
        cohort_id="pending-20260728",
        starting_bankroll=125.0,
        legacy_starting_bankroll=legacy_starting_bankroll,
        confirm_legacy_starting_bankroll=confirmation,
        max_days_to_close=14.0,
        expected_legacy_open_trade_count=expected_trade_count,
        expected_legacy_open_rows_sha256=expected_rows_sha256,
        confirm_cohort=confirm_cohort,
        confirm_paper_only=confirm_paper_only,
    )


def _open_exposure() -> SimpleNamespace:
    return SimpleNamespace(unresolved_trade_count=11, rows_sha256="a" * 64)


def test_initializer_requires_literal_paper_only_confirmation_before_reading_state():
    args = _args(confirm_paper_only="paper-only")

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
    ) as fingerprint:
        with pytest.raises(SystemExit, match="confirm-paper-only"):
            initializer.main()

    fingerprint.assert_not_called()


def test_initializer_requires_repeat_legacy_baseline_before_reading_state():
    args = _args(confirmation=776.0)

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
    ) as fingerprint:
        with pytest.raises(SystemExit, match="confirm-legacy-starting-bankroll"):
            initializer.main()

    fingerprint.assert_not_called()


def test_initializer_requires_repeat_cohort_before_reading_state():
    args = _args(confirm_cohort="another-pending-cohort")

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
    ) as fingerprint:
        with pytest.raises(SystemExit, match="confirm-cohort"):
            initializer.main()

    fingerprint.assert_not_called()


def test_initializer_requires_expected_open_exposure_before_provisioning():
    args = _args(expected_trade_count=10)

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
        return_value=_open_exposure(),
    ), patch.object(initializer, "resolve_runtime_paper_cohort") as resolve, patch.object(
        initializer,
        "initialize_legacy_pending_paper_cohort_manifest",
    ) as initialize:
        with pytest.raises(SystemExit, match="expected-legacy-open-trade-count"):
            initializer.main()

    resolve.assert_not_called()
    initialize.assert_not_called()


def test_initializer_refuses_a_legacy_runtime_cohort():
    args = _args()
    legacy = SimpleNamespace(cohort_id="legacy")

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
        return_value=_open_exposure(),
    ), patch.object(
        initializer,
        "resolve_runtime_paper_cohort",
        return_value=legacy,
    ), patch.object(
        initializer,
        "initialize_legacy_pending_paper_cohort_manifest",
    ) as initialize:
        with pytest.raises(SystemExit, match="Refusing to provision the legacy cohort"):
            initializer.main()

    initialize.assert_not_called()


def test_initializer_binds_pending_kind_and_explicit_open_exposure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    args = _args()
    cohort = SimpleNamespace(
        cohort_id="pending-20260728",
        starting_bankroll=125.0,
        db_path=tmp_path / "paper_trades.db",
    )
    manifest_path = tmp_path / "cohort.json"

    with patch.object(initializer, "parse_args", return_value=args), patch.object(
        initializer,
        "legacy_open_exposure_fingerprint",
        return_value=_open_exposure(),
    ), patch.object(
        initializer,
        "resolve_runtime_paper_cohort",
        return_value=cohort,
    ) as resolve, patch.object(
        initializer,
        "initialize_legacy_pending_paper_cohort_manifest",
        return_value=manifest_path,
    ) as initialize:
        assert initializer.main() == 0

    assert resolve.call_args.kwargs["cohort_kind"] == "legacy_pending"
    assert initialize.call_args.kwargs["legacy_starting_bankroll"] == pytest.approx(777.0)
    assert initialize.call_args.kwargs["expected_legacy_open_trade_count"] == 11
    assert initialize.call_args.kwargs["expected_legacy_open_rows_sha256"] == "a" * 64
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["cohort_kind"] == "legacy_pending"
    assert receipt["legacy_open_trade_count"] == 11
    assert receipt["legacy_open_rows_sha256"] == "a" * 64
