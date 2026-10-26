"""Politics-only Grok YES/NO veto immediately before a live Kalshi POST.

Official-p still prices the book. This call only asks whether that official
packet supports the side at the snapshot price. Missing key, timeout, and
parse failure fail closed (no POST). Never falls back to the local 7b.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import requests

from utils.event_news_grok_flags import event_news_grok_trace_enabled
from utils.event_news_research import (
    event_news_executed_side_skip_reason,
    snapshot_ask_cents,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_veto")

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_API_KEY_ENV = "XAI_API_KEY"
GROK_MODEL = "grok-4.20-0309-non-reasoning"
GROK_TIMEOUT_SECONDS = 45.0
GROK_MAX_TOKENS = 256
GROK_CALL_LOG_PATH = Path("logs/state/grok_edge_calls.jsonl")
VETO_DECISION_LOG_PATH = Path("logs/state/grok_veto_decisions.jsonl")
_COST_TICKS_PER_USD = Decimal("10000000000")
_TOOL_USAGE_KEYS = (
    "web_search_calls",
    "x_search_calls",
    "x_posts_fetched",
    "x_users_fetched",
)
_YES_NO_SYSTEM = (
    "Answer only YES or NO. YES means the official packet supports buying "
    "this side at this snapshot price. NO means it does not."
)


@dataclass(frozen=True)
class EventNewsGrokVetoPacket:
    ticker: str
    side: str
    yes_ask_cents: int | None
    no_ask_cents: int | None
    official_p: float | None
    metric_name: str
    evidence_urls: tuple[str, ...]
    fee_net_edge: float | None
    proposed_contracts: int
    title: str = ""
    rules: str = ""
    strike: str = ""
    close_time: str = ""
    source_text: str = ""

    def as_prompt_dict(
        self,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        del environ
        return {
            "ticker": self.ticker,
            "side": self.side,
            "yes_ask_cents": self.yes_ask_cents,
            "no_ask_cents": self.no_ask_cents,
            "official_p": self.official_p,
            "metric_name": self.metric_name,
            "evidence_urls": list(self.evidence_urls),
            "fee_net_edge": self.fee_net_edge,
            "proposed_contracts": self.proposed_contracts,
        }


def _signal_meta(analysis: Any) -> dict[str, Any]:
    raw = getattr(analysis, "signal_meta", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return None


def _collect_evidence_urls(analysis: Any, meta: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        url = _http_url(value)
        if url and url not in seen:
            seen.add(url)
            found.append(url)

    news = getattr(analysis, "news_item", None)
    for attr in ("url", "source_url", "link"):
        _add(getattr(news, attr, None))
    for key in (
        "evidence_urls",
        "settlement_source_urls",
        "source_urls",
        "official_source_urls",
    ):
        raw = meta.get(key)
        if isinstance(raw, (list, tuple)):
            for item in raw:
                _add(item)
        else:
            _add(raw)
    _add(meta.get("source_url"))
    _add(meta.get("trigger_evidence_url"))
    evidence = getattr(analysis, "evidence", None) or meta.get("evidence")
    if isinstance(evidence, (list, tuple)):
        for item in evidence:
            _add(getattr(item, "source_url", None) if not isinstance(item, dict) else item.get("source_url"))
            _add(getattr(item, "url", None) if not isinstance(item, dict) else item.get("url"))
    return tuple(found)


def event_news_grok_veto_packet(
    analysis: Any,
    *,
    contracts: int,
) -> EventNewsGrokVetoPacket:
    market = getattr(analysis, "market", None)
    yes_ask, no_ask = snapshot_ask_cents(market)
    meta = _signal_meta(analysis)
    metric = str(
        meta.get("metric_name")
        or getattr(analysis, "metric_name", "")
        or getattr(getattr(analysis, "news_item", None), "metric_name", "")
        or ""
    ).strip()
    return EventNewsGrokVetoPacket(
        ticker=str(getattr(market, "ticker", "") or ""),
        side=str(getattr(analysis, "side", "") or "").strip().lower(),
        yes_ask_cents=yes_ask,
        no_ask_cents=no_ask,
        official_p=_as_float(getattr(analysis, "estimated_probability", None)),
        metric_name=metric,
        evidence_urls=_collect_evidence_urls(analysis, meta),
        fee_net_edge=_as_float(getattr(analysis, "edge", None)),
        proposed_contracts=int(contracts),
        title=str(getattr(market, "title", "") or ""),
        rules=str(
            getattr(market, "rules_primary", "")
            or getattr(market, "rules", "")
            or ""
        ),
        strike=str(
            getattr(market, "strike", "")
            or getattr(market, "yes_sub_title", "")
            or ""
        ),
        close_time=str(getattr(market, "close_time", "") or ""),
        source_text=str(meta.get("source_text") or ""),
    )


def _parse_yes_no(content: str) -> str:
    """Return yes/no from the last YES or NO token; anything else is timeout."""
    last = None
    for raw in str(content or "").split():
        token = raw.strip(".,:;!?\"'").upper()
        if token == "YES":
            last = "yes"
        elif token == "NO":
            last = "no"
    return last if last is not None else "timeout"


def _last_json_object(content: str) -> dict[str, Any] | None:
    """Keep the last valid object. A greedy brace match breaks on preambles."""
    decoder = json.JSONDecoder()
    text = str(content or "")
    last: dict[str, Any] | None = None
    start = 0
    while True:
        index = text.find("{", start)
        if index < 0:
            return last
        try:
            obj, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(obj, dict):
            last = obj
        start = index + 1


# A one-word strike such as "Golf" can appear in a real source quote.
_ECHO_PART_MIN_CHARS = 20


def _collapsed(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _quoted_evidence_text(value: Any) -> str:
    """Return quote text only when the model sent a real sentence.

    A list, a number, or a zero-width string is not a quote. ``str([""])``
    is non-empty, and that must not open the live POST.
    """
    if not isinstance(value, str):
        return ""
    visible = "".join(
        ch for ch in value if unicodedata.category(ch) != "Cf"
    )
    return " ".join(visible.split())


def _quote_repeats_contract(
    evidence: str,
    contract_text: str,
    contract_parts: Sequence[str] | None,
) -> bool:
    """True when the quote only repeats text we already sent.

    Case and whitespace do not make an echo into evidence. A long contract
    field inside a longer quote is still an echo. A short strike is not,
    because that word can appear in a real source sentence.
    """
    quote = _collapsed(evidence)
    if not quote:
        return False
    parts = list(contract_parts) if contract_parts is not None else [contract_text]
    for part in parts:
        collapsed = _collapsed(part)
        if not collapsed:
            continue
        if quote in collapsed:
            return True
        if len(collapsed) >= _ECHO_PART_MIN_CHARS and collapsed in quote:
            return True
    return False


def event_news_grok_grounded_verdict(
    content: str,
    *,
    yes_no: bool,
    allowed_urls: tuple[str, ...] | list[str] | None = None,
    contract_text: str = "",
    contract_parts: Sequence[str] | None = None,
) -> str | None:
    """Accept a verdict only when it quotes evidence from an allowed URL.

    A quote that only repeats the contract text we sent is not evidence.
    The model can echo the prompt without opening the URL.
    """
    obj = _last_json_object(content)
    if not obj:
        return None
    evidence = _quoted_evidence_text(obj.get("quoted_evidence"))
    url = obj.get("url")
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not evidence or not any(ch.isalnum() for ch in evidence) or not url:
        return None
    if _quote_repeats_contract(evidence, contract_text, contract_parts):
        return None
    allowed = tuple(allowed_urls or ())
    if url not in allowed:
        return None
    verdict = str(obj.get("verdict") or "").strip().upper()
    if yes_no:
        if verdict == "YES":
            return "yes"
        if verdict == "NO":
            return "no"
        return None
    if verdict == "CONTRADICT":
        return "contradict"
    if verdict == "OK":
        return "ok"
    return None


def _trace_source_count(payload: dict[str, Any], usage: dict[str, Any], message: dict[str, Any]) -> object:
    if "num_sources_used" in usage:
        return usage.get("num_sources_used")
    for container in (payload, usage, message):
        for key in ("citations", "sources"):
            values = container.get(key)
            if isinstance(values, list):
                return len(values)
    return ""


def event_news_grok_trace_log(
    kind: str,
    ticker: str,
    body: Any,
    packet_text: str,
    urls: list[str],
    *,
    latency_ms: int | None = None,
) -> None:
    """Log response metadata and the raw reply. Never log the API key."""
    payload = body if isinstance(body, dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    message: dict[str, Any] = {}
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        raw_message = choices[0].get("message")
        if isinstance(raw_message, dict):
            message = raw_message
    tool_calls = message.get("tool_calls")
    tool_count = len(tool_calls) if isinstance(tool_calls, list) else 0
    content = message.get("content")
    raw_reply = content if isinstance(content, str) else ""
    prompt_tokens = usage.get("prompt_tokens", "")
    completion_tokens = usage.get("completion_tokens", "")
    total_tokens = usage.get("total_tokens", "")
    if (
        total_tokens == ""
        and isinstance(prompt_tokens, int)
        and isinstance(completion_tokens, int)
    ):
        total_tokens = prompt_tokens + completion_tokens
    log.info(
        "[EVENT_NEWS_GROK_TRACE] kind=%s ticker=%s model=%s response_id=%s "
        "num_sources_used=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s "
        "latency_ms=%s tool_calls=%s packet_hash=%s urls=%s raw_reply=%s",
        kind,
        ticker,
        payload.get("model") or "",
        payload.get("id") or "",
        _trace_source_count(payload, usage, message),
        prompt_tokens,
        completion_tokens,
        total_tokens,
        "" if latency_ms is None else latency_ms,
        tool_count,
        hashlib.sha256(packet_text.encode("utf-8")).hexdigest()[:16],
        ",".join(urls)[:500],
        json.dumps(raw_reply, ensure_ascii=True),
    )


def _utc_stamp(now: datetime | None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _chat_usage(body: Any) -> dict[str, Any]:
    """Map chat prompt/completion tokens onto the paper call-log keys."""
    meta: dict[str, Any] = {}
    if not isinstance(body, dict):
        return meta
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return meta
    for source, dest in (
        ("prompt_tokens", "input_tokens"),
        ("input_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("output_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        if dest in meta:
            continue
        number = _usage_int(usage.get(source))
        if number is not None:
            meta[dest] = number
    if "total_tokens" not in meta and "input_tokens" in meta and "output_tokens" in meta:
        meta["total_tokens"] = meta["input_tokens"] + meta["output_tokens"]
    details = usage.get("server_side_tool_usage_details")
    if isinstance(details, dict):
        for key in _TOOL_USAGE_KEYS:
            number = _usage_int(details.get(key))
            if number is not None:
                meta[key] = number
    ticks = _usage_int(usage.get("cost_in_usd_ticks"))
    if ticks is not None:
        meta["cost_in_usd_ticks"] = ticks
    return meta


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def append_chat_call(
    *,
    kind: str,
    ticker: str,
    grounded: bool,
    body: Any,
    skip_reason: str | None = None,
    verdict: str = "",
    call_log_path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Append one chat call. A log failure must not change the caller result.

    This does not reserve or debit the paper spend cap. Prompt text stays out.
    """
    try:
        path = GROK_CALL_LOG_PATH if call_log_path is None else call_log_path
        meta = _chat_usage(body)
        ticks = meta.get("cost_in_usd_ticks")
        if isinstance(ticks, int):
            cost = Decimal(ticks) / _COST_TICKS_PER_USD
            cost_source = "provider"
        else:
            cost = Decimal("0")
            cost_source = "none"
        record: dict[str, Any] = {
            "ts": _utc_stamp(now),
            "kind": kind,
            "ticker": ticker,
            "prompt_hash": "",
            "grounded": bool(grounded),
            "tools": [],
            "sources": [],
            "verdict": verdict,
            "probability": None,
            "confidence": None,
            "rationale": "",
            "attempt": 1,
            "cost_usd": format(cost, "f"),
            "cost_source": cost_source,
            "skip_reason": skip_reason,
            "venue": "kalshi",
        }
        record.update(meta)
        _append_jsonl(path, record)
    except Exception:
        return


