from __future__ import annotations

import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

os.environ.setdefault("KALSHI_API_KEY_ID", "test-key")
os.environ.setdefault("KALSHI_API_KEY_SECRET", _PEM)

from scripts.edge_replay import side_flip_counterfactual


def _raw_row(index: int) -> dict[str, object]:
    return {
        "ticker": "KXTEST",
        "decision_ts": f"2026-05-13T0{index * 5}:00:00+00:00",
        "resolved_yes": False,
        "contracts": 1,
        "market_yes_price": 20.0,
    }


def _gate_row(
    *,
    decision_ts: str,
    market_yes_price: float | None,
    ticker: str = "KXTEST",
    model_prob: float = 0.80,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "decision_ts": decision_ts,
        "candidate": True,
        "readiness": True,
        "paper_price": True,
        "signed_edge": True,
        "side": "yes",
        "model_prob": model_prob,
        "market_yes_price": market_yes_price,
    }


def _run_proxy(monkeypatch, gate_rows: list[dict[str, object]]):
    iterator = iter(gate_rows)
    monkeypatch.setattr(side_flip_counterfactual.g1, "_admission_row", lambda row, *, g1_threshold: next(iterator))
    return side_flip_counterfactual._production_proxy_rows([_raw_row(index) for index in range(len(gate_rows))])


def test_side_flip_counterfactual_excludes_none_price_from_price_delta(monkeypatch):
    accepted, skipped = _run_proxy(
        monkeypatch,
        [
            _gate_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=20.0),
            _gate_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=None),
        ],
    )

    assert len(accepted) == 1
    assert skipped["skipped_no_price"] == 1


def test_side_flip_counterfactual_does_not_treat_zero_as_missing(monkeypatch):
    accepted, skipped = _run_proxy(
        monkeypatch,
        [
            _gate_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=0.0),
            _gate_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=0.0),
        ],
    )

    assert len(accepted) == 1
    assert skipped.get("skipped_no_price", 0) == 0
    assert skipped["paper_duplicate_position"] == 1


def test_side_flip_counterfactual_does_not_treat_50_as_missing(monkeypatch):
    accepted, skipped = _run_proxy(
        monkeypatch,
        [
            _gate_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=50.0),
            _gate_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=50.0),
        ],
    )

    assert len(accepted) == 1
    assert skipped.get("skipped_no_price", 0) == 0
    assert skipped["paper_duplicate_position"] == 1


def test_side_flip_counterfactual_skipped_no_price_counter_surfaces_in_output():
    payload = side_flip_counterfactual.build_cli_summary(
        {
            "summary": {
                "verdict_label": "diagnostic_only_revert",
                "admitted_rows": 1,
                "ic16_slice_count": 0,
                "excess_wins_vs_market_flip": 0.0,
                "production_proxy_skipped": {"skipped_no_price": 2},
            }
        },
        summary_path="summary.json",
        report_path="report.md",
    )

    assert payload["production_proxy_skipped_no_price"] == 2
