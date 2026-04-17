from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scripts.daily_review as daily_review
import scripts.trade_log_summary as trade_log_summary
from utils.reporting_helpers import resolve_recent_window, warn_if_full_trade_root_scan


def _tmp_root() -> Path:
    root = Path(__file__).resolve().parent / "_tmp_reporting_window_hardening" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _make_trade_root(root: Path) -> Path:
    (root / "archive" / "2026" / "04").mkdir(parents=True, exist_ok=True)
    (root / "live").mkdir(parents=True, exist_ok=True)
    (root / "live" / "trades.jsonl").write_text("", encoding="utf-8")
    return root


def test_resolve_recent_window_defaults_to_last_24_hours():
    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    since, until, used_default = resolve_recent_window(None, None, now=now)

    assert used_default is True
    assert until == now
    assert since == now - timedelta(hours=24)


def test_resolve_recent_window_preserves_explicit_bounds():
    since = datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 16, 23, 59, tzinfo=timezone.utc)

    effective_since, effective_until, used_default = resolve_recent_window(since, until)

    assert used_default is False
    assert effective_since == since
    assert effective_until == until


def test_warn_if_full_trade_root_scan_emits_once(capsys):
    temp_root = _tmp_root()
    root = _make_trade_root(temp_root / "logs" / "trades")

    try:
        warn_if_full_trade_root_scan(root, since=None, until=None)

        captured = capsys.readouterr()
        assert "scanning full trade-log root with no time filter" in captured.err
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_warn_if_full_trade_root_scan_skips_when_filtered(capsys):
    temp_root = _tmp_root()
    root = _make_trade_root(temp_root / "logs" / "trades")
    since = datetime(2026, 4, 16, 0, 0, tzinfo=timezone.utc)

    try:
        warn_if_full_trade_root_scan(root, since=since, until=None)

        captured = capsys.readouterr()
        assert captured.err == ""
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_daily_review_main_defaults_to_recent_window(monkeypatch):
    captured: dict[str, datetime | None] = {}
    temp_root = _tmp_root()

    def _fake_build_daily_review(**kwargs):
        captured["since"] = kwargs["since"]
        captured["until"] = kwargs["until"]
        return ["PIPELINE REVIEW"]

    monkeypatch.setattr("scripts.daily_review.build_daily_review", _fake_build_daily_review)
    monkeypatch.setattr("scripts.daily_review.write_report_line", lambda *args, **kwargs: None)
    monkeypatch.setattr("scripts.daily_review.REPORTS_DIR", temp_root / "reports")
    monkeypatch.setattr("scripts.daily_review.DEFAULT_REPORT_PATH", temp_root / "reports" / "daily_review.txt")
    monkeypatch.setattr("sys.argv", ["daily_review"])

    try:
        assert daily_review.main() == 0
        assert captured["since"] is not None
        assert captured["until"] is not None
        assert captured["until"] - captured["since"] == timedelta(hours=24)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_trade_log_summary_warns_on_root_scan_without_filter(monkeypatch, capsys):
    temp_root = _tmp_root()
    root = _make_trade_root(temp_root / "logs" / "trades")

    try:
        monkeypatch.setattr(
            "scripts.trade_log_summary.summarize",
            lambda *args, **kwargs: {
                "path": root,
                "lines_total": 0,
                "lines_malformed": 0,
                "records_kept": 0,
                "event_counts": {},
                "skip_reasons": {},
                "analysis_rejected_reasons": {},
                "stale_news_age_buckets": {},
                "stale_news_sources": {},
                "stale_news_tickers": {},
                "sources": {},
                "tickers": {},
                "signal_types": {},
            },
        )
        monkeypatch.setattr("sys.argv", ["trade_log_summary", "--path", str(root)])

        assert trade_log_summary.main() == 0

        captured = capsys.readouterr()
        assert "scanning full trade-log root with no time filter" in captured.err
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
