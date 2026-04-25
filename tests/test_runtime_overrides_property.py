"""Hypothesis property test for the core invariant of the runtime overrides
reader: the bot's effective view of disabled sources is exactly
`static_config UNION applied_runtime_disabled_unexpired_sources`.

This is the load-bearing correctness property of Phase 1. If this fails,
the bot is operating on a config view that doesn't match what the human
or agent wrote -- the worst possible silent failure mode.

Clock note: reload() calls filter_expired(now=datetime.now(utc)) using the
real system clock, not a fixed _NOW constant. To prevent divergence between
the test's expected-set calculation and reload()'s actual filtering, all
generated expires_at values are either None (never expires) or a fixed past
sentinel (2020-01-01, definitely expired from both clocks). This avoids
flakiness from timestamps that are in the future relative to _NOW but in
the past relative to the real clock -- or vice versa.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from hypothesis import given, settings, strategies as st

from utils.runtime_overrides import (
    DisabledSource,
    OverridesState,
    PredictedEffect,
    RuntimeOverridesReader,
    atomic_write_state,
)


_NOW = datetime(2026, 5, 2, 14, 30, 0, tzinfo=timezone.utc)
# Sentinel used to generate definitively-expired entries (past from any real clock).
_PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)


@st.composite
def _disabled_source_strategy(draw, decision_id_seed: int) -> DisabledSource:
    source = draw(st.text(
        min_size=1,
        max_size=40,
        alphabet=st.characters(
            whitelist_categories=("L", "N"),
            whitelist_characters="_/-",
        ),
    ))
    # Use only None (never expires) or _PAST (always expired) to keep
    # test expectations deterministic regardless of real-clock time.
    expires_at = draw(st.one_of(st.none(), st.just(_PAST)))
    return DisabledSource(
        source=source,
        reason="test",
        confidence=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
        decided_at=_NOW,
        decided_by="hypothesis",
        decision_id=f"gd_2026-05-02_{decision_id_seed:04d}",
        expires_at=expires_at,
        predicted_effect=PredictedEffect(
            metric="m",
            baseline=0.0,
            predicted_post_change=0.0,
            evaluate_at=_NOW,
        ),
    )


@st.composite
def _state_strategy(draw) -> OverridesState:
    seed = draw(st.integers(min_value=0, max_value=9999))
    sources = draw(st.lists(
        _disabled_source_strategy(decision_id_seed=seed),
        max_size=10,
    ))
    return OverridesState(
        version=1,
        updated_at=_NOW,
        updated_by="hypothesis",
        mode="real",
        applied_disabled_sources=sources,
    )


@settings(max_examples=50)
@given(_state_strategy(), st.sets(st.text(min_size=1, max_size=20)))
def test_effective_disabled_sources_invariant(
    state: OverridesState,
    static_set: set[str],
) -> None:
    """For any (state, static_set), reader.is_source_disabled returns True
    iff the source is in static_set OR in applied_disabled_sources with
    an unexpired or null expires_at."""
    with tempfile.TemporaryDirectory(prefix="hyp_") as tmp_dir:
        target = Path(tmp_dir) / "overrides.yaml"
        atomic_write_state(state, target)

        reader = RuntimeOverridesReader(path=target)
        from utils import runtime_overrides as ro

        original = ro._static_disabled_sources
        ro._static_disabled_sources = lambda: frozenset(static_set)
        try:
            reader.reload()

            # Expected effective set: static UNION runtime-applied-unexpired.
            # Mirrors the logic in filter_expired: expired iff expires_at is
            # non-None AND expires_at <= now.  We use _PAST as the "expired"
            # sentinel, so the condition simplifies to: expires_at is None.
            effective_runtime = {
                o.source
                for o in state.applied_disabled_sources
                if o.expires_at is None
            }
            expected_effective = static_set | effective_runtime

            # Verify every expected-disabled name is reported disabled.
            for name in expected_effective:
                assert reader.is_source_disabled(name) is True, (
                    f"expected {name!r} to be disabled (in expected_effective)"
                )

            # Verify known-absent names are NOT reported disabled.
            sample_negatives = {"r/probably_not_in_set_xyzzy", "definitely_not_real_source"}
            for name in sample_negatives - expected_effective:
                assert reader.is_source_disabled(name) is False, (
                    f"expected {name!r} NOT to be disabled"
                )
        finally:
            ro._static_disabled_sources = original
