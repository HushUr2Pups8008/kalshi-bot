"""Append-only grounding verdicts. The file is gitignored under logs/state.

Never write an API key, a headline, or a raw model reply.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GROUNDING_LOG_PATH = Path("logs/state/event_news_grounding.jsonl")


def append_event_news_grounding_record(
    *,
    kind: str,
    ticker: str,
    side: str,
    verdict: str,
    response_id: str,
    sources: list[str],
    path: Path | None = None,
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "ticker": ticker,
        "side": side,
        "verdict": verdict,
        "response_id": response_id,
        "sources": sources,
        "outcome": None,
    }
    target = path or GROUNDING_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def response_sources(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    found: list[str] = []
    citations = body.get("citations")
    if isinstance(citations, list):
        for item in citations:
            if isinstance(item, str) and item.strip():
                found.append(item.strip())
            elif isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url.strip():
                    found.append(url.strip())
    return found


def response_id(body: Any) -> str:
    if isinstance(body, dict) and isinstance(body.get("id"), str):
        return body["id"].strip()
    return ""
