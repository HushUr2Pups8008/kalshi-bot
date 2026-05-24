"""T0-budget CI check (rapid-learning framework v3 §6 safeguard 1).

Reference:
    docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
    §6 (safeguards) and IC §16.7 (deadlock path).

Final Phase 2 deliverable. Walks the rolling 14-day commit history, classifies
each commit via I-5 (`scripts/edge_replay/tier_classifier.py`), and reports
the T0 percentage. Read-only — no merge blocking outside the exit code.

------------------------------------------------------------------------------
Verdict logic (mirrors framework v3 §6 deadlock-path replacement):

- cap not exceeded                                       -> pass  (exit 0)
- cap exceeded AND T1/T2 attempt in trailing 7d          -> warn  (exit 0)
- cap exceeded AND no T1 attempt AND active memo present -> warn  (exit 0)
- cap exceeded AND no T1 attempt AND no active memo      -> block (exit 1)
- infrastructure error (git not available, parse failure)-> error (exit 2)

------------------------------------------------------------------------------
Why warn-not-block when actively attempting T1 work:

Reviewer D's deadlock-path amendment to IC §16.7 explicitly replaces the
hard-block with alarm-plus-memo because the original safeguard could
deadlock the framework when no T1-ready work was queued. The warn verdict
preserves operator visibility (the message still surfaces in CI) without
freezing merges when the team is demonstrably trying to ship behavioral
changes.

Why warn-not-block when an active operator memo exists:

The memo is the operator's explicit acknowledgement that no T1-ready work
is queued. Per IC §16.7, this is the escape valve that prevents framework
deadlock. The window-relative freshness check ensures stale memos don't
permanently waive the safeguard.

------------------------------------------------------------------------------
Pure stdlib. Only intra-project import: `scripts.edge_replay.tier_classifier`.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from scripts.edge_replay.tier_classifier import classify_tier

__all__ = [
    "DEFAULT_DEADLOCK_DAYS",
    "DEFAULT_T0_CAP",
    "DEFAULT_WINDOW_DAYS",
    "BudgetReport",
    "CommitTier",
    "compute_budget_report",
    "main",
]


# ---------------------------------------------------------------------------
# Defaults — sourced from framework v3 §6 safeguard 1 + IC §16.7.
# ---------------------------------------------------------------------------

DEFAULT_WINDOW_DAYS: int = 14
DEFAULT_T0_CAP: float = 0.40
DEFAULT_DEADLOCK_DAYS: int = 7

# Memo glob — operators date the file for readability; the gate keys off
# the suffix so a typo'd date prefix doesn't silently disable the memo.
_MEMO_SUFFIX: str = "-t1-deadlock-escalation-memo.md"
_MEMO_GLOB: str = f"docs/governance/*{_MEMO_SUFFIX}"


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitTier:
    """One commit's I-5 classification result."""

    sha: str
    short_sha: str
    ts_utc: str  # ISO-8601, Z-suffixed at presentation
    subject: str
    tier: Literal["T0", "T1", "T2", "T3"]
    changed_files: list[str]


@dataclass(frozen=True)
class BudgetReport:
    """Aggregate budget report for one rolling window."""

    window_days: int
    total_commits: int
    tier_counts: dict[str, int]  # always {"T0": n, "T1": n, "T2": n, "T3": n}
    t0_ratio: float  # 0..1
    t0_cap: float  # 0.40 default
    cap_exceeded: bool
    t1_attempt_in_trailing_7d: bool
    escalation_memo_path: Path | None
    escalation_memo_active: bool
    verdict: Literal["pass", "warn", "block"]
    commits: list[CommitTier] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default git runner
# ---------------------------------------------------------------------------


