"""Tests for the post-OBS-003 SKIPPED-stream attribution audit harness.

Pre-staged during PROFIT-PHASE2-001 soak. The audit script is read-only
and safe to run today against pre-OBS-003 data; these tests pin its
shape so that when OBS-003 lands and the SKIPPED stream gains the new
`reason` set + `signal_meta`, the aggregator continues to produce the
contract Lever B sizing depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.simulations.post_obs003_skipped_attribution_audit import (
    _is_blendtask_emitted,
    _load_skipped,
    aggregate_by_reason,
    analyze,
    counterfactual_edge_bins,
    source_class_per_gate,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def test_aggregate_by_reason_counts_only_skipped():
    records = [
        {"type": "SKIPPED", "reason": "G1_blended_confidence"},
        {"type": "SKIPPED", "reason": "G1_blended_confidence"},
        {"type": "SKIPPED", "reason": "G6_recency_score"},
    ]
    out = aggregate_by_reason(records)
    assert out["G1_blended_confidence"] == 2
    assert out["G6_recency_score"] == 1


def test_aggregate_by_reason_uses_no_reason_sentinel_when_missing():
    records = [{"type": "SKIPPED"}, {"type": "SKIPPED", "reason": "G1_blended_confidence"}]
    out = aggregate_by_reason(records)
    assert out["<no_reason>"] == 1
    assert out["G1_blended_confidence"] == 1


def test_counterfactual_edge_bins_partitions_into_lever_b_buckets():
    records = [
        {"reason": "G1_blended_confidence", "edge": -0.05},  # lt_-0.02
        {"reason": "G1_blended_confidence", "edge": -0.01},  # [-0.02,0)
        {"reason": "G1_blended_confidence", "edge": 0.01},   # [0,0.02)
        {"reason": "G1_blended_confidence", "edge": 0.03},   # [0.02,0.05)
        {"reason": "G1_blended_confidence", "edge": 0.07},   # ge_0.05
        {"reason": "G6_recency_score", "edge": 0.04},        # different reason — excluded
    ]
    bins = counterfactual_edge_bins(records, "G1_blended_confidence")
    assert bins["lt_-0.02"] == 1
    assert bins["[-0.02,0)"] == 1
    assert bins["[0,0.02)"] == 1
    assert bins["[0.02,0.05)"] == 1
    assert bins["ge_0.05"] == 1
    assert sum(bins.values()) == 5


def test_counterfactual_edge_bins_handles_missing_edge():
    records = [
        {"reason": "G1_blended_confidence"},
        {"reason": "G1_blended_confidence", "edge": 0.03},
    ]
    bins = counterfactual_edge_bins(records, "G1_blended_confidence")
    assert bins["<no_edge>"] == 1
    assert bins["[0.02,0.05)"] == 1


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("G1_blended_confidence", True),
        ("G2_evidence_source_class_diversity", True),
        ("G6_recency_score", True),
        ("structural_tier2_veto", True),
        ("cooldown", False),
        ("opposing_position", False),
        ("", False),
    ],
)
def test_is_blendtask_emitted_partitions_correctly(reason, expected):
    assert _is_blendtask_emitted({"reason": reason}) is expected


def test_source_class_per_gate_groups_by_reason_then_source_class():
    records = [
        {"reason": "G1_blended_confidence", "signal_meta": {"source_class": "news"}},
        {"reason": "G1_blended_confidence", "signal_meta": {"source_class": "news"}},
        {"reason": "G1_blended_confidence", "signal_meta": {"source_class": "official"}},
        {"reason": "G6_recency_score", "signal_meta": {"source_class": "analysis"}},
    ]
    out = source_class_per_gate(records)
    assert out["G1_blended_confidence"]["news"] == 2
    assert out["G1_blended_confidence"]["official"] == 1
    assert out["G6_recency_score"]["analysis"] == 1


def test_source_class_per_gate_uses_unknown_sentinel_when_missing():
    records = [{"reason": "G1_blended_confidence"}]
    out = source_class_per_gate(records)
    assert out["G1_blended_confidence"]["<unknown>"] == 1


def test_load_skipped_filters_non_skipped_and_blank_lines(tmp_path):
    p = tmp_path / "trades.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"type": "SKIPPED", "reason": "G1_blended_confidence", "ts": "2026-05-15T00:00:00Z"}),
                "",
                json.dumps({"type": "PAPER_TRADE", "ts": "2026-05-15T00:00:00Z"}),
                "not-json",
                json.dumps({"type": "SKIPPED", "reason": "G6_recency_score", "ts": "2026-05-16T00:00:00Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = _load_skipped([p])
    assert len(out) == 2
    assert {r["reason"] for r in out} == {"G1_blended_confidence", "G6_recency_score"}


def test_load_skipped_since_filter_excludes_older_records(tmp_path):
    p = tmp_path / "trades.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "SKIPPED", "reason": "G1_blended_confidence", "ts": "2026-05-10T00:00:00Z"},
            {"type": "SKIPPED", "reason": "G1_blended_confidence", "ts": "2026-05-15T00:00:00Z"},
            {"type": "SKIPPED", "reason": "G1_blended_confidence", "ts": "2026-05-20T00:00:00Z"},
        ],
    )
    out = _load_skipped([p], since="2026-05-15")
    assert len(out) == 2


def test_load_skipped_skips_missing_paths(tmp_path):
    out = _load_skipped([tmp_path / "does-not-exist.jsonl"])
    assert out == []


def test_analyze_returns_expected_shape(tmp_path):
    p = tmp_path / "trades.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "SKIPPED", "reason": "G1_blended_confidence", "edge": 0.03,
             "signal_meta": {"source_class": "news"}, "ts": "2026-05-15T00:00:00Z"},
            {"type": "SKIPPED", "reason": "cooldown", "ts": "2026-05-15T00:00:00Z"},
            {"type": "PAPER_TRADE", "ts": "2026-05-15T00:00:00Z"},
        ],
    )
    report = analyze(paths=[p])
    assert report["total_skipped"] == 2
    assert report["blendtask_emitted"] == 1
    assert report["executor_emitted"] == 1
    assert report["by_reason"]["G1_blended_confidence"] == 1
    assert report["by_reason"]["cooldown"] == 1
    assert report["g1_edge_bins"]["[0.02,0.05)"] == 1
    assert "source_class_per_gate" in report
    assert report["source_class_per_gate"]["G1_blended_confidence"]["news"] == 1
