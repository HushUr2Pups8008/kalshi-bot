"""Pre-load harness for Lever B G1 calibration deploy.

Spec: docs/superpowers/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md
Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
Wave-3 if Wave-2 (A.1+) stalls. Lowers `G1_CONFIDENCE_THRESHOLD`
from 0.05 → 0.04 (and proportionally G1_FAILSAFE from 0.10 → 0.08)
in `tasks/trade_readiness_gate.py` per the spec §4 conservative-half-
step recommendation.

Per spec §3 + Codex 2026-05-03 G1 admittance counterfactual: this
is an ATTRIBUTION / CALIBRATION lever, not an edge-production one.
Loosening G1 to 0.04 admits 32/197 G1-killed candidates but only
1 of those clears the paper_min_edge=0.02 floor. Predicted lift:
1-2 PAPER_TRADE / 14 d.

Strict-xfail today (G1 still at 0.05); flips xpass on the deploy
commit, forcing marker removal in the same hunk.
"""

from __future__ import annotations

import pytest


_LEVER_B_G1_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever B G1 calibration tightening not yet landed. "
    "Spec §4 recommends conservative half-step: G1 = 0.04, G1_FAILSAFE = "
    "0.08. Lands in Wave 3 if Wave 2 (A.1+) stalls. Trips on the deploy "
    "commit; remove the marker in the same hunk."
)


@pytest.mark.xfail(reason=_LEVER_B_G1_XFAIL_REASON, strict=True)
def test_g1_confidence_threshold_lowered_to_spec_value():
    """Pin the post-Lever-B outcome that `G1_CONFIDENCE_THRESHOLD` is
    lowered from 0.05 to 0.04 per spec §4. The exact value is operator-
    tunable: the spec ranks 0.04 (conservative) > 0.03 (moderate) >
    0.025 (aggressive); operator picks at deploy time. Test passes if
    the value is in the conservative range [0.025, 0.045)."""
    from tasks.trade_readiness_gate import G1_CONFIDENCE_THRESHOLD
    assert 0.025 <= G1_CONFIDENCE_THRESHOLD < 0.045, (
        f"G1_CONFIDENCE_THRESHOLD = {G1_CONFIDENCE_THRESHOLD!r}; spec §4 "
        f"recommends 0.04 (conservative range [0.025, 0.045)). "
        f"If a value outside this range is chosen at deploy, update this "
        f"test to match the deploy decision."
    )


def test_g1_failsafe_threshold_proportionally_scaled():
    """Permanent invariant (not xfail-pinned): `G1_FAILSAFE_CONFIDENCE_THRESHOLD`
    must always be ~2× `G1_CONFIDENCE_THRESHOLD`. Today: G1 = 0.05,
    FAILSAFE = 0.10 (2× base) — passes. Post-Lever-B: G1 = 0.04 →
    FAILSAFE = 0.08 (still 2×) — must still pass. Catches a deploy
    that lowers G1 without proportionally lowering FAILSAFE (which
    would inadvertently widen the gap between base and failsafe gates
    and re-introduce the original 0.05/0.35 mis-calibration this spec
    fixes)."""
    from tasks.trade_readiness_gate import (
        G1_CONFIDENCE_THRESHOLD,
        G1_FAILSAFE_CONFIDENCE_THRESHOLD,
    )
    assert G1_FAILSAFE_CONFIDENCE_THRESHOLD > G1_CONFIDENCE_THRESHOLD, (
        f"Failsafe ({G1_FAILSAFE_CONFIDENCE_THRESHOLD}) must be strictly "
        f"greater than base ({G1_CONFIDENCE_THRESHOLD}). The 2× relationship "
        f"is load-bearing per spec §4."
    )
    ratio = G1_FAILSAFE_CONFIDENCE_THRESHOLD / G1_CONFIDENCE_THRESHOLD
    assert 1.5 <= ratio <= 2.5, (
        f"failsafe/base ratio = {ratio:.2f}; spec §4 sets ~2× (G1 = 0.04, "
        f"FAILSAFE = 0.08; or G1 = 0.05, FAILSAFE = 0.10). Ratio outside "
        f"[1.5, 2.5] suggests scale was not preserved at deploy."
    )


def test_g1_constants_present_in_decision_blender_today():
    """Positive control: `G1_CONFIDENCE_THRESHOLD` and
    `G1_FAILSAFE_CONFIDENCE_THRESHOLD` continue to exist in
    `tasks/trade_readiness_gate.py`. Catches a regression where a
    refactor renamed or removed the constants."""
    import tasks.trade_readiness_gate as db
    assert hasattr(db, "G1_CONFIDENCE_THRESHOLD"), (
        "`G1_CONFIDENCE_THRESHOLD` removed from tasks.trade_readiness_gate.py; "
        "Lever B spec assumes the constant exists."
    )
    assert hasattr(db, "G1_FAILSAFE_CONFIDENCE_THRESHOLD"), (
        "`G1_FAILSAFE_CONFIDENCE_THRESHOLD` removed from "
        "tasks/trade_readiness_gate.py; Lever B spec assumes the constant "
        "exists."
    )


def test_g1_threshold_is_currently_0_05():
    """Positive control: today the threshold is exactly 0.05 (the BSR
    landing constant). Pins the pre-Lever-B baseline so the deploy's
    delta is clear at PR-review time."""
    from tasks.trade_readiness_gate import G1_CONFIDENCE_THRESHOLD
    assert G1_CONFIDENCE_THRESHOLD == pytest.approx(0.05, abs=0.001), (
        f"G1_CONFIDENCE_THRESHOLD = {G1_CONFIDENCE_THRESHOLD!r}; expected "
        f"0.05 today. If this fires, Lever B has already deployed (or the "
        f"baseline drifted unexpectedly) — update the xfail markers above "
        f"to remove."
    )
