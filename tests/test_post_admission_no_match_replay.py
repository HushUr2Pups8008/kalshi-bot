"""Offline counterfactual replay coverage for post-admission no-match records."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.post_admission_no_match_replay import (
    _score,
    format_json_summary,
    format_text_summary,
    parse_date_end,
    parse_date_start,
    summarize,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "post_admission_no_match_replay.jsonl"
REPLAY_TOKEN = "post_admission_no_match_replay"


def test_summarize_separates_absent_malformed_truncated_and_valid_shadow_evidence():
    summary = summarize(FIXTURE_PATH, since=None, until=None)

    assert summary["lines_total"] == 10
    assert summary["lines_malformed"] == 1
    assert summary["target_records"] == 7
    assert summary["shadow_counts"] == {
        "absent": 2,
        "malformed": 1,
        "valid": 4,
        "truncated": 1,
        "untruncated": 3,
    }
    assert summary["candidate_rejection_reasons"] == {
        "below_min_post_weight_score": 3,
        "no_token_overlap": 1,
        "weight_demoted_below_min_score": 3,
    }
    assert summary["best_rejected_post_weight_score"] == {
        "available": 4,
        "unavailable": 0,
        "tied_best": 1,
        "histogram": {
            "[0.00,0.02)": 0,
            "[0.02,0.05)": 2,
            "[0.05,0.10)": 2,
            "[0.10,1.00]": 0,
        },
    }
    assert summary["near_miss"] == {
        "minimum_ratio": 0.95,
        "descriptive_report_threshold_only": True,
        "frozen_threshold_available": 3,
        "frozen_threshold_unavailable": 1,
        "near_miss": 1,
        "not_near_miss": 2,
        "unavailable": 1,
        "at_or_above_frozen_threshold": 0,
    }
    assert summary["candidate_composition"] == {
        "single_candidate_total": 1,
        "complete_same_reason": 2,
        "truncated_excluded_from_full_pool_claims": 1,
    }


def test_valid_shadow_with_missing_frozen_threshold_is_unavailable_not_a_match():
    summary = summarize(FIXTURE_PATH, since=None, until=None)

    assert summary["shadow_counts"]["valid"] == 4
    assert summary["near_miss"]["frozen_threshold_unavailable"] == 1
    assert summary["near_miss"]["unavailable"] == 1
    assert summary["near_miss"]["near_miss"] == 1


def test_unparseable_shadow_score_or_persisted_invalid_status_is_malformed_not_a_crash(tmp_path):
    valid_record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[3])
    infinite_score = json.loads(json.dumps(valid_record))
    infinite_score["post_admission_counterfactual_shadow"]["candidates"][0]["post_weight_score"] = float("inf")
    nan_score = json.loads(json.dumps(valid_record))
    nan_score["post_admission_counterfactual_shadow"]["candidates"][0]["post_weight_score"] = float("nan")
    persisted_invalid_status = json.loads(json.dumps(valid_record))
    persisted_invalid_status["post_admission_counterfactual_shadow_status"] = "invalid"
    path = tmp_path / "invalid.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in (infinite_score, nan_score, persisted_invalid_status))
        + "\n",
        encoding="utf-8",
    )

    summary = summarize(path, since=None, until=None)

    assert summary["target_records"] == 3
    assert summary["shadow_counts"] == {
        "absent": 0,
        "malformed": 3,
        "valid": 0,
        "truncated": 0,
        "untruncated": 0,
    }
    assert _score(10**10000) is None


def test_wrong_context_or_frozen_threshold_contradiction_is_malformed_and_excluded(tmp_path):
    valid_record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[3])
    wrong_venue = json.loads(json.dumps(valid_record))
    wrong_venue["venue"] = "kalshi"
    wrong_reason = json.loads(json.dumps(valid_record))
    wrong_reason["reason"] = "other_reason"
    post_score_at_or_above_threshold = json.loads(json.dumps(valid_record))
    post_score_at_or_above_threshold["post_admission_min_match_score"] = 0.05
    weight_demoted_pre_score_below_threshold = json.loads(json.dumps(valid_record))
    weight_demoted_pre_score_below_threshold["post_admission_min_match_score"] = 0.1
    candidate = weight_demoted_pre_score_below_threshold["post_admission_counterfactual_shadow"]["candidates"][0]
    candidate["pre_weight_score"] = 0.09
    candidate["post_weight_score"] = 0.08
    path = tmp_path / "contradictory.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in (
                wrong_venue,
                wrong_reason,
                post_score_at_or_above_threshold,
                weight_demoted_pre_score_below_threshold,
            )
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize(path, since=None, until=None)

    assert summary["target_records"] == 4
    assert summary["shadow_counts"] == {
        "absent": 0,
        "malformed": 4,
        "valid": 0,
        "truncated": 0,
        "untruncated": 0,
    }
    assert summary["candidate_rejection_reasons"] == {}
    assert summary["best_rejected_post_weight_score"]["available"] == 0
    assert summary["near_miss"]["at_or_above_frozen_threshold"] == 0


def test_nonfinite_frozen_threshold_is_unavailable_without_a_match_inference(tmp_path):
    valid_record = json.loads(FIXTURE_PATH.read_text(encoding="utf-8").splitlines()[3])
    infinite_threshold = json.loads(json.dumps(valid_record))
    infinite_threshold["post_admission_min_match_score"] = float("inf")
    nan_threshold = json.loads(json.dumps(valid_record))
    nan_threshold["post_admission_min_match_score"] = float("nan")
    path = tmp_path / "unavailable-threshold.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in (infinite_threshold, nan_threshold)) + "\n",
        encoding="utf-8",
    )

    summary = summarize(path, since=None, until=None)

    assert summary["shadow_counts"]["valid"] == 2
    assert summary["near_miss"]["frozen_threshold_available"] == 0
    assert summary["near_miss"]["frozen_threshold_unavailable"] == 2
    assert summary["near_miss"]["unavailable"] == 2
    assert summary["near_miss"]["at_or_above_frozen_threshold"] == 0


def test_filters_honor_explicit_date_window_and_exclude_test_records():
    date_summary = summarize(
        FIXTURE_PATH,
        since=parse_date_start("2026-08-03"),
        until=parse_date_end("2026-08-03"),
    )
    excluded_summary = summarize(
        FIXTURE_PATH,
        since=None,
        until=None,
        exclude_test=True,
    )

    assert date_summary["target_records"] == 2
    assert date_summary["shadow_counts"] == {
        "absent": 0,
        "malformed": 0,
        "valid": 2,
        "truncated": 0,
        "untruncated": 2,
    }
    assert excluded_summary["target_records"] == 6
    assert excluded_summary["shadow_counts"]["absent"] == 1


def test_default_summary_keeps_legacy_absent_shadow_rows_and_does_not_write_fixture():
    before_contents = FIXTURE_PATH.read_bytes()
    before_paths = sorted(path.name for path in FIXTURE_PATH.parent.iterdir())

    summary = summarize(FIXTURE_PATH, since=None, until=None)

    assert summary["shadow_counts"]["absent"] == 2
    assert FIXTURE_PATH.read_bytes() == before_contents
    assert sorted(path.name for path in FIXTURE_PATH.parent.iterdir()) == before_paths


def test_cli_json_is_read_only_and_labels_counterfactual_evidence():
    before_contents = FIXTURE_PATH.read_bytes()
    before_paths = sorted(path.name for path in FIXTURE_PATH.parent.iterdir())

    result = subprocess.run(
        [
            sys.executable,
            "scripts/post_admission_no_match_replay.py",
            "--path",
            str(FIXTURE_PATH),
            "--since",
            "2026-08-02",
            "--until",
            "2026-08-03",
            "--exclude-test",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(result.stdout)
    assert payload["counterfactual_evidence_only"] is True
    assert payload["not_matcher_policy_approval"] is True
    assert payload["path"] == str(FIXTURE_PATH)
    assert payload["filters"] == {
        "since": "2026-08-02T00:00:00+00:00",
        "until": "2026-08-03T23:59:59.999999+00:00",
        "exclude_test": True,
    }
    assert payload["near_miss"]["minimum_ratio"] == 0.95
    assert payload["near_miss"]["descriptive_report_threshold_only"] is True
    assert payload["counts"]["target_records"] == 4
    assert "PM-T1" not in result.stdout
    assert FIXTURE_PATH.read_bytes() == before_contents
    assert sorted(path.name for path in FIXTURE_PATH.parent.iterdir()) == before_paths


def test_report_text_and_json_make_the_non_inference_boundary_explicit():
    summary = summarize(FIXTURE_PATH, since=None, until=None)

    text = format_text_summary(summary)
    payload = json.loads(format_json_summary(summary))

    assert "COUNTERFACTUAL EVIDENCE ONLY" in text
    assert "not matcher-policy approval" in text
    assert "does not infer a match" in text
    assert "0.95" in text
    assert "not runtime policy" in text
    assert payload["counterfactual_evidence_only"] is True
    assert payload["not_matcher_policy_approval"] is True
    assert payload["near_miss"]["minimum_ratio"] == 0.95
    assert payload["near_miss"]["descriptive_report_threshold_only"] is True
    assert "candidate_tickers" not in payload


def test_replay_report_stays_out_of_runtime_behavioral_consumers():
    allowed_paths = {
        Path("scripts/post_admission_no_match_replay.py"),
        Path("tests/test_post_admission_no_match_replay.py"),
    }
    observed_paths = {
        path.relative_to(REPO_ROOT)
        for path in REPO_ROOT.rglob("*.py")
        if REPLAY_TOKEN in path.read_text(encoding="utf-8", errors="ignore")
    }

    assert observed_paths <= allowed_paths
