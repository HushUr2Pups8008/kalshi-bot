"""Atomic-write tests for runtime_overrides.

The agent (Phase 2+) and humans use this helper to write the YAML file
without ever leaving a partial file visible to the bot reader. This is
load-bearing for the no-corruption-on-concurrent-access guarantee.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from utils.runtime_overrides import (
    DisabledKeyword,
    DisabledSource,
    OverridesState,
    PredictedEffect,
    ThresholdOverride,
    atomic_write_state,
    load_from_disk,
)


def _utc() -> datetime:
    return datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)


def _make_state(updated_by: str) -> OverridesState:
    return OverridesState(
        version=1,
        updated_at=_utc(),
        updated_by=updated_by,
        mode="shadow",
    )


class TestAtomicWriteState:
    def test_writes_valid_yaml_round_trip(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        # Build a non-trivial state covering all sections to catch regressions
        # where the serializer silently drops fields.
        decided_at = _utc()
        evaluate_at = datetime(2026, 5, 9, 14, 30, 0, tzinfo=timezone.utc)
        pe = PredictedEffect(
            metric="anchor_rate",
            baseline=0.99,
            predicted_post_change=0.85,
            evaluate_at=evaluate_at,
        )
        ds = DisabledSource(
            source="r/Turkey",
            reason="zero matches over 7d",
            confidence=0.94,
            decided_at=decided_at,
            decided_by="agent",
            decision_id="gd_2026-05-02_0001",
            expires_at=None,
            predicted_effect=pe,
        )
        dk = DisabledKeyword(
            keyword="trump may deadline",
            reason="time-bounded",
            confidence=0.82,
            decided_at=decided_at,
            decided_by="agent",
            decision_id="gd_2026-05-02_0002",
            expires_at=evaluate_at,
            predicted_effect=pe,
        )
        to = ThresholdOverride(
            path="EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA",
            value=21600,
            reason="slow cadence",
            confidence=0.71,
            decided_at=decided_at,
            decided_by="agent",
            decision_id="gd_2026-05-02_0003",
            expires_at=None,
            predicted_effect=pe,
        )
        state = OverridesState(
            version=1,
            updated_at=_utc(),
            updated_by="test-writer",
            mode="shadow",
            applied_disabled_sources=[ds],
            applied_disabled_keywords=[dk],
            applied_threshold_overrides=[to],
            last_applied_batch={"batch_id": "gd_2026-05-02_batch_001", "applied_count": 3},
        )
        atomic_write_state(state, target)
        assert target.exists()

        loaded = load_from_disk(target)
        # Top-level scalars
        assert loaded.version == state.version
        assert loaded.updated_at == state.updated_at
        assert loaded.updated_by == state.updated_by
        assert loaded.mode == state.mode
        # Applied lists -- check counts AND key fields of each entry
        assert len(loaded.applied_disabled_sources) == 1
        assert loaded.applied_disabled_sources[0].source == "r/Turkey"
        assert loaded.applied_disabled_sources[0].confidence == 0.94
        assert loaded.applied_disabled_sources[0].decision_id == "gd_2026-05-02_0001"
        assert loaded.applied_disabled_sources[0].predicted_effect.evaluate_at == evaluate_at
        assert len(loaded.applied_disabled_keywords) == 1
        assert loaded.applied_disabled_keywords[0].keyword == "trump may deadline"
        assert loaded.applied_disabled_keywords[0].expires_at == evaluate_at
        assert len(loaded.applied_threshold_overrides) == 1
        assert loaded.applied_threshold_overrides[0].path == "EARLY_MAX_NEWS_AGE_BY_SOURCE.IAEA"
        assert loaded.applied_threshold_overrides[0].value == 21600
        # Proposed lists empty
        assert loaded.proposed_disabled_sources == []
        assert loaded.proposed_disabled_keywords == []
        assert loaded.proposed_threshold_overrides == []
        # last_applied_batch round-trips as dict
        assert loaded.last_applied_batch == {"batch_id": "gd_2026-05-02_batch_001", "applied_count": 3}

    def test_no_temp_file_left_behind_on_success(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("a"), target)
        siblings = list(tmp_path.iterdir())
        # Only the target file. No .tmp / .partial / etc.
        assert siblings == [target]

    def test_existing_file_replaced_atomically(self, tmp_path: Path):
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)
        atomic_write_state(_make_state("second"), target)
        loaded = load_from_disk(target)
        assert loaded.updated_by == "second"

    def test_concurrent_read_during_write_never_truncated(self, tmp_path: Path):
        """A reader running in parallel with a writer must always see a
        complete, valid file -- never a half-written one.

        This test runs a writer in a tight loop while a reader runs in
        parallel and asserts the loaded state is always valid.
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("initial"), target)

        stop = threading.Event()
        errors: list[Exception] = []
        write_count = [0]  # mutable container so writer thread can update

        def writer():
            try:
                count = 0
                while not stop.is_set():
                    atomic_write_state(_make_state(f"writer_{count}"), target)
                    count += 1
                write_count[0] = count
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                while not stop.is_set():
                    state = load_from_disk(target)
                    assert isinstance(state, OverridesState)
                    assert state.version == 1
                    # Brief pause so we don't pin a CPU
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),  # two readers to stress more
        ]
        for t in threads:
            t.start()

        # Run for 0.5s of contention
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)

        assert errors == [], f"concurrent access produced errors: {errors}"
        assert write_count[0] > 0, (
            "writer thread did not complete any iterations -- the test would "
            "have passed vacuously without exercising the race window"
        )


class TestAtomicWriteFailureModes:
    def test_replace_failure_preserves_previous_file(self, tmp_path: Path, monkeypatch):
        """If os.replace raises mid-write, the target on disk must still be
        the previous valid version -- never empty, never partial.
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)
        original_bytes = target.read_bytes()

        import utils.runtime_overrides as mod

        def boom(*_args, **_kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod._os, "replace", boom)

        with pytest.raises(OSError, match="simulated rename failure"):
            atomic_write_state(_make_state("second"), target)

        # Target must still exist and contain the original content unchanged.
        assert target.exists()
        assert target.read_bytes() == original_bytes

        # And no .tmp file should be left behind in user-visible state for
        # production code -- but acceptable here since the rename failed
        # mid-flight. Just confirm the target itself is intact.
        loaded = load_from_disk(target)
        assert loaded.updated_by == "first"

    def test_temp_file_cleaned_up_on_replace_failure(self, tmp_path: Path, monkeypatch):
        """If os.replace fails, the implementation should remove the orphan
        temp file so subsequent writes don't trip over it. (Defensive: the
        cleanup is best-effort -- if it can't remove the temp, the next
        successful write will overwrite it anyway.)
        """
        target = tmp_path / "overrides.yaml"
        atomic_write_state(_make_state("first"), target)

        import utils.runtime_overrides as mod

        def boom(*_args, **_kwargs):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(mod._os, "replace", boom)

        with pytest.raises(OSError):
            atomic_write_state(_make_state("second"), target)

        # The implementation should have cleaned up its own temp file.
        siblings = sorted(p.name for p in tmp_path.iterdir())
        assert siblings == ["overrides.yaml"], f"orphan temp file left behind: {siblings}"
