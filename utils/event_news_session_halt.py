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
import logging
import os
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from typing import Any

HALT_LATCH_PATH = Path("logs/state/politics_session_halt.json")
OPERATOR_ALERT_PATH = Path("logs/state/politics_operator_alert.json")
CASH_CENTS_PATH = Path("logs/state/politics_cash_cents.json")
SESSION_HALT_DOLLAR_CAP = 10.0
CASH_DROP_CENTS = 500

log = logging.getLogger(__name__)


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
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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


def _usable_cash_cents(dollars: Any) -> int | None:
    """Positive finite cents. None, 0, NaN, and inf are not a cash read."""
    if isinstance(dollars, bool) or not isinstance(dollars, Real):
        return None
    number = float(dollars)
    if number != number or number == float("inf") or number == float("-inf"):
        return None
    if number <= 0:
        return None
    cents = int(round(number * 100))
    if cents <= 0:
        return None
    return cents


def record_cash_cents(
    dollars: Any,
    path: Path | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Store every valid cash read. A bad read does not overwrite the file.

    ``start_cents`` is the first valid read of the UTC date. ``last_cents``
    and ``ts`` update on every later valid read, up or down. ``prev_cents``
    is the previous valid ``last_cents`` on that date. None, 0, NaN, inf,
    and a non-number log ``cash=not_measured`` and return False.
    """
    cents = _usable_cash_cents(dollars)
    if cents is None:
        log.warning("[CASH_CENTS] cash=not_measured reason=invalid_read")
        return False
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    target = path or CASH_CENTS_PATH
    today = moment.date().isoformat()
    existing = _read_json(target)
    start = cents
    prev: int | None = None
    if existing and existing.get("date") == today:
        try:
            start = int(existing["start_cents"])
        except (KeyError, TypeError, ValueError):
            start = cents
        try:
            prev = int(existing["last_cents"])
        except (KeyError, TypeError, ValueError):
            prev = None
    payload: dict[str, Any] = {
        "date": today,
        "last_cents": cents,
        "start_cents": start,
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if prev is not None:
        payload["prev_cents"] = prev
    _write_json(target, payload)
    return True
