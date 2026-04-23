
from scripts.validate_trade_log_cutover import render_report, validate_cutover
from tests._helpers import cleanup_tmp_dir, make_tmp_dir, write_jsonl


def test_validate_cutover_passes_for_matching_legacy_and_partitioned_layouts():
    tmp = make_tmp_dir("validate_trade_log_cutover")
    try:
        legacy = tmp / "trades.jsonl"
        new_root = tmp / "trades"
        db_path = tmp / "missing.db"
        records = [
            {"type": "SIGNAL", "source": "Reuters", "ticker": "KX1", "ts": "2026-04-11T00:00:00+00:00"},
            {"type": "EARLY_STALE_DROP", "source": "Reuters", "reason": "stale_by_source_policy", "ticker": "KX1", "ts": "2026-04-11T00:01:00+00:00"},
        ]
        write_jsonl(legacy, records)
        write_jsonl(new_root / "archive" / "2026" / "04" / "2026-04-11.jsonl", records)

        result = validate_cutover(legacy, new_root, db_path, since=None, until=None)

        assert result["comparison"]["ok"] is True
        assert result["comparison"]["mismatched_count"] == 0
    finally:
        cleanup_tmp_dir(tmp)


def test_validate_cutover_reports_mismatch_for_different_datasets():
    tmp = make_tmp_dir("validate_trade_log_cutover")
    try:
        legacy = tmp / "trades.jsonl"
        new_root = tmp / "trades"
        db_path = tmp / "missing.db"
        write_jsonl(
            legacy,
            [
                {"type": "SIGNAL", "source": "Reuters", "ticker": "KX1", "ts": "2026-04-11T00:00:00+00:00"},
            ],
        )
        write_jsonl(
            new_root / "archive" / "2026" / "04" / "2026-04-11.jsonl",
            [
                {"type": "SKIPPED", "reason": "cooldown", "source": "Reuters", "ticker": "KX1", "ts": "2026-04-11T00:00:00+00:00"},
            ],
        )

        result = validate_cutover(legacy, new_root, db_path, since=None, until=None)

        assert result["comparison"]["ok"] is False
        mismatch_metrics = {row["metric"] for row in result["comparison"]["mismatched"]}
        assert "records.per_event_type_counts.SIGNAL" in mismatch_metrics or "trade_log_summary.event_counts.SIGNAL" in mismatch_metrics
    finally:
        cleanup_tmp_dir(tmp)


def test_render_report_is_readable_and_includes_pass_fail_summary():
    tmp = make_tmp_dir("validate_trade_log_cutover")
    try:
        legacy = tmp / "trades.jsonl"
        new_root = tmp / "trades"
        db_path = tmp / "missing.db"
        records = [
            {"type": "SIGNAL", "source": "Reuters", "ticker": "KX1", "ts": "2026-04-11T00:00:00+00:00"},
        ]
        write_jsonl(legacy, records)
        write_jsonl(new_root / "archive" / "2026" / "04" / "2026-04-11.jsonl", records)

        report = render_report(validate_cutover(legacy, new_root, db_path, since=None, until=None))

        assert "TRADE LOG CUTOVER VALIDATION" in report
        assert "Status            : PASS" in report
        assert "Matched Checks" in report
        assert "Mismatches" in report
    finally:
        cleanup_tmp_dir(tmp)