def _plain_depth(value: Any) -> int | float | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str) and value.strip():
        return value
    return None


def _side_depth(market: Any, side: str) -> int | float | str | None:
    for name in (f"{side}_ask_size_fp", f"{side}_ask_size"):
        found = _plain_depth(getattr(market, name, None))
        if found is not None:
            return found
    return None


def _decision_seen(path: Path, ticker: str, side: str, day: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("ticker") or "") == ticker
            and str(row.get("side") or "") == side
            and str(row.get("date") or "") == day
        ):
            return True
    return False


def _record_veto_decision(
    analysis: Any,
    packet: EventNewsGrokVetoPacket,
    result: str,
    *,
    now: datetime | None,
) -> None:
    """First ticker/side/UTC-date row wins. Later calls still hit Grok."""
    try:
        path = VETO_DECISION_LOG_PATH
        stamp = _utc_stamp(now)
        day = stamp[:10]
        ticker = packet.ticker
        side = packet.side
        if _decision_seen(path, ticker, side, day):
            return
        ask = packet.yes_ask_cents if side == "yes" else packet.no_ask_cents
        if type(ask) is not int:
            ask = None
        _append_jsonl(
            path,
            {
                "ts": stamp,
                "date": day,
                "ticker": ticker,
                "side": side,
                "ask_cents": ask,
                "depth": _side_depth(getattr(analysis, "market", None), side),
                "decision": (
                    "allow" if result == "yes" else "block" if result == "no" else "unavailable"
                ),
                "fee_net_edge": packet.fee_net_edge,
                "contracts": packet.proposed_contracts,
            },
        )
    except Exception:
        return


