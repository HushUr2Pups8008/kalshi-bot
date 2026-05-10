from __future__ import annotations

import json
from pathlib import Path

from scripts.edge_replay.cycle17d_schema_audit import audit_schema_compatibility, main
from tests._helpers import write_jsonl


def _row(**overrides):
    row = {
        "ticker": "KXTEST-26",
        "decision_ts": "2026-05-10T12:00:00+00:00",
        "signal_source": "legal_watch",
        "headline": "Court order published",
        "market_yes_price": 37.5,
        "resolved_yes": True,
        "edge": 0.11,
        "model_prob": 0.68,
        "confidence": 0.91,
        "side": "yes",
        "series_ticker": "KXTEST",
        "market_family": "KXTEST",
        "signal_type": "court_order",
        "news_class": "legal",
        "decision_kind": "paper_trade",
        "contracts": 1,
    }
    row.update(overrides)
    return row


def _write_inputs(tmp_path: Path, *, bad_cycle15b: bool = False) -> tuple[dict[str, Path], Path]:
    paths = {
        "cycle13_live": tmp_path / "cycle13_live.jsonl",
        "cycle13_local": tmp_path / "cycle13_local.jsonl",
        "cycle15b": tmp_path / "cycle15b.jsonl",
        "cycle16d": tmp_path / "cycle16d.jsonl",
    }
    for source, path in paths.items():
        row = _row(ticker=f"{source.upper()}-26")
        if bad_cycle15b and source == "cycle15b":
            row.pop("ticker")
        write_jsonl(path, [row])

    merged = tmp_path / "merged.jsonl"
    write_jsonl(
        merged,
        [
            _row(ticker="PRE-26", cohort="PRE_FIX"),
            _row(ticker="POST-26", cohort="POST_FIX_REBUILT"),
        ],
    )
    return paths, merged


def test_schema_audit_passes_compatible_vintages_and_writes_json(tmp_path, capsys):
    inputs, merged = _write_inputs(tmp_path)
    output = tmp_path / "schema_audit.json"

    report = audit_schema_compatibility(inputs=inputs, merged=merged, output=output)

    assert report["compatible"] is True
    assert report["summary"]["source_count"] == 4
    assert report["summary"]["merged_row_count"] == 2
    assert report["sources"]["cycle13_live"]["missing_required_fields"] == {}
    assert report["merged"]["cohort_flags"]["invalid"] == {}
    assert report["scorer"]["charter_sweep_fields"] == [
        "signal_source",
        "market_family",
        "signal_type",
        "news_class",
        "cohort",
    ]
    assert report["scorer"]["production_proxy_required_fields"] == [
        "market_yes_price",
        "edge",
        "model_prob",
        "confidence",
        "resolved_yes",
    ]
    assert output.exists()

    status = main(
        [
            "--cycle13-live",
            str(inputs["cycle13_live"]),
            "--cycle13-local",
            str(inputs["cycle13_local"]),
            "--cycle15b",
            str(inputs["cycle15b"]),
            "--cycle16d",
            str(inputs["cycle16d"]),
            "--merged",
            str(merged),
            "--output",
            str(output),
            "--json",
        ]
    )

    stdout_report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert stdout_report["compatible"] is True
    assert stdout_report["output_path"] == str(output)


def test_schema_audit_fails_when_required_field_missing(tmp_path):
    inputs, merged = _write_inputs(tmp_path, bad_cycle15b=True)
    output = tmp_path / "schema_audit.json"

    report = audit_schema_compatibility(inputs=inputs, merged=merged, output=output)

    assert report["compatible"] is False
    assert report["sources"]["cycle15b"]["missing_required_fields"] == {"ticker": 1}
    assert report["summary"]["blocking_issue_count"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["compatible"] is False


def test_schema_audit_fails_when_merged_market_family_missing(tmp_path):
    inputs, merged = _write_inputs(tmp_path)
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0].pop("market_family")
    write_jsonl(merged, rows)
    output = tmp_path / "schema_audit.json"

    report = audit_schema_compatibility(inputs=inputs, merged=merged, output=output)

    assert report["compatible"] is False
    assert report["merged"]["missing_required_fields"] == {"market_family": 1}


def test_schema_audit_fails_when_production_proxy_input_missing(tmp_path):
    inputs, merged = _write_inputs(tmp_path)
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0].pop("market_yes_price")
    write_jsonl(merged, rows)
    output = tmp_path / "schema_audit.json"

    report = audit_schema_compatibility(inputs=inputs, merged=merged, output=output)

    assert report["compatible"] is False
    assert report["merged"]["structurally_compatible"] is True
    assert report["merged"]["production_proxy_ready"] is False
    assert report["merged"]["missing_production_proxy_fields"] == {"market_yes_price": 1}


def test_schema_audit_fails_when_production_proxy_input_invalid(tmp_path):
    inputs, merged = _write_inputs(tmp_path)
    rows = [json.loads(line) for line in merged.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["confidence"] = 1.5
    rows[1]["resolved_yes"] = "maybe"
    write_jsonl(merged, rows)
    output = tmp_path / "schema_audit.json"

    report = audit_schema_compatibility(inputs=inputs, merged=merged, output=output)

    assert report["compatible"] is False
    assert report["merged"]["invalid_value_types"] == {"confidence": 1, "resolved_yes": 1}
