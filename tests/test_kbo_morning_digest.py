from __future__ import annotations

import sys

from scripts.kbo_morning_digest import (
    MAX_DETAIL_ITEMS,
    MAX_RAW_LINE_CHARS,
    collect_morning_digest,
    summarize_morning_digest,
)


def test_collect_morning_digest_bounds_sections_and_truncates_raw_lines():
    source_lines = [
        "PIPELINE REVIEW",
        "1. INGESTION  [source: fixture]",
        "  Source-attributed items observed : 12",
        "  Fresh passes                     : 9",
        "  " + "x" * (MAX_RAW_LINE_CHARS + 40),
        "2. MATCHING  [source: fixture]",
        "  Low-quality flagged              : 2 (25.0%)",
        "  Pre-LLM gate would-block         : 3 (37.5%)",
    ]

    digest = collect_morning_digest(
        source_lines=source_lines,
        generated_at="2026-05-18T12:00:00Z",
        source_label="fixture:daily_review",
        max_sections=1,
        max_items_per_section=2,
    )

    assert digest["schema_version"] == 1
    assert digest["source_label"] == "fixture:daily_review"
    assert digest["generated_at"] == "2026-05-18T12:00:00Z"
    assert digest["truncated"] is True
    assert [section["title"] for section in digest["sections"]] == ["1. INGESTION"]
    assert len(digest["sections"][0]["items"]) == 2
    assert all(len(item) <= MAX_RAW_LINE_CHARS for item in digest["sections"][0]["items"])
    assert "MATCHING" not in str(digest)


def test_summarize_morning_digest_returns_compact_structured_output_not_raw_dump():
    source_lines = [
        "PIPELINE REVIEW",
        "1. INGESTION  [source: fixture]",
        "  Source-attributed items observed : 12",
        "  Fresh passes                     : 9",
        "  Dropped early stale              : 3",
        "2. MATCHING  [source: fixture]",
        "  Low-quality flagged              : 2 (25.0%)",
        "  Pre-LLM gate would-block         : 3 (37.5%)",
        "3. ANALYSIS  [source: fixture]",
        "  LLM attempted                    : 1",
        "  LLM fallback to keyword          : 0",
    ]

    digest = collect_morning_digest(
        source_lines=source_lines,
        generated_at="2026-05-18T12:00:00Z",
        source_label="fixture:daily_review",
    )
    summary = summarize_morning_digest(digest, max_bullets=3)

    assert summary == {
        "schema_version": 1,
        "generated_at": "2026-05-18T12:00:00Z",
        "source_label": "fixture:daily_review",
        "section_count": 3,
        "truncated": False,
        "bullets": [
            "1. INGESTION: Source-attributed items observed : 12; Fresh passes                     : 9",
            "2. MATCHING: Low-quality flagged              : 2 (25.0%); Pre-LLM gate would-block         : 3 (37.5%)",
            "3. ANALYSIS: LLM attempted                    : 1; LLM fallback to keyword          : 0",
        ],
    }
    assert "PIPELINE REVIEW" not in str(summary)
    assert all(len(bullet) <= 180 for bullet in summary["bullets"])


def test_collect_morning_digest_requires_fixture_input_and_does_not_import_daily_review():
    sys.modules.pop("scripts.daily_review", None)

    digest = collect_morning_digest(
        source_lines=["1. INGESTION", "  Records included: 0"],
        generated_at="2026-05-18T12:00:00Z",
        source_label="fixture:unit",
    )

    assert digest["sections"] == [
        {"title": "1. INGESTION", "items": ["Records included: 0"]}
    ]
    assert "scripts.daily_review" not in sys.modules


def test_default_bounds_keep_digest_small_for_operator_handoff():
    source_lines = []
    for index in range(20):
        source_lines.append(f"{index + 1}. SECTION {index}")
        source_lines.extend(f"  item {index}-{item}" for item in range(20))

    digest = collect_morning_digest(
        source_lines=source_lines,
        generated_at="2026-05-18T12:00:00Z",
        source_label="fixture:large",
    )

    assert len(digest["sections"]) <= 8
    assert all(len(section["items"]) <= MAX_DETAIL_ITEMS for section in digest["sections"])
    assert digest["truncated"] is True
