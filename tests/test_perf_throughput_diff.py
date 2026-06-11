"""Tests for scripts/perf_throughput_diff.py.

WHY: the diff tool parses the emitted report text by anchored label. PR #132
changed section 8 to carry TWO 'Win rate' lines (the lifetime gate line and the
informational post-P0 'target' line) and split the drawdown clarifier onto its
own line. These tests pin that the parser still extracts the GATE win rate (not
the post-P0 one) and the gate drawdown, so a future report-format change that
breaks the verification tool fails loudly here.
"""
from __future__ import annotations

from scripts import perf_throughput_diff as ptd

# A minimal report in the POST-#132 section-8 format.
_NEW_FORMAT = """\
Signal pipeline (events in window):

  Signals detected           :  200
  Opportunities identified    :  200  (100.0% of signals)
  Trades placed (PAPER_TRADE) :   25  (12.5% of opportunities)
  Skipped by executor        :  150  (75.0% of opportunities)

  DB resolved trades         :    8
  DB open trades             :    9

Go-live readiness:

  Cohort basis    : LIFETIME (all resolved trades, INCLUDING the frozen
                    pre-P0 cohort ...)

  Resolved trades : 17 / 20 required  [FAIL]
  Win rate        : 47% / 52% required  [FAIL]
  Drawdown        : 18.0% / 20% max  [PASS]
                    (decline from starting bankroll, not peak-to-trough)
  Peak-to-trough  : 25.0%  (informational; max entry-sampled equity decline)

  Post-P0 view    : INFORMATIONAL (current-regime cohort, ts >= 2026-05-12);
    Win rate      : 65% / 52% target
"""

_OLD_FORMAT = """\
  Signals detected           :  167
  Opportunities identified    :  167  (100.0% of signals)
  Trades placed (PAPER_TRADE) :   15  (9.0% of opportunities)
  Skipped by executor        :  172  (103.0% of opportunities)
  DB resolved trades         :    5
  DB open trades             :   10
  Resolved trades : 14 / 20 required  [FAIL]
  Win rate        : 43% / 52% required  [FAIL]
  Drawdown        : 38.2% / 20% max  [FAIL]
"""


def test_extracts_gate_win_rate_not_post_p0():
    m = ptd._extract(_NEW_FORMAT)
    # Gate win rate is 47% (the "/ 52% required" line), NOT the post-P0 65%.
    assert m["Go-live win rate %"] == 47.0
    assert m["Go-live drawdown %"] == 18.0
    assert m["Go-live resolved"] == 17.0


def test_extracts_throughput_funnel():
    m = ptd._extract(_NEW_FORMAT)
    assert m["Signals detected"] == 200.0
    assert m["Opportunities"] == 200.0
    assert m["Trades placed"] == 25.0
    assert m["Skipped by executor"] == 150.0
    assert m["DB resolved trades"] == 8.0
    assert m["DB open trades"] == 9.0


def test_diff_reports_deltas(tmp_path):
    old = tmp_path / "analysis_old.txt"
    new = tmp_path / "analysis_new.txt"
    old.write_text(_OLD_FORMAT, encoding="utf-8")
    new.write_text(_NEW_FORMAT, encoding="utf-8")
    out = ptd.diff(old, new)
    # throughput up (15 -> 25 trades), readiness resolved up (14 -> 17)
    assert "Trades placed" in out
    assert "+10" in out  # 25 - 15 trades placed
    assert "+3" in out   # 17 - 14 go-live resolved
    # drawdown improved 38.2 -> 18.0
    assert "-20.2" in out
