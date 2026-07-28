from __future__ import annotations

import hashlib
import json

import pytest

from scripts.simulations.feedback_neutral_replay import main


def _decision_record(
    *,
    decision_at: str = "2026-07-28T12:00:00+00:00",
    venue: str = "kalshi",
) -> dict[str, object]:
    return {
        "type": "FEEDBACK_DECISION",
        "feedback_decision_version": 1,
        "lifecycle_id": "decision-1",
        "venue": venue,
        "ticker": "KXTEST-1",
        "source": "Reuters",
        "series_ticker": "KXTEST",
        "decision_at": decision_at,
        "source_receipt": {
            "channel": "source",
            "key": "Reuters",
            "series_ticker": None,
            "applied_multiplier": 1.2,
            "status": "canonical",
            "canonical_basis_sha256": "a" * 64,
            "delivered_event_count": 12,
            "effective_sample_count": 10,
            "algorithm_version": "canonical-feedback-v1",
            "as_of": decision_at,
        },
        "keyword_receipts": [],
        "probability_actual": 0.65,
        "probability_keyword_neutral": 0.60,
        "keyword_counterfactual_status": "captured_keyword_probability_not_sizing_replayed:scored",
        "sizing_inputs": {
            "executed_price_cents": 51.0,
            "side": "yes",
            "bankroll": 500.0,
            "kelly_fraction": 0.5,
            "max_bet_dollars": 75.0,
            "min_bet_dollars": 2.0,
            "min_edge": 0.02,
            "confidence": 0.8,
            "days_to_close": 2.0,
            "time_discount_half_life": 14.0,
            "time_discount_floor": 0.2,
            "source_multiplier": 1.2,
            "is_paper_trading": True,
            "paper_flat_contracts": 2,
            "floor_clamp_applied": False,
            "floor_clamp_kelly_multiplier": 0.5,
        },
        "actual": {
            "source_multiplier": 1.2,
            "kelly_fraction": 0.12,
            "kelly_dollars": 8.0,
            "capped_dollars": 8.0,
            "paper_placeholder_applied": False,
        },
        "source_neutral": {
            "status": "scorable",
            "sizing_error": None,
            "source_multiplier": 1.0,
            "kelly_fraction": 0.10,
            "kelly_dollars": 6.0,
            "capped_dollars": 6.0,
            "paper_placeholder_applied": False,
        },
        "keyword_neutral": None,
        "all_neutral": None,
        "gate": {
            "enqueued": False,
            "trade_blocked_reason": "G7_open_exposure_drawdown",
        },
    }


def _write_jsonl(path, *records: dict[str, object]) -> bytes:
    content = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    path.write_text(content, encoding="utf-8")
    return content.encode("utf-8")


