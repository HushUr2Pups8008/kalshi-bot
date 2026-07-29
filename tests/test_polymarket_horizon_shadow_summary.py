from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import polymarket_horizon_shadow_summary as summary


_COHORT_ID = "legacy-pending-20260729"
_COHORT_KIND = "legacy_pending"


def _record(
    *,
    ts: datetime,
    production_candidates: int = 2,
    shadow_candidates: int = 3,
    production_qualifying: int = 0,
    shadow_qualifying: int = 1,
    cohort_id: str = _COHORT_ID,
    cohort_kind: str = _COHORT_KIND,
) -> dict[str, object]:
    return {
        "type": "POLYMARKET_HORIZON_SHADOW",
        "ts": ts.isoformat(),
        "venue": "polymarket_us",
        "runtime_paper_cohort_id": cohort_id,
        "runtime_paper_cohort_kind": cohort_kind,
        "production_horizon_days": 14.0,
        "shadow_horizon_start_days": 14.0,
        "shadow_horizon_end_days": 30.0,
        "production_candidate_count": production_candidates,
        "shadow_candidate_count": shadow_candidates,
        "production_qualifying_match_count": production_qualifying,
        "shadow_qualifying_match_count": shadow_qualifying,
        "production_no_token_overlap_count": production_candidates
        - production_qualifying,
        "production_below_min_post_weight_score_count": 0,
        "production_weight_demoted_below_min_score_count": 0,
        "production_min_match_score": 0.08,
        "shadow_no_token_overlap_count": shadow_candidates - shadow_qualifying,
        "shadow_below_min_post_weight_score_count": 0,
        "shadow_weight_demoted_below_min_score_count": 0,
        "shadow_min_match_score": 0.08,
        "shadow_analysis_status": "not_evaluated_shadow_only",
    }


def test_summarize_records_requires_valid_paired_scope_and_keeps_collecting():
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    valid = _record(ts=now - timedelta(hours=1))
    invalid = {**_record(ts=now), "shadow_horizon_start_days": 13.0}
    other_cohort = _record(ts=now, cohort_id="other-cohort")

    result = summary.summarize_records(
        [valid, invalid, other_cohort],
        cohort_id=_COHORT_ID,
        cohort_kind=_COHORT_KIND,
        now=now,
    )

    assert result == {
        "scoped_records": 2,
        "excluded_records": 1,
        "invalid_records": 1,
        "out_of_study_horizon_records": 0,
        "valid_snapshots": 1,
        "first_snapshot_utc": (now - timedelta(hours=1)).isoformat(),
        "last_snapshot_utc": (now - timedelta(hours=1)).isoformat(),
        "production_candidate_count": 2,
        "shadow_candidate_count": 3,
        "production_qualifying_match_count": 0,
        "shadow_qualifying_match_count": 1,
        "review_status": "collecting",
        "review_trigger": "need_49_more_valid_snapshots_or_7d_elapsed",
        "automatic_promotion": False,
    }


def test_summarize_records_requires_manual_review_after_seven_days():
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    record = _record(ts=now - timedelta(days=7))

    result = summary.summarize_records(
        [record],
        cohort_id=_COHORT_ID,
        cohort_kind=_COHORT_KIND,
        now=now,
    )

    assert result["valid_snapshots"] == 1
    assert result["review_status"] == "manual_review_required"
    assert result["review_trigger"] == "7d_elapsed_before_50_valid_snapshots"
    assert result["automatic_promotion"] is False


def test_summarize_records_excludes_other_horizon_regimes():
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    valid = _record(ts=now - timedelta(hours=1))
    other_horizons = [
        {
            **_record(ts=now, production_candidates=9, shadow_candidates=8),
            "production_horizon_days": 10.0,
            "shadow_horizon_start_days": 10.0,
            "shadow_horizon_end_days": 30.0,
        },
        {
            **_record(ts=now, production_candidates=9, shadow_candidates=8),
            "production_horizon_days": 7.0,
            "shadow_horizon_start_days": 7.0,
            "shadow_horizon_end_days": 21.0,
        },
        {
            **_record(ts=now, production_candidates=9, shadow_candidates=8),
            "production_horizon_days": 14.0,
            "shadow_horizon_start_days": 14.0,
            "shadow_horizon_end_days": 21.0,
        },
    ]

    result = summary.summarize_records(
        [valid, *other_horizons],
        cohort_id=_COHORT_ID,
        cohort_kind=_COHORT_KIND,
        now=now,
    )

    assert result["scoped_records"] == 4
    assert result["invalid_records"] == 0
    assert result["out_of_study_horizon_records"] == 3
    assert result["valid_snapshots"] == 1
    assert result["production_candidate_count"] == 2
    assert result["shadow_candidate_count"] == 3
    assert result["review_trigger"] == "need_49_more_valid_snapshots_or_7d_elapsed"


def test_cli_runs_directly_from_repo_root(tmp_path):
    trades_path = tmp_path / "trades.jsonl"
    trades_path.write_text("", encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/polymarket_horizon_shadow_summary.py",
            "--trades",
            str(trades_path),
            "--runtime-paper-cohort-id",
            _COHORT_ID,
            "--runtime-paper-cohort-kind",
            _COHORT_KIND,
            "--json",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid_snapshots"] == 0
