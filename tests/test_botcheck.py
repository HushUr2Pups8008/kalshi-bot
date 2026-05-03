"""Tests for scripts/botcheck.py.

Process detection logic is tested against the behavioral contract defined by
the ~/.zshrc shell helpers:
    _bot_pids()        → _kalshi_bot_pids()
    _caffeinate_pids() → _kalshi_caffeinate_pids()
    print_bot_section()       → botstatus()
    print_caffeinate_section() → botcaff()
"""

from datetime import datetime, timezone
from pathlib import Path

from scripts.botcheck import (
    ProcessInfo,
    _bot_pids,
    _caffeinate_pids,
    _default_repo_root,
    human_duration,
    parse_sessions,
    print_bot_section,
    print_caffeinate_section,
    session_duration,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Synthetic test paths — must be portable across machines (no hardcoded
# /Users/<name>). The values do not need to exist on disk; tests only verify
# the parser logic against these strings as opaque tokens.
_REPO_ROOT_FIXTURE = Path.home() / "vscode" / "kalshi-bot"
PYTHON_PATH = _REPO_ROOT_FIXTURE / ".venv" / "bin" / "python"
MAIN_PATH = _REPO_ROOT_FIXTURE / "main.py"
CAFFEINATE_CMD = f"/usr/bin/caffeinate -dimsu {PYTHON_PATH} {MAIN_PATH}"


def _bot_proc(pid: int = 74105, ppid: int = 1, command: str | None = None) -> ProcessInfo:
    return ProcessInfo(
        pid=pid,
        ppid=ppid,
        cpu="27.7",
        mem="1.0",
        etimes=121,
        command=command or (
            "/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework"
            "/Versions/3.14/Resources/Python.app/Contents/MacOS/Python "
            f"{MAIN_PATH}"
        ),
    )


def _caff_proc(pid: int = 74107, ppid: int = 74105) -> ProcessInfo:
    return ProcessInfo(
        pid=pid,
        ppid=ppid,
        cpu="0.0",
        mem="0.0",
        etimes=125,
        command=CAFFEINATE_CMD,
    )


# ---------------------------------------------------------------------------
# human_duration
# ---------------------------------------------------------------------------

def test_human_duration_uses_unambiguous_units():
    assert human_duration(45) == "45s"
    assert human_duration(11 * 60 + 45) == "11m 45s"
    assert human_duration(2 * 3600 + 13 * 60) == "2h 13m"
    assert human_duration(86_400 + 4 * 3600 + 2 * 60) == "1d 4h 02m"


# ---------------------------------------------------------------------------
# parse_sessions
# ---------------------------------------------------------------------------

def test_parse_sessions_pairs_boot_with_next_shutdown_marker():
    sessions = parse_sessions([
        "2026-04-20 01:00:00,000 UTC INFO     main                 [BOOT] version=0.30.0 pid=111 ctx=runtime cwd=/repo",
        "2026-04-20 01:15:30,000 UTC INFO     main                 Shutdown signal received",
        "2026-04-20 01:15:31,000 UTC INFO     main                 Bot shutting down...",
        "2026-04-20 02:00:00,000 UTC INFO     main                 [BOOT] version=0.30.1 pid=222 ctx=runtime cwd=/repo",
    ])

    assert len(sessions) == 2
    assert sessions[0].version == "0.30.0"
    assert sessions[0].pid == 111
    assert sessions[0].shutdown_ts == datetime(2026, 4, 20, 1, 15, 30, tzinfo=timezone.utc)
    assert sessions[1].version == "0.30.1"
    assert sessions[1].shutdown_ts is None


def test_parse_sessions_records_next_boot_when_shutdown_missing():
    sessions = parse_sessions([
        "2026-04-20 01:00:00,000 UTC INFO     main                 [BOOT] version=0.30.0 pid=111 ctx=runtime cwd=/repo",
        "2026-04-20 02:00:00,000 UTC INFO     main                 [BOOT] version=0.30.1 pid=222 ctx=runtime cwd=/repo",
    ])

    assert len(sessions) == 2
    assert sessions[0].shutdown_ts is None
    assert sessions[0].next_boot_ts == datetime(2026, 4, 20, 2, 0, tzinfo=timezone.utc)


def test_session_duration_is_honest_for_unpaired_inactive_session():
    session = parse_sessions([
        "2026-04-20 01:00:00,000 UTC INFO     main                 [BOOT] version=0.30.0 pid=111 ctx=runtime cwd=/repo",
    ])[0]

    assert session_duration(
        session,
        datetime(2026, 4, 20, 2, 0, tzinfo=timezone.utc),
        active=False,
    ) == "n/a (shutdown not observed)"
    assert session_duration(
        session,
        datetime(2026, 4, 20, 2, 0, tzinfo=timezone.utc),
        active=True,
    ) == "1h 00m"


# ---------------------------------------------------------------------------
# _bot_pids — mirrors _kalshi_bot_pids()
# ---------------------------------------------------------------------------

def test_bot_pids_launchd_pid_takes_priority_over_scan():
    """_bot_pids mirrors _kalshi_bot_pids priority-1: launchd PID → return immediately."""
    bot = _bot_proc(pid=74105)
    rows = [bot]

    result = _bot_pids(rows, launchd_pid=74105, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [74105]


def test_bot_pids_launchd_pid_returned_regardless_of_command_shape():
    """When launchd reports a live PID, return it even if command doesn't look like Python.

    _kalshi_bot_pids() uses `ps -p $launchd_pid -o pid=` (existence check only),
    not a command filter.  This test pins that the Python mirror behaves the same.
    """
    # Command is an unrecognised shape — shell helper doesn't check, neither should we.
    wrapper = ProcessInfo(pid=99, ppid=1, cpu="0.0", mem="0.0", etimes=5, command="some-wrapper")
    rows = [wrapper]

    result = _bot_pids(rows, launchd_pid=99, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [99]


def test_bot_pids_falls_back_to_scan_when_launchd_pid_absent():
    """When launchd_pid is None, scan ps for commands ending in ' {main}'."""
    bot = _bot_proc(pid=74105)
    rows = [bot]

    result = _bot_pids(rows, launchd_pid=None, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [74105]


def test_bot_pids_scan_excludes_exact_caffeinate_command():
    """_kalshi_bot_pids() excludes the caffeinate wrapper from bot pids."""
    caff = _caff_proc(pid=10, ppid=1)
    bot = _bot_proc(pid=11, ppid=10)
    rows = [caff, bot]

    result = _bot_pids(rows, launchd_pid=None, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [11], "caffeinate must not appear in bot pids"


def test_bot_pids_python_app_path_form_detected():
    """Python.app deep-framework path is detected by the scan (ends with ' main.py')."""
    rows = [_bot_proc(pid=74105)]  # command ends with MAIN_PATH per fixture

    result = _bot_pids(rows, launchd_pid=None, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [74105]


def test_bot_pids_not_running_when_launchd_pid_not_in_ps():
    """If launchd_pid is alive per launchctl but absent from ps rows, fall through to scan."""
    rows = []  # empty — PID not in ps at this moment

    result = _bot_pids(rows, launchd_pid=99999, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert result == []


# ---------------------------------------------------------------------------
# _caffeinate_pids — mirrors _kalshi_caffeinate_pids()
# ---------------------------------------------------------------------------

def test_caffeinate_pids_detects_exact_command_match():
    """_caffeinate_pids mirrors pgrep -f '^/usr/bin/caffeinate -dimsu ... main$'."""
    caff = _caff_proc(pid=74107, ppid=74105)
    rows = [caff]

    result = _caffeinate_pids(rows, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [74107]


def test_caffeinate_pids_empty_when_not_running():
    """When caffeinate is absent, _caffeinate_pids returns empty."""
    bot = _bot_proc(pid=74105)
    rows = [bot]

    result = _caffeinate_pids(rows, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert result == []


def test_caffeinate_pids_is_independent_of_ppid():
    """_caffeinate_pids detects caffeinate regardless of its PPID.

    _kalshi_caffeinate_pids() uses pgrep -f (command match only) — PPID is
    irrelevant.  Caffeinate may be a child of the bot, not a parent of it.
    """
    # caffeinate with ppid=1 (orphaned / reparented) — must still be detected
    caff = _caff_proc(pid=99, ppid=1)
    rows = [caff]

    result = _caffeinate_pids(rows, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert [p.pid for p in result] == [99]


def test_caffeinate_pids_rejects_partial_command_match():
    """Command must be the exact caffeinate invocation, not a substring match."""
    # Caffeinate with extra trailing args — not a match.
    not_caff = ProcessInfo(
        pid=55,
        ppid=1,
        cpu="0.0",
        mem="0.0",
        etimes=10,
        command=f"{CAFFEINATE_CMD} --extra-flag",
    )
    rows = [not_caff]

    result = _caffeinate_pids(rows, python_path=PYTHON_PATH, main_path=MAIN_PATH)

    assert result == []


# ---------------------------------------------------------------------------
# print_bot_section — mirrors botstatus()
# ---------------------------------------------------------------------------

def test_bot_section_shows_pid_and_command(capsys):
    """print_bot_section mirrors botstatus() output structure."""
    bot = _bot_proc(pid=74105)

    current = print_bot_section([bot], now_epoch=1_800_000_000, now=datetime(2026, 4, 21, 1, 41, 0, tzinfo=timezone.utc))

    out = capsys.readouterr().out
    assert current == bot
    assert "bot python process" in out
    assert "launchd may report this PID" in out
    assert "Bot PID    : 74105" in out
    assert "main.py" in out


def test_bot_section_not_running_when_empty(capsys):
    """print_bot_section mirrors botstatus() 'kalshi_bot is not running' path."""
    current = print_bot_section([], now_epoch=1_800_000_000, now=datetime(2026, 4, 21, 1, 41, 0, tzinfo=timezone.utc))

    out = capsys.readouterr().out
    assert current is None
    assert "kalshi_bot is not running" in out


# ---------------------------------------------------------------------------
# print_caffeinate_section — mirrors botcaff()
# ---------------------------------------------------------------------------

def test_caffeinate_section_shows_pid_and_command(capsys):
    """print_caffeinate_section mirrors botcaff() output structure."""
    caff = _caff_proc(pid=74107, ppid=74105)

    print_caffeinate_section([caff], now_epoch=1_800_000_000)

    out = capsys.readouterr().out
    assert "caffeinate assertion/helper process" in out
    assert "may be child of bot, not parent" in out
    assert "Caffeinate PID: 74107" in out
    assert "not related" not in out
    assert "not running" not in out


def test_caffeinate_section_not_running_when_empty(capsys):
    """print_caffeinate_section mirrors botcaff() 'caffeinate helper is not running' path."""
    print_caffeinate_section([], now_epoch=1_800_000_000)

    out = capsys.readouterr().out
    assert "caffeinate helper is not running" in out


def test_caffeinate_section_child_of_bot_shown_without_relationship_inference(capsys):
    """Caffeinate that is a child of the bot PID is shown — no PPID gate.

    This is the launchd shape: Python bot (pid=74105) spawns caffeinate as a
    child (ppid=74105).  botcaff() shows caffeinate regardless; so must we.
    """
    caff = _caff_proc(pid=74107, ppid=74105)

    print_caffeinate_section([caff], now_epoch=1_800_000_000)

    out = capsys.readouterr().out
    assert "Caffeinate PID: 74107" in out
    assert "Parent PID    : 74105" in out
    assert "not related" not in out


def test_caffeinate_section_shown_when_bot_and_caffeinate_both_present(capsys):
    """Both bot and caffeinate sections report correctly with no contradiction.

    Regression: old code gated caffeinate display on PPID relationship; when
    current was synthetic (ppid=0), caffeinate_child check failed and showed
    'not running or not related to current bot' even when caffeinate was live.
    """
    bot = _bot_proc(pid=74105, ppid=1)
    caff = _caff_proc(pid=74107, ppid=74105)

    current = print_bot_section([bot], now_epoch=1_800_000_000, now=datetime(2026, 4, 21, 1, 41, 0, tzinfo=timezone.utc))
    print_caffeinate_section([caff], now_epoch=1_800_000_000)

    out = capsys.readouterr().out
    assert current == bot
    assert "Bot PID    : 74105" in out
    assert "Caffeinate PID: 74107" in out
    assert "not related" not in out
    assert "not running" not in out


# ---------------------------------------------------------------------------
# _default_repo_root — portability helper
# ---------------------------------------------------------------------------

def test_default_repo_root_respects_kalshi_home_env(monkeypatch, tmp_path):
    """KALSHI_HOME env var beats every detection heuristic."""
    monkeypatch.setenv("KALSHI_HOME", str(tmp_path / "explicit"))
    assert _default_repo_root() == tmp_path / "explicit"


def test_default_repo_root_prefers_hyphen_when_both_exist(monkeypatch, tmp_path):
    """When KALSHI_HOME is unset, kalshi-bot (hyphen) wins over kalshi_bot."""
    monkeypatch.delenv("KALSHI_HOME", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / "vscode" / "kalshi-bot").mkdir(parents=True)
    (fake_home / "vscode" / "kalshi_bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    assert _default_repo_root() == fake_home / "vscode" / "kalshi-bot"


def test_default_repo_root_falls_back_to_underscore_when_only_legacy_exists(
    monkeypatch, tmp_path
):
    """MacBook legacy layout (kalshi_bot) is detected when hyphen path absent."""
    monkeypatch.delenv("KALSHI_HOME", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / "vscode" / "kalshi_bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    assert _default_repo_root() == fake_home / "vscode" / "kalshi_bot"


def test_default_repo_root_default_when_neither_dir_exists(monkeypatch, tmp_path):
    """Final fallback is the canonical hyphen path even if no directory exists."""
    monkeypatch.delenv("KALSHI_HOME", raising=False)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    assert _default_repo_root() == fake_home / "vscode" / "kalshi-bot"
