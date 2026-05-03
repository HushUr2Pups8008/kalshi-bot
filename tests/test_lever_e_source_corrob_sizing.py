from __future__ import annotations

import json

from scripts.simulations import lever_e_source_corrob_sizing as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _evidence(eid, source_class):
    return {
        "type": "EVIDENCE_INGESTION",
        "evidence_id": eid,
        "source_class": source_class,
        "source": f"{source_class}-{eid}",
    }


def _blend(ticker, ids):
    return {
        "type": "BLEND_DECISION",
        "market_ticker": ticker,
        "evidence_ids_contributing": ids,
        "ts": "2026-04-28T00:00:00+00:00",
    }


def _opportunity(ticker, edge=0.0):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "edge": edge,
        "ts": "2026-04-28T00:00:01+00:00",
    }


def _paper(ticker):
    return {
        "type": "PAPER_TRADE",
        "ticker": ticker,
        "signal_headline": f"{ticker} headline",
        "signal_source": "Reuters",
        "ts": "2026-04-28T00:00:02+00:00",
    }


def test_source_class_threshold_sizing(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _evidence("e1", "news"),
            _evidence("e2", "official"),
            _evidence("e3", "market"),
            _blend("KXONE", ["e1"]),
            _opportunity("KXONE", 0.0),
            _blend("KXTWO", ["e1", "e2"]),
            _opportunity("KXTWO", 0.03),
            _paper("KXTWO"),
            _blend("KXTHREE", ["e1", "e2", "e3"]),
            _opportunity("KXTHREE", -0.02),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["source_class_count_distribution"] == {1: 1, 2: 1, 3: 1}
    assert report["source_count_distribution"] == {1: 1, 2: 1, 3: 1}
    assert report["source_class_thresholds"]["N>=2"]["retained_opportunities"] == 2
    assert report["source_class_thresholds"]["N>=2"]["retained_paper_trades"] == 1
    assert report["source_class_thresholds"]["N>=3"]["retained_opportunities"] == 1
    assert report["source_thresholds"]["N>=2"]["retained_opportunities"] == 2


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_evidence("e1", "news"), _blend("KXONE", ["e1"]), _opportunity("KXONE")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_total"] == 1
    assert "source_class_thresholds" in payload
    assert "source_thresholds" in payload
