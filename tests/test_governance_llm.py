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