def event_news_grok_veto_decision(
    packet: EventNewsGrokVetoPacket,
    *,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    timeout_seconds: float = GROK_TIMEOUT_SECONDS,
    grounded: bool | None = None,
) -> str:
    """Return yes, no, or timeout. Never logs the API key.

    Chat completions stay ungrounded. ``grounded`` and
    EVENT_NEWS_GROK_GROUNDING do not add search_parameters. A transport
    error, HTTP non-200, or timeout stays timeout, which is not model no.
    """
    del grounded
    started = time.monotonic()

    def _elapsed_ms() -> int:
        return int(round((time.monotonic() - started) * 1000))

    env = os.environ if environ is None else environ
    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    if not key:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=missing_key",
            packet.ticker,
            _elapsed_ms(),
        )
        return "timeout"

    def _logged(body: Any, skip: str | None, verdict: str) -> None:
        append_chat_call(
            kind="veto",
            ticker=packet.ticker,
            grounded=False,
            body=body,
            skip_reason=skip,
            verdict=verdict,
        )

    user_content = json.dumps(
        packet.as_prompt_dict(environ=env),
        separators=(",", ":"),
    )
    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "max_tokens": GROK_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _YES_NO_SYSTEM},
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
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=timeout",
            packet.ticker,
            _elapsed_ms(),
        )
        _logged(None, "timeout", "timeout")
        return "timeout"
    except Exception:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=request_error",
            packet.ticker,
            _elapsed_ms(),
        )
        _logged(None, "request_error", "timeout")
        return "timeout"

    status = getattr(response, "status_code", None)
    if status != 200:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=http_%s",
            packet.ticker,
            _elapsed_ms(),
            status,
        )
        _logged(None, "http", "timeout")
        return "timeout"
    body: Any = None
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except Exception:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=parse_fail",
            packet.ticker,
            _elapsed_ms(),
        )
        _logged(body, "parse_fail", "timeout")
        return "timeout"
    if event_news_grok_trace_enabled(env):
        event_news_grok_trace_log(
            "veto",
            packet.ticker,
            body,
            user_content,
            list(packet.evidence_urls),
            latency_ms=_elapsed_ms(),
        )
    result = _parse_yes_no(str(content))
    log.info(
        "[EVENT_NEWS_GROK_VETO] result=%s ticker=%s contracts=%s elapsed_ms=%s",
        result,
        packet.ticker,
        packet.proposed_contracts,
        _elapsed_ms(),
    )
    _logged(body, "parse_fail" if result == "timeout" else None, result)
    return result


