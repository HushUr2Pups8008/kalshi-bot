"""Politics session-halt state.

The latch keeps a tripped halt across a restart so a new process cannot
seed a fresh baseline and trade. The only reset is deleting the latch
file. A new process does not clear it. A new UTC date resets the cash
file's start cents and does not clear the latch.

The cash file is integer cents for the health check. Tests monkeypatch
the path constants.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HALT_LATCH_PATH = Path("logs/state/politics_session_halt.json")
OPERATOR_ALERT_PATH = Path("logs/state/politics_operator_alert.json")
CASH_CENTS_PATH = Path("logs/state/politics_cash_cents.json")
SESSION_HALT_DOLLAR_CAP = 10.0


def session_halt_loss_line(seed_equity: float, loss_limit_pct: float) -> float:
    """min(percent of the seeded session equity, $10)."""
    return min(float(loss_limit_pct) * float(seed_equity), SESSION_HALT_DOLLAR_CAP)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def halt_latch_present(path: Path | None = None) -> bool:
    data = _read_json(path or HALT_LATCH_PATH)
    return bool(data and data.get("halted") is True)


def write_halt_latch(
    *,
    seed_equity: float,
    loss: float,
    line: float,
    path: Path | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_json(
        path or HALT_LATCH_PATH,
        {
            "halted": True,
            "line": line,
            "loss": loss,
            "seed_equity": seed_equity,
            "ts": now,
        },
    )


def write_operator_alert(reason: str, path: Path | None = None) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_json(
        path or OPERATOR_ALERT_PATH,
        {"reason": str(reason), "ts": now},
    )


def record_cash_cents(
    dollars: float,
    path: Path | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Store the UTC-day cash integer. A new UTC date resets the start."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    cents = int(round(float(dollars) * 100))
    target = path or CASH_CENTS_PATH
    today = moment.date().isoformat()
    existing = _read_json(target)
    start = cents
    if existing and existing.get("date") == today:
        try:
            start = int(existing["start_cents"])
        except (KeyError, TypeError, ValueError):
            start = cents
    _write_json(
        target,
        {
            "date": today,
            "last_cents": cents,
            "start_cents": start,
            "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
