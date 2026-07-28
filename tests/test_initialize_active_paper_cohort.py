"""Explicit cutover-baseline contracts for active paper cohort provisioning."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import initialize_active_paper_cohort as initializer


def test_initializer_direct_script_help_resolves_project_imports():
    completed = subprocess.run(
        [sys.executable, str(Path(initializer.__file__)), "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--legacy-starting-bankroll" in completed.stdout


def _args(*, legacy_starting_bankroll: float, confirmation: float) -> SimpleNamespace:
    return SimpleNamespace(
        cohort_id="active-20260728",
        starting_bankroll=125.0,
        legacy_starting_bankroll=legacy_starting_bankroll,
        confirm_legacy_starting_bankroll=confirmation,
        max_days_to_close=14.0,
        confirm_cohort="active-20260728",
    )


def test_initializer_uses_explicit_confirmed_legacy_baseline_not_cfg(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    manifest_path = tmp_path / "cohort.json"
    manifest_path.write_text(
        json.dumps(
            {
                "legacy_baseline_attestation": "a" * 64,
                "legacy_baseline_verification": "operator_attested_unverified",
            }
        ),
        encoding="utf-8",
    )
    cohort = SimpleNamespace(
        cohort_id="active-20260728",
        starting_bankroll=125.0,
        db_path=tmp_path / "paper_trades.db",
    )

    with patch.object(initializer, "parse_args", return_value=_args(legacy_starting_bankroll=777.0, confirmation=777.0)), patch.object(
        initializer,
        "resolve_runtime_paper_cohort",
        return_value=cohort,
    ) as resolve, patch.object(
        initializer,
        "initialize_active_paper_cohort_manifest",
        return_value=manifest_path,
    ) as initialize:
        assert initializer.main() == 0

    assert resolve.call_args.kwargs["legacy_starting_bankroll"] == pytest.approx(777.0)
    assert initialize.call_args.kwargs["legacy_starting_bankroll"] == pytest.approx(777.0)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["legacy_starting_bankroll"] == pytest.approx(777.0)
    assert receipt["legacy_baseline_verification"] == "operator_attested_unverified"


def test_initializer_requires_repeat_confirmation_before_any_provisioning():
    with patch.object(initializer, "parse_args", return_value=_args(legacy_starting_bankroll=500.0, confirmation=499.0)), patch.object(
        initializer,
        "resolve_runtime_paper_cohort",
    ) as resolve:
        with pytest.raises(SystemExit, match="confirm-legacy-starting-bankroll"):
            initializer.main()

    resolve.assert_not_called()
