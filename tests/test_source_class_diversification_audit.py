from __future__ import annotations

import json

from scripts.simulations import source_class_diversification_audit as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_source_class_audit_joins_opportunity_to_blend_evidence(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        {
            "type": "EVIDENCE_INGESTION",
            "market_ticker": "KXTEST",
            "evidence_id": "ev-news",
            "source_class": "news",
            "ts": "2026-04-28T00:00:00+00:00",
        },
        {
            "type": "EVIDENCE_INGESTION",
            "market_ticker": "KXTEST",
            "evidence_id": "ev-official",
            "source_class": "official",
            "ts": "2026-04-28T00:00:01+00:00",
        },
        {
            "type": "OPPORTUNITY",
            "ticker": "KXTEST",
            "headline": "test",
            "source": "Reuters",
            "edge": 0.06,
            "ts": "2026-04-28T00:00:10+00:00",
        },
        {
            "type": "BLEND_DECISION",
            "market_ticker": "KXTEST",
            "evidence_ids_contributing": ["ev-news", "ev-official"],
            "trade_blocked_reason": None,
            "ts": "2026-04-28T00:00:11+00:00",
        },
    ])

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["opportunity_total"] == 1
    assert report["opportunity_source_class_counts"] == {"news": 1, "official": 1}
    assert report["opportunity_class_diversity"]["2_classes"] == 1
    assert report["edge_sign_by_class_count"]["positive"]["news"] == 1


def test_source_class_audit_json_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        {"type": "OPPORTUNITY", "ticker": "KXTEST", "headline": "h", "source": "Reuters", "edge": 0.0, "ts": "2026-04-28T00:00:10+00:00"},
    ])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_total"] == 1
    assert payload["opportunity_class_diversity"]["unknown"] == 1
