"""Tests for the python -m utils.runtime_overrides CLI shim."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


VALID_YAML = textwrap.dedent("""
    version: 1
    updated_at: "2026-05-02T14:30:00+00:00"
    updated_by: "test"
    mode: shadow
    applied:
      disabled_sources:
        - source: "r/Test"
          reason: "x"
          confidence: 0.9
          decided_at: "2026-05-02T14:30:00+00:00"
          decided_by: "test"
          decision_id: "gd_2026-05-02_0099"
          expires_at: null
          predicted_effect:
            metric: "m"
            baseline: 0
            predicted_post_change: 0
            evaluate_at: "2026-05-09T14:30:00+00:00"
      disabled_keywords: []
      threshold_overrides: []
    proposed:
      disabled_sources: []
      disabled_keywords: []
      threshold_overrides: []
""").lstrip()


def _run_cli(args: list[str], path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "utils.runtime_overrides", *args],
        env={"OVERRIDES_PATH": str(path), "PYTHONPATH": str(Path(__file__).parent.parent)},
        capture_output=True, text=True, timeout=10,
    )


class TestCliStatus:
    def test_status_existing_file(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--status"], p)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "r/Test" in result.stdout
        assert "shadow" in result.stdout

    def test_status_missing_file(self, tmp_path: Path):
        p = tmp_path / "missing.yaml"
        result = _run_cli(["--status"], p)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "no overrides file" in result.stdout.lower() or "default" in result.stdout.lower()


class TestCliValidate:
    def test_validate_valid_file_returns_0(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--validate", str(p)], path=p)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "valid" in result.stdout.lower()

    def test_validate_invalid_file_returns_2(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("not: valid: yaml: : :")
        result = _run_cli(["--validate", str(p)], path=p)
        assert result.returncode == 2
        assert "invalid" in result.stdout.lower() or "invalid" in result.stderr.lower()


class TestCliRevertBatch:
    def test_revert_drops_overrides_for_matching_batch_id(self, tmp_path: Path):
        # Build a state with two batches; revert one
        from utils.runtime_overrides import (
            DisabledSource, OverridesState, PredictedEffect, atomic_write_state,
        )
        from datetime import datetime, timezone
        now = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
        eff = PredictedEffect(metric="m", baseline=0, predicted_post_change=0, evaluate_at=now)

        s1 = DisabledSource(
            source="r/A", reason="x", confidence=0.9, decided_at=now,
            decided_by="t", decision_id="gd_2026-05-02_0001",
            expires_at=None, predicted_effect=eff,
        )
        s2 = DisabledSource(
            source="r/B", reason="x", confidence=0.9, decided_at=now,
            decided_by="t", decision_id="gd_2026-05-02_0002",
            expires_at=None, predicted_effect=eff,
        )
        state = OverridesState(
            version=1, updated_at=now, updated_by="t", mode="real",
            applied_disabled_sources=[s1, s2],
            last_applied_batch={
                "batch_id": "gb_2026-05-02_0001",
                "decision_ids": ["gd_2026-05-02_0001"],
            },
        )
        p = tmp_path / "overrides.yaml"
        atomic_write_state(state, p)

        # Revert the batch -- s1 should drop, s2 stays
        result = _run_cli(["--revert-batch", "gb_2026-05-02_0001"], path=p)
        assert result.returncode == 0, f"stderr: {result.stderr}"

        from utils.runtime_overrides import load_from_disk
        new_state = load_from_disk(p)
        assert {o.source for o in new_state.applied_disabled_sources} == {"r/B"}

    def test_revert_unknown_batch_returns_1(self, tmp_path: Path):
        p = tmp_path / "overrides.yaml"
        p.write_text(VALID_YAML)
        result = _run_cli(["--revert-batch", "gb_does_not_exist"], path=p)
        assert result.returncode == 1
        assert "no batch" in result.stdout.lower() or "not found" in result.stdout.lower()
