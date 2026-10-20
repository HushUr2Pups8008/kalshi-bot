from __future__ import annotations

import json

from scripts.simulations import post_soak_landing_simulation as sim


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_post_soak_simulation_applies_match_obs003_and_exec002_steps(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [
        {
            "type": "MATCH_DIAGNOSTIC",
            "ticker": "KXTRUMP-1",
            "headline": "Trump posts unrelated comment",
            "source": "Reuters",
            "match_score": 0.08,
            "matched_tokens": ["trump"],
            "heuristic_flags": ["single_named_entity_only", "minimal_overlap"],
            "ts": "2026-04-28T00:00:00+00:00",
        },
        {
            "type": "OPPORTUNITY",
            "ticker": "KXTRUMP-1",
            "headline": "Trump posts unrelated comment",
            "source": "Reuters",
            "edge": 0.0,
            "ts": "2026-04-28T00:00:01+00:00",
        },
        {
            "type": "OPPORTUNITY",
            "ticker": "KXKEEP-1",
            "headline": "supported match",
            "source": "AP",
            "edge": 0.0,
            "ts": "2026-04-28T00:01:00+00:00",
        },
        {
            "type": "BLEND_DECISION",
            "market_ticker": "KXKEEP-1",
            "trade_blocked_reason": "G1_blended_confidence",
            "ts": "2026-04-28T00:01:01+00:00",
        },
        {
            "type": "PAPER_TRADE",
            "ticker": "KXFISAEXTEND-26APR-MAY01",
            "series_ticker": "KXFISAEXTEND",
            "signal_headline": "same fisa headline",
            "signal_source": "VitalLaw.com",
            "ts": "2026-04-28T00:02:00+00:00",
        },
        {
            "type": "PAPER_TRADE",
            "ticker": "KXFISAEXTEND-26APR-MAY02",
            "series_ticker": "KXFISAEXTEND",
            "signal_headline": "same fisa headline",
            "signal_source": "VitalLaw.com",
            "ts": "2026-04-28T00:02:05+00:00",
        },
    ])

    report = sim.analyze([tmp_path / "logs/trades"])
    rows = {row["step"]: row for row in report["steps"]}

    assert rows["baseline"]["opportunity"] == 2
    assert rows["MATCH-001_B_prime"]["opportunity"] == 1
    assert rows["OBS-003"]["skipped"] == 1
    assert rows["EXEC-002"]["paper_trade"] == 1
    assert rows["EXEC-002"]["skipped"] == 2


def test_post_soak_simulation_json_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [{"type": "OPPORTUNITY", "ticker": "KX", "headline": "h", "source": "s", "ts": "2026-04-28T00:00:00+00:00"}])

    assert sim.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["steps"][0]["step"] == "baseline"
