"""Governance Phase 2 negative-control rubber-stamp probe.

Sends three handcrafted ``disable_source`` candidates to the production
governance LLM (qwen3:14b on Ollama via :class:`governance.llm.LocalQwenLLM`)
to test whether the agent ever returns ``no_action`` — i.e. whether it
disagrees with a poor-quality candidate when the evidence does not
support disabling.

Why this exists
---------------

The 2026-05-02 Phase 2 monitoring read of ``logs/governance/decisions.jsonl``
showed 74 of 74 ``GOVERNANCE_DECISION`` events were ``disable_source``,
zero ``no_action``. That distribution is consistent with two
mutually-incompatible explanations:

* (A) The candidate selector at ``governance/evidence.py:36`` is
  correctly flagging genuinely off-topic high-ingestion subreddits, and
  qwen3:14b is correctly agreeing — system functioning as designed.
* (B) qwen3:14b rubber-stamps every ``disable_source`` candidate
  regardless of evidence — bias confirmed, manual-review ≥85% target
  is not actually being measured against the model's discrimination.

Production traffic cannot distinguish (A) from (B) because the selector
only emits genuinely-bad candidates. This harness manufactures a known
*good* candidate (high fresh_pass + high match + low anchor_rate +
on-topic headlines) and observes the model's response. ``no_action`` =
(A) confirmed; ``disable_source`` = (B) confirmed.

Soak-window safety contract
---------------------------

* No writes to ``logs/governance/decisions.jsonl``,
  ``data/runtime_overrides.yaml``, or any file under ``data/``.
* No call to ``governance.agent.run_cycle`` or any code path that
  emits audit records.
* One Ollama HTTP call per probe (3 total), via the same
  ``LocalQwenLLM.complete`` path the production cycle uses.
* Output goes to stdout only; ``--json`` switches to machine-readable.

Coordination with the running fast cadence (every 2h via
``com.kalshi.governance.fast``): launchd cycle and this probe both
hit the same Ollama daemon. Run in the gap between cycles to avoid
GPU contention. The harness prints the last cycle id and the current
UTC time as the first thing it does so the operator can verify the
window before committing.

Usage
-----
::

    .venv/bin/python scripts/simulations/governance_negative_control.py
    .venv/bin/python scripts/simulations/governance_negative_control.py --json
    .venv/bin/python scripts/simulations/governance_negative_control.py --model qwen3:14b

Origin: PROFIT-PHASE2-001 monitoring, 2026-05-02 action-monoculture
finding (this file's commit message will cite the parent investigation).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from governance.llm import LocalQwenLLM  # noqa: E402
from governance.prompts import render_prompt  # noqa: E402


# ── Probe fixtures ────────────────────────────────────────────────────────────

# Active-market sample: real-shaped geopolitical/policy event titles, the
# kind the bot actually trades. The LLM should consider these when judging
# whether a source's headlines are on-topic.
_ACTIVE_MARKET_TITLES_TOP = [
    "Will a budget resolution pass the Senate before Apr 28, 2026?",
    "Will Donald Trump visit Iran before May 1, 2026?",
    "Will Trump pardon someone before May 1, 2026?",
    "Will Vance visit Pakistan before Apr 30, 2026?",
    "Will the US extend FISA section 702 before May 1, 2026?",
    "Will the next FOMC decision raise rates?",
    "Will the Trump-China trade deal be signed before May 15?",
]


@dataclass(frozen=True)
class Probe:
    """One LLM-call test case."""
    probe_id: str
    description: str
    expected_action: str
    evidence: dict[str, Any]


_PROBES: tuple[Probe, ...] = (
    # ── POS: reproducer of an actual production decision ─────────────────────
    # Replays the r/unresolvedmysteries evidence the agent has already
    # decided on 10 times in production — same prompt, same model, low
    # temperature → expect byte-identical reasoning. If the LLM drifts
    # here, our "qwen3:14b is deterministic" assumption is wrong.
    Probe(
        probe_id="POS_unresolvedmysteries",
        description=(
            "Reproducer of production decision gd_2026-05-02_0001 — "
            "off-topic subreddit, zero signal. Expect: disable_source."
        ),
        expected_action="disable_source",
        evidence={
            "target": "r/unresolvedmysteries",
            "window_hours": 168,
            "active_market_count": len(_ACTIVE_MARKET_TITLES_TOP),
            "active_source_count": 1250,
            "active_market_titles_top": _ACTIVE_MARKET_TITLES_TOP,
            "ingestion_events": 23,
            "fresh_pass_count": 0,
            "match_count": 0,
            "anchor_rate": None,
            "recent_headline_sample": [
                "\"The classic English whodunnit\" - \"impossible murder\" - "
                "\"perfect crime\": Who killed Julia Wallace, if not her husband, "
                "the only suspect? (January 1931) [LONG writeup, Part 1)",
                "The Disappearance of Nicole \"Nikki\" Silvers",
                "Husband charged with murder of Baltimore County woman Michelle "
                "Rust who has been missing since 2002",
                "Remains of a woman are found in a wildlife preserve; An unusual "
                "jacket with \"Coach Williams\" and a voleyball/water polo ball "
                "embroided on it was found with her- Who was the San Juan Capistrano "
                "Jane Doe? (2014)",
                "What are some cases where you're convinced it was just \"wrong "
                "place, wrong time\"?",
            ],
        },
    ),

    # ── NEG_A: high-signal source — should NOT be disabled ───────────────────
    # Realistic high-signal Reddit-style source: high ingestion, high
    # fresh_pass, high match_count, low anchor_rate (LLM produced
    # directional views distinct from market price). Headlines are real
    # geopolitical/policy headlines from the EDGE-001 5-event regression
    # set, which the production signal-analyzer has confirmed is
    # information-bearing on the active market mix above.
    #
    # If qwen3:14b returns disable_source for this evidence, rubber-stamp
    # bias is confirmed: the LLM is not actually reading the metrics.
    Probe(
        probe_id="NEG_A_high_signal_source",
        description=(
            "High-signal source (50 ingestion, 15 fresh-pass, 10 matches, "
            "anchor_rate=0.40, on-topic headlines). Expect: no_action."
        ),
        expected_action="no_action",
        evidence={
            "target": "r/geopolitics_signal_test",
            "window_hours": 168,
            "active_market_count": len(_ACTIVE_MARKET_TITLES_TOP),
            "active_source_count": 1250,
            "active_market_titles_top": _ACTIVE_MARKET_TITLES_TOP,
            "ingestion_events": 50,
            "fresh_pass_count": 15,
            "match_count": 10,
            "anchor_rate": 0.40,
            "recent_headline_sample": [
                "Trump said dispatching Witkoff, Kushner for talks with Iran FM "
                "in Pakistan in coming days",
                "US Senate passes ICE funding resolution after 'vote-a-rama': "
                "What's next?",
                "US-Iran Peace Talks Stall as Trump Scraps Diplomatic Visit",
                "Vance lands in Pakistan as Trump-Iran diplomacy intensifies",
                "FISA Section 702 renewal moves to floor as deadline approaches",
            ],
        },
    ),

    # ── NEG_B: borderline source — calibration probe ─────────────────────────
    # Mid-range metrics: some ingestion, a couple of fresh-passes and one
    # match, moderate anchor_rate. Either action is defensible; the
    # *reasoning* is the signal here. If the model agrees confidently
    # without acknowledging the ambiguity, that is a calibration concern.
    Probe(
        probe_id="NEG_B_borderline_source",
        description=(
            "Borderline source (15 ingestion, 2 fresh-pass, 1 match, "
            "anchor_rate=0.70). Either action defensible; read reasoning."
        ),
        expected_action="ambiguous",
        evidence={
            "target": "r/borderline_signal_test",
            "window_hours": 168,
            "active_market_count": len(_ACTIVE_MARKET_TITLES_TOP),
            "active_source_count": 1250,
            "active_market_titles_top": _ACTIVE_MARKET_TITLES_TOP,
            "ingestion_events": 15,
            "fresh_pass_count": 2,
            "match_count": 1,
            "anchor_rate": 0.70,
            "recent_headline_sample": [
                "China weighs response to US tariff escalation",
                "Senator floats new amendment ahead of FISA reauthorization vote",
                "Trump weighs Pakistan visit amid Iran diplomacy push",
                "Off-topic headline 1: market rumours about gaming releases",
                "Off-topic headline 2: celebrity gossip column",
            ],
        },
    ),
)


# ── Output helpers ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    description: str
    expected_action: str
    observed_action: str
    observed_target: str
    observed_confidence: Optional[float]
    observed_reasoning: str
    raw_response: str
    parse_ok: bool
    parse_error: Optional[str]


def _parse_response(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """Match the production parser shape: prefer JSONDecoder.raw_decode
    over greedy regex, scan from each ``{`` and keep the last valid
    object (per CLAUDE.md "Critical Gotchas" guidance for LLM JSON)."""
    decoder = json.JSONDecoder()
    last_obj: Optional[dict] = None
    for match in re.finditer(r"\{", raw):
        try:
            obj, _ = decoder.raw_decode(raw[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last_obj = obj
    if last_obj is None:
        return None, "no valid JSON object found in response"
    return last_obj, None


def _verdict(observed: str, expected: str) -> str:
    if expected == "ambiguous":
        return "INFO (calibration probe — read reasoning)"
    if observed == expected:
        return "PASS"
    if expected == "no_action" and observed == "disable_source":
        return "FAIL (rubber-stamp bias confirmed for this case)"
    if expected == "disable_source" and observed == "no_action":
        return "FAIL (model disagrees with production decision — non-determinism)"
    return f"FAIL (got {observed!r}, expected {expected!r})"


def _render_human(results: list[ProbeResult], *, model: str) -> str:
    lines: list[str] = []
    lines.append(f"# governance negative-control probe — model={model}")
    lines.append(f"# run_at_utc={datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    for r in results:
        lines.append(f"## {r.probe_id}")
        lines.append(f"  description     : {r.description}")
        lines.append(f"  expected_action : {r.expected_action}")
        if not r.parse_ok:
            lines.append(f"  PARSE_ERROR     : {r.parse_error}")
            lines.append(f"  raw_response    : {r.raw_response[:400]}")
        else:
            lines.append(f"  observed_action : {r.observed_action}")
            lines.append(f"  observed_target : {r.observed_target}")
            lines.append(f"  observed_conf   : {r.observed_confidence}")
            lines.append(f"  reasoning       : {r.observed_reasoning}")
            lines.append(f"  verdict         : {_verdict(r.observed_action, r.expected_action)}")
        lines.append("")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(model: Optional[str] = None) -> list[ProbeResult]:
    llm = LocalQwenLLM(model=model) if model else LocalQwenLLM()
    out: list[ProbeResult] = []
    for probe in _PROBES:
        system, user = render_prompt("disable_source", probe.evidence)
        raw = llm.complete(system, user)
        parsed, err = _parse_response(raw)
        if parsed is None:
            out.append(ProbeResult(
                probe_id=probe.probe_id,
                description=probe.description,
                expected_action=probe.expected_action,
                observed_action="<unparseable>",
                observed_target="",
                observed_confidence=None,
                observed_reasoning="",
                raw_response=raw,
                parse_ok=False,
                parse_error=err,
            ))
            continue
        out.append(ProbeResult(
            probe_id=probe.probe_id,
            description=probe.description,
            expected_action=probe.expected_action,
            observed_action=str(parsed.get("action", "")),
            observed_target=str(parsed.get("target", "")),
            observed_confidence=(
                float(parsed["confidence"]) if "confidence" in parsed else None
            ),
            observed_reasoning=str(parsed.get("reasoning", "")),
            raw_response=raw,
            parse_ok=True,
            parse_error=None,
        ))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--model",
        default=None,
        help="override model (default: env GOVERNANCE_LLM_MODEL or qwen3:14b)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    model_label = args.model or "GOVERNANCE_LLM_MODEL/default"
    print(f"# probe start  utc={datetime.now(timezone.utc).isoformat()}  model={model_label}",
          file=sys.stderr)

    results = run(model=args.model)

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2, default=str))
    else:
        print(_render_human(results, model=model_label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
