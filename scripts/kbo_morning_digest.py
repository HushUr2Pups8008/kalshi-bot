"""Fixture-only KBO morning digest collector.

Phase 1 intentionally accepts pre-collected report lines and reduces them into a
bounded structured payload. It does not execute ``scripts.daily_review`` or touch
live runtime state; later operator-facing synthesis can consume the returned
plain dictionaries.
"""

from __future__ import annotations

import re
from typing import Iterable, Any

MAX_SECTIONS = 8
MAX_DETAIL_ITEMS = 6
MAX_RAW_LINE_CHARS = 160
MAX_BULLET_CHARS = 180

_SECTION_RE = re.compile(r"^\s*(\d+\.\s+[^\[]+?)(?:\s+\[.*)?\s*$")


def _truncate(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def _section_title(line: str) -> str | None:
    match = _SECTION_RE.match(line)
    if match is None:
        return None
    return _truncate(match.group(1), MAX_RAW_LINE_CHARS)


def collect_morning_digest(
    *,
    source_lines: Iterable[str],
    generated_at: str,
    source_label: str,
    max_sections: int = MAX_SECTIONS,
    max_items_per_section: int = MAX_DETAIL_ITEMS,
) -> dict[str, Any]:
    """Collect a bounded digest from fixture/mock report lines.

    ``source_lines`` is required on purpose: this function is a static reducer,
    not a runtime collector. Callers that need live data must be implemented in a
    later approved phase behind separate safety gates.
    """

    section_limit = max(0, max_sections)
    item_limit = max(0, max_items_per_section)
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    truncated = False

    for raw_line in source_lines:
        line = str(raw_line).rstrip("\n")
        title = _section_title(line)
        if title is not None:
            if len(sections) >= section_limit:
                truncated = True
                current = None
                continue
            current = {"title": title, "items": []}
            sections.append(current)
            continue

        if current is None:
            continue

        item = line.strip()
        if not item:
            continue
        if len(current["items"]) >= item_limit:
            truncated = True
            continue
        current["items"].append(_truncate(item, MAX_RAW_LINE_CHARS))

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_label": source_label,
        "sections": sections,
        "truncated": truncated,
    }


def summarize_morning_digest(
    digest: dict[str, Any],
    *,
    max_bullets: int = 5,
    items_per_bullet: int = 2,
) -> dict[str, Any]:
    """Return a compact structured summary for operator/KBO synthesis."""

    bullets: list[str] = []
    for section in digest.get("sections", [])[: max(0, max_bullets)]:
        title = str(section.get("title") or "Untitled")
        items = [str(item) for item in section.get("items", [])[: max(0, items_per_bullet)]]
        if items:
            bullet = f"{title}: {'; '.join(items)}"
        else:
            bullet = title
        bullets.append(_truncate(bullet, MAX_BULLET_CHARS))

    return {
        "schema_version": 1,
        "generated_at": digest.get("generated_at"),
        "source_label": digest.get("source_label"),
        "section_count": len(digest.get("sections", [])),
        "truncated": bool(digest.get("truncated")),
        "bullets": bullets,
    }
