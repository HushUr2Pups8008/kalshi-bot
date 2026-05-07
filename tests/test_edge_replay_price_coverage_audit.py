import json
from pathlib import Path

from scripts.edge_replay.price_coverage_audit import audit_price_coverage, load_jsonl


def _write_prices(path: Path, rows_by_ticker: dict[str, list[dict[str, object]]]) -> None:
    path.write_text(json.dumps(rows_by_ticker), encoding="utf-8")


def test_price_coverage_audit_uses_last_price_at_or_before_decision(tmp_path):
    prices_path = tmp_path / "prices.json"
    _write_prices(
        prices_path,
        {
            "KXTEST-26MAY01": [
                {"ts": "2026-05-01T00:00:00+00:00", "yes_price": 41},
                {"ts": "2026-05-01T00:05:00+00:00", "yes_price": 44},
            ]
        },
    )
    rows = [
        {
            "ticker": "KXTEST-26MAY01",
            "decision_ts": "2026-05-01T00:06:00+00:00",
            "decision_kind": "dossier_update",
            "market_yes_price": None,
        }
    ]

    report = audit_price_coverage(rows, prices_path)

    assert report["overall"]["status"] == "pass"
    assert report["overall"]["coverage"] == 1.0
    assert report["per_ticker"][0]["covered_reconstructed_rows"] == 1


def test_price_coverage_audit_flags_per_ticker_anomaly_without_failing_overall(tmp_path):
    prices_path = tmp_path / "prices.json"
    _write_prices(
        prices_path,
        {
            "KXGOOD": [{"ts": "2026-05-01T00:00:00+00:00", "yes_price": 50}],
        },
    )
    rows = [
        {"ticker": "KXGOOD", "decision_ts": f"2026-05-01T00:0{i}:00+00:00", "market_yes_price": None}
        for i in range(9)
    ]
    rows.append({"ticker": "KXMISSING", "decision_ts": "2026-05-01T00:00:00+00:00", "market_yes_price": None})

    report = audit_price_coverage(rows, prices_path)

    assert report["overall"]["status"] == "pass"
    assert report["overall"]["coverage"] == 0.9
    assert [row["ticker"] for row in report["anomalies"]] == ["KXMISSING"]
    assert [row["ticker"] for row in report["escalations"]] == ["KXMISSING"]
    assert report["missing_examples"][0]["reason"] == "no_price_series_for_ticker"


def test_price_coverage_audit_fails_below_overall_threshold(tmp_path):
    prices_path = tmp_path / "prices.json"
    _write_prices(prices_path, {})
    rows = [
        {"ticker": "KXMISSING", "decision_ts": "2026-05-01T00:00:00+00:00", "market_yes_price": None},
        {"ticker": "KXMISSING", "decision_ts": "2026-05-01T00:01:00+00:00", "market_yes_price": None},
    ]

    report = audit_price_coverage(rows, prices_path)

    assert report["overall"]["status"] == "escalation_required"
    assert report["overall"]["passes_overall_threshold"] is False


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "dataset.jsonl"
    path.write_text('{"ticker":"KX1"}\n\n{"ticker":"KX2"}\n', encoding="utf-8")

    assert load_jsonl(path) == [{"ticker": "KX1"}, {"ticker": "KX2"}]