def test_source_replay_aggregates_captured_values_and_hashes_exact_input(tmp_path, capsys):
    input_path = tmp_path / "decisions.jsonl"
    raw_bytes = _write_jsonl(input_path, _decision_record())

    exit_code = main(["--input", str(input_path), "--json"])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"] is True
    assert report["no_live_policy_change"] is True
    assert report["claim_scope"] == "captured sizing counterfactual only; not profit evidence"
    assert report["inputs"] == [
        {
            "path": str(input_path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "bytes": len(raw_bytes),
        }
    ]
    source = report["results"]["source"]["by_venue"]["kalshi"]
    assert source["actual_capped_dollars"] == 8.0
    assert source["neutral_capped_dollars"] == 6.0
    assert source["delta_capped_dollars"] == -2.0
    assert source["decision_rows"][0]["gate"]["trade_blocked_reason"] == "G7_open_exposure_drawdown"
    encoded_report = json.dumps(report, sort_keys=True)
    assert "net_pnl" not in encoded_report
    assert "expectancy" not in encoded_report
    assert input_path.read_bytes() == raw_bytes


def test_source_replay_marks_multiplier_mismatch_unscorable(tmp_path, capsys):
    input_path = tmp_path / "decisions.jsonl"
    record = _decision_record()
    record["sizing_inputs"]["source_multiplier"] = 1.1
    _write_jsonl(input_path, record)

    exit_code = main(["--input", str(input_path), "--json"])

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["scorable"] == 0
    assert report["coverage"]["unscorable_by_reason"] == {"source_multiplier_mismatch": 1}
    assert report["results"]["source"]["by_venue"] == {}


def test_strict_source_replay_writes_incomplete_report_for_missing_snapshot_field(tmp_path, capsys):
    input_path = tmp_path / "decisions.jsonl"
    output_path = tmp_path / "report.json"
    record = _decision_record()
    del record["actual"]["paper_placeholder_applied"]
    _write_jsonl(input_path, record)

    exit_code = main(
        [
            "--input",
            str(input_path),
            "--require-complete-snapshot",
            "--output",
            str(output_path),
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"]["status"] == "incomplete_snapshot"
    assert report["coverage"]["unscorable_by_reason"] == {"missing_actual_paper_placeholder_applied": 1}
    assert json.loads(output_path.read_text(encoding="utf-8"))["verdict"]["status"] == "incomplete_snapshot"


def test_strict_replay_rejects_malformed_jsonl_and_unavailable_keyword_channel(tmp_path, capsys):
    input_path = tmp_path / "decisions.jsonl"
    raw_bytes = _write_jsonl(input_path, _decision_record()) + b"{not-json}\n"
    input_path.write_bytes(raw_bytes)

    exit_code = main(
        [
            "--input",
            str(input_path),
            "--channel",
            "keyword",
            "--require-complete-snapshot",
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["malformed_lines"] == 1
    assert report["coverage"]["unscorable_by_reason"] == {
        "keyword_counterfactual_unavailable": 1,
        "malformed_jsonl": 1,
    }


def test_source_replay_filters_window_and_keeps_venues_separate(tmp_path, capsys):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    _write_jsonl(first_path, _decision_record(decision_at="2026-07-27T23:59:00+00:00"))
    _write_jsonl(
        second_path,
        _decision_record(
            decision_at="2026-07-28T01:00:00+00:00",
            venue="polymarket_us",
        ),
    )

    exit_code = main(
        [
            "--input",
            str(first_path),
            "--input",
            str(second_path),
            "--since",
            "2026-07-28T00:00:00+00:00",
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["selected"] == 1
    assert set(report["results"]["source"]["by_venue"]) == {"polymarket_us"}


def test_replay_rejects_output_alias_of_input_without_mutating_evidence(tmp_path):
    input_path = tmp_path / "decisions.jsonl"
    alias_path = tmp_path / "alias.jsonl"
    raw_bytes = _write_jsonl(input_path, _decision_record())
    alias_path.symlink_to(input_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["--input", str(input_path), "--output", str(alias_path), "--json"])

    assert exc_info.value.code == 2
    assert input_path.read_bytes() == raw_bytes


def test_replay_rejects_output_hardlink_of_input_without_mutating_evidence(tmp_path):
    input_path = tmp_path / "decisions.jsonl"
    hardlink_path = tmp_path / "hardlink.jsonl"
    raw_bytes = _write_jsonl(input_path, _decision_record())
    hardlink_path.hardlink_to(input_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["--input", str(input_path), "--output", str(hardlink_path), "--json"])

    assert exc_info.value.code == 2
    assert input_path.read_bytes() == raw_bytes


def test_strict_replay_rejects_duplicate_inputs_and_lifecycle_ids(tmp_path, capsys):
    first_path = tmp_path / "first.jsonl"
    same_content_path = tmp_path / "same-content.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_raw = _write_jsonl(first_path, _decision_record())
    same_content_path.write_bytes(first_raw)
    duplicate_lifecycle = _decision_record(venue="polymarket_us")
    duplicate_lifecycle["ticker"] = "OTHER-1"
    _write_jsonl(second_path, duplicate_lifecycle)

    exit_code = main(
        [
            "--input",
            str(first_path),
            "--input",
            str(first_path),
            "--input",
            str(same_content_path),
            "--input",
            str(second_path),
            "--require-complete-snapshot",
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["scorable"] == 1
    assert report["coverage"]["unscorable_by_reason"] == {
        "duplicate_input_path": 1,
        "duplicate_input_content": 1,
        "duplicate_lifecycle_id": 1,
    }


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda record: record["sizing_inputs"].pop("bankroll"),
            "missing_sizing_inputs_bankroll",
        ),
        (
            lambda record: record.__setitem__("probability_actual", float("nan")),
            "nonfinite_json_constant",
        ),
    ],
)
def test_strict_replay_rejects_incomplete_or_nonfinite_snapshots(
    tmp_path,
    capsys,
    mutate,
    expected_reason,
):
    input_path = tmp_path / "decisions.jsonl"
    record = _decision_record()
    mutate(record)
    _write_jsonl(input_path, record)

    exit_code = main(
        [
            "--input",
            str(input_path),
            "--require-complete-snapshot",
            "--json",
        ]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["unscorable_by_reason"] == {expected_reason: 1}
