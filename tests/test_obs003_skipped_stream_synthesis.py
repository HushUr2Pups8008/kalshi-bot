"""Tests for `scripts/simulations/obs003_skipped_stream_synthesis.py`.

Pre-staged during the PROFIT-PHASE2-001 soak per the OBS-003 spec §7 step 4
("bothealth aggregator validation"). The synthesizer is the canonical
reference shape for the SKIPPED-stream histogram `bothealth.sh` will need
to produce once OBS-003 lands and the executor / BlendTask SKIPPED streams
are unified.

These tests pin:

  1. The synthesized stream's record shape against OBS-003 spec §5 keys.
  2. The aggregator histogram counts against Codex's 2026-05-03 post-soak
     landing simulation prediction (78 BlendTask SKIPPED + 9 executor
     SKIPPED = 87 total on the post-MATCH-001 archive).
  3. JSONL round-trip for the operator workflow.

All tests pass today (the synthesizer is self-contained and prod code is
not invoked). No xfail markers — the canonical-reference role is the
primary value.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.simulations.obs003_skipped_stream_synthesis import (
    aggregate_by_reason,
    emit_jsonl,
    render_histogram,
    synthesize,
)


_REQUIRED_KEYS = {
    "reason",
    "ticker",
    "headline",
    "source",
    "method",
    "llm_direction",
    "llm_magnitude",
    "model_probability",
    "market_price",
    "edge",
    "min_edge_threshold",
}


def test_synthesize_record_count_matches_codex_simulation():
    """Codex's 2026-05-03 simulation predicts 87 total SKIPPED records on the
    post-MATCH-001 archive (78 BlendTask-emitted + 9 executor-emitted)."""
    records = synthesize()
    assert len(records) == 87, (
        f"synthesizer must produce 87 records to match the Codex landing simulation; "
        f"got {len(records)}"
    )


def test_synthesize_each_record_has_required_obs003_keys():
    """Every synthesized SKIPPED record must carry the executor-compatible key
    set per OBS-003 spec §5."""
    records = synthesize()
    for i, r in enumerate(records):
        assert r["type"] == "SKIPPED", f"record {i}: type must be SKIPPED"
        missing = _REQUIRED_KEYS - r.keys()
        assert missing == set(), (
            f"record {i} (reason={r.get('reason')!r}): missing keys {missing}"
        )


def test_blendtask_records_carry_blendtask_signal_meta_marker():
    """The BlendTask-emitted records carry signal_meta={'emitter': 'BlendTask'}
    to disambiguate them from executor-emitted records in downstream audits."""
    records = synthesize()
    blendtask_reasons = {
        "G1_blended_confidence",
        "G6_recency_score",
        "G2_evidence_source_class_diversity",
    }
    for r in records:
        if r["reason"] in blendtask_reasons:
            assert r.get("signal_meta", {}).get("emitter") == "BlendTask"


def test_aggregate_by_reason_matches_landing_simulation_distribution():
    """The histogram must match Codex's 2026-05-03 simulation prediction
    exactly: G1 × 59, G6 × 14, G2 × 5, plus the executor's pre-existing
    reason set."""
    histogram = aggregate_by_reason(synthesize())
    assert histogram["G1_blended_confidence"] == 59
    assert histogram["G6_recency_score"] == 14
    assert histogram["G2_evidence_source_class_diversity"] == 5
    # Executor reasons (lightly sized; total executor count = 9):
    blendtask_total = histogram["G1_blended_confidence"] + histogram["G6_recency_score"] + histogram["G2_evidence_source_class_diversity"]
    executor_total = sum(histogram.values()) - blendtask_total
    assert executor_total == 9, f"executor SKIPPED total must be 9; got {executor_total}"


def test_aggregator_ignores_non_skipped_records():
    """Mixed input with non-SKIPPED records must not contaminate the histogram."""
    mixed = synthesize() + [
        {"type": "OPPORTUNITY", "reason": "G1_blended_confidence"},
        {"type": "PAPER_TRADE", "reason": "should_not_appear"},
        {"type": "BLEND_DECISION", "reason": "neither_should_this"},
    ]
    histogram = aggregate_by_reason(mixed)
    # Only the 87 synthetic SKIPPED records contribute.
    assert sum(histogram.values()) == 87


def test_render_histogram_emits_markdown_table():
    histogram = Counter({"G1_blended_confidence": 59, "G6_recency_score": 14})
    rendered = render_histogram(histogram)
    assert rendered.startswith("| reason | count |")
    assert "| `G1_blended_confidence` | 59 |" in rendered
    assert "| `G6_recency_score` | 14 |" in rendered


def test_emit_jsonl_round_trips_through_disk(tmp_path: Path):
    """The operator workflow writes the synthetic stream to disk; the
    JSON round-trip must preserve every record exactly."""
    fixture = tmp_path / "obs003_fixture.jsonl"
    n_written = emit_jsonl(fixture)
    assert n_written == 87
    parsed = [json.loads(line) for line in fixture.read_text().splitlines() if line.strip()]
    assert parsed == synthesize()


def test_blendtask_records_carry_post_blend_edge_not_fast_lane(tmp_path: Path):
    """Spec §5: BlendTask-emitted SKIPPED records carry the post-blend
    `model_probability` / `edge`. Synthesizer encodes blended_p in the
    [0.04, 0.06) range to reflect G1-floor-adjacent values; market_price is
    fixed at 0.50; edge is `model_probability - market_price`. Pin the
    arithmetic to keep the synthesizer faithful to OBS-003 §5."""
    records = synthesize()
    blendtask_reasons = {
        "G1_blended_confidence",
        "G6_recency_score",
        "G2_evidence_source_class_diversity",
    }
    for r in records:
        if r["reason"] in blendtask_reasons:
            assert r["edge"] == pytest.approx(r["model_probability"] - r["market_price"])
            assert r["market_price"] == 0.50
