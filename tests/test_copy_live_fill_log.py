"""The daily live-fill copy must not wipe the committed report.

The push half of the job may stage only docs/reports/live-fill-log.md.
A failed push is recorded and still exits 0 so the desk keeps running.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.copy_live_fill_log import (
    REPORT_RELATIVE,
    copy_live_fill_log,
    git_env_for_fill_log,
    publish_live_fill_report,
)
from trading.executor import LIVE_FILL_LOG_ARCHIVE_PATH, LIVE_FILL_LOG_PATH


def test_runtime_fill_log_is_gitignored_and_the_report_stays_in_docs():
    assert LIVE_FILL_LOG_PATH.name == "live-fill-log.md"
    assert LIVE_FILL_LOG_PATH.parent.name == "state"
    assert LIVE_FILL_LOG_PATH.parent.parent.name == "logs"
    assert LIVE_FILL_LOG_ARCHIVE_PATH.parts[-3:] == (
        "docs",
        "reports",
        "live-fill-log.md",
    )


def test_missing_or_empty_runtime_log_does_not_replace_the_report(tmp_path):
    report = tmp_path / "docs" / "live-fill-log.md"
    report.parent.mkdir()
    report.write_text("kept\n", encoding="utf-8")
    missing = tmp_path / "logs" / "state" / "live-fill-log.md"
    assert copy_live_fill_log(missing, report) is False
    assert report.read_text(encoding="utf-8") == "kept\n"

    missing.parent.mkdir(parents=True)
    missing.write_text(" \n", encoding="utf-8")
    assert copy_live_fill_log(missing, report) is False
    assert report.read_text(encoding="utf-8") == "kept\n"


def test_daily_copy_replaces_the_report_with_the_runtime_log(tmp_path):
    source = tmp_path / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "2026-10-12T19:11:24Z, KXTRUMPSAY-26OCT19-GOLF, yes, 89, 8, abc, 201\n",
        encoding="utf-8",
    )
    report = tmp_path / "docs" / "reports" / "live-fill-log.md"
    report.parent.mkdir(parents=True)
    report.write_text("old\n", encoding="utf-8")
    assert copy_live_fill_log(source, report) is True
    assert report.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert "KALSHI-ACCESS" not in report.read_text(encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "fill-log@example.com")
    _git(repo, "config", "user.name", "fill-log")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", str(tmp_path / "no-hooks"))
    (repo / ".git" / "info" / "exclude").write_text("logs/\n", encoding="utf-8")
    report = repo / REPORT_RELATIVE
    report.parent.mkdir(parents=True)
    report.write_text("old\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    _git(repo, "add", "--", REPORT_RELATIVE, "README.md")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "HEAD")
    return repo, remote


def _recording_runner(commands: list[list[str]]):
    def run_git(args, repo, env):
        commands.append(list(args))
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    return run_git


def test_dry_run_does_not_copy_commit_or_push(tmp_path, capsys):
    repo, _remote = _init_repo(tmp_path)
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("new fill\n", encoding="utf-8")
    dest = repo / REPORT_RELATIVE

    def boom(*_args, **_kwargs):
        raise AssertionError("git must not run during dry-run")

    rc = publish_live_fill_report(
        repo,
        dry_run=True,
        source=source,
        dest=dest,
        run_git=boom,
    )
    assert rc == 0
    assert dest.read_text(encoding="utf-8") == "old\n"
    assert "dry-run: would-copy" in capsys.readouterr().out
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert not (repo / "logs" / "state" / "fill-log-push.err").exists()


def test_unchanged_report_does_not_commit(tmp_path, capsys):
    repo, remote = _init_repo(tmp_path)
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("old\n", encoding="utf-8")
    before = _git(remote, "rev-parse", "main").stdout.strip()
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
    )
    assert rc == 0
    assert "unchanged" in capsys.readouterr().out
    assert _git(remote, "rev-parse", "main").stdout.strip() == before
    assert _git(repo, "status", "--porcelain").stdout == ""


def test_changed_report_stages_only_that_path_and_pushes(tmp_path):
    repo, remote = _init_repo(tmp_path)
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("2026-10-13T00:00:00Z, KXTEST, yes, 40, 1, abc, 201\n", encoding="utf-8")
    (repo / "README.md").write_text("local edit that must stay unstaged\n", encoding="utf-8")
    commands: list[list[str]] = []
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
        run_git=_recording_runner(commands),
        now=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )
    assert rc == 0
    assert ["add", "--", REPORT_RELATIVE] in commands
    flat = " ".join(" ".join(command) for command in commands)
    assert "add -A" not in flat
    assert "add ." not in flat
    pushed = _git(remote, "log", "-1", "--format=%s", "main").stdout.strip()
    assert pushed == "ops: live fill log 2026-10-13"
    names = _git(remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main").stdout
    assert names.strip() == REPORT_RELATIVE
    status = _git(repo, "status", "--porcelain").stdout
    assert "README.md" in status
    assert REPORT_RELATIVE not in status
    assert not (repo / "logs" / "state" / "fill-log-push.err").exists()


def test_already_staged_other_file_is_left_alone(tmp_path):
    repo, remote = _init_repo(tmp_path)
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("new fill\n", encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text("staged other work\n", encoding="utf-8")
    _git(repo, "add", "--", "README.md")
    before = _git(remote, "rev-parse", "main").stdout.strip()
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
    )
    assert rc == 0
    assert _git(remote, "rev-parse", "main").stdout.strip() == before
    staged = _git(repo, "diff", "--cached", "--name-only").stdout.strip().splitlines()
    assert staged == ["README.md"]
    err = (repo / "logs" / "state" / "fill-log-push.err").read_text(encoding="utf-8")
    assert "index already has staged files" in err


def test_push_failure_retries_once_then_writes_err(tmp_path, capsys):
    repo, remote = _init_repo(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("new fill\n", encoding="utf-8")
    commands: list[list[str]] = []
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
        run_git=_recording_runner(commands),
        now=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )
    assert rc == 0
    assert commands.count(["push"]) == 2
    assert ["pull", "--rebase"] not in commands
    err = (repo / "logs" / "state" / "fill-log-push.err").read_text(encoding="utf-8")
    assert "retry git push failed" in err
    assert "add -A" not in err
    local = _git(repo, "log", "-1", "--format=%s").stdout.strip()
    assert local == "ops: live fill log 2026-10-13"
    assert "pushed" not in capsys.readouterr().out


def test_rebase_runs_when_origin_is_ahead_and_aborts_on_conflict(tmp_path):
    repo, remote = _init_repo(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "fill-log@example.com")
    _git(other, "config", "user.name", "fill-log")
    _git(other, "config", "commit.gpgsign", "false")
    report = other / REPORT_RELATIVE
    report.write_text("origin moved\n", encoding="utf-8")
    _git(other, "add", "--", REPORT_RELATIVE)
    _git(other, "commit", "-m", "origin report")
    _git(other, "push")

    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("desk fill\n", encoding="utf-8")
    commands: list[list[str]] = []
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
        run_git=_recording_runner(commands),
        now=datetime(2026, 10, 13, tzinfo=timezone.utc),
    )
    assert rc == 0
    assert ["pull", "--rebase"] in commands
    err = (repo / "logs" / "state" / "fill-log-push.err").read_text(encoding="utf-8")
    assert "git pull --rebase failed" in err
    assert _git(remote, "log", "-1", "--format=%s", "main").stdout.strip() == "origin report"
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "ops: live fill log 2026-10-13"


def test_push_ref_publishes_a_side_branch_and_leaves_main(tmp_path, monkeypatch, capsys):
    """Main is protected. The daily report must land somewhere else."""
    repo, remote = _init_repo(tmp_path)
    monkeypatch.setenv("FILL_LOG_PUSH_REF", "ops/live-fill-log")
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("2026-10-14T00:00:00Z, KXTEST, yes, 40, 1, abc, 201\n", encoding="utf-8")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    remote_main = _git(remote, "rev-parse", "main").stdout.strip()

    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
        now=datetime(2026, 10, 14, tzinfo=timezone.utc),
    )
    assert rc == 0
    assert "pushed" in capsys.readouterr().out
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert _git(remote, "rev-parse", "main").stdout.strip() == remote_main
    assert _git(repo, "status", "--porcelain").stdout == ""
    assert (
        _git(remote, "log", "-1", "--format=%s", "ops/live-fill-log").stdout.strip()
        == "ops: live fill log 2026-10-14"
    )

    source.write_text("day two\n", encoding="utf-8")
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
        now=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before
    subjects = _git(remote, "log", "--format=%s", "ops/live-fill-log").stdout
    assert "ops: live fill log 2026-10-15" in subjects
    assert "ops: live fill log 2026-10-14" in subjects
    assert not (repo / "logs" / "state" / "fill-log-push.err").exists()


def test_invalid_push_ref_does_not_move_main(tmp_path, monkeypatch):
    repo, remote = _init_repo(tmp_path)
    monkeypatch.setenv("FILL_LOG_PUSH_REF", "../main")
    source = repo / "logs" / "state" / "live-fill-log.md"
    source.parent.mkdir(parents=True)
    source.write_text("new fill\n", encoding="utf-8")
    before = _git(remote, "rev-parse", "main").stdout.strip()
    rc = publish_live_fill_report(
        repo,
        dry_run=False,
        source=source,
        dest=repo / REPORT_RELATIVE,
    )
    assert rc == 0
    assert _git(remote, "rev-parse", "main").stdout.strip() == before
    err = (repo / "logs" / "state" / "fill-log-push.err").read_text(encoding="utf-8")
    assert "FILL_LOG_PUSH_REF is not a branch name" in err


def test_in_repo_credentials_are_ignored(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    key = repo / "secret.key"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    token = repo / "token.txt"
    token.write_text("ghp_shouldnotappear\n", encoding="utf-8")
    monkeypatch.setenv("FILL_LOG_GIT_SSH_KEY", str(key))
    monkeypatch.setenv("FILL_LOG_GIT_TOKEN_FILE", str(token))
    env = git_env_for_fill_log(repo)
    assert "GIT_SSH_COMMAND" not in env
    assert "credential.helper" not in " ".join(env.values())
    assert "ghp_shouldnotappear" not in " ".join(env.values())


def test_outside_credentials_are_referenced_not_inlined(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    key = outside / "kalshi-fill-log"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    token = outside / "token.txt"
    token.write_text("ghp_shouldnotappear\n", encoding="utf-8")
    monkeypatch.setenv("FILL_LOG_GIT_SSH_KEY", str(key))
    monkeypatch.setenv("FILL_LOG_GIT_TOKEN_FILE", str(token))
    env = git_env_for_fill_log(repo)
    assert "IdentitiesOnly=yes" in env["GIT_SSH_COMMAND"]
    assert str(key) in env["GIT_SSH_COMMAND"]
    helper = " ".join(value for key_name, value in env.items() if key_name.startswith("GIT_CONFIG_"))
    assert "credential.helper" in helper
    assert str(token) in helper
    assert "ghp_shouldnotappear" not in helper
    assert "not-a-real-key" not in helper
