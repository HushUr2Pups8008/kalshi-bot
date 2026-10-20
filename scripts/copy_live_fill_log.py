"""Copy the gitignored live fill log onto the committed report and push it.

The desk appends to logs/state/live-fill-log.md. Launchd runs this once a
day at 06:05 local time. After the copy, a changed
docs/reports/live-fill-log.md is the only path that may be staged. The
commit message is "ops: live fill log YYYY-MM-DD" using the UTC date.
The push is tried twice. A second failure is written to
logs/state/fill-log-push.err and the process still exits 0.
When FILL_LOG_PUSH_REF names a branch, the report commit is pushed there
from a temporary worktree. The checkout that launchd is using stays put.
That is how the 06:05 job publishes while main rejects a direct push.

Credentials stay outside the repo. FILL_LOG_GIT_SSH_KEY, or
~/.ssh/kalshi-fill-log when that file exists, is used for SSH.
FILL_LOG_GIT_TOKEN_FILE is read by a credential helper and is never placed
on the git command line. A key or token file inside the repo is ignored.
When neither is set, git uses its ambient credential helper.
Before the copy, the job starts scripts/pnl_report.py. A failed report
does not stop the copy.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPORT_RELATIVE = "docs/reports/live-fill-log.md"
ERR_RELATIVE = "logs/state/fill-log-push.err"


def copy_live_fill_log(source: Path, dest: Path) -> bool:
    """Replace dest with source. Return False when source is missing or empty."""
    if not source.is_file():
        return False
    payload = source.read_bytes()
    if not payload.strip():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(dest)
    return True


def _inside_repo(path: Path, repo: Path) -> bool:
    try:
        path.resolve().relative_to(repo.resolve())
    except ValueError:
        return False
    return True


def _redact(text: str) -> str:
    cleaned = re.sub(r"https://[^/\s:@]+:[^@\s/]+@", "https://redacted@", text)
    cleaned = re.sub(r"(ghp_|github_pat_|gho_)[A-Za-z0-9_]+", "[redacted]", cleaned)
    cleaned = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1[redacted]", cleaned)
    return cleaned


def git_env_for_fill_log(repo: Path) -> dict[str, str]:
    """Build a git environment. Refuse key and token files that live in the repo."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_EDITOR", "true")
    key = os.environ.get("FILL_LOG_GIT_SSH_KEY", "").strip()
    if not key:
        candidate = Path.home() / ".ssh" / "kalshi-fill-log"
        if candidate.is_file():
            key = str(candidate)
    if key:
        key_path = Path(key)
        if key_path.is_file() and not _inside_repo(key_path, repo):
            env["GIT_SSH_COMMAND"] = (
                "ssh -i "
                + shlex.quote(str(key_path))
                + " -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
            )
    token_file = os.environ.get("FILL_LOG_GIT_TOKEN_FILE", "").strip()
    if token_file:
        token_path = Path(token_file)
        if token_path.is_file() and not _inside_repo(token_path, repo):
            quoted = shlex.quote(str(token_path))
            helper = (
                "!f() { echo username=x-access-token; "
                f'echo "password=$(/bin/cat {quoted})"; '
                "}; f"
            )
            count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
            env["GIT_CONFIG_COUNT"] = str(count + 1)
            env[f"GIT_CONFIG_KEY_{count}"] = "credential.helper"
            env[f"GIT_CONFIG_VALUE_{count}"] = helper
    return env


def _write_err(repo: Path, message: str) -> None:
    path = repo / ERR_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _redact(message).encode("ascii", "replace").decode("ascii").strip()
    path.write_text(safe + "\n", encoding="utf-8")


def _clear_err(repo: Path) -> None:
    path = repo / ERR_RELATIVE
    if path.is_file():
        path.unlink()


