"""LLM client surface for the governance agent.

Two implementations:
- FakeLLM: deterministic test double; returns canned responses keyed by
  the SHA256 of (system + user). Records every call.
- LocalQwenLLM: thin wrapper over Ollama's HTTP API at localhost:11434
  (Task 14).

Tasks 16-24 depend on FakeLLM. LocalQwenLLM is exercised only by
the smoke runbook (Task 26) and lives in this module so the Protocol
boundary is one module rather than two.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """The agent's view of an LLM: it sends a system+user pair, gets a string back."""
    def complete(self, system: str, user: str) -> str: ...
    def model_name(self) -> str: ...


def prompt_hash(system: str, user: str) -> str:
    """Deterministic hash for keying canned FakeLLM responses."""
    return sha256((system + "\n---\n" + user).encode("utf-8")).hexdigest()


def canned_response_for_action(action: str, *, target: str = "X") -> str:
    """Build a valid LLM-output JSON for a given action. Used to seed FakeLLM
    in tests without hand-writing JSON each time."""
    if action == "no_action":
        return json.dumps({
            "action": "no_action",
            "target": "",
            "reasoning": "Evidence does not justify a change.",
            "confidence": 0.05,
            "predicted_effect": None,
        })
    return json.dumps({
        "action": action,
        "target": target,
        "reasoning": f"Test reasoning for {action} on {target}.",
        "confidence": 0.85,
        "predicted_effect": {
            "metric": "test_metric",
            "baseline": 0.5,
            "predicted_post_change": 0.4,
            "evaluate_at_days": 7,
        },
    })


@dataclass
class FakeLLM:
    """In-test LLM. Returns canned responses by prompt hash; default to
    no_action when no key matches."""
    canned: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        key = prompt_hash(system, user)
        if key in self.canned:
            return self.canned[key]
        return canned_response_for_action("no_action")

    def model_name(self) -> str:
        return "fake-llm"


import re
from datetime import datetime, timedelta
from governance.decision import Decision, PredictedEffect


class LLMResponseParseError(ValueError):
    """Raised when an LLM response cannot be turned into a valid Decision."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)
_REQUIRED_FIELDS = ("action", "target", "reasoning", "confidence", "predicted_effect")


def _strip_fences(raw: str) -> str:
    m = _FENCE_RE.match(raw)
    return m.group(1) if m else raw


def parse_llm_response_to_decision(
    raw: str,
    *,
    decision_id: str,
    batch_id: str,
    decided_at: datetime,
    decided_by: str,
    cadence: str,
    model_used: str,
    evidence_summary: dict[str, Any],
) -> Decision:
    """Validate raw LLM JSON and produce a Decision instance.

    Raises LLMResponseParseError on schema violations. Decision-level
    invariants (confidence range, ID format, etc.) are enforced by
    Decision.__post_init__ and surface as ValueError; we don't catch
    those — the Decision's own validation is the right boundary."""
    try:
        body = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise LLMResponseParseError(f"LLM output is not valid JSON: {exc}") from exc

    missing = [f for f in _REQUIRED_FIELDS if f not in body]
    if missing:
        raise LLMResponseParseError(f"LLM output missing required fields: {missing}")

    action = body["action"]
    target = body.get("target", "") or ""
    reasoning = body["reasoning"]
    confidence = float(body["confidence"])
    pe_in = body["predicted_effect"]

    predicted_effect: PredictedEffect | None
    if action == "no_action" or pe_in is None:
        predicted_effect = None
    else:
        days = int(pe_in.get("evaluate_at_days", 7))
        days = max(1, min(30, days))  # clamp into valid range
        predicted_effect = PredictedEffect(
            metric=str(pe_in["metric"]),
            baseline=float(pe_in["baseline"]),
            predicted_post_change=float(pe_in["predicted_post_change"]),
            evaluate_at=decided_at + timedelta(days=days),
        )

    proposed_change: dict[str, Any]
    if action == "disable_source":
        proposed_change = {"before": "source_active", "after": "source_disabled", "expires_at": None}
    elif action == "disable_keyword":
        proposed_change = {"before": "keyword_active", "after": "keyword_disabled", "expires_at": None}
    elif action == "tune_threshold":
        proposed_change = {
            "before": pe_in.get("baseline") if pe_in else None,
            "after": pe_in.get("predicted_post_change") if pe_in else None,
            "expires_at": None,
        }
    else:
        proposed_change = {}

    return Decision(
        decision_id=decision_id,
        batch_id=batch_id,
        decided_at=decided_at,
        decided_by=decided_by,
        cadence=cadence,  # type: ignore[arg-type]
        action=action,
        target=target,
        proposed_change=proposed_change,
        confidence=confidence,
        reasoning=reasoning,
        evidence_summary=evidence_summary,
        predicted_effect=predicted_effect,
        model_used=model_used,
    )
