from __future__ import annotations

import json

from scripts.simulations import matcher_jaccard_threshold_bisection as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _match(ticker, headline, score, source="Reuters", flags=None):
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ticker": ticker,
        "headline": headline,
        "source": source,
        "match_score": score,
        "matched_tokens": ["iran"],
        "heuristic_flags": flags or [],
        "ts": "2026-04-28T03:42:20+00:00",
    }


def _opportunity(ticker, headline, source="Reuters"):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": headline,
        "source": source,
        "edge": 0.0,
        "ts": "2026-04-28T03:42:21+00:00",
    }


def test_threshold_sweep_counts_retained_diagnostics_and_opportunities(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        _match("KXLOW", "low score", 0.061),
        _match("KXHIGH", "high score", 0.120),
        _opportunity("KXLOW", "low score"),
        _opportunity("KXHIGH", "high score"),
    ])

    report = audit.analyze([tmp_path / "logs/trades"], thresholds=(0.06, 0.10))

    rows = {row["threshold"]: row for row in report["threshold_rows"]}
    assert rows[0.06]["match_diagnostics_retained"] == 2
    assert rows[0.06]["opportunities_retained"] == 2
    assert rows[0.10]["match_diagnostics_retained"] == 1
    assert rows[0.10]["opportunities_retained"] == 1
    assert rows[0.10]["opportunity_retention"] == 0.5


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXHIGH", "high score", 0.120), _opportunity("KXHIGH", "high score")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--threshold", "0.10", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_total"] == 1
    assert payload["threshold_rows"][0]["threshold"] == 0.10
