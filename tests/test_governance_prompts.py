"""Prompt rendering — system prompt + per-action templates.

Snapshot tests detect accidental drift. When intentionally changing a
prompt, update the snapshot in the test file in the same commit so the
diff makes the change reviewable.
"""

from __future__ import annotations


import pytest

from governance.prompts import (
    SYSTEM_PROMPT,
    DISABLE_SOURCE_TEMPLATE,
    LLM_OUTPUT_SCHEMA,
    render_prompt,
)


def test_system_prompt_advertises_json_output_schema():
    assert "JSON" in SYSTEM_PROMPT
    assert '"action"' in SYSTEM_PROMPT
    assert '"target"' in SYSTEM_PROMPT
    assert '"reasoning"' in SYSTEM_PROMPT
    assert '"confidence"' in SYSTEM_PROMPT
    assert '"predicted_effect"' in SYSTEM_PROMPT
    for action in ("disable_source", "disable_keyword", "tune_threshold", "no_action"):
        assert action in SYSTEM_PROMPT


def test_system_prompt_carries_anchor_rate_polarity_callout():
    """PROFIT-GOV-002 fix: the prompt must spell out anchor_rate polarity so
    qwen3:14b does not invert it. Pinned via the H5 bisection (Codex commit
    83bf954) — A5_ANCHOR_ONLY was the minimum-content variant that flipped
    the high-signal NEG_A case to no_action without breaking POS shapes.

    If anchor_rate semantics are reworded, run the negative-control harness
    (scripts/simulations/governance_negative_control.py) in a clean window
    and confirm NEG_A_high_signal_source returns no_action with conf >= 0.7
    before updating these assertions.
    """
    assert "Interpreting disable_source evidence:" in SYSTEM_PROMPT
    assert "LLM anchor rate" in SYSTEM_PROMPT
    # HIGH = no edge = disable
    assert "HIGH (>=0.95)" in SYSTEM_PROMPT
    assert "NO information edge" in SYSTEM_PROMPT
    assert "DISABLE signal" in SYSTEM_PROMPT
    # LOW = informative = keep
    assert "LOW (<=0.50)" in SYSTEM_PROMPT
    assert "INFORMATIVE signal" in SYSTEM_PROMPT
    assert "KEEP this source" in SYSTEM_PROMPT


def test_llm_output_schema_lists_all_required_fields():
    required = {"action", "target", "reasoning", "confidence", "predicted_effect"}
    assert required <= set(LLM_OUTPUT_SCHEMA["required"])


def test_disable_source_template_substitutes_evidence_fields():
    rendered = DISABLE_SOURCE_TEMPLATE.format(
        target="r/Turkey",
        window_hours=168,
        ingestion_events=408,
        fresh_pass_count=7,
        match_count=0,
        anchor_rate_pct="n/a",
        active_market_count=287,
        headline_sample_block="- a\n- b\n- c",
        active_market_titles_block="- m1\n- m2",
    )
    assert "r/Turkey" in rendered
    assert "408" in rendered
    assert "n/a" in rendered
    assert "- a" in rendered


def test_render_prompt_returns_system_user_pair_for_disable_source():
    evidence = {
        "candidate_action": "disable_source",
        "target": "r/Turkey",
        "ingestion_events": 408,
        "fresh_pass_count": 7,
        "match_count": 0,
        "anchor_rate": None,
        "recent_headline_sample": ["a", "b"],
        "active_market_titles_top": ["X", "Y"],
        "active_market_count": 2,
        "active_source_count": 42,
        "window_hours": 168,
    }
    sys_p, user_p = render_prompt("disable_source", evidence)
    assert sys_p == SYSTEM_PROMPT
    assert "r/Turkey" in user_p
    assert "408" in user_p


def test_render_prompt_rejects_unknown_action():
    with pytest.raises(ValueError, match="action"):
        render_prompt("set_market_position", {})


from pathlib import Path

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def test_disable_source_prompt_matches_golden():
    evidence = {
        "candidate_action": "disable_source",
        "target": "r/Turkey",
        "ingestion_events": 408,
        "fresh_pass_count": 7,
        "match_count": 0,
        "anchor_rate": None,
        "recent_headline_sample": [
            "Turkey discussion of AKP economic policy",
            "Istanbul mayoral election analysis",
            "NATO exercises this week",
            "Lira exchange rate debate",
            "Erdogan speech reactions",
        ],
        "active_market_titles_top": [
            "Will Iran agree to a peace deal this month?",
            "Will Trump pardon X by Y?",
        ],
        "active_market_count": 287,
        "active_source_count": 42,
        "window_hours": 168,
    }
    sys_p, user_p = render_prompt("disable_source", evidence)
    expected = (_FIXTURE_DIR / "governance_prompt_disable_source.txt").read_text(encoding="utf-8")
    assert user_p == expected


def test_dump_prompt_revision_writes_versioned_file(tmp_path):
    from governance.prompts import dump_prompt_revision
    out_path = dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
    assert out_path.exists()
    assert out_path.name.endswith("-0.30.0.txt")
    content = out_path.read_text(encoding="utf-8")
    assert "SYSTEM_PROMPT" in content
    assert "DISABLE_SOURCE_TEMPLATE" in content


def test_dump_prompt_revision_refuses_to_overwrite(tmp_path):
    from governance.prompts import dump_prompt_revision
    dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
    with pytest.raises(FileExistsError):
        dump_prompt_revision(revision_label="0.30.0", out_dir=tmp_path)