def event_news_paper_grounding_skip(
    analysis: Any,
    *,
    contracts: int,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> bool:
    """Chat grounding is gone. This does not call xAI and does not skip."""
    del analysis, contracts, environ, post
    return False


def event_news_grok_veto_allows_live_post(
    analysis: Any,
    *,
    contracts: int,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """True only on an explicit Grok YES. Politics live POST gate."""
    market = getattr(analysis, "market", None)
    side = str(getattr(analysis, "side", "") or "")
    if event_news_executed_side_skip_reason(market, side) is not None:
        return False
    meta = _signal_meta(analysis)
    skip = str(meta.get("skip_reason") or meta.get("research_skip_reason") or "")
    if skip == "no_edge":
        return False
    official_p = str(meta.get("blend_bypass_reason") or "") == "official_p" or str(
        meta.get("source_lane") or ""
    ) == "research_official_p"
    if not official_p:
        return False
    edge = _as_float(getattr(analysis, "edge", None))
    if edge is not None and edge <= 0:
        return False
    packet = event_news_grok_veto_packet(analysis, contracts=contracts)
    result = event_news_grok_veto_decision(
        packet,
        environ=environ,
        post=post,
    )
    env = os.environ if environ is None else environ
    if str(env.get(XAI_API_KEY_ENV) or "").strip():
        _record_veto_decision(analysis, packet, result, now=now)
    return result == "yes"
