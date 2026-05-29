from __future__ import annotations

import json
from pathlib import Path

from scripts.simulations.paper_admission_replay import (
    ReplayConfig,
    analyze,
    build_candidates,
    render,
    replay_candidate,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def _blend(
    ticker: str,
    *,
    ts: str = "2026-05-29T00:00:00Z",
    reason: str | None = None,
    edge: float = 0.04,
    evidence_source_classes: list[str] | None = None,
    evidence_items: list[dict] | None = None,
    regime_weights: dict[str, float] | None = None,
) -> dict:
    return {
        "type": "BLEND_DECISION",
        "ts": ts,
        "market_ticker": ticker,
        "regime_weights": regime_weights or {"fast": 1 / 3, "interpretation": 1 / 3, "structural": 1 / 3},
        "regime_confidence": 0.25,
        "blended_p": 0.55,
        "blended_confidence": 0.40,
        "disagreement_score": 0.05,
        "trade_blocked_reason": reason,
        "edge": edge,
        "evidence_source_classes": evidence_source_classes or [],
        "evidence_items": evidence_items or [],
    }


def test_build_candidates_joins_blend_with_nearby_skipped():
    records = [
        _blend("KXTEST-1", reason=None),
        {
            "type": "SKIPPED",
            "ts": "2026-05-29T00:00:03Z",
            "ticker": "KXTEST-1",
            "reason": "edge +0.0100 below min_edge 0.02",
            "edge": 0.01,
        },
    ]

    candidates = build_candidates(records)

    assert len(candidates) == 1
    assert candidates[0]["ticker"] == "KXTEST-1"
    assert candidates[0]["recorded_reason"] == "executor_min_edge"
    assert candidates[0]["edge"] == 0.01


def test_trigger_evidence_can_clear_g2_source_class_diversity():
    candidate = build_candidates(
        [
            _blend(
                "KXTEST-G2",
                reason="G2_evidence_source_class_diversity",
                evidence_source_classes=["news"],
            ),
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:00:01Z",
                "ticker": "KXTEST-G2",
                "reason": "G2_evidence_source_class_diversity",
                "edge": 0.04,
                "signal_meta": {"source_class": "official"},
            },
        ]
    )[0]

    baseline = replay_candidate(candidate, ReplayConfig())
    with_trigger = replay_candidate(candidate, ReplayConfig(include_trigger_evidence=True))

    assert baseline["admitted"] is False
    assert baseline["first_reason"] == "G2_evidence_source_class_diversity"
    assert with_trigger["admitted"] is True
    assert with_trigger["cleared_reasons"] == ["G2_evidence_source_class_diversity"]


def test_trigger_evidence_uses_runtime_metadata_keys():
    candidate = build_candidates(
        [
            _blend(
                "KXTEST-G2",
                reason="G2_evidence_source_class_diversity",
                evidence_source_classes=["news"],
            ),
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:00:01Z",
                "ticker": "KXTEST-G2",
                "reason": "G2_evidence_source_class_diversity",
                "edge": 0.04,
                "signal_meta": {
                    "trigger_evidence_source_class": "official",
                    "trigger_evidence_original_weight": 0.8,
                    "trigger_evidence_ingested_ts": "2026-05-29T00:00:00Z",
                },
            },
        ]
    )[0]

    assert candidate["trigger_source_class"] == "official"
    assert candidate["trigger_item"] == (0.8, "2026-05-29T00:00:00Z")
    assert replay_candidate(
        candidate,
        ReplayConfig(include_trigger_evidence=True),
    )["admitted"] is True


