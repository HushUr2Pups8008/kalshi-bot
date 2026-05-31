from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

_DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "scripts/db_snapshot_backup.sh is launchd-driven (com.kalshi.db-backup.plist) "
        "and uses BSD `stat -f` plus the `sqlite3` CLI; Linux CI lacks both. The script "
        "is macOS-only by design — runs locally on the operator host."
    ),
)


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)


def _create_sqlite(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        conn.commit()


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "scripts").mkdir()
    shutil.copy2(REPO_ROOT / "scripts/db_snapshot_backup.sh", repo / "scripts/db_snapshot_backup.sh")
    _create_sqlite(repo / "data/paper_trades.db", "paper")
    _create_sqlite(repo / "data/evidence_store.db", "evidence")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    return repo


def _integrity(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


@_DARWIN_ONLY
def test_db_snapshot_backup_seeds_online_safe_sqlite_copies(tmp_path: Path):
    repo = _seed_repo(tmp_path)

    result = subprocess.run(
        ["bash", "scripts/db_snapshot_backup.sh", "--retention-days", "7"],
        cwd=repo,
        # The script's output root honors KALSHI_OUTPUT_ROOT > KALSHI_LOG_ROOT >
        # REPO_ROOT/logs. conftest sets KALSHI_LOG_ROOT to a shared temp; pin
        # KALSHI_OUTPUT_ROOT to this test's repo/logs so output stays isolated here.
        env={**os.environ, "KALSHI_OUTPUT_ROOT": str(repo / "logs")},
        check=True,
        capture_output=True,
        text=True,
    )

    snapshots = sorted((repo / "logs/backups/db_snapshots").glob("????-??-??T????Z"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "paper_trades.db").is_file()
    assert (snapshots[0] / "evidence_store.db").is_file()
    assert _integrity(snapshots[0] / "paper_trades.db") == "ok"
    assert _integrity(snapshots[0] / "evidence_store.db") == "ok"
    assert "snapshot ok:" in result.stdout


@_DARWIN_ONLY
def test_db_snapshot_backup_prunes_expired_snapshot_directories(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    old_snapshot = repo / "logs/backups/db_snapshots/2000-01-01T0000Z"
    old_snapshot.mkdir(parents=True)
    (old_snapshot / "paper_trades.db").write_text("stale", encoding="utf-8")
    old_time = time.time() - 3 * 24 * 60 * 60
    os.utime(old_snapshot, (old_time, old_time))

    subprocess.run(
        ["bash", "scripts/db_snapshot_backup.sh", "--retention-days", "1"],
        cwd=repo,
        env={**os.environ, "KALSHI_OUTPUT_ROOT": str(repo / "logs")},
        check=True,
        capture_output=True,
        text=True,
    )

    assert not old_snapshot.exists()
    snapshots = sorted((repo / "logs/backups/db_snapshots").glob("????-??-??T????Z"))
    assert len(snapshots) == 1
