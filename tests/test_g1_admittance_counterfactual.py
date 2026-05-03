from __future__ import annotations

import json

from scripts.simulations import g1_admittance_counterfactual as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _blend(ticker, confidence, regime, reason="G1_blended_confidence"):
    return {
        "type": "BLEND_DECISION",
        "market_ticker": ticker,
        "blended_p": 0.56,
        "blended_confidence": confidence,
        "regime_confidence": regime,
        "trade_blocked_reason": reason,
        "ts": "2026-04-28T00:00:00+00:00",
    }


def _signal(ticker, market_price=0.50):
    return {
        "type": "SIGNAL_ANALYSIS_DETAIL",
        "ticker": ticker,
        "market_price": market_price,
        "ts": "2026-04-28T00:00:02+00:00",
    }


def test_g1_floor_sweep_counts_admitted_candidates_and_edges(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _blend("KXADMIT04", 0.20, 0.21),
            _signal("KXADMIT04"),
            _blend("KXADMIT03", 0.20, 0.16),
            _signal("KXADMIT03", 0.54),
            _blend("KXBLOCK", 0.10, 0.20),
            _signal("KXBLOCK"),
            _blend("KXOTHER", 0.90, 0.90, reason="G2_evidence_source_class_diversity"),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"], floors=(0.04, 0.03))
    rows = {row["g1_floor"]: row for row in report["floor_rows"]}

    assert report["g1_kill_total"] == 3
    assert rows[0.04]["g1_kills_admitted"] == 1
    assert rows[0.03]["g1_kills_admitted"] == 2
    assert rows[0.04]["edge_ge_0_02"] == 1
    assert rows[0.03]["edge_ge_0_02"] == 2


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_blend("KX", 0.20, 0.21), _signal("KX")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--floor", "0.04", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["g1_kill_total"] == 1
    assert payload["floor_rows"][0]["g1_floor"] == 0.04
