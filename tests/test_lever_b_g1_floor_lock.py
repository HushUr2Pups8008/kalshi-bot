"""Strict-xfail harness for Lever B G1 0.04 floor lock.

Spec: docs/superpowers/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md §3.
Status: pre-loaded during PROFIT-PHASE2-001 soak; xpasses on the Wave-3
Lever B deploy commit when the constants move to 0.04 / 0.08.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LOCK: G1=0.04 lands in Wave-3 >= 2026-06-06; xpass on deploy "
        "commit triggers marker removal in same hunk"
    ),
)
def test_g1_confidence_threshold_locked_at_0_04():
    """LOCK: tasks/trade_readiness_gate.py:G1_CONFIDENCE_THRESHOLD must equal 0.04 post-Lever-B-deploy."""
    from tasks.trade_readiness_gate import G1_CONFIDENCE_THRESHOLD

    assert G1_CONFIDENCE_THRESHOLD == 0.04, (
        f"Lever B floor expected 0.04; got {G1_CONFIDENCE_THRESHOLD}. "
        "If this floor was sized to a different value, revise the LOCK addendum before landing."
    )


@pytest.mark.xfail(
    strict=True,
    reason="LOCK: G1 failsafe = 0.08 (2x primary) lands with G1=0.04",
)
def test_g1_failsafe_threshold_locked_at_0_08():
    """LOCK: G1_FAILSAFE_CONFIDENCE_THRESHOLD must equal 0.08."""
    from tasks.trade_readiness_gate import G1_FAILSAFE_CONFIDENCE_THRESHOLD

    assert G1_FAILSAFE_CONFIDENCE_THRESHOLD == 0.08, (
        f"Lever B failsafe expected 0.08; got {G1_FAILSAFE_CONFIDENCE_THRESHOLD}."
    )


@pytest.mark.xfail(
    strict=True,
    reason="LOCK: 2x ratio between failsafe and primary preserves regime-uncertainty headroom",
)
def test_g1_failsafe_to_primary_ratio_is_2x():
    """LOCK: failsafe = 2 * primary at the locked 0.04 / 0.08 values."""
    from tasks.trade_readiness_gate import (
        G1_CONFIDENCE_THRESHOLD,
        G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    )

    assert (
        G1_CONFIDENCE_THRESHOLD == 0.04
        and G1_FAILSAFE_CONFIDENCE_THRESHOLD == pytest.approx(2 * G1_CONFIDENCE_THRESHOLD)
    ), (
        "failsafe-to-primary invariant expected locked 0.04 base with 2.0 ratio; got "
        f"base={G1_CONFIDENCE_THRESHOLD}, "
        f"ratio={G1_FAILSAFE_CONFIDENCE_THRESHOLD / G1_CONFIDENCE_THRESHOLD:.3f}."
    )
