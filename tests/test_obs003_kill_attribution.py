from __future__ import annotations

import json

from scripts import obs003_kill_attribution


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _opportunity(ticker="KXTEST", ts="2026-04-28T03:42:20+00:00"):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": "Test headline",
        "edge": 0.0,
        "ts": ts,
    }


def _blend(ticker="KXTEST", reason="G1_blended_confidence", ts="2026-04-28T03:42:21+00:00"):
    return {
        "type": "BLEND_DECISION",
        "market_ticker": ticker,
        "trade_blocked_reason": reason,
        "ts": ts,
    }


def test_happy_path_accounts_for_skipped_exit(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        _opportunity(),
        _blend(reason="G1_blended_confidence"),
        {
            "type": "SKIPPED",
            "ticker": "KXTEST",
            "reason": "edge +0.0000 below min_edge 0.02",
            "ts": "2026-04-28T03:42:22+00:00",
        },
    ])

    report = obs003_kill_attribution.analyze([tmp_path / "logs/trades"])

    assert report["opportunity_total"] == 1
    assert report["accounted_exits_total"] == 1
    assert report["accounted_exit_types"] == {"SKIPPED": 1}
    assert report["silent_exits_total"] == 0
    assert report["validation"]["balanced"] is True


def test_silent_exit_uses_matching_blend_decision_reason(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        _opportunity(ticker="KXSILENT"),
        _blend(ticker="KXSILENT", reason="G3_disagreement_score"),
    ])

    report = obs003_kill_attribution.analyze([tmp_path / "logs/trades"])

    assert report["opportunity_total"] == 1
    assert report["accounted_exits_total"] == 0
    assert report["silent_exits_total"] == 1
    assert report["silent_exit_reasons"] == {"G3_disagreement_score": 1}
    assert report["per_ticker_silent_exit_drivers"]["KXSILENT"] == {"G3_disagreement_score": 1}


def test_unattributed_exit_surfaces_example(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_opportunity(ticker="KXMISSING")])

    report = obs003_kill_attribution.analyze([tmp_path / "logs/trades"])

    assert report["opportunity_total"] == 1
    assert report["unattributed_total"] == 1
    assert report["unattributed_examples"][0]["ticker"] == "KXMISSING"
    assert report["validation"]["balanced"] is True
