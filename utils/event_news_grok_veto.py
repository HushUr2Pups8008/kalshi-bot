"""Politics-only Grok YES/NO veto immediately before a live Kalshi POST.

Official-p still prices the book. This call only asks whether that official
packet supports the side at the snapshot price. Missing key, timeout, and
parse failure fail closed (no POST). Never falls back to the local 7b.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

from utils.event_news_research import (
    event_news_executed_side_skip_reason,
    is_event_news_paper_cohort,
    snapshot_ask_cents,
)
from utils.logger import get_logger

log = get_logger("event_news_grok_veto")

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_API_KEY_ENV = "XAI_API_KEY"
GROK_MODEL = "grok-4.20-0309-non-reasoning"
GROK_TIMEOUT_SECONDS = 45.0
GROK_MAX_TOKENS = 256
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

    def as_prompt_dict(self) -> dict[str, Any]:
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


def event_news_grok_veto_decision(
    packet: EventNewsGrokVetoPacket,
    *,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    timeout_seconds: float = GROK_TIMEOUT_SECONDS,
) -> str:
    """Return yes, no, or timeout. Never logs the API key."""
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

    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "max_tokens": GROK_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _YES_NO_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(packet.as_prompt_dict(), separators=(",", ":")),
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
            timeout=timeout_seconds,
        )
    except requests.Timeout:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=timeout",
            packet.ticker,
            _elapsed_ms(),
        )
        return "timeout"
    except Exception:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=request_error",
            packet.ticker,
            _elapsed_ms(),
        )
        return "timeout"

    status = getattr(response, "status_code", None)
    if status != 200:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=http_%s",
            packet.ticker,
            _elapsed_ms(),
            status,
        )
        return "timeout"
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except Exception:
        log.info(
            "[EVENT_NEWS_GROK_VETO] result=timeout ticker=%s elapsed_ms=%s reason=parse_fail",
            packet.ticker,
            _elapsed_ms(),
        )
        return "timeout"
    result = _parse_yes_no(str(content))
    log.info(
        "[EVENT_NEWS_GROK_VETO] result=%s ticker=%s contracts=%s elapsed_ms=%s",
        result,
        packet.ticker,
        packet.proposed_contracts,
        _elapsed_ms(),
    )
    return result


def event_news_grok_veto_allows_live_post(
    analysis: Any,
    *,
    contracts: int,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
) -> bool:
    """True only on an explicit Grok YES. Politics live POST gate."""
    if not is_event_news_paper_cohort():
        return True
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
    return event_news_grok_veto_decision(
        packet,
        environ=environ,
        post=post,
    ) == "yes"
