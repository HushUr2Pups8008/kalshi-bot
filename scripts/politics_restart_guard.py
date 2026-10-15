#!/usr/bin/env python3
"""Cap politics launchd restarts at POLITICS_RESTART_CAP per rolling hour.

Exit 0 and print the sleep seconds when the start may proceed.
Exit 75 when the hour is already full. The start script turns 75 into
exit 0 so launchd SuccessfulExit=false leaves the job down on purpose.
A handled stop is a separate alert written by main.py.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.event_news_session_halt import write_operator_alert  # noqa: E402

WINDOW_SECONDS = 3600
DEFAULT_CAP = 5
CAP_EXIT = 75


def plan_restart(
    stamps: list[float],
    now: float,
    *,
    cap: int = DEFAULT_CAP,
) -> tuple[str, int, list[float]]:
    """Return action, sleep seconds, and the stamps to persist.

    The first start in the window sleeps 0. Later starts sleep
    min(30 * (n - 1), 180). A start past the cap is not recorded.
    """
    fresh = [
        float(stamp)
        for stamp in stamps
        if float(stamp) <= now and now - float(stamp) < WINDOW_SECONDS
    ]
    if len(fresh) >= cap:
        return "capped", 0, fresh
    attempt = len(fresh) + 1
    sleep_seconds = 0 if attempt == 1 else min(30 * (attempt - 1), 180)
    fresh.append(float(now))
    return "start", sleep_seconds, fresh


def _state_path() -> Path:
    raw = os.environ.get("POLITICS_RESTART_STATE", "").strip()
    if raw:
        return Path(raw)
    return ROOT / "logs" / "state" / "politics_restart_guard.json"


def _cap() -> int:
    raw = os.environ.get("POLITICS_RESTART_CAP", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_CAP


def _load_stamps(path: Path) -> list[float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    stamps = data.get("stamps")
    if not isinstance(stamps, list):
        return []
    loaded: list[float] = []
    for stamp in stamps:
        if isinstance(stamp, bool):
            continue
        try:
            loaded.append(float(stamp))
        except (TypeError, ValueError):
            continue
    return loaded


def _write_stamps(path: Path, stamps: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"stamps": stamps}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    path = _state_path()
    action, sleep_seconds, stamps = plan_restart(
        _load_stamps(path),
        time.time(),
        cap=_cap(),
    )
    if action == "capped":
        write_operator_alert("restart_cap")
        print("politics restart cap reached", file=sys.stderr)
        return CAP_EXIT
    _write_stamps(path, stamps)
    print(sleep_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