def _default_git_runner(repo_path: Path) -> Callable[..., str]:
    """Build a runner that shells out to real git inside `repo_path`.

    Two subcommand kinds:
      - ("log", since_iso): `git log --since=<iso> --pretty=...` returning
        "SHA TS SUBJECT" lines.
      - ("show", sha): `git show --name-only --format= <sha>` returning
        newline-joined file paths.

    Subprocess errors propagate as RuntimeError so `main()` can map them
    to exit code 2 (infrastructure error) without swallowing the cause.
    """

    def run(kind: str, *args: Any) -> str:
        if kind == "log":
            since_iso = args[0]
            cmd = [
                "git",
                "log",
                f"--since={since_iso}",
                # %H = full SHA, %cI = committer ISO-8601, %s = subject.
                # We use committer date (%c) not author date (%a) because
                # the rolling window measures *when work landed on main*,
                # not when the patch was originally authored. Rebases and
                # backports must be measured at land-time.
                "--pretty=format:%H %cI %s",
                "--no-merges",
            ]
        elif kind == "show":
            sha = args[0]
            cmd = ["git", "show", "--name-only", "--format=", sha]
        else:
            raise ValueError(f"unknown runner kind: {kind!r}")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "git binary not found on PATH; T0-budget check requires git"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git command failed: {shlex.join(cmd)}\nstderr: {exc.stderr}"
            ) from exc
        return result.stdout

    return run


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_log_lines(stdout: str) -> list[tuple[str, datetime, str]]:
    """Parse `git log --pretty=format:%H %cI %s` output.

    Returns a list of (sha, committer_dt_utc, subject) tuples in the order
    git returned them (newest first). We do not require commits to be in
    any particular order here; downstream filters check committer time
    against the rolling window.

    Lines that fail to parse are skipped silently (no commit data is worse
    than a partial report — the alternative is the entire check 500'ing
    on one weird subject line).
    """
    parsed: list[tuple[str, datetime, str]] = []
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # Expect: "<40 hex> <iso8601> <subject...>"
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        sha, ts_raw, subject = parts
        try:
            # Python <3.11 doesn't accept "Z" in fromisoformat; map it.
            ts_normalized = ts_raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_normalized)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        parsed.append((sha, dt, subject))
    return parsed


def _files_for_commit(runner: Callable[..., str], sha: str) -> list[str]:
    """Return the list of files changed in `sha` (forward-slash, normalized)."""
    out = runner("show", sha)
    files: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        files.append(line.replace("\\", "/"))
    return files


# ---------------------------------------------------------------------------
# Memo detection
# ---------------------------------------------------------------------------


def _find_active_memo(
    repo_path: Path,
    now_utc: datetime,
    window_days: int,
) -> tuple[Path | None, bool]:
    """Locate the freshest matching memo and decide if it's "active".

    Returns (path_or_None, active_bool). A memo is active iff its mtime is
    within `window_days` of `now_utc`. This window-relative freshness is
    load-bearing: a single old memo must not permanently disable the gate.
    """
    glob_pattern = _MEMO_GLOB
    matches = list(repo_path.glob(glob_pattern))
    if not matches:
        return None, False
    # Sort by mtime descending — newest first
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest = matches[0]
    mtime = datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
    age = now_utc - mtime
    active = age <= timedelta(days=window_days)
    return newest, active


# ---------------------------------------------------------------------------
# Public API — compute_budget_report
# ---------------------------------------------------------------------------


