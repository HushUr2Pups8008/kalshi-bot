"""Pre-load harness for Lever C cross-series headline correlation deploy.

Spec: docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md
Status: pre-loaded during PROFIT-PHASE2-001 soak; lands as part of
Wave-3 if Wave-2 + Lever B both stall. Adds a cross-series headline
correlation guard at the BlendTask enqueue point per spec §2.

Empirical basis: Codex's 2026-05-03 cross-series overlap audit
(`docs/governance/2026-05-03-cross-series-headline-overlap-audit.md`)
sized the §3.2 normalized-string hash at 49.2 % overlap on the
13-day archive — far above the 15 % decision threshold.

Per spec §2: Lever C is a RISK-CONTROL lever, NOT an edge-production
one. Expected outcome: fewer correlated-burst paper trades, not
higher trade count. The post-deploy validation criterion is
suppression of cross-series bursts in trades.jsonl, not a conversion
rate lift.

Strict-xfail today (no `cross_series_correlation_window_seconds`
config knob; no `cross_series_headline_in_window` reason string in
BlendTask). Flips xpass on the deploy commit, forcing marker
removal in the same hunk.
"""

from __future__ import annotations

import pytest


_LEVER_C_XFAIL_REASON = (
    "PROFIT-EDGE-004 Lever C cross-series correlation guard not yet "
    "landed. Spec §2 adds a BlendTask enqueue-time check against a "
    "headline hash window. Lands in Wave 3 if Wave 2 (A.1+) and "
    "Lever B both stall. Trips on the deploy commit; remove the marker."
)


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_cross_series_correlation_window_config_knob_exists():
    """Pin the post-Lever-C outcome that
    `config.cfg.cross_series_correlation_window_seconds` exists with a
    sensible default. Spec §2 default: 1 h (3600 s). Operator may tune
    at deploy time; test passes if the value is a positive number ≤
    24 h (anything larger would be operationally absurd)."""
    from config import cfg
    assert hasattr(cfg, "cross_series_correlation_window_seconds"), (
        "`cfg.cross_series_correlation_window_seconds` not present. "
        "Lever C spec §2 calls for adding this config knob. Default "
        "should be 3600 s (1 h)."
    )
    val = cfg.cross_series_correlation_window_seconds
    assert isinstance(val, (int, float)) and 0 < val <= 86400, (
        f"`cfg.cross_series_correlation_window_seconds` = {val!r}; "
        f"expected a positive number ≤ 86400 (24 h). Spec §2 default "
        f"is 3600."
    )


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_blend_task_uses_cross_series_headline_in_window_reason_string():
    """Pin the post-Lever-C outcome that `BlendTask` source references
    the reason string `cross_series_headline_in_window` (per spec §2
    code sketch). Source-inspection contract: catches a refactor that
    accidentally renames the reason string mid-deploy and breaks the
    OBS-003 SKIPPED-emission attribution."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    blend_task = repo_root / "tasks" / "blend_task.py"
    assert blend_task.exists(), "tasks/blend_task.py must exist"
    body = blend_task.read_text(encoding="utf-8")
    assert "cross_series_headline_in_window" in body, (
        "tasks/blend_task.py does not reference the reason string "
        "`cross_series_headline_in_window`. Spec §2 mandates this exact "
        "string for OBS-003 SKIPPED-stream attribution. If the deploy "
        "renamed the reason, update this test AND the post-OBS-003 "
        "SKIPPED-stream attribution audit "
        "(scripts/simulations/post_obs003_skipped_attribution_audit.py) "
        "to match."
    )


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_normalized_headline_hash_function_exists():
    """Pin the post-Lever-C outcome that a normalized-headline-hash
    function exists per spec §3.2. Operator may name it
    `_normalize_headline_hash`, `_cross_series_hash`, or similar; test
    passes if any callable matches the spec §3.2 contract (input: str
    headline; output: str hash). Source-inspection finds the function
    by docstring or signature pattern."""
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    blend_task = repo_root / "tasks" / "blend_task.py"
    body = blend_task.read_text(encoding="utf-8")
    has_hash_helper = (
        "_normalize_headline" in body
        or "_cross_series_hash" in body
        or "headline_hash" in body
    )
    assert has_hash_helper, (
        "tasks/blend_task.py does not define a headline-hash helper. "
        "Spec §3.2 calls for a normalized-string hash (lowercase, "
        "stop-word strip, whitespace collapse) used as the key for the "
        "cross-series correlation window. If the operator named it "
        "differently, extend this test's substring list."
    )


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_headline_hash_normalizes_parent_spec_section_3_2_variants():
    """LOCK addendum §3 test 2: cosmetic variants of the same headline
    must collide under the parent spec §3.2 normalized hash."""
    from tasks import blend_task

    helper = getattr(blend_task, "_headline_hash", None) or getattr(
        blend_task, "_normalize_headline_hash", None
    )
    assert helper is not None, "Lever C deploy must expose a headline-hash helper"
    assert helper("Trump signs bill - VitalLaw.com") == helper("trump signs bill")


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_cross_series_suppression_within_window_reason_path_exists():
    """LOCK addendum §3 test 3: within-window suppression path exists and
    emits `cross_series_headline_in_window`."""
    from pathlib import Path

    body = (Path(__file__).resolve().parent.parent / "tasks/blend_task.py").read_text(encoding="utf-8")
    assert "_recent_headline_enqueues" in body
    assert "cross_series_headline_in_window" in body
    assert "cross_series_correlation_window_seconds" in body


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_cross_series_suppression_releases_after_window_contract_exists():
    """LOCK addendum §3 test 4: implementation must compare elapsed time
    against `cross_series_correlation_window_seconds`, not permanently
    suppress a hash after first sighting."""
    from pathlib import Path

    body = (Path(__file__).resolve().parent.parent / "tasks/blend_task.py").read_text(encoding="utf-8")
    assert "time.monotonic" in body
    assert "cross_series_correlation_window_seconds" in body
    assert "<" in body or "<=" in body


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_headline_hash_recorded_after_gate_pass_not_at_entry_contract_exists():
    """LOCK addendum §2/§3 test 5: headline hash is recorded only after
    a candidate passes the existing readiness path, so failed candidates
    do not poison the suppression window."""
    from pathlib import Path

    body = (Path(__file__).resolve().parent.parent / "tasks/blend_task.py").read_text(encoding="utf-8")
    hash_write = body.find("_recent_headline_enqueues[")
    enqueue = body.find("trading_queue")
    readiness = body.find("evaluate_readiness")
    assert readiness != -1 and hash_write != -1 and enqueue != -1
    assert readiness < hash_write < enqueue


@pytest.mark.xfail(reason=_LEVER_C_XFAIL_REASON, strict=True)
def test_cross_series_window_zero_disables_contract_exists():
    """LOCK addendum §3 test 6: window=0 is the env-revert path and must
    bypass cross-series suppression entirely."""
    from pathlib import Path

    body = (Path(__file__).resolve().parent.parent / "tasks/blend_task.py").read_text(encoding="utf-8")
    assert "cross_series_correlation_window_seconds" in body
    assert "<= 0" in body or "== 0" in body


def test_existing_skipped_emission_reasons_unchanged_today():
    """Positive control: existing SKIPPED-emission reason strings
    (G1-G6 + structural_tier* + cooldown / opposing-position) continue
    to be valid reason strings. Catches a refactor that re-organises
    the reason taxonomy mid-Lever-C-deploy. The post-OBS-003 SKIPPED
    attribution audit reads these reason strings; renaming them
    silently would break the audit."""
    expected_reason_prefixes = (
        "G1_",
        "G2_",
        "G3_",
        "G4_",
        "G5_",
        "G6_",
    )
    # Source-inspection check rather than runtime — runtime would
    # require a full BlendTask invocation with a synthetic candidate.
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent
    readiness_gate = repo_root / "tasks" / "trade_readiness_gate.py"
    assert readiness_gate.exists()
    body = readiness_gate.read_text(encoding="utf-8")
    for prefix in expected_reason_prefixes:
        assert prefix in body, (
            f"reason prefix `{prefix}` not found in "
            f"tasks/trade_readiness_gate.py. Lever C deploy must not "
            f"rename existing G1-G6 reasons."
        )
