from __future__ import annotations

import hashlib
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


def _convert_to_closed_wal(path: Path, value: str) -> None:
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        conn.execute("INSERT INTO sample(value) VALUES (?)", (value,))
        conn.commit()
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    path.with_name(f"{path.name}-wal").unlink(missing_ok=True)
    path.with_name(f"{path.name}-shm").unlink(missing_ok=True)


def _sample_values(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [str(row[0]) for row in conn.execute("SELECT value FROM sample ORDER BY rowid")]


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


def _run_backup(
    repo: Path,
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/db_snapshot_backup.sh", *args],
        cwd=repo,
        env={
            **os.environ,
            "KALSHI_OUTPUT_ROOT": str(repo / "logs"),
            **(extra_env or {}),
        },
        check=check,
        capture_output=True,
        text=True,
    )


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns


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
def test_db_snapshot_backup_closed_wal_without_sidecars_uses_safe_fallback(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    evidence_db = repo / "data/evidence_store.db"
    _convert_to_closed_wal(evidence_db, "closed-wal")
    before = _fingerprint(evidence_db)
    probe = subprocess.run(
        ["sqlite3", "-readonly", str(evidence_db), "SELECT COUNT(*) FROM sample;"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode != 0
    assert "unable to open database" in probe.stderr

    result = _run_backup(repo)

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    assert _sample_values(snapshot / "evidence_store.db") == [
        "evidence",
        "closed-wal",
    ]
    assert _fingerprint(evidence_db) == before
    assert _sample_values(evidence_db) == ["evidence", "closed-wal"]
    assert result.returncode == 0


@_DARWIN_ONLY
def test_db_snapshot_backup_preserves_active_evidence_wal_commits(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    evidence_db = repo / "data/evidence_store.db"
    evidence_conn = sqlite3.connect(evidence_db)
    try:
        assert evidence_conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        evidence_conn.execute("PRAGMA wal_autocheckpoint=0")
        evidence_conn.execute("INSERT INTO sample(value) VALUES ('active-wal')")
        evidence_conn.commit()
        wal = evidence_db.with_name("evidence_store.db-wal")
        shm = evidence_db.with_name("evidence_store.db-shm")
        assert wal.is_file()
        assert shm.is_file()
        before = {path: _fingerprint(path) for path in (evidence_db, wal)}

        _run_backup(repo)

        snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
        assert _sample_values(snapshot / "evidence_store.db") == [
            "evidence",
            "active-wal",
        ]
        assert {path: _fingerprint(path) for path in (evidence_db, wal)} == before
        assert shm.is_file()
    finally:
        evidence_conn.close()


@_DARWIN_ONLY
def test_db_snapshot_backup_evidence_failure_leaves_no_visible_bundle(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    (repo / "data/evidence_store.db").write_text(
        "not a sqlite database",
        encoding="utf-8",
    )

    result = _run_backup(repo, check=False)

    archive_root = repo / "logs/backups/db_snapshots"
    assert result.returncode == 2
    assert "ERROR: evidence_store.db backup failed" in result.stderr
    assert not list(archive_root.glob("????-??-??T????Z"))
    assert not list(archive_root.glob(".*.tmp.*"))


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


@_DARWIN_ONLY
def test_db_snapshot_backup_default_does_not_inspect_weather_state(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    weather_db = repo / "data/weather_shadow.db"
    weather_db.write_text("not a sqlite database", encoding="utf-8")
    before = _fingerprint(weather_db)

    result = _run_backup(repo)

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    assert (snapshot / "paper_trades.db").is_file()
    assert (snapshot / "evidence_store.db").is_file()
    assert not (snapshot / "weather_shadow.db").exists()
    assert "weather_shadow" not in result.stdout
    assert _fingerprint(weather_db) == before


@_DARWIN_ONLY
def test_db_snapshot_backup_opt_in_skips_absent_weather_without_creation(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    weather_db = repo / "data/weather_shadow.db"

    result = _run_backup(repo, "--include-weather")

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    assert (snapshot / "paper_trades.db").is_file()
    assert (snapshot / "evidence_store.db").is_file()
    assert not (snapshot / "weather_shadow.db").exists()
    assert "weather_shadow.db: skipped (not found)" in result.stdout
    assert not weather_db.exists()


@_DARWIN_ONLY
def test_db_snapshot_backup_opt_in_backs_up_and_restores_weather_without_mutation(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    weather_db = repo / "data/weather_shadow.db"
    _create_sqlite(weather_db, "weather")
    before = _fingerprint(weather_db)

    result = _run_backup(repo, "--include-weather")

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    weather_snapshot = snapshot / "weather_shadow.db"
    assert (snapshot / "paper_trades.db").is_file()
    assert (snapshot / "evidence_store.db").is_file()
    assert weather_snapshot.is_file()
    assert _integrity(weather_snapshot) == "ok"
    with sqlite3.connect(weather_snapshot) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone() == ("weather",)
    assert f"  weather_shadow.db: {weather_snapshot.stat().st_size} bytes" in result.stdout
    assert _fingerprint(weather_db) == before
    assert not weather_db.with_name("weather_shadow.db-wal").exists()
    assert not weather_db.with_name("weather_shadow.db-shm").exists()


@_DARWIN_ONLY
def test_db_snapshot_backup_handles_spaces_apostrophes_quotes_and_backslashes(
    tmp_path: Path,
):
    special_parent = tmp_path / "space ' apostrophe \" quote \\ backslash"
    special_parent.mkdir()
    repo = _seed_repo(special_parent)
    weather_db = repo / "data/weather_shadow.db"
    _create_sqlite(weather_db, "weather")
    sources = (
        repo / "data/paper_trades.db",
        repo / "data/evidence_store.db",
        weather_db,
    )
    before = {path: _fingerprint(path) for path in sources}

    result = _run_backup(repo, "--include-weather")

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    for name in ("paper_trades.db", "evidence_store.db", "weather_shadow.db"):
        assert _integrity(snapshot / name) == "ok"
    assert "weather_shadow.db:" in result.stdout
    assert {path: _fingerprint(path) for path in sources} == before


@_DARWIN_ONLY
def test_db_snapshot_backup_readonly_weather_source_preserves_live_wal_sidecars(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    weather_db = repo / "data/weather_shadow.db"
    weather_conn = sqlite3.connect(weather_db)
    try:
        assert weather_conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        weather_conn.execute("PRAGMA wal_autocheckpoint=0")
        weather_conn.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        weather_conn.execute("INSERT INTO sample(value) VALUES ('weather-wal')")
        weather_conn.commit()
        durable_sources = (
            weather_db,
            weather_db.with_name("weather_shadow.db-wal"),
        )
        shm = weather_db.with_name("weather_shadow.db-shm")
        assert all(path.is_file() for path in (*durable_sources, shm))
        before = {path: _fingerprint(path) for path in durable_sources}
        shm_size = shm.stat().st_size

        _run_backup(repo, "--include-weather")

        snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
        with sqlite3.connect(snapshot / "weather_shadow.db") as conn:
            assert conn.execute("SELECT value FROM sample").fetchone() == (
                "weather-wal",
            )
        assert {path: _fingerprint(path) for path in durable_sources} == before
        # Read-only WAL readers update transient SHM read marks. The durable DB
        # and WAL bytes must remain exact; the coordination sidecar must remain.
        assert shm.is_file()
        assert shm.stat().st_size == shm_size
    finally:
        weather_conn.close()


@_DARWIN_ONLY
def test_db_snapshot_backup_readonly_weather_source_does_not_recover_hot_journal(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    weather_db = repo / "data/weather_shadow.db"
    _create_sqlite(weather_db, "before-crash")
    crash = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sqlite3, sys; "
            "c=sqlite3.connect(sys.argv[1]); "
            "c.execute('PRAGMA journal_mode=DELETE'); "
            "c.execute('BEGIN IMMEDIATE'); "
            "c.execute(\"UPDATE sample SET value='uncommitted'\"); "
            "os._exit(0)",
            str(weather_db),
        ],
        check=False,
    )
    assert crash.returncode == 0
    journal = weather_db.with_name("weather_shadow.db-journal")
    assert journal.is_file()
    sources = (weather_db, journal)
    before = {path: _fingerprint(path) for path in sources}

    result = _run_backup(repo, "--include-weather")

    snapshot = next((repo / "logs/backups/db_snapshots").iterdir())
    with sqlite3.connect(snapshot / "weather_shadow.db") as conn:
        assert conn.execute("SELECT value FROM sample").fetchone() == (
            "before-crash",
        )
    assert result.returncode == 0
    assert {path: _fingerprint(path) for path in sources} == before


@_DARWIN_ONLY
def test_db_snapshot_backup_handles_publish_destination_race_without_nesting(
    tmp_path: Path,
):
    repo = _seed_repo(tmp_path)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    stat_shim = shim_dir / "stat"
    stat_shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "source_path=\"${!#}\"\n"
        "stage_dir=\"$(dirname \"$source_path\")\"\n"
        "stage_name=\"$(basename \"$stage_dir\")\"\n"
        "stamp=\"${stage_name#.}\"\n"
        "stamp=\"${stamp%%.tmp.*}\"\n"
        "mkdir -p \"$(dirname \"$stage_dir\")/$stamp\"\n"
        "exec /usr/bin/stat \"$@\"\n",
        encoding="utf-8",
    )
    stat_shim.chmod(0o755)

    result = _run_backup(
        repo,
        check=False,
        extra_env={"PATH": f"{shim_dir}:{os.environ['PATH']}"},
    )

    archive_root = repo / "logs/backups/db_snapshots"
    visible = [path for path in archive_root.iterdir() if not path.name.startswith(".")]
    hidden = [path for path in archive_root.iterdir() if path.name.startswith(".")]
    assert len(visible) == 1
    if result.returncode == 0:
        assert {path.name for path in visible[0].glob("*.db")} == {
            "paper_trades.db",
            "evidence_store.db",
        }
        assert not [path for path in visible[0].iterdir() if path.is_dir()]
    else:
        assert result.returncode == 2
        assert "ERROR: snapshot publish failed" in result.stderr
        assert not list(visible[0].iterdir())
    assert hidden == []


@_DARWIN_ONLY
def test_db_snapshot_backup_reports_mktemp_failure_as_backup_error(tmp_path: Path):
    repo = _seed_repo(tmp_path)
    _create_sqlite(repo / "data/weather_shadow.db", "weather")
    missing_tmpdir = repo / "missing-tmpdir"

    result = _run_backup(
        repo,
        "--include-weather",
        check=False,
        extra_env={"TMPDIR": str(missing_tmpdir)},
    )

    assert result.returncode == 2
    assert "ERROR: weather_shadow.db restore temp creation failed" in result.stderr
    assert not missing_tmpdir.exists()
