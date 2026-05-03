from __future__ import annotations

import json

from scripts.simulations import match001_bprime_anchor_sizing as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _match(ticker, tokens, flags):
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "matched_tokens": tokens,
        "heuristic_flags": flags,
        "match_score": 0.08,
        "ts": "2026-04-28T00:00:00+00:00",
    }


def _opportunity(ticker):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "ts": "2026-04-28T00:00:01+00:00",
    }


def test_bprime_suppresses_ticker_only_weak_overlap(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _match("KXTRUMPIRAN-26MAY01", ["trump"], ["single_named_entity_only", "minimal_overlap"]),
            _match("KXKEEP-1", ["iran", "deal"], ["single_named_entity_only", "minimal_overlap"]),
            _opportunity("KXTRUMPIRAN-26MAY01"),
            _opportunity("KXKEEP-1"),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["suppressed_match_keys"] == 1
    assert report["opportunities_retained"] == 1
    assert report["canonical_ticker_guard_status"] == "FAIL"
    assert report["canonical_ticker_suppressed_match_count"] == 1


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXKEEP-1", ["iran", "deal"], ["minimal_overlap"])])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["match_diagnostic_total"] == 1
    assert "canonical_event_tickers" in payload
