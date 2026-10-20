"""Ask Grok to read market rules before an entry.

The live flag defaults off, so the live order path does not call this
unless that flag is on. A finished check skips only when mismatch is
true. A timeout, a request error, or a missing key skips too: the order
does not proceed. Missing rules text still does not skip, because there
is nothing to check. The stored file is gitignored and does not contain
an API key or a headline.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from json import JSONDecoder
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from utils.event_news_desk_flags import (
    EVENT_NEWS_GROK_RULES_CHECK,
    event_news_desk_flag_enabled,
)
from utils.event_news_grok_veto import (
    GROK_MODEL,
    GROK_TIMEOUT_SECONDS,
    XAI_API_KEY_ENV,
    XAI_CHAT_URL,
)
from utils.logger import get_logger

log = get_logger("event_news_rules_check")

RULES_LOG_PATH = Path("logs/state/event_news_rules_check.jsonl")
MODEL_COUNT_LOGIC = (
    "The model counts Truth Social posts in the contract week from the "
    "Factbase feed. Deleted posts stay out of the live count. The week "
    "uses America/New_York. A phrase market counts phrase hits, not posts."
)
SAY_COUNT_LOGIC = (
    "The model counts phrase hits in the contract week from the Factbase "
    "feed. It does not count Truth Social posts and it does not count "
    "presidential actions. The week uses America/New_York."
)
ACT_COUNT_LOGIC = (
    "The model counts presidential actions on whitehouse.gov in the "
    "contract window. It does not count Truth Social posts and it does "
    "not count phrase hits."
)
_RAW_REPLY_LIMIT = 8000
_SYSTEM = (
    "Reply with one JSON object only. Keys are what_counts, source, "
    "cutoff_time, trap, and mismatch. mismatch is true only when the "
    "rules disagree with the model count logic. Otherwise mismatch is false."
)


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return None


def _rules_text(market: object) -> str:
    for name in ("rules_primary", "rules"):
        value = getattr(market, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _source_url(market: object) -> str | None:
    for name in ("contract_terms_url", "rules_url", "settlement_source_url"):
        found = _http_url(getattr(market, name, None))
        if found:
            return found
    sources = getattr(market, "settlement_sources", None)
    if isinstance(sources, (list, tuple)):
        for item in sources:
            found = _http_url(getattr(item, "url", None) if not isinstance(item, str) else item)
            if found:
                return found
    return None


def _default_fetch(url: str) -> str:
    from analysis.research_gate import _fetch_direct_source

    evidence = _fetch_direct_source(
        url,
        "official",
        "contract_terms",
        allow_official_pdf_and_homepage=True,
    )
    if evidence is None:
        return ""
    snippet = getattr(evidence, "snippet", None)
    return snippet.strip() if isinstance(snippet, str) else ""


def _last_object(text: str) -> dict[str, Any] | None:
    decoder = JSONDecoder()
    found: dict[str, Any] | None = None
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = obj
    return found


def _count_logic(ticker: str) -> str | None:
    """Series rubric. Unknown series must not receive the Truth Social text."""
    series = str(ticker or "").split("-", 1)[0].upper()
    if series == "KXTRUTHSOCIAL":
        return MODEL_COUNT_LOGIC
    if series == "KXTRUMPSAY":
        return SAY_COUNT_LOGIC
    if series == "KXTRUMPACT":
        return ACT_COUNT_LOGIC
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _usage_cost(body: object) -> tuple[float | None, int | None, int | None]:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return None, None, None
    cost = None
    for name in ("cost", "cost_usd", "total_cost"):
        cost = _number(usage.get(name))
        if cost is not None:
            break
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    prompt_tokens = prompt if type(prompt) is int else None
    completion_tokens = completion if type(completion) is int else None
    return cost, prompt_tokens, completion_tokens


def _journal_fields(
    *,
    raw_reply: str,
    elapsed_ms: int,
    cost: float | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "raw_reply": raw_reply[:_RAW_REPLY_LIMIT],
        "elapsed_ms": elapsed_ms,
        "cost": cost,
    }
    if prompt_tokens is not None:
        fields["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        fields["completion_tokens"] = completion_tokens
    return fields


def _store(record: dict[str, object], path: Path | None) -> None:
    target = path or RULES_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def event_news_rules_check_skip(
    analysis: object,
    *,
    live: bool,
    environ: Mapping[str, str] | None = None,
    post: Callable[..., Any] | None = None,
    fetch: Callable[[str], str] | None = None,
    log_path: Path | None = None,
) -> bool:
    """True when the order must not proceed.

    A finished answer skips only when mismatch is true. An error, a
    timeout, or a missing key also skips. A finished answer whose
    mismatch is not true does not skip.
    """
    if not event_news_desk_flag_enabled(
        EVENT_NEWS_GROK_RULES_CHECK,
        live=live,
        environ=environ,
    ):
        return False
    market = getattr(analysis, "market", None)
    ticker = str(getattr(market, "ticker", "") or "")
    count_logic = _count_logic(ticker)
    if count_logic is None:
        return False
    rules = _rules_text(market)
    url = _source_url(market)
    source_text = ""
    if url:
        try:
            source_text = (fetch or _default_fetch)(url)
        except Exception:
            log.info("[EVENT_NEWS_RULES] source_fetch_failed ticker=%s", getattr(market, "ticker", ""))
            source_text = ""
        if not isinstance(source_text, str):
            source_text = ""
    if not rules and not source_text.strip():
        return False
    import os

    env = os.environ if environ is None else environ
    key = str(env.get(XAI_API_KEY_ENV) or "").strip()
    side = str(getattr(analysis, "side", "") or "")
    if not key:
        log.info("[EVENT_NEWS_RULES] result=timeout ticker=%s reason=missing_key", ticker)
        return True
    user = json.dumps(
        {
            "ticker": ticker,
            "model_count_logic": count_logic,
            "rules": rules[:4000],
            "source_url": url or "",
            "source_text": source_text[:4000],
        },
        ensure_ascii=True,
    )
    payload = {
        "model": GROK_MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    sender = post if post is not None else requests.post
    started = time.monotonic()
    try:
        response = sender(
            XAI_CHAT_URL,
            json=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=GROK_TIMEOUT_SECONDS,
        )
        body = response.json()
        content = str(body["choices"][0]["message"]["content"])
        response_id = body.get("id") if isinstance(body.get("id"), str) else ""
        cost, prompt_tokens, completion_tokens = _usage_cost(body)
    except Exception:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info("[EVENT_NEWS_RULES] result=timeout ticker=%s", ticker)
        try:
            _store(
                {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ticker": ticker,
                    "side": side,
                    "response_id": "",
                    "mismatch": False,
                    "what_counts": "",
                    "source": "",
                    "cutoff_time": "",
                    "trap": "",
                    "outcome": None,
                    **_journal_fields(
                        raw_reply="",
                        elapsed_ms=elapsed_ms,
                        cost=None,
                        prompt_tokens=None,
                        completion_tokens=None,
                    ),
                },
                log_path,
            )
        except Exception:
            log.warning("[EVENT_NEWS_RULES] journal_failed ticker=%s", ticker)
        return True
    elapsed_ms = int((time.monotonic() - started) * 1000)
    answer = _last_object(content)
    mismatch = bool(answer and answer.get("mismatch") is True)
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker": ticker,
        "side": side,
        "response_id": response_id,
        "mismatch": mismatch,
        "what_counts": "" if not answer else str(answer.get("what_counts") or ""),
        "source": "" if not answer else str(answer.get("source") or ""),
        "cutoff_time": "" if not answer else str(answer.get("cutoff_time") or ""),
        "trap": "" if not answer else str(answer.get("trap") or ""),
        "outcome": None,
        **_journal_fields(
            raw_reply=content,
            elapsed_ms=elapsed_ms,
            cost=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    }
    try:
        _store(record, log_path)
    except Exception:
        log.warning("[EVENT_NEWS_RULES] journal_failed ticker=%s", ticker)
    log.info(
        "[EVENT_NEWS_RULES] ticker=%s mismatch=%s response_id=%s",
        ticker,
        mismatch,
        response_id,
    )
    return mismatch
