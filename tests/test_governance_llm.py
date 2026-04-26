"""LLMClient Protocol + FakeLLM + LocalQwenLLM smoke."""

from __future__ import annotations

from hashlib import sha256

import pytest

from governance.llm import (
    FakeLLM,
    LLMClient,
    canned_response_for_action,
    prompt_hash,
)


def test_fakellm_satisfies_protocol():
    fake = FakeLLM()
    assert isinstance(fake, LLMClient)


def test_fakellm_records_calls():
    fake = FakeLLM()
    fake.complete("sys", "user1")
    fake.complete("sys", "user2")
    assert len(fake.calls) == 2
    assert fake.calls[0] == {"system": "sys", "user": "user1"}
    assert fake.calls[1] == {"system": "sys", "user": "user2"}


def test_fakellm_returns_canned_response_by_prompt_hash():
    h = prompt_hash("sys", "user")
    fake = FakeLLM(canned={h: '{"action": "no_action"}'})
    assert fake.complete("sys", "user") == '{"action": "no_action"}'


def test_fakellm_returns_default_no_action_when_no_canned_match():
    fake = FakeLLM()
    out = fake.complete("sys", "novel-user")
    assert '"action": "no_action"' in out


def test_canned_response_for_action_emits_valid_json_for_each_action():
    import json as _json
    for action in ("disable_source", "disable_keyword", "tune_threshold", "no_action"):
        body = canned_response_for_action(action, target="X")
        parsed = _json.loads(body)
        assert parsed["action"] == action


def test_fakellm_model_name_is_stable():
    fake = FakeLLM()
    assert fake.model_name() == "fake-llm"


from datetime import datetime, timezone, timedelta

from governance.decision import Decision
from governance.llm import (
    LLMResponseParseError,
    parse_llm_response_to_decision,
)


def test_parse_disable_source_response_to_decision():
    raw = canned_response_for_action("disable_source", target="r/Turkey")
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        model_used="fake-llm",
        evidence_summary={"ingestion_events": 408},
    )
    assert isinstance(d, Decision)
    assert d.action == "disable_source"
    assert d.target == "r/Turkey"
    assert d.confidence == 0.85
    assert d.predicted_effect is not None
    assert d.predicted_effect.metric == "test_metric"
    expected_eval = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)
    assert d.predicted_effect.evaluate_at == expected_eval


def test_parse_no_action_response_to_decision():
    raw = canned_response_for_action("no_action")
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="governance-agent-v0.2.1",
        cadence="fast",
        model_used="fake-llm",
        evidence_summary={},
    )
    assert d.action == "no_action"
    assert d.predicted_effect is None


def test_parse_rejects_malformed_json():
    with pytest.raises(LLMResponseParseError, match="JSON"):
        parse_llm_response_to_decision(
            "not json",
            decision_id="gd_2026-05-02_0001",
            batch_id="gb_2026-05-02_0001",
            decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
            decided_by="x", cadence="fast", model_used="m", evidence_summary={},
        )


def test_parse_rejects_missing_required_field():
    raw = '{"action": "disable_source", "target": "r/X"}'  # missing confidence/reasoning/predicted_effect
    with pytest.raises(LLMResponseParseError, match="required|missing|confidence|reasoning"):
        parse_llm_response_to_decision(
            raw,
            decision_id="gd_2026-05-02_0001",
            batch_id="gb_2026-05-02_0001",
            decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
            decided_by="x", cadence="fast", model_used="m", evidence_summary={},
        )


def test_parse_clamps_evaluate_at_days_into_valid_range():
    """If LLM emits evaluate_at_days outside [1, 30], parser clamps and
    proceeds — better to apply a slightly-off evaluation date than to
    drop an otherwise-valid decision."""
    import json as _j
    body = _j.loads(canned_response_for_action("disable_source", target="r/X"))
    body["predicted_effect"]["evaluate_at_days"] = 100  # out of range
    raw = _j.dumps(body)
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="x", cadence="fast", model_used="m", evidence_summary={},
    )
    expected_eval = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc) + timedelta(days=30)
    assert d.predicted_effect.evaluate_at == expected_eval


def test_parse_strips_markdown_fences_around_json():
    """Some local models like to emit ```json ... ``` even when told not to.
    The parser strips a single fence layer."""
    raw = "```json\n" + canned_response_for_action("no_action") + "\n```"
    d = parse_llm_response_to_decision(
        raw,
        decision_id="gd_2026-05-02_0001",
        batch_id="gb_2026-05-02_0001",
        decided_at=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc),
        decided_by="x", cadence="fast", model_used="m", evidence_summary={},
    )
    assert d.action == "no_action"
