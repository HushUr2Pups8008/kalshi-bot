"""Once-per-day politics watch of an open live row.

Asks whether the official settlement source contradicts the open side.
Logs EVENT_NEWS_GROK_WATCH. Does not exit, cancel, or place an order.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from utils.event_news_grok_veto import (
    GROK_MAX_TOKENS,
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
    XAI_API_KEY_ENV,
    XAI_CHAT_URL,
)
from utils.event_news_research import is_event_news_paper_cohort
from utils.logger import get_logger

log = get_logger("event_news_grok_watch")

_WATCH_SYSTEM = (
    "Answer only CONTRADICT or OK. CONTRADICT means the official settlement "
    "source contradicts this open side. OK means it does not."
)
_WATCH_USER = "Does the official source contradict this side?"
_LONGSHOT_FILL = 0.35


class EventNewsGrokWatchResult:
    def __init__(self, result: str, reason: str, elapsed_ms: int) -> None:
        self.result = result
        self.reason = reason
        self.elapsed_ms = elapsed_ms


def event_news_grok_watch_decision(
    packet: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> EventNewsGrokWatchResult:
    """Return contradict, ok, or timeout. Never submits an order."""
    started = time.monotonic()
    ticker = str(packet.get("ticker") or "")
    side = str(packet.get("side") or "")
    env = os.environ if environ is None else environ
    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    if not key:
        result = EventNewsGrokWatchResult("timeout", "missing_key", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "max_tokens": GROK_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _WATCH_SYSTEM},
            {
                "role": "user",
                "content": _WATCH_USER
                + "\n"
                + json.dumps(dict(packet), separators=(",", ":"), default=str),
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    sender = post if post is not None else requests.post
    try:
        response = sender(
            XAI_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=GROK_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        result = EventNewsGrokWatchResult("timeout", "timeout", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    except Exception:
        result = EventNewsGrokWatchResult("timeout", "request_error", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    status = getattr(response, "status_code", None)
    if status != 200:
        result = EventNewsGrokWatchResult("timeout", f"http_{status}", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    try:
        body = response.json()
        content = str(body["choices"][0]["message"]["content"])
    except Exception:
        result = EventNewsGrokWatchResult("timeout", "parse_fail", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    parsed = _parse_contradict_ok(content)
    if parsed is None:
        result = EventNewsGrokWatchResult("timeout", "parse_fail", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result
    result = EventNewsGrokWatchResult(parsed, "", _elapsed_ms(started))
    _log_watch(result, ticker, side)
    return result


def run_event_news_grok_watch_once(
    positions: list[Any] | None,
    *,
    market_lookup: Callable[[str], Any],
    state_path: Path,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> list[EventNewsGrokWatchResult]:
    """Check each open row at most once per UTC day. Read-only besides xAI."""
    if not is_event_news_paper_cohort():
        return []
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    today = clock.astimezone(timezone.utc).date().isoformat()
    state = _load_state(state_path)
    results: list[EventNewsGrokWatchResult] = []
    changed = False
    for row in positions or ():
        ticker = str(_field(row, "ticker", "market_ticker") or "").strip()
        count = _signed_count(row)
        if not ticker or count is None or count == 0.0:
            continue
        side = "no" if count < 0 else "yes"
        if state.get(ticker) == today:
            continue
        fill = _fill_price(row, count)
        if fill is None:
            results.append(EventNewsGrokWatchResult("timeout", "missing_fill", 0))
            _log_watch(results[-1], ticker, side)
            continue
        if fill <= _LONGSHOT_FILL:
            continue
        try:
            market = market_lookup(ticker)
        except Exception:
            market = None
        if market is None:
            results.append(EventNewsGrokWatchResult("timeout", "market_lookup", 0))
            _log_watch(results[-1], ticker, side)
            continue
        packet = {
            "ticker": ticker,
            "side": side,
            "fill_price": fill,
            "settlement_sources": _settlement_urls(market),
            "contract_terms_url": str(getattr(market, "contract_terms_url", "") or ""),
        }
        result = event_news_grok_watch_decision(packet, environ=environ, post=post)
        results.append(result)
        if result.reason != "missing_key":
            state[ticker] = today
            changed = True
    if changed:
        _save_state(state_path, state)
    return results


def _parse_contradict_ok(content: str) -> str | None:
    last = None
    for raw in str(content or "").split():
        token = raw.strip(".,:;!?\"'").upper()
        if token == "CONTRADICT":
            last = "contradict"
        elif token == "OK":
            last = "ok"
    return last


def _signed_count(row: Any) -> float | None:
    raw = _field(row, "position_fp", "position")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _fill_price(row: Any, count: float) -> float | None:
    raw = _field(row, "market_exposure_dollars")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        exposure = abs(float(raw))
    except (TypeError, ValueError):
        return None
    size = abs(count)
    if size == 0.0:
        return None
    return exposure / size


def _field(row: Any, *names: str) -> Any:
    if isinstance(row, dict):
        for name in names:
            if row.get(name) is not None:
                return row.get(name)
        return None
    for name in names:
        value = getattr(row, name, None)
        if value is not None:
            return value
    return None


def _settlement_urls(market: Any) -> list[str]:
    found: list[str] = []
    for source in getattr(market, "settlement_sources", ()) or ():
        if isinstance(source, str):
            url = source
        elif isinstance(source, dict):
            url = str(source.get("url") or "")
        else:
            url = str(getattr(source, "url", "") or "")
        url = url.strip()
        if url and url not in found:
            found.append(url)
    return found


def _load_state(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(ticker): str(day)
        for ticker, day in payload.items()
        if str(ticker).strip() and str(day).strip()
    }


def _save_state(path: Path, state: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(sorted(state.items())), indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _log_watch(result: EventNewsGrokWatchResult, ticker: str, side: str) -> None:
    if result.reason:
        log.info(
            "[EVENT_NEWS_GROK_WATCH] result=%s ticker=%s side=%s elapsed_ms=%s reason=%s",
            result.result,
            ticker,
            side,
            result.elapsed_ms,
            result.reason,
        )
        return
    log.info(
        "[EVENT_NEWS_GROK_WATCH] result=%s ticker=%s side=%s elapsed_ms=%s",
        result.result,
        ticker,
        side,
        result.elapsed_ms,
    )
