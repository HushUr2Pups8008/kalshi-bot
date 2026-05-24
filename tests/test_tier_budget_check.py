"""Tests for the T0-budget CI check (rapid-learning framework v3 §6 safeguard 1).

Reference:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §6 (safeguards) and IC §16.7 (deadlock path).

Test discipline: each assertion encodes WHY the behavior matters per
~/.claude/rules/validation.md. A test that cannot fail when the deadlock
logic changes is not a test.

The check has three meaningful verdicts:
- pass : T0 ratio within cap
- warn : cap exceeded BUT (T1/T2 attempt in trailing 7d OR active memo)
- block: cap exceeded AND no T1 attempt AND no active memo
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from scripts.edge_replay.tier_budget_check import (
    DEFAULT_T0_CAP,
    DEFAULT_WINDOW_DAYS,
    compute_budget_report,
    main,
)


# ---------------------------------------------------------------------------
# Helpers — build deterministic synthetic git histories
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    """Match `git log --date=iso-strict-local` style: 2026-05-23T12:00:00+00:00."""
    return dt.isoformat(timespec="seconds")


def make_log_runner(commits: list[dict]):
    """Build a `git_log_runner` callable returning canned commit data.

    Each commit dict: {sha, ts (datetime), subject, files (list[str])}.
    The runner accepts two subcommand kinds:
      - ("log", since_iso): returns "SHA TS SUBJECT" lines
      - ("show", sha): returns newline-joined file paths

    Tests pass a synthetic history so we never invoke real git, satisfying
    the determinism rule (~/.claude/rules/validation.md — tests must encode
    WHY, not depend on the host repo's commit ordering).
    """
    by_sha = {c["sha"]: c for c in commits}

    def runner(kind: str, *args) -> str:
        if kind == "log":
            lines = []
            for c in commits:
                lines.append(f"{c['sha']} {_iso(c['ts'])} {c['subject']}")
            return "\n".join(lines)
        if kind == "show":
            sha = args[0]
            c = by_sha.get(sha)
            if c is None:
                return ""
            return "\n".join(c["files"])
        raise ValueError(f"unknown runner kind: {kind}")

    return runner


# ---------------------------------------------------------------------------
# Empty history
# ---------------------------------------------------------------------------


def test_empty_history_passes_with_zero_ratio(tmp_path, monkeypatch):
    """No commits in window → ratio is 0, verdict is pass.

    A repo with no recent activity must not be flagged as a deadlock; the
    deadlock condition is "we keep merging T0 with no T1 attempt", not
    "nothing merged at all".
    """
    monkeypatch.chdir(tmp_path)
    report = compute_budget_report(
        git_log_runner=make_log_runner([]),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.total_commits == 0
    assert report.t0_ratio == 0.0
    assert report.cap_exceeded is False
    assert report.verdict == "pass"


# ---------------------------------------------------------------------------
# Pure-T0 paths
# ---------------------------------------------------------------------------


def test_all_t0_no_t1_attempt_no_memo_blocks(tmp_path, monkeypatch):
    """100% T0 + zero T1/T2 in trailing 7d + no memo → BLOCK.

    This is the framework's core safety property: if everything we merge is
    cosmetic and nobody is attempting paper-mode behavioral changes, we are
    rapid-learning theatre. Block forces the operator to either ship a T1
    or write an explicit deadlock memo (IC §16.7).
    """
    monkeypatch.chdir(tmp_path)
    commits = [
        {
            "sha": f"sha{i:02d}" + "0" * 36,
            "ts": NOW - timedelta(days=i),
            "subject": f"docs: tweak {i}",
            "files": ["docs/foo.md"],
        }
        for i in range(5)
    ]
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.t0_ratio == 1.0
    assert report.cap_exceeded is True
    assert report.t1_attempt_in_trailing_7d is False
    assert report.escalation_memo_active is False
    assert report.verdict == "block"


def test_all_t0_with_active_memo_warns(tmp_path, monkeypatch):
    """100% T0 + no T1 attempt + active memo → WARN (no block).

    Per IC §16.7: a fresh operator escalation memo replaces the hard block.
    The memo is the operator's explicit acknowledgement that no T1-ready
    work is queued; the system must respect that signal or the framework
    deadlocks with no escape valve.
    """
    monkeypatch.chdir(tmp_path)
    gov = tmp_path / "docs" / "governance"
    gov.mkdir(parents=True)
    memo = gov / "2026-05-22-t1-deadlock-escalation-memo.md"
    memo.write_text("operator memo\n", encoding="utf-8")
    # Force the mtime within the window (default 14d).
    fresh_ts = (NOW - timedelta(days=2)).timestamp()
    os.utime(memo, (fresh_ts, fresh_ts))

    commits = [
        {
            "sha": f"sha{i:02d}" + "0" * 36,
            "ts": NOW - timedelta(days=i),
            "subject": f"docs: tweak {i}",
            "files": ["docs/foo.md"],
        }
        for i in range(5)
    ]
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.cap_exceeded is True
    assert report.escalation_memo_active is True
    assert report.escalation_memo_path is not None
    assert report.verdict == "warn"


def test_stale_memo_does_not_unlock(tmp_path, monkeypatch):
    """Memo older than window_days → escalation_memo_active=False → BLOCK.

    Stale memos must not unlock the gate, or a single old escalation would
    permanently waive the safeguard. The window-relative freshness check
    forces the operator to re-affirm the deadlock state.
    """
    monkeypatch.chdir(tmp_path)
    gov = tmp_path / "docs" / "governance"
    gov.mkdir(parents=True)
    memo = gov / "2026-04-01-t1-deadlock-escalation-memo.md"
    memo.write_text("ancient memo\n", encoding="utf-8")
    stale_ts = (NOW - timedelta(days=30)).timestamp()
    os.utime(memo, (stale_ts, stale_ts))

    commits = [
        {
            "sha": f"sha{i:02d}" + "0" * 36,
            "ts": NOW - timedelta(days=i),
            "subject": f"docs: tweak {i}",
            "files": ["docs/foo.md"],
        }
        for i in range(5)
    ]
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.escalation_memo_path is not None  # file exists
    assert report.escalation_memo_active is False  # but stale
    assert report.verdict == "block"


# ---------------------------------------------------------------------------
# Mixed tier ratios
# ---------------------------------------------------------------------------


def test_mixed_ratio_under_cap_passes(tmp_path, monkeypatch):
    """3 T0 + 7 T1 → 30% T0 → under 40% cap → pass.

    This is the happy path: a healthy mix of mechanical and behavioral
    commits. No deadlock concerns even though no memo exists.
    """
    monkeypatch.chdir(tmp_path)
    commits = []
    for i in range(3):
        commits.append({
            "sha": f"t0_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i, hours=1),
            "subject": f"docs: minor {i}",
            "files": ["docs/foo.md"],
        })
    for i in range(7):
        commits.append({
            "sha": f"t1_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i, hours=2),
            "subject": f"feat: behavioral {i}",
            "files": ["analysis/market_matcher.py"],
        })
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.tier_counts["T0"] == 3
    assert report.tier_counts["T1"] == 7
    assert report.t0_ratio == pytest.approx(0.3, abs=0.01)
    assert report.cap_exceeded is False
    assert report.verdict == "pass"


def test_over_cap_with_recent_t1_attempt_warns(tmp_path, monkeypatch):
    """45% T0 ratio + T1 commit in trailing 7d → WARN (not block).

    Cap exceeded means we are bumping against the safeguard, but a recent
    T1 attempt proves we are not wheel-spinning on cosmetic work. The
    warn-not-block keeps the gate from punishing teams that are actively
    trying to ship behavioral work and momentarily over-rotate on T0.
    """
    monkeypatch.chdir(tmp_path)
    commits = []
    # 9 T0 commits
    for i in range(9):
        commits.append({
            "sha": f"t0_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i % 14, hours=1),
            "subject": f"docs: minor {i}",
            "files": ["docs/foo.md"],
        })
    # 11 T1 commits all within the 14d window, one in trailing 7d.
    # Distribute days_back across {1, 8, 9, 10, 11, 12, 13, 8, 9, 10, 11}
    # so all 11 land inside the rolling window and exactly one is <= 7d.
    t1_days_back = [1, 8, 9, 10, 11, 12, 13, 8, 9, 10, 11]
    for i in range(11):
        commits.append({
            "sha": f"t1_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=t1_days_back[i], hours=2),
            "subject": f"feat: behavioral {i}",
            "files": ["analysis/market_matcher.py"],
        })
    # 9/20 = 45% T0
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.t0_ratio == pytest.approx(0.45, abs=0.01)
    assert report.cap_exceeded is True
    assert report.t1_attempt_in_trailing_7d is True
    assert report.verdict == "warn"


def test_over_cap_with_active_memo_warns(tmp_path, monkeypatch):
    """45% T0 + no recent T1 + active memo → WARN.

    Same as test_all_t0_with_active_memo_warns but at the cap boundary
    rather than 100% T0. Ensures the memo unlock applies anywhere the cap
    is exceeded, not just at 100%.
    """
    monkeypatch.chdir(tmp_path)
    gov = tmp_path / "docs" / "governance"
    gov.mkdir(parents=True)
    memo = gov / "2026-05-20-t1-deadlock-escalation-memo.md"
    memo.write_text("ack\n", encoding="utf-8")
    fresh_ts = (NOW - timedelta(days=3)).timestamp()
    os.utime(memo, (fresh_ts, fresh_ts))

    commits = []
    for i in range(9):
        commits.append({
            "sha": f"t0_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i % 14, hours=1),
            "subject": f"docs: minor {i}",
            "files": ["docs/foo.md"],
        })
    # 11 T1, all 8-13d old → all within 14d window, none in trailing 7d.
    for i in range(11):
        commits.append({
            "sha": f"t1_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=8 + (i % 6), hours=2),
            "subject": f"feat: behavioral {i}",
            "files": ["analysis/market_matcher.py"],
        })
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.cap_exceeded is True
    assert report.t1_attempt_in_trailing_7d is False
    assert report.escalation_memo_active is True
    assert report.verdict == "warn"


def test_t2_commit_counts_as_t1_attempt(tmp_path, monkeypatch):
    """A T2 commit in trailing 7d satisfies the "T1 attempt" condition.

    Per spec: T2 is more conservative than T1 but still represents a
    paper-mode behavioral attempt. Not counting T2 would punish teams who
    correctly route prompt edits (T2) instead of pretending they are
    replay-decidable (T1).
    """
    monkeypatch.chdir(tmp_path)
    commits = []
    # 9 T0 to push ratio over cap
    for i in range(9):
        commits.append({
            "sha": f"t0_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i % 14, hours=1),
            "subject": f"docs: {i}",
            "files": ["docs/foo.md"],
        })
    # 11 T2 (governance/prompts.py is T2 per classifier), one recent,
    # all within 14d window.
    t2_days_back = [2, 8, 9, 10, 11, 12, 13, 8, 9, 10, 11]
    for i in range(11):
        commits.append({
            "sha": f"t2_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=t2_days_back[i]),
            "subject": f"feat: prompt {i}",
            "files": ["governance/prompts.py"],
        })
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.tier_counts["T2"] == 11
    assert report.cap_exceeded is True
    assert report.t1_attempt_in_trailing_7d is True  # T2 counts
    assert report.verdict == "warn"


# ---------------------------------------------------------------------------
# Memo glob matching
# ---------------------------------------------------------------------------


def test_memo_glob_matches_arbitrary_date_prefix(tmp_path, monkeypatch):
    """Memo filename pattern: `docs/governance/<any-prefix>-t1-deadlock-escalation-memo.md`.

    The date prefix is not parsed; only the suffix `-t1-deadlock-escalation-memo.md`
    matters. Operators date the file for human readability; the gate keys
    off the suffix so a typo'd date doesn't silently disable the memo.
    """
    monkeypatch.chdir(tmp_path)
    gov = tmp_path / "docs" / "governance"
    gov.mkdir(parents=True)
    # Wildly nonstandard prefix
    memo = gov / "operator-jp-quick-note-t1-deadlock-escalation-memo.md"
    memo.write_text("ack\n", encoding="utf-8")
    fresh_ts = (NOW - timedelta(days=1)).timestamp()
    os.utime(memo, (fresh_ts, fresh_ts))

    commits = [
        {
            "sha": f"sha{i:02d}" + "0" * 36,
            "ts": NOW - timedelta(days=i),
            "subject": f"docs: tweak {i}",
            "files": ["docs/foo.md"],
        }
        for i in range(5)
    ]
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert report.escalation_memo_path is not None
    assert report.escalation_memo_active is True
    assert report.verdict == "warn"


# ---------------------------------------------------------------------------
# CLI / main()
# ---------------------------------------------------------------------------


def test_cli_pass_returns_zero(tmp_path, monkeypatch, capsys):
    """`main()` exits 0 when verdict is pass.

    CI relies on this exit code; flipping it would break the gate.
    """
    monkeypatch.chdir(tmp_path)

    def fake_runner(kind, *args):
        # No commits → empty history → pass
        if kind == "log":
            return ""
        return ""

    rc = main(argv=["--json"], git_log_runner=fake_runner, now_utc=NOW)
    assert rc == 0


def test_cli_block_returns_one(tmp_path, monkeypatch, capsys):
    """`main()` exits 1 when verdict is block.

    The non-zero exit is the entire point of the CI gate; if this returns
    0 the gate is a no-op.
    """
    monkeypatch.chdir(tmp_path)
    commits = [
        {
            "sha": f"sha{i:02d}" + "0" * 36,
            "ts": NOW - timedelta(days=i),
            "subject": f"docs: tweak {i}",
            "files": ["docs/foo.md"],
        }
        for i in range(5)
    ]
    runner = make_log_runner(commits)
    rc = main(argv=[], git_log_runner=runner, now_utc=NOW)
    assert rc == 1


def test_cli_json_flag_emits_parseable_json(tmp_path, monkeypatch, capsys):
    """`--json` prints a JSON document on stdout (operator can `| jq`)."""
    monkeypatch.chdir(tmp_path)
    commits = [
        {
            "sha": "abc" + "0" * 37,
            "ts": NOW - timedelta(hours=1),
            "subject": "feat: thing",
            "files": ["analysis/market_matcher.py"],
        }
    ]
    runner = make_log_runner(commits)
    rc = main(argv=["--json"], git_log_runner=runner, now_utc=NOW)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["verdict"] in {"pass", "warn", "block"}
    assert parsed["t0_cap"] == DEFAULT_T0_CAP
    assert parsed["window_days"] == DEFAULT_WINDOW_DAYS
    assert "tier_counts" in parsed
    assert rc == 0


def test_cli_warn_returns_zero(tmp_path, monkeypatch, capsys):
    """Warn verdict (cap exceeded but T1 attempt present) still exits 0.

    The framework explicitly chose warn-not-block when active T1 work is
    visible; the CI gate must respect that or it becomes the hard-block
    that v3 was designed to avoid.
    """
    monkeypatch.chdir(tmp_path)
    commits = []
    for i in range(9):
        commits.append({
            "sha": f"t0_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=i % 14, hours=1),
            "subject": f"docs: {i}",
            "files": ["docs/foo.md"],
        })
    t1_days_back = [1, 8, 9, 10, 11, 12, 13, 8, 9, 10, 11]
    for i in range(11):
        commits.append({
            "sha": f"t1_{i:02d}" + "0" * 35,
            "ts": NOW - timedelta(days=t1_days_back[i], hours=2),
            "subject": f"feat: {i}",
            "files": ["analysis/market_matcher.py"],
        })
    runner = make_log_runner(commits)
    rc = main(argv=[], git_log_runner=runner, now_utc=NOW)
    assert rc == 0


# ---------------------------------------------------------------------------
# Tier counts shape & runner injection contract
# ---------------------------------------------------------------------------


def test_tier_counts_always_include_all_four_tiers(tmp_path, monkeypatch):
    """tier_counts dict must contain T0/T1/T2/T3 keys even when zero.

    Downstream JSON consumers (dashboards, operator scripts) iterate this
    dict; missing keys would force every consumer to defensive-default.
    """
    monkeypatch.chdir(tmp_path)
    commits = [
        {
            "sha": "abc" + "0" * 37,
            "ts": NOW - timedelta(hours=1),
            "subject": "feat: thing",
            "files": ["analysis/market_matcher.py"],
        }
    ]
    report = compute_budget_report(
        git_log_runner=make_log_runner(commits),
        now_utc=NOW,
        repo_path=tmp_path,
    )
    assert set(report.tier_counts.keys()) == {"T0", "T1", "T2", "T3"}
    assert report.tier_counts["T1"] == 1
    assert report.tier_counts["T0"] == 0
    assert report.tier_counts["T2"] == 0
    assert report.tier_counts["T3"] == 0


def test_runner_injection_replaces_real_git(tmp_path, monkeypatch):
    """If `git_log_runner` is provided, no real git invocation must occur.

    This lock prevents the helper from silently falling back to subprocess
    when tests inject a stub — a regression here would make CI tests
    depend on the host repo's history (non-deterministic).
    """
    monkeypatch.chdir(tmp_path)
    sentinel_calls = []

    def tracking_runner(kind, *args):
        sentinel_calls.append((kind, args))
        if kind == "log":
            return ""
        return ""

    compute_budget_report(
        git_log_runner=tracking_runner,
        now_utc=NOW,
        repo_path=tmp_path,
    )
    # At minimum, one "log" call must have happened via the injected runner.
    assert any(c[0] == "log" for c in sentinel_calls)
