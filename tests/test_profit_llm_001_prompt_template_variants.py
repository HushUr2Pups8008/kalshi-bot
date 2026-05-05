"""Strict-xfail harness for PROFIT-LLM-001 prompt-template sizing variants."""

from __future__ import annotations

import pytest


_XFAIL_REASON = (
    "PROFIT-LLM-001 prompt-template sizing harness is pre-loaded only; "
    "variants land when Branch D fires and Step A audit begins."
)


def _variants():
    from analysis import signal_analyzer

    return getattr(signal_analyzer, "PROFIT_LLM_001_PROMPT_TEMPLATE_VARIANTS", {})


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_prompt_variants_define_a1_to_a4_keys():
    assert {"A1", "A2", "A3", "A4"}.issubset(set(_variants()))


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a1_baseline_is_current_prompt():
    from analysis.signal_analyzer import _LLM_SYSTEM_PROMPT

    assert _variants()["A1"] == _LLM_SYSTEM_PROMPT


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a2_chain_of_thought_guidance_present():
    assert "step by step" in _variants()["A2"].lower()


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a3_calibration_anchor_prefers_none_when_uncertain():
    variant = _variants()["A3"].lower()

    assert "uncertain" in variant
    assert "magnitude" in variant
    assert "none" in variant


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a4_worked_example_icl_present():
    variant = _variants()["A4"].lower()

    assert "example" in variant
    assert "json" in variant


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a5_compact_json_only_variant_present():
    variant = _variants()["A5"].lower()

    assert "json" in variant
    assert "compact" in variant or "concise" in variant
    assert "no prose" in variant or "json only" in variant


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a6_resolution_criteria_variant_present():
    variant = _variants()["A6"].lower()

    assert "resolution" in variant
    assert "criteria" in variant
    assert "market" in variant


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a7_probability_delta_variant_present():
    variant = _variants()["A7"].lower()

    assert "probability" in variant
    assert "delta" in variant or "movement" in variant
    assert "confidence" in variant


@pytest.mark.xfail(reason=_XFAIL_REASON, strict=True)
def test_profit_llm_001_a8_no_effect_default_variant_present():
    variant = _variants()["A8"].lower()

    assert "no effect" in variant or "none" in variant
    assert "direct" in variant
    assert "causal" in variant or "causally" in variant
