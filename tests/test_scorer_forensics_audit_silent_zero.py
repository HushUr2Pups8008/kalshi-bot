from __future__ import annotations

import os
from collections import Counter

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()

os.environ.setdefault("KALSHI_API_KEY_ID", "test-key")
os.environ.setdefault("KALSHI_API_KEY_SECRET", _PEM)

from scripts.edge_replay.scorer_forensics_audit import (
    apply_episode_gate,
    build_cli_summary,
)


def _scored_row(
    *,
    decision_ts: str,
    market_yes_price: float | None,
    ticker: str = "KXTEST",
    model_prob: float = 0.80,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "decision_ts": decision_ts,
        "would_have_traded": True,
        "would_have_won": False,
        "counterfactual_pnl": 0.0,
        "readiness_admitted": True,
        "side": "yes",
        "model_prob": model_prob,
        "market_yes_price": market_yes_price,
        "edge": 0.20,
    }


def _episode_gate(rows: list[dict[str, object]]) -> tuple[list[dict[str, object]], Counter[str]]:
    _, gated_rows, skipped = apply_episode_gate(
        rows,
        require_readiness=True,
        require_price_sanity=False,
        require_signed_edge=False,
    )
    return gated_rows, skipped


def test_scorer_forensics_excludes_none_price_from_price_delta():
    rows, skipped = _episode_gate(
        [
            _scored_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=20.0),
            _scored_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=None),
        ]
    )

    assert skipped["skipped_no_price"] == 1
    assert rows[1]["would_have_traded"] is False
    assert rows[1]["forensic_skip_reason"] == "skipped_no_price"


def test_scorer_forensics_does_not_treat_zero_as_missing():
    rows, skipped = _episode_gate(
        [
            _scored_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=0.0),
            _scored_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=0.0),
        ]
    )

    assert skipped["skipped_no_price"] == 0
    assert skipped["paper_duplicate_position"] == 1
    assert rows[1]["forensic_skip_reason"] == "paper_duplicate_position"


def test_scorer_forensics_does_not_treat_50_as_missing():
    rows, skipped = _episode_gate(
        [
            _scored_row(decision_ts="2026-05-13T00:00:00+00:00", market_yes_price=50.0),
            _scored_row(decision_ts="2026-05-13T05:00:00+00:00", market_yes_price=50.0),
        ]
    )

    assert skipped["skipped_no_price"] == 0
    assert skipped["paper_duplicate_position"] == 1
    assert rows[1]["forensic_skip_reason"] == "paper_duplicate_position"


def test_scorer_forensics_skipped_no_price_counter_surfaces_in_output():
    payload = build_cli_summary(
        {
            "verdict": {
                "production_proxy_trades": 1,
                "production_proxy_wins": 0,
                "production_proxy_ic16_accepted_slices": 0,
            },
            "variant_summaries": [
                {"name": "baseline_abs_edge", "skipped": {}},
                {"name": "production_proxy", "skipped": {"skipped_no_price": 3}},
            ],
        },
        output="out.json",
        corrected_scores="corrected.json",
        report_doc="report.md",
    )

    assert payload["production_proxy_skipped_no_price"] == 3