def test_replay_preserves_multiple_readiness_failures():
    candidate = build_candidates(
        [
            _blend(
                "KXTEST-G2G6",
                reason="G2_evidence_source_class_diversity",
                evidence_source_classes=["news"],
                evidence_items=[
                    {"original_weight": 1.0, "ingested_ts": "2026-05-27T00:00:00Z"},
                ],
            ),
            {
                "type": "GATE_SUMMARY",
                "ts": "2026-05-29T00:00:00Z",
                "ticker": "KXTEST-G2G6",
                "binding_constraint": "G2_evidence_source_class_diversity",
                "gate_chain": [
                    "G4: PASS",
                    "G1: PASS",
                    "G3: PASS",
                    "G2_evidence_source_class_diversity: FAIL",
                    "G6_recency_score: FAIL",
                ],
            },
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:00:01Z",
                "ticker": "KXTEST-G2G6",
                "reason": "G2_evidence_source_class_diversity",
                "edge": 0.04,
                "signal_meta": {"trigger_evidence_source_class": "official"},
            },
        ]
    )[0]

    result = replay_candidate(candidate, ReplayConfig(include_trigger_evidence=True))

    assert result["admitted"] is False
    assert result["first_reason"] == "G6_recency_score"
    assert result["cleared_reasons"] == ["G2_evidence_source_class_diversity"]


def test_unknown_regime_interpretation_half_life_can_clear_g6():
    candidate = build_candidates(
        [
            _blend(
                "KXTEST-G6",
                reason="G6_recency_score",
                ts="2026-05-29T00:00:00Z",
                evidence_items=[
                    {"original_weight": 1.0, "ingested_ts": "2026-05-27T00:00:00Z"},
                ],
            ),
        ]
    )[0]

    fast_default = replay_candidate(candidate, ReplayConfig(unknown_regime_default="fast"))
    interpretation_default = replay_candidate(
        candidate,
        ReplayConfig(unknown_regime_default="interpretation"),
    )

    assert fast_default["admitted"] is False
    assert fast_default["first_reason"] == "G6_recency_score"
    assert interpretation_default["admitted"] is True
    assert interpretation_default["recency_score"] > fast_default["recency_score"]


def test_lower_paper_min_edge_can_clear_executor_min_edge():
    candidate = build_candidates(
        [
            _blend("KXTEST-EDGE", reason=None, edge=0.01),
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:00:01Z",
                "ticker": "KXTEST-EDGE",
                "reason": "edge +0.0100 below min_edge 0.02",
                "edge": 0.01,
            },
        ]
    )[0]

    baseline = replay_candidate(candidate, ReplayConfig(paper_min_edge=0.02))
    lowered = replay_candidate(candidate, ReplayConfig(paper_min_edge=0.01))

    assert baseline["admitted"] is False
    assert baseline["first_reason"] == "executor_min_edge"
    assert lowered["admitted"] is True


def test_analyze_summarizes_scenarios_and_reason_deltas(tmp_path):
    path = tmp_path / "events.jsonl"
    _write_jsonl(
        path,
        [
            _blend(
                "KXTEST-G2",
                reason="G2_evidence_source_class_diversity",
                evidence_source_classes=["news"],
            ),
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:00:01Z",
                "ticker": "KXTEST-G2",
                "reason": "G2_evidence_source_class_diversity",
                "edge": 0.04,
                "signal_meta": {"source_class": "official"},
            },
            _blend("KXTEST-EDGE", ts="2026-05-29T00:01:00Z", edge=0.01),
            {
                "type": "SKIPPED",
                "ts": "2026-05-29T00:01:01Z",
                "ticker": "KXTEST-EDGE",
                "reason": "edge +0.0100 below min_edge 0.02",
                "edge": 0.01,
            },
        ],
    )

    report = analyze(paths=[path])

    baseline = report["scenarios"]["baseline"]
    combined = report["scenarios"]["combined"]
    assert baseline["admitted"] == 0
    assert baseline["reason_counts"] == {
        "G2_evidence_source_class_diversity": 1,
        "executor_min_edge": 1,
    }
    assert combined["admitted"] == 2
    assert report["reason_deltas"]["combined"] == {
        "G2_evidence_source_class_diversity": -1,
        "executor_min_edge": -1,
    }
    assert "scenario | admitted" in render(report)
