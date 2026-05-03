from __future__ import annotations

import json

from scripts.simulations import match001_tokenization_equivalence_audit as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _match(ticker, tokens):
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "matched_tokens": tokens,
        "heuristic_flags": ["single_named_entity_only", "minimal_overlap"],
        "ts": "2026-04-28T00:00:00+00:00",
    }


def test_substring_simulation_diverges_from_tokenized_ticker_setdiff(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXTRUMPIRAN-26MAY01", ["trump"]), _match("KXOTHER-1", ["iran"])])

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["simulation_bprime_substring_suppressed_keys"] == 1
    assert report["production_current_pre_bprime_suppressed_keys"] == 1
    assert report["bprime_tokenize_setdiff_suppressed_keys"] == 0
    assert report["simulation_vs_tokenize_setdiff_symmetric_diff"] == 1
    assert report["ticker_token_examples"]["KXTRUMPIRAN-26MAY01"] == ["kxtrumpiran-26may01"]


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXOTHER-1", ["iran"])])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["match_diagnostic_total"] == 1
    assert "bprime_tokenize_setdiff_suppressed_keys" in payload
