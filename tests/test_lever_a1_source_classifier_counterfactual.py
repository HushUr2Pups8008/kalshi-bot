from __future__ import annotations

import json

from scripts.simulations import lever_a1_source_classifier_counterfactual as audit


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _row(event_type, source):
    return {
        "type": event_type,
        "source": source,
        "ticker": "KXTEST",
        "headline": f"{source} headline",
        "ts": "2026-04-28T00:00:00+00:00",
    }


def test_post_a1_classifier_expected_labels():
    assert audit.classify_post_a1("Department of War News Feed") == "official"
    assert audit.classify_post_a1("UN News - Global perspective Human stories") == "official"
    assert audit.classify_post_a1("Press releases - RSS") == "official"
    assert audit.classify_post_a1("Top Stories From the International Atomic Energy Agency") == "official"
    assert audit.classify_post_a1("Breaking Defense") == "news"


def test_counterfactual_reports_archive_shape_limitation(tmp_path):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(
        log,
        [
            {"type": "EVIDENCE_INGESTION", "evidence_id": "e1", "source_class": "other"},
            _row("OPPORTUNITY", "UN News - Global perspective Human stories"),
            _row("MATCH_DIAGNOSTIC", "Breaking Defense"),
        ],
    )

    report = audit.analyze([tmp_path / "logs/trades"])

    assert report["evidence_ingestion_total"] == 1
    assert report["evidence_ingestion_with_source_string"] == 0
    assert report["opportunity_source_label_replay"]["post_a1_distribution"] == {"official": 1}
    assert report["match_diagnostic_source_label_replay"]["post_a1_distribution"] == {"news": 1}


def test_json_output_round_trips(tmp_path, capsys):
    log = tmp_path / "logs/trades/live/trades.jsonl"
    _write_jsonl(log, [_row("OPPORTUNITY", "News - The White House")])

    assert audit.main(["--path", str(tmp_path / "logs/trades"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["opportunity_source_label_replay"]["event_total"] == 1
