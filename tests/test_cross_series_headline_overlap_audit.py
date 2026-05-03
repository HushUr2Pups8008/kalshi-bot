from __future__ import annotations

import json

from scripts.simulations import cross_series_headline_overlap_audit as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _opportunity(ticker, headline):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": headline,
        "source": "Reuters",
        "ts": "2026-04-28T00:00:00+00:00",
    }


def test_cross_series_exact_normalized_headline_overlap(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _opportunity("KXTRUMPIRAN-26MAY01", "Trump says Iran talks resume!"),
            _opportunity("KXMOCTRUMP25-26-MAY01", "Trump says Iran talks resume"),
            _opportunity("KXTRUMPIRAN-26MAY02", "Series-local duplicate"),
            _opportunity("KXTRUMPIRAN-26MAY03", "Series-local duplicate"),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["opportunity_total"] == 4
    assert report["cross_series_headline_groups"] == 1
    assert report["cross_series_opportunity_count"] == 2
    assert report["verdict"] == "supports_lever_c"
    assert report["top_groups"][0]["series"] == ["KXMOCTRUMP25", "KXTRUMPIRAN"]


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_opportunity("KXONE-1", "one headline")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_total"] == 1
    assert payload["cross_series_headline_groups"] == 0
