"""LLM prompts for the governance agent.

The SYSTEM_PROMPT is the long-lived contract: it tells the model the
output schema. Per-action templates (DISABLE_SOURCE_TEMPLATE etc.)
provide the per-call evidence framing.

Snapshot tests in tests/test_governance_prompts.py detect drift.
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = """You are a governance agent for a Kalshi prediction-market trading bot.

Your job: decide whether a candidate change to the bot's news-source list,
keyword filters, or pipeline thresholds is warranted, based on diagnostic
evidence about how the pipeline is currently performing.

For each candidate, you must decide one of:
  - "disable_source"  : the named source is harmful (consuming budget without producing usable signal)
  - "disable_keyword" : the named keyword is producing false matches or no longer adds signal value
  - "tune_threshold"  : the named threshold should change to a new value
  - "no_action"       : the evidence does not justify changing the candidate

Interpreting disable_source evidence:
- LLM anchor rate (final_probability == market_price): the rate at which our LLM agreed with market price.
  - HIGH (>=0.95) = the source contributes NO information edge. The LLM has no opinion that differs from market consensus. This is a strong DISABLE signal.
  - LOW (<=0.50)  = the source produces directional views distinct from market price. This is INFORMATIVE signal — KEEP this source.
  - MID (0.50-0.95) = some information edge, no clear signal either way.

Decision criteria:
  1. Does the evidence concretely show the candidate target is operationally harmful or stale?
  2. What measurable metric will move if you make the change?
  3. By how much (baseline vs predicted_post_change)?
  4. How confident are you (0.0 = guess, 1.0 = certain)?

Predict the effect on a single named metric. The bot's outcome-evaluator
will check your prediction against measured outcomes. Bad predictions
are a worse failure mode than conservative no_action.

Output ONLY valid JSON, no prose, matching this schema:
{
  "action": "disable_source" | "disable_keyword" | "tune_threshold" | "no_action",
  "target": <string — source name, keyword, or threshold path; empty for no_action>,
  "reasoning": <string — 2-5 sentences explaining the decision>,
  "confidence": <float in [0.0, 1.0]>,
  "predicted_effect": {
    "metric": <string — name of the metric you predict will move>,
    "baseline": <float — current value>,
    "predicted_post_change": <float — predicted value after change applies>,
    "evaluate_at_days": <integer in [1, 30] — when to check>
  }
}

If action is "no_action", set "predicted_effect" to null and "target" to "".
"""


LLM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action", "target", "reasoning", "confidence", "predicted_effect"],
    "properties": {
        "action": {"enum": ["disable_source", "disable_keyword", "tune_threshold", "no_action"]},
        "target": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "predicted_effect": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": ["metric", "baseline", "predicted_post_change", "evaluate_at_days"],
                    "properties": {
                        "metric": {"type": "string"},
                        "baseline": {"type": "number"},
                        "predicted_post_change": {"type": "number"},
                        "evaluate_at_days": {"type": "integer", "minimum": 1, "maximum": 30},
                    },
                },
            ],
        },
    },
}


DISABLE_SOURCE_TEMPLATE = """CANDIDATE ACTION: disable_source
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
- Ingestion events: {ingestion_events}
- Fresh-pass count: {fresh_pass_count}
- Match count (events that produced a MATCH_DIAGNOSTIC): {match_count}
- LLM anchor rate (final_probability == market_price): {anchor_rate_pct}

RECENT HEADLINE SAMPLE (3-5 most recent):
{headline_sample_block}

CURRENT ACTIVE MARKETS ({active_market_count} total — top 20):
{active_market_titles_block}

Should this source be disabled? Output JSON per schema.
"""


DISABLE_KEYWORD_TEMPLATE = """CANDIDATE ACTION: disable_keyword
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
{phrase_summary_block}

CURRENT ACTIVE MARKETS ({active_market_count} total — top 20):
{active_market_titles_block}

Should this keyword be disabled? Output JSON per schema.
"""


TUNE_THRESHOLD_TEMPLATE = """CANDIDATE ACTION: tune_threshold
TARGET: {target}

EVIDENCE (window: last {window_hours}h):
- Current value: {current_value}
- Active markets: {active_market_count}
- Active sources: {active_source_count}

Should this threshold be tuned? If yes, to what value? Output JSON per schema.
"""


def _format_block(items: list[str], prefix: str = "- ") -> str:
    if not items:
        return f"{prefix}(none)"
    return "\n".join(f"{prefix}{item}" for item in items)


def render_prompt(action: str, evidence: dict[str, Any]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given action+evidence."""
    common = {
        "target": evidence.get("target", ""),
        "window_hours": evidence.get("window_hours", 168),
        "active_market_count": evidence.get("active_market_count", 0),
        "active_source_count": evidence.get("active_source_count", 0),
        "active_market_titles_block": _format_block(
            evidence.get("active_market_titles_top", []),
        ),
    }

    if action == "disable_source":
        anchor = evidence.get("anchor_rate")
        anchor_pct = "n/a" if anchor is None else f"{anchor * 100:.1f}%"
        user = DISABLE_SOURCE_TEMPLATE.format(
            **common,
            ingestion_events=evidence.get("ingestion_events", 0),
            fresh_pass_count=evidence.get("fresh_pass_count", 0),
            match_count=evidence.get("match_count", 0),
            anchor_rate_pct=anchor_pct,
            headline_sample_block=_format_block(
                evidence.get("recent_headline_sample", []),
            ),
        )
    elif action == "disable_keyword":
        phrase_summary = evidence.get("candidate_phrase_summary") or {}
        phrase_block = _format_block(
            [f"{k}: {v}" for k, v in sorted(phrase_summary.items())],
            prefix="- ",
        )
        user = DISABLE_KEYWORD_TEMPLATE.format(
            **common,
            phrase_summary_block=phrase_block,
        )
    elif action == "tune_threshold":
        user = TUNE_THRESHOLD_TEMPLATE.format(
            **common,
            current_value=evidence.get("current_value", "unknown"),
        )
    else:
        raise ValueError(f"unknown action for render_prompt: {action!r}")

    return SYSTEM_PROMPT, user


def dump_prompt_revision(*, revision_label: str, out_dir):
    """Write the current prompt set to a versioned file. Refuses to
    overwrite — historical context must not be erased accidentally.
    Returns the path written."""
    from pathlib import Path
    from datetime import datetime, timezone
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir_path / f"{today}-{revision_label}.txt"
    if out_path.exists():
        raise FileExistsError(out_path)
    body = (
        "# SYSTEM_PROMPT\n\n" + SYSTEM_PROMPT + "\n\n"
        "# DISABLE_SOURCE_TEMPLATE\n\n" + DISABLE_SOURCE_TEMPLATE + "\n\n"
        "# DISABLE_KEYWORD_TEMPLATE\n\n" + DISABLE_KEYWORD_TEMPLATE + "\n\n"
        "# TUNE_THRESHOLD_TEMPLATE\n\n" + TUNE_THRESHOLD_TEMPLATE + "\n"
    )
    out_path.write_text(body, encoding="utf-8")
    return out_path
