from __future__ import annotations

import json
import importlib
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _base_row(index: int, *, resolved_yes: bool, signal_source: str = "source-a") -> dict[str, object]:
    decision_ts = datetime(2026, 4, 20, tzinfo=timezone.utc) + timedelta(hours=5 * index)
    return {
        "confidence": 0.85,
        "contracts": 1,
        "decision_kind": "dossier_update",
        "decision_ts": decision_ts.isoformat(),
        "dossier_version": 1,
        "edge": 0.20,
        "headline": f"fixture headline {index}",
        "llm_called": False,
        "market_title": f"Fixture market {index}",
        "market_yes_price": 60.0,
        "model_prob": 0.80,
        "news_class": "news",
        "resolution": "yes" if resolved_yes else "no",
        "resolved_yes": resolved_yes,
        "series_ticker": "KXFIXTURE",
        "side": "yes",
        "signal_source": signal_source,
        "signal_type": "state",
        "ticker": f"KXFIXTURE-{index:03d}",
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _run_counterfactual(tmp_path: Path, rows: list[dict[str, object]], capsys) -> dict[str, object]:
    side_flip_counterfactual = importlib.import_module("scripts.edge_replay.side_flip_counterfactual")

    dataset = tmp_path / "replay_dataset.jsonl"
    scores = tmp_path / "counterfactual_scores.json"
    prices = tmp_path / "historical_prices.json"
    coverage = tmp_path / "coverage_audit.json"
    output_dir = tmp_path / "out"
    report = tmp_path / "report.md"

    _write_jsonl(dataset, rows)
    _write_json(
        scores,
        {
            "rows": [
                {
                    **row,
                    "readiness_admitted": True,
                    "would_have_traded": True,
                    "would_have_won": bool(row["resolved_yes"]),
                    "would_have_won_if_taken": bool(row["resolved_yes"]),
                    "counterfactual_pnl": 0.0,
                }
                for row in rows
            ],
            "summary": {"fixture": True},
        },
    )
    _write_json(
        prices,
        {
            str(row["ticker"]): [
                {
                    "source": "fixture",
                    "ts": str(row["decision_ts"]),
                    "yes_price": row["market_yes_price"],
                }
            ]
            for row in rows
        },
    )
    _write_json(
        coverage,
        {
            "overall": {"fixture": True},
            "per_ticker": {str(row["ticker"]): {"covered": True} for row in rows},
        },
    )

    assert (
        side_flip_counterfactual.main(
            [
                "--dataset",
                str(dataset),
                "--scores",
                str(scores),
                "--prices",
                str(prices),
                "--coverage",
                str(coverage),
                "--output-dir",
                str(output_dir),
                "--write-report",
                str(report),
                "--json",
            ]
        )
        == 0
    )

    stdout_payload = json.loads(capsys.readouterr().out)
    summary_payload = json.loads((output_dir / "side_flip_summary.json").read_text(encoding="utf-8"))

    assert stdout_payload == summary_payload
    assert report.exists()
    assert (output_dir / "side_flip_per_row.jsonl").exists()
    assert len((output_dir / "side_flip_per_row.jsonl").read_text(encoding="utf-8").splitlines()) == len(rows)
    return summary_payload


def test_trivial_inversion_disqualification_fires_when_excess_is_sampling_noise(tmp_path, capsys):
    rows = [
        {
            **_base_row(index, resolved_yes=False),
            "market_yes_price": 4.1,
        }
        for index in range(12)
    ]

    payload = _run_counterfactual(tmp_path, rows, capsys)

    assert payload["verdict"] == "revert_trivial_inversion"
    assert payload["actual_flip_wins"] == 12
    assert payload["excess_wins_vs_market"] <= 0.5


def test_ic16_positive_slice_without_uniform_consistency_reverts(tmp_path, capsys):
    rows = [
        _base_row(index, resolved_yes=False, signal_source="source-a")
        for index in range(16)
    ] + [
        _base_row(index + 16, resolved_yes=True, signal_source="source-b")
        for index in range(4)
    ]

    payload = _run_counterfactual(tmp_path, rows, capsys)

    assert payload["verdict"] == "revert_uniform_consistency_failed"
    assert payload["ic16_slice_count"] >= 1
    assert payload["excess_wins_vs_market"] > 0.5


def test_ic16_positive_consistent_excess_wins_keeps_candidate(tmp_path, capsys):
    rows = [_base_row(index, resolved_yes=False) for index in range(12)]

    payload = _run_counterfactual(tmp_path, rows, capsys)

    assert payload["verdict"] == "keep_candidate"
    assert payload["ic16_slice_count"] >= 1
    assert payload["excess_wins_vs_market"] > 0.5
