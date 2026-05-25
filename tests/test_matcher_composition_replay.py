"""Tests for scripts/matcher_composition_replay.py (PROFIT-ALIGN settle-the-debate)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.matcher_composition_replay import (
    _classify_match,
    _multipliers,
    _Sad,
    run,
)


def _md(ts: str, ticker: str, tokens: list[str], score: float) -> dict:
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ts": ts,
        "ticker": ticker,
        "matched_tokens": tokens,
        "match_score": score,
        "headline": "h",
        "source": "s",
    }


def _sad(ts: str, ticker: str, direction: str, magnitude: str = "small") -> dict:
    return {
        "type": "SIGNAL_ANALYSIS_DETAIL",
        "ts": ts,
        "ticker": ticker,
        "llm_direction": direction,
        "llm_magnitude": magnitude,
        "llm_confidence": 0.85,
    }


def _write_archive(tmp_path: Path, records: list[dict]) -> None:
    archive = tmp_path / "archive" / "2026" / "05"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "2026-05-20.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_weights(tmp_path: Path, weights: dict) -> Path:
    p = tmp_path / "matcher_token_weights.json"
    p.write_text(json.dumps(weights), encoding="utf-8")
    return p


class TestMultipliers:
    def test_no_weights_returns_one(self):
        assert _multipliers(["t"], "KX", {}) == (1.0, 1.0)

    def test_no_overlap_returns_one(self):
        assert _multipliers([], "KX", {"KX:t": {"weight": 0.1}}) == (1.0, 1.0)

    def test_single_token_min_equals_mean(self):
        w = {"KX:trump": {"weight": 0.1}}
        mn, mu = _multipliers(["trump"], "KX", w)
        assert mn == 0.1
        assert mu == 0.1

    def test_multi_token_mean_dilutes_min(self):
        w = {"KX:trump": {"weight": 0.1}}
        mn, mu = _multipliers(["trump", "iran"], "KX", w)
        assert mn == 0.1
        assert mu == pytest.approx(0.55)  # (0.1 + 1.0) / 2

    def test_corrupted_weight_falls_back_to_one(self):
        w = {"KX:t": {"weight": "bad"}}
        mn, mu = _multipliers(["t"], "KX", w)
        assert mn == 1.0
        assert mu == 1.0


class TestClassifyMatch:
    def test_no_sad(self):
        assert _classify_match(None) == "no_sad"

    def test_actionable_yes(self):
        s = _Sad(ts=datetime.now(timezone.utc), ticker="KX", llm_direction="yes", llm_magnitude="small")
        assert _classify_match(s) == "actionable"

    def test_actionable_no(self):
        s = _Sad(ts=datetime.now(timezone.utc), ticker="KX", llm_direction="no", llm_magnitude="small")
        assert _classify_match(s) == "actionable"

    def test_noise_neutral(self):
        s = _Sad(ts=datetime.now(timezone.utc), ticker="KX", llm_direction="neutral", llm_magnitude="none")
        assert _classify_match(s) == "noise"


class TestRunIntegration:
    def test_empty_archive_returns_zero_buckets(self, tmp_path: Path):
        wpath = _write_weights(tmp_path, {})
        out = run(
            trade_log_root=tmp_path,
            weights_path=wpath,
        )
        assert out["total_pre_feedback_matches"] == 0
        assert out["buckets"]["pass_both"]["count"] == 0
        assert out["buckets"]["mean_only_pass"]["count"] == 0

    def test_pre_feedback_filter_excludes_post_ship_events(self, tmp_path: Path):
        wpath = _write_weights(tmp_path, {})
        # one event before ship, one after
        _write_archive(tmp_path, [
            _md("2026-05-20T12:00:00+00:00", "KXA-1", ["trump"], 0.20),
            _md("2026-05-25T00:00:00+00:00", "KXA-2", ["trump"], 0.20),
        ])
        out = run(trade_log_root=tmp_path, weights_path=wpath)
        # Only the pre-ship event counts. With no weights, min=mean=1.0, score=0.20
        # passes 0.06 → pass_both
        assert out["total_pre_feedback_matches"] == 1
        assert out["buckets"]["pass_both"]["count"] == 1

    def test_mean_only_pass_cell_populates_correctly(self, tmp_path: Path):
        """Construct an overlap where mean lets through but min rejects.

        match_score = 0.10, threshold = 0.06.
        Tokens = [trump, supporting]. weights: KXA:trump=0.10, supporting unset (1.0).
        min_mult = 0.10 → score_min = 0.01 < 0.06 → FAIL
        mean_mult = 0.55 → score_mean = 0.055 < 0.06 → still FAIL (gotcha — need bigger base)

        Use match_score=0.20:
        score_min = 0.20 × 0.10 = 0.02 → FAIL
        score_mean = 0.20 × 0.55 = 0.11 → PASS → mean_only_pass
        """
        wpath = _write_weights(tmp_path, {
            "KXBRIDGE:trump": {"weight": 0.10},
        })
        _write_archive(tmp_path, [
            _md("2026-05-20T12:00:00+00:00", "KXBRIDGE-1", ["trump", "supporting"], 0.20),
            # Add a follow-up LLM neutral SAD
            _sad("2026-05-20T12:00:05+00:00", "KXBRIDGE-1", "neutral", "none"),
        ])
        out = run(trade_log_root=tmp_path, weights_path=wpath)
        assert out["buckets"]["mean_only_pass"]["count"] == 1
        # SAD was neutral → classified as noise
        assert out["buckets"]["mean_only_pass"]["actionable"] == 0
        # SAD did fire → sad_observed=1
        assert out["buckets"]["mean_only_pass"]["no_sad"] == 0

    def test_mean_only_pass_actionable_signal_classified(self, tmp_path: Path):
        wpath = _write_weights(tmp_path, {
            "KXBRIDGE:trump": {"weight": 0.10},
        })
        _write_archive(tmp_path, [
            _md("2026-05-20T12:00:00+00:00", "KXBRIDGE-1", ["trump", "supporting"], 0.20),
            _sad("2026-05-20T12:00:05+00:00", "KXBRIDGE-1", "yes", "small"),
        ])
        out = run(trade_log_root=tmp_path, weights_path=wpath)
        assert out["buckets"]["mean_only_pass"]["count"] == 1
        assert out["buckets"]["mean_only_pass"]["actionable"] == 1

    def test_no_followup_sad_classified_no_sad(self, tmp_path: Path):
        """Match that didn't reach LLM gets classified no_sad."""
        wpath = _write_weights(tmp_path, {})
        _write_archive(tmp_path, [
            _md("2026-05-20T12:00:00+00:00", "KXA-1", ["trump"], 0.20),
            # No SAD follow-up
        ])
        out = run(trade_log_root=tmp_path, weights_path=wpath)
        assert out["buckets"]["pass_both"]["count"] == 1
        assert out["buckets"]["pass_both"]["no_sad"] == 1
