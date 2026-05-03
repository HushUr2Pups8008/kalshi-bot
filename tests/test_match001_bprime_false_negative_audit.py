from __future__ import annotations

import json

from scripts.simulations import match001_bprime_false_negative_audit as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _match(ticker, tokens, score=0.07):
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "matched_tokens": tokens,
        "match_score": score,
        "heuristic_flags": ["single_named_entity_only", "minimal_overlap"],
        "ts": "2026-04-28T00:00:00+00:00",
    }


def test_false_negative_proxy_counts_weak_substring_escape(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _match("KXTRUMPIRAN-26MAY01", ["trump"]),  # suppressed, not escape
            _match("KXTRUMPIRAN-26MAY01", ["trump", "visit"]),  # escaped weak support
            _match("KXOTHER-1", ["iran"]),  # no ticker substring
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["bprime_shape_total"] == 3
    assert report["bprime_suppressed_total"] == 1
    assert report["substring_escape_total"] == 1
    assert report["likely_false_negative_total"] == 1


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXTRUMPIRAN-26MAY01", ["trump", "visit"])])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["likely_false_negative_total"] == 1
