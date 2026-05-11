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
import gzip
import json
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


LOG_TS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2}),(?P<ms>\d{3}) UTC "
)
BOOT_RE = re.compile(r"\[BOOT\]\s+version=(?P<version>\S+)\s+pid=(?P<pid>\d+)")
# New sigil banner: # ===== v0.29.59 | env=prod | model=qwen2.5:7b | py=3.14.4 =====
SIGIL_RE = re.compile(
    r"^#\s*={5,}\s+v(?P<version>\S+)\s+\|\s+env=\S+\s+\|\s+model=\S+\s+\|\s+py=\S+\s+=+\s*$"
)
SHUTDOWN_MARKERS = (
    "Shutdown signal received",
    "Bot shutting down...",
)
SIGNAL_FLOW_EVENTS = (
    "EARLY_FRESH_PASS",
    "MATCH_DIAGNOSTIC",
    "ANALYSIS_REJECTED",
    "OPPORTUNITY",
    "BLEND_DECISION",
    "SKIPPED",
    "PAPER_TRADE",
    "LIVE_ORDER",
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


@dataclass(frozen=True)
class SignalFlowStats:
    path: Path
    since: datetime
    records_kept: int
    lines_total: int
    lines_malformed: int
    counts: Counter[str]
    latest_ts_by_type: dict[str, datetime]


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


def _parse_trade_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_trade_log_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return

    archive_root = path / "archive"
    if archive_root.exists():
        yield from sorted(
            candidate
            for candidate in archive_root.rglob("*")
            if candidate.name.endswith((".jsonl", ".jsonl.gz"))
        )

    legacy = path / "trades.jsonl"
    live = path / "live" / "trades.jsonl"
    if legacy.exists():
        yield legacy
    if live.exists():
        yield live


def _iter_trade_records(
    path: Path,
    *,
    lines_total: list[int],
    lines_malformed: list[int],
) -> Iterable[dict[str, Any]]:
    for file_path in _iter_trade_log_files(path):
        opener = gzip.open if file_path.name.endswith(".gz") else open
        try:
            with opener(file_path, "rt", encoding="utf-8") as fh:
                for raw_line in fh:
                    lines_total[0] += 1
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        lines_malformed[0] += 1
                        continue
                    if isinstance(record, dict):
                        yield record
                    else:
                        lines_malformed[0] += 1
        except OSError:
            continue


def summarize_signal_flow(
    path: Path,
    *,
    now: datetime,
    window_hours: float,
) -> SignalFlowStats:
    since = now - timedelta(hours=window_hours)
    lines_total = [0]
    lines_malformed = [0]
    counts: Counter[str] = Counter()
    latest_ts_by_type: dict[str, datetime] = {}
    records_kept = 0

    for record in _iter_trade_records(
        path,
        lines_total=lines_total,
        lines_malformed=lines_malformed,
    ):
        event_type = str(record.get("type") or "").strip()
        if event_type not in SIGNAL_FLOW_EVENTS:
            continue
        ts = _parse_trade_ts(record.get("ts"))
        if ts is not None and ts < since:
            continue
        records_kept += 1
        counts[event_type] += 1
        if ts is not None and (
            event_type not in latest_ts_by_type or ts > latest_ts_by_type[event_type]
        ):
            latest_ts_by_type[event_type] = ts

    return SignalFlowStats(
        path=path,
        since=since,
        records_kept=records_kept,
        lines_total=lines_total[0],
        lines_malformed=lines_malformed[0],
        counts=counts,
        latest_ts_by_type=latest_ts_by_type,
    )


def parse_sessions(lines: Iterable[str]) -> list[BotSession]:
    """Parse bot.log lines into BotSession records.

    Recognises two boot-marker formats:
    1. Legacy [BOOT] marker (has timestamp on the same line):
           2026-05-09 12:00:00,000 UTC INFO ... [BOOT] version=X pid=Y ...
    2. Sigil banner (no timestamp on its own line; timestamp from the next
       timestamped log line):
           # ===== v0.29.59 | env=prod | model=qwen2.5:7b | py=3.14.4 =====
           2026-05-11 00:00:52,338 UTC INFO ...
    """
    sessions: list[BotSession] = []
    current: BotSession | None = None
    # When a sigil banner is seen we stash its version and wait for the next
    # timestamped line to provide the boot timestamp.
    pending_sigil_version: str | None = None

    for line in lines:
        # Check for sigil banner first (no timestamp on this line).
        sigil_match = SIGIL_RE.match(line)
        if sigil_match:
            pending_sigil_version = sigil_match.group("version")
            continue

        ts = parse_log_ts(line)
        if ts is None:
            continue

        # If a sigil banner is pending, this first timestamped line provides
        # the boot timestamp.
        if pending_sigil_version is not None:
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
                version=pending_sigil_version,
                pid=None,
            )
            pending_sigil_version = None
            # Still process this line for shutdown markers below.

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

    Path-form tolerance: launchd may report the python or main argument in
    relative form (e.g. 'main.py' instead of '/abs/path/main.py') depending on
    how the plist was authored.  We match structurally:
      - prefix '/usr/bin/caffeinate -dimsu ' is invariant
      - python basename appears somewhere in the command args
      - main basename is the last space-separated token
    This is equivalent to the shell pgrep -f match for any path-form variant.
    """
    prefix = "/usr/bin/caffeinate -dimsu "
    python_basename = Path(python_path).name
    main_basename = Path(main_path).name
    result = []
    for p in rows:
        if not p.command.startswith(prefix):
            continue
        args = p.command[len(prefix):]
        tokens = args.split()
        if len(tokens) < 2:
            continue
        # python token: any token whose basename matches
        # main token: last token whose basename matches
        if (
            any(Path(t).name == python_basename for t in tokens[:-1])
            and Path(tokens[-1]).name == main_basename
        ):
            result.append(p)
    return result


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
        print("Result     : no boot markers found ([BOOT] or sigil banner)")
        print()
        return
    last = sessions[-1]
    print(f"Boot UTC   : {last.boot_ts.isoformat()}")
    print(f"Version    : {last.version}")
    print(f"Log PID    : {last.pid if last.pid is not None else 'n/a'}")
    print(f"Age        : {human_duration((now - last.boot_ts).total_seconds())}")
    print()


def print_signal_flow_section(stats: SignalFlowStats, *, now: datetime) -> None:
    print("=== Signal-flow heartbeat (structured trade log) ===")
    print(f"Trade log  : {stats.path}")
    print(f"Window UTC : since {stats.since.isoformat()}")
    print(f"Records    : {stats.records_kept} kept / {stats.lines_total} scanned")
    if stats.lines_malformed:
        print(f"Malformed  : {stats.lines_malformed}")
    if not stats.counts:
        print("Result     : no signal-flow records found in window")
        print()
        return

    for event_type in SIGNAL_FLOW_EVENTS:
        count = stats.counts.get(event_type, 0)
        latest = stats.latest_ts_by_type.get(event_type)
        age = human_duration((now - latest).total_seconds()) if latest is not None else "n/a"
        latest_text = latest.isoformat() if latest is not None else "n/a"
        print(f"{event_type:<17}: {count:>5} latest={latest_text} age={age}")
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


def _default_repo_root() -> Path:
    """Resolve the repo root for botcheck without a hardcoded user/repo path.

    Order: $KALSHI_HOME, then ~/vscode/kalshi-bot (Mac Studio), then
    ~/vscode/kalshi_bot (legacy MacBook), then ~/vscode/kalshi-bot as a final
    default if neither directory exists.
    """
    if (env := os.environ.get("KALSHI_HOME")):
        return Path(env)
    home = Path.home()
    for name in ("kalshi-bot", "kalshi_bot"):
        candidate = home / "vscode" / name
        if candidate.exists():
            return candidate
    return home / "vscode" / "kalshi-bot"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_home = _default_repo_root()
    parser.add_argument("--home", type=Path, default=default_home)
    parser.add_argument("--label", default=os.environ.get("KALSHI_LAUNCHD_LABEL", "com.jake.kalshi-bot"))
    parser.add_argument("--python", type=Path, default=Path(os.environ.get("KALSHI_PYTHON", default_home / ".venv/bin/python")))
    parser.add_argument("--main", type=Path, default=Path(os.environ.get("KALSHI_MAIN", default_home / "main.py")))
    parser.add_argument("--log", type=Path, default=Path(os.environ.get("KALSHI_APP_LOG", default_home / "logs/app/bot.log")))
    parser.add_argument("--trades-log", type=Path, default=default_home / "logs/trades")
    parser.add_argument("--signal-window-hours", type=float, default=24.0)
    args = parser.parse_args()

    now_epoch = time.time()
    now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)

    launchd_output = launchd_print(args.label)
    wrapper_pid = launchd_pid(launchd_output)

    rows = process_table()
    bots = _bot_pids(rows, launchd_pid=wrapper_pid, python_path=args.python, main_path=args.main)
    caffeinates = _caffeinate_pids(rows, python_path=args.python, main_path=args.main)

    sessions = read_sessions(args.log)
    signal_flow = summarize_signal_flow(
        args.trades_log,
        now=now,
        window_hours=args.signal_window_hours,
    )

    print_launchd(args.label, launchd_output, wrapper_pid)
    current_proc = print_bot_section(bots, now_epoch=now_epoch, now=now)
    print_caffeinate_section(caffeinates, now_epoch=now_epoch)
    print_last_boot(args.log, sessions, now)
    print_signal_flow_section(signal_flow, now=now)
    print_history(sessions=sessions, current_proc=current_proc, now=now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