def _run_git(args: list[str], repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _names(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def push_ref_from_env() -> str | None:
    """Return the side branch name, or None when the job should push upstream.

    An empty variable keeps the historical upstream push. A value that is not
    a single branch name is rejected by the caller.
    """
    raw = os.environ.get("FILL_LOG_PUSH_REF", "")
    if raw.strip() == "":
        return None
    ref = raw.strip()
    if (
        len(ref) > 100
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref)
        or ref.startswith("/")
        or ref.endswith("/")
        or ".." in ref.split("/")
        or "@" in ref
    ):
        raise ValueError("FILL_LOG_PUSH_REF is not a branch name")
    return ref


def _push_side_ref(git, repo: Path, ref: str, stamp: str) -> bool:
    """Commit the copied report in a detached worktree and push that branch.

    The live checkout's HEAD is not moved. The first publish starts at the
    current HEAD. Later publishes start at the side branch tip so the push
    fast-forwards.
    """
    head = git("rev-parse", "--verify", "HEAD")
    if head.returncode != 0 or not (head.stdout or "").strip():
        _write_err(repo, "fill log could not read HEAD")
        return False
    base = head.stdout.strip()
    fetched = git(
        "fetch",
        "origin",
        f"refs/heads/{ref}:refs/remotes/origin/{ref}",
    )
    if fetched.returncode == 0:
        remote_tip = git("rev-parse", "--verify", f"refs/remotes/origin/{ref}")
        if remote_tip.returncode == 0 and (remote_tip.stdout or "").strip():
            base = remote_tip.stdout.strip()
    parent = Path(tempfile.mkdtemp(prefix="kalshi-fill-log-"))
    worktree = parent / "wt"
    added = git("worktree", "add", "--detach", str(worktree), base)
    if added.returncode != 0:
        shutil.rmtree(parent, ignore_errors=True)
        detail = _redact((added.stderr or added.stdout or "").strip())
        _write_err(repo, f"fill log worktree failed: {detail}")
        return False

    def worktree_git(*args: str) -> subprocess.CompletedProcess[str]:
        return git("-C", str(worktree), *args)

    try:
        report = worktree / REPORT_RELATIVE
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes((repo / REPORT_RELATIVE).read_bytes())
        staged = worktree_git("add", "--", REPORT_RELATIVE)
        if staged.returncode != 0:
            _write_err(repo, "fill log side add failed: " + _redact(staged.stderr or ""))
            return False
        names = worktree_git("diff", "--cached", "--name-only")
        if _names(names) != [REPORT_RELATIVE]:
            _write_err(
                repo,
                "refusing to commit the side branch because the index is not only the fill log",
            )
            return False
        committed = worktree_git("commit", "-m", f"ops: live fill log {stamp}")
        if committed.returncode != 0:
            detail = _redact((committed.stderr or committed.stdout or "").strip())
            _write_err(repo, f"fill log side commit failed: {detail}")
            return False
        for tag in ("first", "retry"):
            pushed = worktree_git("push", "origin", f"HEAD:refs/heads/{ref}")
            if pushed.returncode == 0:
                return True
            detail = _redact((pushed.stderr or pushed.stdout or "").strip())
            _write_err(repo, f"{tag} fill log side push failed: {detail}")
        return False
    finally:
        git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(parent, ignore_errors=True)


def _payload_differs(source: Path, dest: Path) -> bool:
    if not source.is_file():
        return False
    source_bytes = source.read_bytes()
    if not source_bytes.strip():
        return False
    if not dest.is_file():
        return True
    return source_bytes != dest.read_bytes()


def _origin_is_ahead(git) -> tuple[bool, str | None]:
    """Return whether @{upstream} has commits that HEAD does not."""
    counted = git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if counted.returncode != 0:
        detail = _redact((counted.stderr or counted.stdout or "").strip())
        return False, f"git upstream check failed: {detail}"
    parts = (counted.stdout or "").split()
    if len(parts) != 2:
        return False, "git upstream check returned an unexpected count"
    _ahead, behind = parts
    return behind != "0", None


def _push_with_one_retry(git, repo: Path) -> bool:
    """Push the fill-log commit. Rebase first when origin is ahead.

    pull --rebase refuses any dirty worktree, even when origin has nothing
    new. Unrelated local edits must not block this job and must not be
    stashed, so the rebase runs only when origin is actually ahead.
    """

    def attempt(tag: str) -> bool:
        fetched = git("fetch", "origin")
        if fetched.returncode != 0:
            detail = _redact((fetched.stderr or fetched.stdout or "").strip())
            _write_err(repo, f"{tag} git fetch origin failed: {detail}")
            return False
        rebase, error = _origin_is_ahead(git)
        if error is not None:
            _write_err(repo, f"{tag} {error}")
            return False
        if rebase:
            pull = git("pull", "--rebase")
            if pull.returncode != 0:
                git("rebase", "--abort")
                detail = _redact((pull.stderr or pull.stdout or "").strip())
                _write_err(repo, f"{tag} git pull --rebase failed: {detail}")
                return False
        push = git("push")
        if push.returncode != 0:
            detail = _redact((push.stderr or push.stdout or "").strip())
            _write_err(repo, f"{tag} git push failed: {detail}")
            return False
        return True

    if attempt("first"):
        _clear_err(repo)
        return True
    if attempt("retry"):
        _clear_err(repo)
        return True
    return False


def publish_live_fill_report(
    repo: Path,
    *,
    dry_run: bool,
    source: Path,
    dest: Path,
    run_git=None,
    now: datetime | None = None,
) -> int:
    """Copy, and push only the report when it changed. Always return 0."""
    repo = repo.resolve()
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    env = git_env_for_fill_log(repo)

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        if run_git is not None:
            return run_git(list(args), repo, env)
        return _run_git(list(args), repo, env)

    try:
        if dry_run:
            print("dry-run: would-copy" if _payload_differs(source, dest) else "dry-run: unchanged")
            return 0
        copied = copy_live_fill_log(source, dest)
        print("copied" if copied else "left-in-place")
        status = git("status", "--porcelain", "--", REPORT_RELATIVE)
        if status.returncode != 0:
            _write_err(repo, "git status failed: " + _redact(status.stderr or ""))
            return 0
        if not (status.stdout or "").strip():
            print("unchanged")
            return 0
        staged = git("diff", "--cached", "--name-only")
        if staged.returncode != 0:
            _write_err(repo, "git diff --cached failed: " + _redact(staged.stderr or ""))
            return 0
        if _names(staged):
            _write_err(repo, "index already has staged files; fill log was not staged")
            return 0
        added = git("add", "--", REPORT_RELATIVE)
        if added.returncode != 0:
            _write_err(repo, "git add failed: " + _redact(added.stderr or ""))
            return 0
        staged_after = git("diff", "--cached", "--name-only")
        if _names(staged_after) != [REPORT_RELATIVE]:
            git("restore", "--staged", "--", REPORT_RELATIVE)
            _write_err(repo, "refusing to commit because the index is not only the fill log")
            return 0
        try:
            side_ref = push_ref_from_env()
        except ValueError as exc:
            git("restore", "--staged", "--", REPORT_RELATIVE)
            _write_err(repo, str(exc))
            return 0
        if side_ref is not None:
            git("restore", "--staged", "--", REPORT_RELATIVE)
            if _push_side_ref(git, repo, side_ref, stamp):
                restored = git("restore", "--source=HEAD", "--worktree", "--", REPORT_RELATIVE)
                if restored.returncode != 0:
                    _write_err(repo, "fill log side push landed, but the checkout report was not restored")
                else:
                    _clear_err(repo)
                    print("pushed")
            return 0
        message = f"ops: live fill log {stamp}"
        committed = git("commit", "-m", message)
        if committed.returncode != 0:
            git("restore", "--staged", "--", REPORT_RELATIVE)
            _write_err(repo, "git commit failed: " + _redact(committed.stderr or ""))
            return 0
        if _push_with_one_retry(git, repo):
            print("pushed")
        return 0
    except Exception as exc:
        _write_err(repo, "fill log push failed: " + _redact(str(exc)))
        return 0


def run_daily_pnl_report(repo: Path, runner=None) -> None:
    """Start the P&L report. A missing script or a failed run does not raise."""
    script = repo / "scripts" / "pnl_report.py"
    if not script.is_file():
        return
    launch = runner if runner is not None else subprocess.run
    try:
        launch(
            [sys.executable, str(script), "--repo", str(repo), "--write-fill-log"],
            cwd=str(repo),
            check=False,
        )
    except Exception:
        return


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg != "--dry-run" for arg in args):
        repo = Path(__file__).resolve().parents[1]
        _write_err(repo, "unknown fill-log argument")
        return 0
    repo = Path(__file__).resolve().parents[1]
    if "--dry-run" not in args:
        run_daily_pnl_report(repo)
    from trading.executor import LIVE_FILL_LOG_ARCHIVE_PATH, LIVE_FILL_LOG_PATH

    return publish_live_fill_report(
        Path(__file__).resolve().parents[1],
        dry_run="--dry-run" in args,
        source=LIVE_FILL_LOG_PATH,
        dest=LIVE_FILL_LOG_ARCHIVE_PATH,
    )


if __name__ == "__main__":
    sys.exit(main())
