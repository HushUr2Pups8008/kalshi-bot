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

from utils.event_news_grok_flags import (
    event_news_grok_trace_enabled,
    event_news_market_status_settled,
    event_news_watch_skip_settled_enabled,
)
from utils.event_news_grok_veto import (
    GROK_MAX_TOKENS,
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
    XAI_API_KEY_ENV,
    XAI_CHAT_URL,
    append_chat_call,
    event_news_grok_trace_log,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_watch")

_WATCH_SYSTEM = (
    "Answer only CONTRADICT or OK. CONTRADICT means the official settlement "
    "source contradicts this open side. OK means it does not."
)
_WATCH_USER = "Does the official source contradict this side?"
_LONGSHOT_FILL = 0.35


class EventNewsGrokWatchResult:
    def __init__(
        self,
        result: str,
        reason: str,
        elapsed_ms: int,
        reply: str = "",
    ) -> None:
        self.result = result
        self.reason = reason
        self.elapsed_ms = elapsed_ms
        self.reply = reply


def event_news_grok_watch_decision(
    packet: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    grounded: bool | None = None,
) -> EventNewsGrokWatchResult:
    """Return contradict, ok, or timeout. Never submits an order.

    Chat completions stay ungrounded. ``grounded`` and
    EVENT_NEWS_GROK_GROUNDING do not add search_parameters.
    """
    del grounded
    started = time.monotonic()
    ticker = str(packet.get("ticker") or "")
    side = str(packet.get("side") or "")
    env = os.environ if environ is None else environ
    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    if not key:
        result = EventNewsGrokWatchResult("timeout", "missing_key", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return result

    def _logged(result: EventNewsGrokWatchResult, body: Any = None) -> EventNewsGrokWatchResult:
        skip = None if result.result in {"contradict", "ok"} else (result.reason or "timeout")
        append_chat_call(
            kind="watch",
            ticker=ticker,
            grounded=False,
            body=body,
            skip_reason=skip,
            verdict=result.result,
        )
        return result

    user_content = (
        _WATCH_USER
        + "\n"
        + json.dumps(dict(packet), separators=(",", ":"), default=str)
    )
    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "max_tokens": GROK_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _WATCH_SYSTEM},
            {"role": "user", "content": user_content},
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
        return _logged(result)
    except Exception:
        result = EventNewsGrokWatchResult("timeout", "request_error", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return _logged(result)
    status = getattr(response, "status_code", None)
    if status != 200:
        result = EventNewsGrokWatchResult("timeout", f"http_{status}", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return _logged(result)
    body: Any = None
    try:
        body = response.json()
        content = str(body["choices"][0]["message"]["content"])
    except Exception:
        result = EventNewsGrokWatchResult("timeout", "parse_fail", _elapsed_ms(started))
        _log_watch(result, ticker, side)
        return _logged(result, body)
    urls = _watch_urls(packet)
    if event_news_grok_trace_enabled(env):
        event_news_grok_trace_log(
            "watch",
            ticker,
            body,
            user_content,
            urls,
            latency_ms=_elapsed_ms(started),
        )
    parsed = _parse_contradict_ok(content)
    if parsed is None:
        result = EventNewsGrokWatchResult(
            "timeout",
            "parse_fail",
            _elapsed_ms(started),
            reply=content,
        )
        _log_watch(result, ticker, side)
        return _logged(result, body)
    result = EventNewsGrokWatchResult(
        parsed,
        "",
        _elapsed_ms(started),
        reply=content,
    )
    _log_watch(result, ticker, side)
    return _logged(result, body)


def event_news_watch_series_record(
    market: Any,
    *,
    event_series_ticker: Callable[[str], str],
    series_metadata: Callable[[str], Any],
) -> Any:
    """Return the series record for a market whose own source fields are empty."""
    series_ticker = str(getattr(market, "series_ticker", "") or "").strip()
    if not series_ticker:
        event_ticker = str(getattr(market, "event_ticker", "") or "").strip()
        if not event_ticker:
            return None
        series_ticker = str(event_series_ticker(event_ticker) or "").strip()
        if not series_ticker:
            return None
    return series_metadata(series_ticker)


def run_event_news_grok_watch_once(
    positions: list[Any] | None,
    *,
    market_lookup: Callable[[str], Any],
    state_path: Path,
    series_lookup: Callable[[Any], Any] | None = None,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> list[EventNewsGrokWatchResult]:
    """Check each open row at most once per UTC day. Read-only besides xAI."""
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
        if _state_day(state.get(ticker)) == today:
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
        if event_news_watch_skip_settled_enabled(environ):
            if event_news_market_status_settled(getattr(market, "status", "")):
                result = EventNewsGrokWatchResult("skipped", "settled", 0)
                _log_watch(result, ticker, side)
                results.append(result)
                continue
        urls, terms, lookup_error = _sources_for_packet(market, series_lookup)
        if not urls:
            reason = "series_lookup" if lookup_error else "no_sources"
            result_name = "timeout" if lookup_error else "skipped"
            result = EventNewsGrokWatchResult(result_name, reason, 0)
            _log_watch(result, ticker, side)
            results.append(result)
            continue
        packet = {
            "ticker": ticker,
            "side": side,
            "fill_price": fill,
            "settlement_sources": urls,
            "contract_terms_url": terms,
        }
        result = event_news_grok_watch_decision(packet, environ=environ, post=post)
        results.append(result)
        if result.reason != "missing_key":
            state[ticker] = {
                "date": today,
                "result": result.result,
                "reply": result.reply,
            }
            changed = True
    if changed:
        _save_state(state_path, state)
    return results


def _watch_urls(packet: Mapping[str, Any]) -> list[str]:
    raw_urls = packet.get("settlement_sources")
    urls = [str(item) for item in raw_urls] if isinstance(raw_urls, list) else []
    terms = str(packet.get("contract_terms_url") or "")
    if terms:
        urls.append(terms)
    return urls


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


def _sources_for_packet(
    market: Any,
    series_lookup: Callable[[Any], Any] | None,
) -> tuple[list[str], str, str]:
    """Fill empty market source fields from the series. Do not replace real ones."""
    urls = _settlement_urls(market)
    terms = str(getattr(market, "contract_terms_url", "") or "").strip()
    if urls and terms:
        return urls, terms, ""
    if series_lookup is None:
        return urls, terms, ""
    try:
        series = series_lookup(market)
    except Exception:
        return urls, terms, "series_lookup"
    if series is None:
        return urls, terms, ""
    if not urls:
        urls = _settlement_urls(series)
    if not terms:
        terms = str(getattr(series, "contract_terms_url", "") or "").strip()
    return urls, terms, ""


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


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    loaded: dict[str, Any] = {}
    for ticker, value in payload.items():
        key = str(ticker).strip()
        if not key:
            continue
        if isinstance(value, str) and value.strip():
            loaded[key] = value.strip()
        elif isinstance(value, dict) and str(value.get("date") or "").strip():
            loaded[key] = {
                "date": str(value.get("date") or "").strip(),
                "result": str(value.get("result") or ""),
                "reply": str(value.get("reply") or ""),
            }
    return loaded


def _state_day(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("date") or "")
    return ""


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(dict(sorted(state.items())), indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


def _log_reply(reply: str) -> str:
    text = " ".join(str(reply or "").split())
    if len(text) > 500:
        return text[:500]
    return text


def _log_watch(result: EventNewsGrokWatchResult, ticker: str, side: str) -> None:
    reply = _log_reply(result.reply)
    if result.reason and reply:
        log.info(
            "[EVENT_NEWS_GROK_WATCH] result=%s ticker=%s side=%s elapsed_ms=%s "
            "reason=%s reply=%s",
            result.result,
            ticker,
            side,
            result.elapsed_ms,
            result.reason,
            reply,
        )
        return
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
    if reply:
        log.info(
            "[EVENT_NEWS_GROK_WATCH] result=%s ticker=%s side=%s elapsed_ms=%s reply=%s",
            result.result,
            ticker,
            side,
            result.elapsed_ms,
            reply,
        )
        return
    log.info(
        "[EVENT_NEWS_GROK_WATCH] result=%s ticker=%s side=%s elapsed_ms=%s",
        result.result,
        ticker,
        side,
        result.elapsed_ms,
    )
