from __future__ import annotations

import json

from scripts.simulations import pre_llm_gate_reenable_audit as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _match(ticker, score, would_block=False):
    return {
        "type": "MATCH_DIAGNOSTIC",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "match_score": score,
        "would_fail_pre_llm_gate": would_block,
        "ts": "2026-04-28T03:42:20+00:00",
    }


def _detail(ticker, quality=True, would_block=False):
    return {
        "type": "SIGNAL_ANALYSIS_DETAIL",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "pre_llm_quality_pass": quality,
        "pre_llm_would_block": would_block,
        "ts": "2026-04-28T03:42:21+00:00",
    }


def _opportunity(ticker, edge=0.0):
    return {
        "type": "OPPORTUNITY",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "edge": edge,
        "ts": "2026-04-28T03:42:22+00:00",
    }


def _paper(ticker):
    return {
        "type": "PAPER_TRADE",
        "ticker": ticker,
        "headline": f"{ticker} headline",
        "source": "Reuters",
        "ts": "2026-04-28T03:42:23+00:00",
    }


def test_gate_and_threshold_retention_curve(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _match("KXKEEP", 0.09),
            _detail("KXKEEP"),
            _opportunity("KXKEEP", 0.06),
            _paper("KXKEEP"),
            _match("KXGATE", 0.09),
            _detail("KXGATE", quality=False, would_block=True),
            _opportunity("KXGATE", 0.04),
            _match("KXSCORE", 0.05),
            _detail("KXSCORE"),
            _opportunity("KXSCORE", 0.03),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"], thresholds=(0.04, 0.08))
    rows = {row["threshold"]: row for row in report["threshold_rows"]}

    assert rows[0.04]["opportunities_retained"] == 2
    assert rows[0.04]["gate_blocked_opportunities"] == 1
    assert rows[0.08]["opportunities_retained"] == 1
    assert rows[0.08]["score_blocked_opportunities"] == 1
    assert rows[0.08]["paper_trades_retained"] == 1


def test_nonzero_edge_enrichment_is_reported(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            _match("KXZERO", 0.09),
            _detail("KXZERO"),
            _opportunity("KXZERO", 0.0),
            _match("KXEDGE", 0.09),
            _detail("KXEDGE"),
            _opportunity("KXEDGE", 0.06),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"], thresholds=(0.08,))
    row = report["threshold_rows"][0]

    assert report["baseline_nonzero_edge_count"] == 1
    assert row["nonzero_edge_retained"] == 1
    assert row["mean_abs_edge_lift"] == 1.0


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_match("KXKEEP", 0.09), _detail("KXKEEP"), _opportunity("KXKEEP")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--threshold", "0.08", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_total"] == 1
    assert payload["threshold_rows"][0]["threshold"] == 0.08
