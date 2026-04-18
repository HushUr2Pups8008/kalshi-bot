from __future__ import annotations

import sys
from pathlib import Path

from scripts import decision_funnel_summary
from scripts import keyword_promotion_report
from scripts import keyword_shadow_eval
from scripts import trade_log_summary


FIXTURES_DIR = Path("tests/fixtures/report_snapshots")


def _run_script_main(main_func, argv: list[str], monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "argv", argv)
    result = main_func()
    output = capsys.readouterr().out
    assert result == 0
    return output


def _assert_snapshot(name: str, output: str) -> None:
    expected = (FIXTURES_DIR / f"{name}.expected.txt").read_text(encoding="utf-8")
    assert output == expected


def test_trade_log_summary_snapshot(monkeypatch, capsys):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    output = _run_script_main(
        trade_log_summary.main,
        [
            "trade_log_summary.py",
            "--path",
            "tests/fixtures/report_snapshots/trade_log_sample.jsonl",
            "--since",
            "2026-04-18",
            "--until",
            "2026-04-18",
            "--top",
            "5",
        ],
        monkeypatch,
        capsys,
    )
    _assert_snapshot("trade_log_summary", output)


def test_decision_funnel_summary_snapshot(monkeypatch, capsys):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    output = _run_script_main(
        decision_funnel_summary.main,
        [
            "decision_funnel_summary.py",
            "--path",
            "tests/fixtures/report_snapshots/trade_log_sample.jsonl",
            "--since",
            "2026-04-18",
            "--until",
            "2026-04-18",
            "--top",
            "5",
        ],
        monkeypatch,
        capsys,
    )
    _assert_snapshot("decision_funnel_summary", output)


def test_keyword_shadow_eval_snapshot(monkeypatch, capsys):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    output = _run_script_main(
        keyword_shadow_eval.main,
        [
            "keyword_shadow_eval.py",
            "--path",
            "tests/fixtures/report_snapshots/keyword_misses_sample.jsonl",
            "--since",
            "2026-04-18",
            "--until",
            "2026-04-18",
        ],
        monkeypatch,
        capsys,
    )
    _assert_snapshot("keyword_shadow_eval", output)


def test_keyword_promotion_report_snapshot(monkeypatch, capsys):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    output = _run_script_main(
        keyword_promotion_report.main,
        [
            "keyword_promotion_report.py",
            "--path",
            "tests/fixtures/report_snapshots/keyword_misses_sample.jsonl",
            "--since",
            "2026-04-18",
            "--until",
            "2026-04-18",
            "--max-examples",
            "2",
        ],
        monkeypatch,
        capsys,
    )
    _assert_snapshot("keyword_promotion_report", output)
