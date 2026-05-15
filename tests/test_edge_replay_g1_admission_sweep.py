import json

import pytest

from scripts.edge_replay import g1_admission_sweep as sweep


def _row(
    *,
    ticker: str,
    ts: str,
    confidence: float,
    price: float = 20.0,
    model_prob: float = 0.80,
    edge: float = 0.60,
    side: str | None = None,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "decision_ts": ts,
        "model_prob": model_prob,
        "confidence": confidence,
        "entry_price_cents": price,
        "edge": edge,
        "side": side,
    }


def test_forbidden_output_label_guard_rejects_outcome_fields():
    with pytest.raises(ValueError, match="forbidden outcome-sensitive field"):
        sweep.guard_output_labels(["threshold", "wins"])


def test_sweep_counts_admissions_without_outcome_fields():
    rows = [
        _row(ticker="KXA", ts="2026-05-01T00:00:00+00:00", confidence=0.060),
        _row(ticker="KXB", ts="2026-05-01T00:01:00+00:00", confidence=0.045),
        _row(ticker="KXC", ts="2026-05-01T00:02:00+00:00", confidence=0.020, price=1.0),
        _row(ticker="KXD", ts="2026-05-01T00:03:00+00:00", confidence=0.060, edge=-0.60, side="yes"),
    ]

    result = sweep.run_sweep(rows, thresholds=(0.05, 0.04, 0.02))

    assert result["rows"] == 4
    assert result["thresholds"][0] == {
        "g1_threshold": 0.05,
        "baseline_abs_edge": 4,
        "readiness_only": 2,
        "paper_price_sanity": 3,
        "readiness_plus_price_sanity": 2,
        "readiness_price_signed_edge": 1,
        "production_proxy": 1,
        "skipped": {
            "baseline_abs_edge": {},
            "readiness_only": {"readiness_not_admitted": 2},
            "paper_price_sanity": {"paper_price_sanity": 1},
            "readiness_plus_price_sanity": {"readiness_not_admitted": 2},
            "readiness_price_signed_edge": {
                "readiness_not_admitted": 2,
                "signed_edge_mismatch": 1,
            },
            "production_proxy": {
                "readiness_not_admitted": 2,
                "signed_edge_mismatch": 1,
            },
        },
    }
    assert result["thresholds"][1]["production_proxy"] == 2
    assert result["thresholds"][2]["production_proxy"] == 2

    forbidden = sweep.FORBIDDEN_OUTPUT_PATTERN
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                assert forbidden.search(str(key)) is None
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)


def test_production_proxy_applies_cooldown_and_duplicate_gates():
    rows = [
        _row(ticker="KXA", ts="2026-05-01T00:00:00+00:00", confidence=0.060),
        _row(ticker="KXA", ts="2026-05-01T01:00:00+00:00", confidence=0.060),
        _row(ticker="KXA", ts="2026-05-01T05:00:00+00:00", confidence=0.060),
    ]

    result = sweep.run_sweep(rows, thresholds=(0.05,))
    threshold = result["thresholds"][0]

    assert threshold["readiness_price_signed_edge"] == 3
    assert threshold["production_proxy"] == 1
    assert threshold["skipped"]["production_proxy"] == {
        "paper_duplicate_position": 1,
        "paper_ticker_cooldown": 1,
    }


def test_admission_row_accepts_legacy_market_yes_price_key():
    """F-11 P1-C: pre-bounce replay rows carry ``market_yes_price`` instead of
    ``entry_price_cents``. The _entry_price_cents() fallback in g1_admission_sweep
    must resolve the price so the row is not skipped as no-price.
    """
    legacy_row = {
        "ticker": "KX-LEGACY",
        "decision_ts": "2026-05-01T00:00:00+00:00",
        "model_prob": 0.80,
        "confidence": 0.10,
        "market_yes_price": 30.0,  # legacy key only — no entry_price_cents
        "edge": 0.50,
        "side": "yes",
    }
    admission = sweep._admission_row(legacy_row, g1_threshold=0.05)
    # price must be resolved (not None) via the legacy fallback;
    # _admission_row stores the resolved value under "market_yes_price" internally
    assert admission["market_yes_price"] is not None, (
        "_admission_row must resolve price from market_yes_price when entry_price_cents is absent"
    )
    assert admission["market_yes_price"] == 30.0


def test_cli_writes_report_with_projection_disclaimer(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _row(ticker="KXA", ts="2026-05-01T00:00:00+00:00", confidence=0.060),
                _row(ticker="KXB", ts="2026-05-01T00:01:00+00:00", confidence=0.045),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.md"

    assert sweep.main(["--dataset", str(dataset), "--report", str(report), "--thresholds", "0.05,0.04"]) == 0

    text = report.read_text(encoding="utf-8")
    assert "This is not an IC §16 replay" in text
    assert "| 0.04 | 2 | 2 | 2 | 2 | 2 | 2 |" in text
