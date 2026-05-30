from pathlib import Path

from scripts import output_path_inventory


def test_classifies_contract_paths():
    assert output_path_inventory.classify_literal("logs/trades/live/trades.jsonl") == "raw"
    assert output_path_inventory.classify_literal("logs/reports/daily/foo.txt") == "report"
    assert output_path_inventory.classify_literal("logs/state/derived/feed_health.json") == "derived"
    assert output_path_inventory.classify_literal("logs/backups/db_snapshots") == "backup"


def test_flags_unclassified_report_like_path(tmp_path: Path):
    source = tmp_path / "writer.py"
    source.write_text('OUT = "analysis_outputs/new_report.json"\\n', encoding="utf-8")

    findings = output_path_inventory.scan_file(source)

    assert len(findings) == 1
    assert findings[0].classification == "unclassified"
    assert findings[0].literal == "analysis_outputs/new_report.json"


def test_allows_documented_legacy_read_paths(tmp_path: Path):
    source = tmp_path / "reader.py"
    source.write_text('DEFAULT = "logs/trades/trades.jsonl"\\n', encoding="utf-8")

    findings = output_path_inventory.scan_file(source)

    assert findings == []
