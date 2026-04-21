"""macOS status report for the Kalshi bot LaunchAgent workflow.

This is shell/status tooling only. It inspects launchd, process state, and
runtime log markers; it does not import or alter bot runtime behavior.

Process detection mirrors the known-good shell helpers in ~/.zshrc:
  _kalshi_bot_pids()       → _bot_pids()
  _kalshi_caffeinate_pids() → _caffeinate_pids()
  botstatus()              → print_bot_section()
  botcaff()                → print_caffeinate_section()

Keep the Python implementations in sync with those shell functions rather than
inventing a separate process model.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


LOG_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2}),(?P<ms>\d{3}) UTC "
)
BOOT_RE = re.compile(r"\[BOOT\]\s+version=(?P<version>\S+)\s+pid=(?P<pid>\d+)")
SHUTDOWN_MARKERS = (
    "Shutdown signal received",
    "Bot shutting down...",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    cpu: str
    mem: str
    etimes: int
    command: str


@dataclass(frozen=True)
class BotSession:
    boot_ts: datetime
    version: str
    pid: int | None
    shutdown_ts: datetime | None = None
    shutdown_marker: str | None = None
    next_boot_ts: datetime | None = None


def human_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "n/a"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def parse_log_ts(line: str) -> datetime | None:
    match = LOG_TS_RE.match(line)
    if not match:
        return None
    raw = f"{match.group('date')} {match.group('time')}.{match.group('ms')}"
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def parse_sessions(lines: Iterable[str]) -> list[BotSession]:
    sessions: list[BotSession] = []
    current: BotSession | None = None
    for line in lines:
        ts = parse_log_ts(line)
        if ts is None:
            continue
        boot_match = BOOT_RE.search(line)
        if boot_match:
            if current is not None:
                sessions.append(BotSession(
                    boot_ts=current.boot_ts,
                    version=current.version,
                    pid=current.pid,
                    shutdown_ts=current.shutdown_ts,
                    shutdown_marker=current.shutdown_marker,
                    next_boot_ts=ts,
                ))
            current = BotSession(
                boot_ts=ts,
                version=boot_match.group("version"),
                pid=int(boot_match.group("pid")),
            )
            continue
        if current is not None and current.shutdown_ts is None:
            marker = next((m for m in SHUTDOWN_MARKERS if m in line), None)
            if marker is not None:
                current = BotSession(
                    boot_ts=current.boot_ts,
                    version=current.version,
                    pid=current.pid,
                    shutdown_ts=ts,
                    shutdown_marker=marker,
                    next_boot_ts=current.next_boot_ts,
                )
    if current is not None:
        sessions.append(current)
    return sessions


def session_duration(session: BotSession, now: datetime, active: bool) -> str:
    if session.shutdown_ts is not None:
        return human_duration((session.shutdown_ts - session.boot_ts).total_seconds())
    if active:
        return human_duration((now - session.boot_ts).total_seconds())
    return "n/a (shutdown not observed)"


def read_sessions(log_path: Path) -> list[BotSession]:
    try:
        return parse_sessions(log_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return []


def run_command(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return ""


def launchd_target(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def launchd_print(label: str) -> str:
    return run_command(["launchctl", "print", launchd_target(label)])


def launchd_pid(launchd_output: str) -> int | None:
    match = re.search(r"^[ \t]*pid = (?P<pid>\d+)", launchd_output, re.MULTILINE)
    return int(match.group("pid")) if match else None


def launchd_state(launchd_output: str) -> str:
    match = re.search(r"^[ \t]*state = (?P<state>\S+)", launchd_output, re.MULTILINE)
    return match.group("state") if match else "not loaded"


def _parse_etime(etime: str) -> int:
    """Parse macOS ps etime format [[dd-]hh:]mm:ss to total seconds.

    macOS ps uses 'etime' (human format); Linux ps has 'etimes' (seconds).
    We use 'etime' to stay portable on macOS and convert to seconds here.
    """
    etime = etime.strip()
    days = 0
    if "-" in etime:
        day_part, etime = etime.split("-", 1)
        days = int(day_part)
    parts = etime.split(":")
    if len(parts) == 3:
        return days * 86_400 + int(parts[0]) * 3_600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return days * 86_400 + int(parts[0]) * 60 + int(parts[1])
    return days * 86_400 + int(parts[0])


def process_table() -> list[ProcessInfo]:
    # Use 'etime' (macOS) not 'etimes' (Linux-only); no spaces in etime value.
    output = run_command(["ps", "-ww", "-axo", "pid=,ppid=,pcpu=,pmem=,etime=,command="])
    rows: list[ProcessInfo] = []
    for raw in output.splitlines():
        parts = raw.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, cpu, mem, etime_str, command = parts
        try:
            rows.append(ProcessInfo(
                pid=int(pid),
                ppid=int(ppid),
                cpu=cpu,
                mem=mem,
                etimes=_parse_etime(etime_str),
                command=command,
            ))
        except ValueError:
            continue
    return rows


# ---------------------------------------------------------------------------
# Process detection — mirrors ~/.zshrc helper functions exactly.
# Do not add relationship inference here; keep these as independent detectors.
# ---------------------------------------------------------------------------

def _bot_pids(
    rows: list[ProcessInfo],
    *,
    launchd_pid: int | None,
    python_path: Path,
    main_path: Path,
) -> list[ProcessInfo]:
    """Mirror _kalshi_bot_pids() from ~/.zshrc.

    Priority 1 (launchd-managed): if launchd reports a live PID, return that
    process row and stop.  The shell equivalent is:
        if [ -n "$launchd_pid" ] && ps -p "$launchd_pid" -o pid= >/dev/null 2>&1
        then printf '%s\\n' "$launchd_pid"; return 0; fi

    Priority 2 (fallback / manual start): scan all processes for commands
    ending in ' {main}' while excluding the exact caffeinate command form.
    Shell equivalent:
        ps -ww -axo pid=,command= | awk '
          $0 != caffeinate_cmd && $0 ~ (" " main "$") { print $1 }'
    """
    main = str(main_path)
    python = str(python_path)
    caffeinate_cmd = f"/usr/bin/caffeinate -dimsu {python} {main}"

    # Priority 1: trust launchd's reported PID (same early-return as shell).
    if launchd_pid is not None:
        proc = next((p for p in rows if p.pid == launchd_pid), None)
        if proc is not None:
            return [proc]

    # Priority 2: scan for Python bot processes by command shape.
    return [
        p for p in rows
        if p.command != caffeinate_cmd and p.command.endswith(f" {main}")
    ]


def _caffeinate_pids(
    rows: list[ProcessInfo],
    *,
    python_path: Path,
    main_path: Path,
) -> list[ProcessInfo]:
    """Mirror _kalshi_caffeinate_pids() from ~/.zshrc.

    Shell equivalent:
        pgrep -f "^/usr/bin/caffeinate -dimsu $KALSHI_PYTHON $KALSHI_MAIN$"

    Exact command match only — no relationship inference with the bot process.
    Caffeinate may be a child of the bot, not a parent; relationship is not
    checked here (mirrors botcaff() behaviour).
    """
    main = str(main_path)
    python = str(python_path)
    caffeinate_cmd = f"/usr/bin/caffeinate -dimsu {python} {main}"
    return [p for p in rows if p.command == caffeinate_cmd]


def process_start_utc(proc: ProcessInfo, now_epoch: float) -> datetime:
    return datetime.fromtimestamp(now_epoch - proc.etimes, tz=timezone.utc)


def short_command(command: str, width: int = 96) -> str:
    if len(command) <= width:
        return command
    return command[: width - 1] + "…"


def percent_value(value: str) -> str:
    return "n/a" if value == "n/a" else f"{value}%"


# ---------------------------------------------------------------------------
# Display sections — mirror botstatus() and botcaff() from ~/.zshrc.
# ---------------------------------------------------------------------------

def print_launchd(label: str, output: str, pid: int | None) -> None:
    print("=== LaunchAgent state ===")
    print(f"Label      : {label}")
    print(f"Target     : {launchd_target(label)}")
    print(f"State      : {launchd_state(output)}")
    print(f"Launchd PID: {pid if pid is not None else 'n/a'}")
    if not output:
        print("Detail     : launchd service not loaded or not readable")
    print()


def print_bot_section(
    bots: list[ProcessInfo],
    *,
    now_epoch: float,
    now: datetime,
) -> ProcessInfo | None:
    """Mirror botstatus() from ~/.zshrc.

    Header mirrors:
        echo "=== bot python process (main.py; launchd may report this PID) ==="
    """
    print("=== bot python process (main.py; launchd may report this PID) ===")
    if not bots:
        print("kalshi_bot is not running")
        print()
        return None
    for proc in bots:
        started = process_start_utc(proc, now_epoch)
        print(f"Bot PID    : {proc.pid}")
        print(f"Parent PID : {proc.ppid if proc.ppid else 'n/a'}")
        print(f"CPU / MEM  : {percent_value(proc.cpu)} / {percent_value(proc.mem)}")
        print(f"Started UTC: {started.isoformat()}")
        print(f"Elapsed    : {human_duration(proc.etimes)}")
        print(f"Command    : {short_command(proc.command)}")
    print()
    return bots[0]


def print_caffeinate_section(
    caffeinates: list[ProcessInfo],
    *,
    now_epoch: float,
) -> None:
    """Mirror botcaff() from ~/.zshrc.

    Header mirrors:
        echo "=== caffeinate assertion/helper process (may be child of bot, not parent) ==="

    Caffeinate is reported independently — no relationship check against the
    bot PID.  The shell helper never conditions caffeinate display on PPID.
    """
    print("=== caffeinate assertion/helper process (may be child of bot, not parent) ===")
    if not caffeinates:
        print("caffeinate helper is not running")
        print()
        return
    for proc in caffeinates:
        started = process_start_utc(proc, now_epoch)
        print(f"Caffeinate PID: {proc.pid}")
        print(f"Parent PID    : {proc.ppid if proc.ppid else 'n/a'}")
        print(f"CPU / MEM     : {percent_value(proc.cpu)} / {percent_value(proc.mem)}")
        print(f"Started UTC   : {started.isoformat()}")
        print(f"Elapsed       : {human_duration(proc.etimes)}")
        print(f"Command       : {short_command(proc.command)}")
    print()


def print_last_boot(log_path: Path, sessions: list[BotSession], now: datetime) -> None:
    print("=== Last bot boot seen in logs ===")
    print(f"Log file   : {log_path}")
    if not sessions:
        print("Result     : no [BOOT] entries found")
        print()
        return
    last = sessions[-1]
    print(f"Boot UTC   : {last.boot_ts.isoformat()}")
    print(f"Version    : {last.version}")
    print(f"Log PID    : {last.pid if last.pid is not None else 'n/a'}")
    print(f"Age        : {human_duration((now - last.boot_ts).total_seconds())}")
    print()


def print_history(
    *,
    sessions: list[BotSession],
    current_proc: ProcessInfo | None,
    now: datetime,
) -> None:
    print("=== Recent runtime history (last 5) ===")
    if not sessions:
        print("No runtime sessions found in active app log.")
        return

    current_pid = current_proc.pid if current_proc is not None else None
    recent = sessions[-5:]
    for idx, session in enumerate(recent, start=1):
        active = (
            session.shutdown_ts is None
            and current_pid is not None
            and (session.pid == current_pid or session == sessions[-1])
        )
        shutdown = (
            session.shutdown_ts.isoformat()
            if session.shutdown_ts is not None
            else ("ACTIVE" if active else "not observed")
        )
        duration = session_duration(session, now, active)
        status = "current active" if active else "closed" if session.shutdown_ts else "unpaired"
        print(
            f"{idx}. version={session.version} pid={session.pid or 'n/a'} "
            f"boot={session.boot_ts.isoformat()} shutdown={shutdown} "
            f"runtime={duration} status={status}"
        )
        if session.shutdown_ts is None and session.next_boot_ts is not None and not active:
            print(
                f"   note: next boot at {session.next_boot_ts.isoformat()} "
                "but no shutdown marker was found before it"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = Path(os.environ.get("KALSHI_HOME", "/Users/Jake/vscode/kalshi_bot"))
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument("--label", default=os.environ.get("KALSHI_LAUNCHD_LABEL", "com.jake.kalshi-bot"))
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("KALSHI_PYTHON", default_home / ".venv/bin/python")))
    parser.add_argument("--main", type=Path, default=Path(os.environ.get("KALSHI_MAIN", default_home / "main.py")))
    parser.add_argument("--log", type=Path, default=Path(os.environ.get("KALSHI_APP_LOG", default_home / "logs/app/bot.log")))
    args = parser.parse_args()

    now_epoch = time.time()
    now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)

    launchd_output = launchd_print(args.label)
    wrapper_pid = launchd_pid(launchd_output)

    rows = process_table()
    bots = _bot_pids(rows, launchd_pid=wrapper_pid, python_path=args.python, main_path=args.main)
    caffeinates = _caffeinate_pids(rows, python_path=args.python, main_path=args.main)

    sessions = read_sessions(args.log)

    print_launchd(args.label, launchd_output, wrapper_pid)
    current_proc = print_bot_section(bots, now_epoch=now_epoch, now=now)
    print_caffeinate_section(caffeinates, now_epoch=now_epoch)
    print_last_boot(args.log, sessions, now)
    print_history(sessions=sessions, current_proc=current_proc, now=now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