def compute_budget_report(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    t0_cap: float = DEFAULT_T0_CAP,
    deadlock_days: int = DEFAULT_DEADLOCK_DAYS,
    repo_path: Path | None = None,
    now_utc: datetime | None = None,
    git_log_runner: Callable[..., str] | None = None,
) -> BudgetReport:
    """Walk rolling commit history and produce the tier-budget report.

    Args:
        window_days: rolling window size in days (default 14).
        t0_cap: maximum allowed fraction of T0 commits (default 0.40).
        deadlock_days: trailing window for "T1 attempt" check (default 7).
        repo_path: repository root (defaults to cwd).
        now_utc: override the "now" reference (testing). Default: real UTC.
        git_log_runner: callable injection for tests. See _default_git_runner
            for the runner protocol.

    Returns:
        BudgetReport with verdict and per-commit classification.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    if repo_path is None:
        repo_path = Path.cwd()
    runner = git_log_runner if git_log_runner is not None else _default_git_runner(repo_path)

    notes: list[str] = []

    # ---- Fetch commit history -------------------------------------------------
    window_start = now_utc - timedelta(days=window_days)
    # git --since accepts ISO-8601; we feed UTC-aware to avoid local-TZ
    # ambiguity per ~/.claude/rules/portability.md.
    since_iso = window_start.isoformat()
    log_stdout = runner("log", since_iso)
    raw_commits = _parse_log_lines(log_stdout)

    # Filter to window in case the runner returned extras (tests do).
    in_window = [(sha, ts, subj) for sha, ts, subj in raw_commits if ts >= window_start]

    # ---- Classify each commit -------------------------------------------------
    classified: list[CommitTier] = []
    for sha, ts, subject in in_window:
        files = _files_for_commit(runner, sha)
        # Path-only classification: at this layer we don't have access to
        # config/env diffs (those signals are produced by the runtime
        # tier_classifications.jsonl ledger, not git history). The budget
        # check is an aggregate signal anyway — under-estimating T3 by one
        # tier here biases toward more T0 in the count, which makes the
        # cap *harder* to satisfy (conservative direction for the safeguard).
        tier = classify_tier(files)
        classified.append(
            CommitTier(
                sha=sha,
                short_sha=sha[:7],
                ts_utc=ts.isoformat(timespec="seconds").replace("+00:00", "Z"),
                subject=subject,
                tier=tier,
                changed_files=files,
            )
        )

    # ---- Aggregate ------------------------------------------------------------
    tier_counts: dict[str, int] = {"T0": 0, "T1": 0, "T2": 0, "T3": 0}
    for ct in classified:
        tier_counts[ct.tier] += 1

    total = len(classified)
    if total == 0:
        t0_ratio = 0.0
        notes.append("no commits in window; nothing to gate")
    else:
        t0_ratio = tier_counts["T0"] / total

    cap_exceeded = t0_ratio > t0_cap

    # ---- T1/T2 attempt in trailing `deadlock_days` ---------------------------
    deadlock_cutoff = now_utc - timedelta(days=deadlock_days)
    t1_attempt = False
    for ct in classified:
        ts = datetime.fromisoformat(ct.ts_utc.replace("Z", "+00:00"))
        if ts >= deadlock_cutoff and ct.tier in ("T1", "T2"):
            t1_attempt = True
            break

    # ---- Escalation memo ------------------------------------------------------
    memo_path, memo_active = _find_active_memo(repo_path, now_utc, window_days)
    if memo_path is not None and not memo_active:
        notes.append(
            f"escalation memo present at {memo_path.name} but stale "
            f"(>{window_days}d old); does not unlock the gate"
        )

    # ---- Verdict --------------------------------------------------------------
    verdict: Literal["pass", "warn", "block"]
    if not cap_exceeded:
        verdict = "pass"
    elif t1_attempt:
        verdict = "warn"
        notes.append(
            f"T0 ratio {t0_ratio:.1%} exceeds cap {t0_cap:.0%}, but a T1/T2 "
            f"attempt is present in the trailing {deadlock_days}d window"
        )
    elif memo_active:
        verdict = "warn"
        notes.append(
            f"T0 ratio {t0_ratio:.1%} exceeds cap {t0_cap:.0%}; no T1 attempt "
            f"in trailing {deadlock_days}d, but an active operator escalation "
            f"memo is present (IC §16.7 deadlock path)"
        )
    else:
        verdict = "block"
        notes.append(
            f"T0 ratio {t0_ratio:.1%} exceeds cap {t0_cap:.0%}; no T1/T2 "
            f"attempt in trailing {deadlock_days}d AND no active operator "
            f"escalation memo. Per IC §16.7, merge a T1/T2 change or write "
            f"`docs/governance/<date>{_MEMO_SUFFIX}` before next T0 merge."
        )

    return BudgetReport(
        window_days=window_days,
        total_commits=total,
        tier_counts=tier_counts,
        t0_ratio=t0_ratio,
        t0_cap=t0_cap,
        cap_exceeded=cap_exceeded,
        t1_attempt_in_trailing_7d=t1_attempt,
        escalation_memo_path=memo_path,
        escalation_memo_active=memo_active,
        verdict=verdict,
        commits=classified,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_human(report: BudgetReport) -> str:
    """Operator-readable summary."""
    lines: list[str] = []
    lines.append(f"T0-budget check (rolling {report.window_days} days)")
    lines.append("=" * 40)
    lines.append(f"total commits: {report.total_commits}")
    if report.total_commits > 0:
        for t in ("T0", "T1", "T2", "T3"):
            n = report.tier_counts[t]
            pct = (n / report.total_commits) * 100
            lines.append(f"  {t} = {n} ({pct:.1f}%)")
    else:
        for t in ("T0", "T1", "T2", "T3"):
            lines.append(f"  {t} = 0 (0.0%)")
    lines.append("")
    lines.append(f"t0_cap = {report.t0_cap * 100:.1f}%")
    lines.append(f"verdict: {report.verdict}")
    lines.append("")
    lines.append(
        f"T1/T2 attempt in trailing 7d: {report.t1_attempt_in_trailing_7d}"
    )
    if report.escalation_memo_path is None:
        memo_line = "Escalation memo: none"
        if not report.cap_exceeded:
            memo_line += " (not needed)"
        lines.append(memo_line)
    else:
        state = "active" if report.escalation_memo_active else "STALE (does not unlock)"
        lines.append(
            f"Escalation memo: {report.escalation_memo_path.name} ({state})"
        )
    if report.notes:
        lines.append("")
        lines.append("notes:")
        for n in report.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def _render_json(report: BudgetReport) -> str:
    """JSON-serializable view of the report (operator can pipe to `jq`)."""
    payload = asdict(report)
    # Path objects don't serialize cleanly; normalize.
    if payload["escalation_memo_path"] is not None:
        payload["escalation_memo_path"] = str(report.escalation_memo_path)
    # asdict already converted CommitTier dataclasses into plain dicts.
    return json.dumps(payload, indent=2, sort_keys=True)


def main(
    argv: list[str] | None = None,
    *,
    git_log_runner: Callable[..., str] | None = None,
    now_utc: datetime | None = None,
) -> int:
    """CLI entry point. Returns process exit code.

    Exit codes:
      0  pass or warn
      1  block
      2  infrastructure error (git missing, parse failure, etc.)
    """
    parser = argparse.ArgumentParser(
        prog="tier_budget_check",
        description=(
            "T0-budget CI check for rapid-learning framework v3 §6. "
            "Reports the T0 share of recent commits and gates against the "
            "deadlock path defined in IC §16.7."
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"rolling window in days (default: {DEFAULT_WINDOW_DAYS})",
    )
    parser.add_argument(
        "--t0-cap",
        type=float,
        default=DEFAULT_T0_CAP,
        help=f"max allowed T0 fraction 0..1 (default: {DEFAULT_T0_CAP})",
    )
    parser.add_argument(
        "--deadlock-days",
        type=int,
        default=DEFAULT_DEADLOCK_DAYS,
        help=f"trailing window for T1 attempt (default: {DEFAULT_DEADLOCK_DAYS})",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=None,
        help="repository root (default: cwd)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable summary",
    )
    args = parser.parse_args(argv)

    try:
        report = compute_budget_report(
            window_days=args.window_days,
            t0_cap=args.t0_cap,
            deadlock_days=args.deadlock_days,
            repo_path=args.repo_path,
            now_utc=now_utc,
            git_log_runner=git_log_runner,
        )
    except RuntimeError as exc:
        # Infrastructure error — keep ASCII-only per portability rule.
        sys.stderr.write(f"[tier_budget_check] infrastructure error: {exc}\n")
        return 2

    if args.json:
        sys.stdout.write(_render_json(report) + "\n")
    else:
        sys.stdout.write(_render_human(report) + "\n")

    if report.verdict == "block":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover — manual run path
    raise SystemExit(main())
