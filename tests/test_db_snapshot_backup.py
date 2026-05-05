from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


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


def test_db_snapshot_backup_seeds_online_safe_sqlite_copies(tmp_path: Path):
    repo = _seed_repo(tmp_path)

    result = subprocess.run(
        ["bash", "scripts/db_snapshot_backup.sh", "--retention-days", "7"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    snapshots = sorted((repo / "mac_archive/db_snapshots").glob("????-??-??T????Z"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "paper_trades.db").is_file()
    assert (snapshots[0] / "evidence_store.db").is_file()
    assert _integrity(snapshots[0] / "paper_trades.db") == "ok"
    assert _integrity(snapshots[0] / "evidence_store.db") == "ok"
    assert "snapshot ok:" in result.stdout


def test_db_snapshot_backup_prunes_expired_snapshot_directories(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    old_snapshot = repo / "mac_archive/db_snapshots/2000-01-01T0000Z"
    old_snapshot.mkdir(parents=True)
    (old_snapshot / "paper_trades.db").write_text("stale", encoding="utf-8")
    old_time = time.time() - 3 * 24 * 60 * 60
    os.utime(old_snapshot, (old_time, old_time))

    subprocess.run(
        ["bash", "scripts/db_snapshot_backup.sh", "--retention-days", "1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    assert not old_snapshot.exists()
    snapshots = sorted((repo / "mac_archive/db_snapshots").glob("????-??-??T????Z"))
    assert len(snapshots) == 1
