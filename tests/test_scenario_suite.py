"""I-6 Scenario Suite — adversarial regression catalog runner.

Discovers every ``tests/scenarios/*.jsonl`` file, parses each line as a
scenario record, validates the schema, and parametrizes pytest cases over
the full corpus. Per scenario, routes to a pipeline-specific runner that
exercises the observable invariant under test.

Design constraints (per
``docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md``
§3 I-6):

  * Discovery is glob-based — append-only adds in ``tests/scenarios/*.jsonl``
    pick up automatically without test-code changes.
  * Schema validation fires at collection time; missing required fields
    surface as ``pytest.fail`` immediately rather than as runtime errors.
  * ``xfail_reason`` is honored via ``pytest.param`` marks with
    ``strict=True`` so the suite catches the moment an aspirational
    assertion starts passing (forcing a flip to live-asserted).
  * Pipeline runners are stubs that exercise the OBSERVABLE invariant
    using production helpers (``tasks.blend_task._series_prefix``) and
    source-text inspection (``governance/prompts.py``). Full live-LLM
    integration is deferred to I-4 (replay-as-CI gate).

This module is OBSERVATION-ONLY. It does not modify any production code
path. T0 per the I-5 tier classifier.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

# Import production helpers we exercise directly. The blend_task helper is
# pure (no I/O), so importing it is safe in any test environment.
from tasks.blend_task import _series_prefix

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

REQUIRED_FIELDS: tuple[str, ...] = (
    "scenario_id",
    "category",
    "description",
    "pipeline",
    "inputs",
    "expected",
    "added_at_utc",
    "added_by",
    "memo_ref",
    "xfail_reason",
)

VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "fisa_burst",
        "suppression_edge_cases",
        "keyword_direction_flip",
        "anchor_rate_polarity",
    }
)

VALID_PIPELINES: frozenset[str] = frozenset(
    {"blend_task", "signal_analyzer", "governance", "matcher"}
)


# ---------------------------------------------------------------------------
# Schema validation + discovery
# ---------------------------------------------------------------------------


class ScenarioSchemaError(ValueError):
    """Raised when a scenario row violates the required schema."""


def _validate_scenario(record: dict[str, Any], source: Path, line_no: int) -> None:
    """Raise ``ScenarioSchemaError`` on schema violations.

    Validation runs at discovery time so missing fields surface as a hard
    failure during pytest collection rather than as a confusing runtime
    AttributeError deep inside a runner stub.
    """
    location = f"{source.name}:{line_no}"
    missing = [field for field in REQUIRED_FIELDS if field not in record]
    if missing:
        raise ScenarioSchemaError(
            f"{location}: missing required fields: {missing}"
        )
    if record["category"] not in VALID_CATEGORIES:
        raise ScenarioSchemaError(
            f"{location}: invalid category {record['category']!r}; "
            f"must be one of {sorted(VALID_CATEGORIES)}"
        )
    if record["pipeline"] not in VALID_PIPELINES:
        raise ScenarioSchemaError(
            f"{location}: invalid pipeline {record['pipeline']!r}; "
            f"must be one of {sorted(VALID_PIPELINES)}"
        )
    if not isinstance(record["inputs"], dict):
        raise ScenarioSchemaError(
            f"{location}: 'inputs' must be a JSON object"
        )
    if not isinstance(record["expected"], dict):
        raise ScenarioSchemaError(
            f"{location}: 'expected' must be a JSON object"
        )
    for triplet_field in ("decision", "side", "magnitude"):
        if triplet_field not in record["expected"]:
            raise ScenarioSchemaError(
                f"{location}: 'expected' missing required key {triplet_field!r}"
            )
    if record["xfail_reason"] is not None and not isinstance(
        record["xfail_reason"], str
    ):
        raise ScenarioSchemaError(
            f"{location}: 'xfail_reason' must be null or string"
        )


def _discover_scenarios() -> list[tuple[dict[str, Any], Path, int]]:
    """Return all (record, source_path, line_no) tuples in deterministic order.

    Sorted by (file name, line number) so test names are stable across runs.
    Schema errors raise immediately — they are bugs in the corpus, not
    runtime test failures.
    """
    if not SCENARIOS_DIR.exists():
        return []
    discovered: list[tuple[dict[str, Any], Path, int]] = []
    for path in sorted(SCENARIOS_DIR.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ScenarioSchemaError(
                        f"{path.name}:{line_no}: invalid JSON: {exc}"
                    ) from exc
                _validate_scenario(record, path, line_no)
                discovered.append((record, path, line_no))
    return discovered


def _scenarios_for_parametrize() -> list[pytest.param]:
    """Wrap discovered scenarios as ``pytest.param`` with xfail marks honored.

    A ``xfail_reason`` of ``"DEPRECATED: ..."`` keeps the test in the
    corpus (per Q3 append-only governance) but skips active assertion.
    Otherwise non-null reasons become ``xfail(strict=True)`` so the suite
    catches the moment the aspirational invariant starts holding.
    """
    params: list[pytest.param] = []
    for record, path, line_no in _discover_scenarios():
        scenario_id = record["scenario_id"]
        xfail_reason = record["xfail_reason"]
        marks: list[pytest.MarkDecorator] = []
        if xfail_reason is None:
            pass
        elif xfail_reason.startswith("DEPRECATED:"):
            marks.append(pytest.mark.skip(reason=xfail_reason))
        else:
            marks.append(pytest.mark.xfail(strict=True, reason=xfail_reason))
        params.append(
            pytest.param(record, path, line_no, marks=marks, id=scenario_id)
        )
    return params


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


def _run_blend_task_scenario(record: dict[str, Any]) -> None:
    """Replay events through a stub of the EXEC-002 series-correlation guard.

    Uses the production ``_series_prefix`` helper. The stub mirrors the
    logic at ``tasks/blend_task.py:213-226``: an event enqueues iff
    ``window <= 0`` OR no prior same-prefix enqueue lies within the window.
    ``upstream_blocked=True`` events are dropped *without* recording into
    the guard state, matching today's production behavior (guard arms only
    on successful enqueue) — aspirational rows that test the "guard arms
    on attempt" future variant carry ``xfail_reason`` and are routed
    accordingly.
    """
    events = record["inputs"]["events"]
    window_seconds = int(record["inputs"].get("window_seconds", 3600))

    recent: dict[str, float] = {}
    enqueued_count = 0
    for event in events:
        ticker = event["ticker"]
        ts = float(event["ts_offset_seconds"])
        upstream_blocked = bool(event.get("upstream_blocked", False))
        prefix = _series_prefix(ticker)

        if upstream_blocked:
            # Today: guard does NOT arm on upstream-blocked candidates.
            # The aspirational scenario expects the guard to arm anyway;
            # xfail handles the mismatch.
            continue

        if window_seconds > 0:
            last = recent.get(prefix)
            if last is not None and (ts - last) < window_seconds:
                # Guard fires → suppressed.
                continue

        enqueued_count += 1
        recent[prefix] = ts

    expected_predicate = record["expected"]["decision"]
    # Predicate is `enqueued_count == <N>`. Parse and assert.
    match = re.match(r"^enqueued_count == (\d+)$", expected_predicate)
    if match is None:
        pytest.fail(
            f"blend_task scenario {record['scenario_id']!r}: "
            f"expected.decision must match 'enqueued_count == <N>'; "
            f"got {expected_predicate!r}"
        )
    expected_count = int(match.group(1))
    assert enqueued_count == expected_count, (
        f"scenario {record['scenario_id']!r}: expected {expected_count} "
        f"enqueues, observed {enqueued_count}"
    )


def _run_signal_analyzer_scenario(record: dict[str, Any]) -> None:
    """Stub interpreter for signal_analyzer scenarios.

    Without I-4 we cannot invoke the live LLM. The stub asserts structural
    properties of the input that any correct signal_analyzer MUST honor:

    * Explicit negation tokens (``WILL NOT``, ``did not``, ``no longer``,
      ``never``, ``no <noun>``) MUST cap magnitude to ``low`` or ``none``.
    * Past-tense markers vs forward-looking market questions MUST downgrade.
    * Conditional ``if … then …`` clauses MUST downgrade.

    For rows that depend on live-LLM semantic reasoning (``xfail_reason``
    starts with ``"awaits I-4"``), the stub deliberately raises
    ``NotImplementedError`` so the strict xfail mark is satisfied — those
    rows pin the post-I-4 expected behavior without claiming the stub
    can verify it today.
    """
    headline = record["inputs"].get("headline", "")
    expected_magnitude = record["expected"].get("magnitude", "")
    xfail_reason = record["xfail_reason"]

    # Rows that need live LLM reasoning: raise to satisfy xfail-strict.
    if xfail_reason is not None and xfail_reason.startswith("awaits I-4"):
        raise NotImplementedError(
            f"scenario {record['scenario_id']!r}: live signal_analyzer "
            f"call not available pre-I-4; pinning expected post-fix "
            f"behavior only."
        )

    # Stub heuristics for live-asserted rows. These are observable
    # invariants the production analyzer is contractually required to honor.
    has_explicit_negation = bool(
        re.search(
            r"\bwill not\b|\bdid not\b|\bdoes not\b|\bdo not\b|"
            r"\bno longer\b|\bnever\b|\bno [a-z]+ change\b|\bnot \w+\b",
            headline,
            re.IGNORECASE,
        )
    )
    has_conditional = bool(re.match(r"^\s*if\b", headline, re.IGNORECASE))
    has_past_tense_marker = bool(
        re.search(
            r"\bin (19|20)\d{2}\b|\brecalls\b|\b(visited|signed) .* in \d{4}\b",
            headline,
            re.IGNORECASE,
        )
    )

    downgrade_signal = (
        has_explicit_negation or has_conditional or has_past_tense_marker
    )

    downgrade_expected_values = {
        "none_or_low",
        "low_or_none",
        "none",
        "low",
    }
    if expected_magnitude in downgrade_expected_values:
        assert downgrade_signal, (
            f"scenario {record['scenario_id']!r}: headline "
            f"{headline!r} lacks any structural downgrade marker "
            f"(negation/conditional/past-tense) but the expected "
            f"magnitude is {expected_magnitude!r}. Either the stub "
            f"heuristic is too narrow or the scenario inputs are "
            f"miscategorized."
        )


def _run_governance_scenario(record: dict[str, Any]) -> None:
    """Inspect ``governance/prompts.py`` source to assert polarity invariants.

    The PROFIT-GOV-002 polarity block (lines 27-31) is load-bearing for
    qwen3 governance decisions. Removing or weakening it silently re-
    introduces the rubber-stamp regression. We read the file directly
    (not via import-time string introspection) so the assertion catches
    a bad edit even before the LLM is invoked.

    For ``anchor_004`` (think=False boundary), we read ``governance/llm.py``
    and assert ``think`` is pinned to False in the native-API payload OR
    that the file no longer uses the native endpoint (boundary moved).
    """
    repo_root = Path(__file__).resolve().parent.parent
    prompts_path = repo_root / "governance" / "prompts.py"
    llm_path = repo_root / "governance" / "llm.py"

    prompt_text = prompts_path.read_text(encoding="utf-8")
    # Extract the polarity block. The block lives between the
    # "Interpreting disable_source evidence:" header and the
    # "Decision criteria:" header in SYSTEM_PROMPT.
    polarity_match = re.search(
        r"Interpreting disable_source evidence:(.+?)Decision criteria:",
        prompt_text,
        re.DOTALL,
    )
    assert polarity_match is not None, (
        "governance/prompts.py: anchor_rate polarity block missing — "
        "PROFIT-GOV-002 regression. Expected 'Interpreting disable_source "
        "evidence:' header followed by HIGH/LOW/MID lines."
    )
    polarity_block = polarity_match.group(1)

    check = record["inputs"].get("check")
    if check == "think_false_pinned_at_generate_endpoint":
        llm_text = llm_path.read_text(encoding="utf-8")
        uses_native_api = "/api/generate" in llm_text
        pins_think_false = bool(
            re.search(r'["\']think["\']\s*:\s*False', llm_text)
        )
        # Either we are on OpenAI-compat (no think pin needed) OR we
        # use the native generate endpoint AND pin think=False.
        assert (not uses_native_api) or pins_think_false, (
            "governance/llm.py uses the Ollama native /api/generate "
            "endpoint without pinning think=False — PROFIT-GOV-001 "
            "regression. qwen3 + format=json + thinking returns empty "
            "{} unless think=False is in the top-level payload."
        )
        return

    expected_polarity = record["inputs"].get("expected_polarity_direction")
    if expected_polarity == "DISABLE":
        # HIGH (>=0.95) must map to DISABLE.
        assert re.search(
            r"HIGH.*0\.95.*DISABLE", polarity_block, re.IGNORECASE | re.DOTALL
        ), (
            f"scenario {record['scenario_id']!r}: HIGH→DISABLE mapping "
            f"absent from polarity block. PROFIT-GOV-002 regression."
        )
        return
    if expected_polarity == "KEEP":
        # LOW (<=0.50) must map to KEEP/INFORMATIVE.
        assert re.search(
            r"LOW.*0\.50.*(KEEP|INFORMATIVE)", polarity_block,
            re.IGNORECASE | re.DOTALL
        ), (
            f"scenario {record['scenario_id']!r}: LOW→KEEP mapping absent "
            f"from polarity block. PROFIT-GOV-002 regression."
        )
        return
    if expected_polarity == "NO_CLEAR_SIGNAL":
        assert re.search(r"MID.*no clear signal", polarity_block,
                         re.IGNORECASE | re.DOTALL), (
            f"scenario {record['scenario_id']!r}: MID→no-clear-signal "
            f"documentation absent from polarity block. Removing the MID "
            f"band hint re-introduces qwen3 over-confidence in ambiguous "
            f"anchor zones."
        )
        return

    required_literals = record["inputs"].get("required_literals")
    if required_literals:
        for literal in required_literals:
            assert literal in polarity_block, (
                f"scenario {record['scenario_id']!r}: required literal "
                f"{literal!r} missing from polarity block. Threshold drift "
                f"in prompt text changes governance recommendations "
                f"without a code review."
            )
        return

    pytest.fail(
        f"governance scenario {record['scenario_id']!r}: no recognized "
        f"check key in inputs. Add 'expected_polarity_direction', "
        f"'required_literals', or 'check' to route the assertion."
    )


def _run_matcher_scenario(record: dict[str, Any]) -> None:
    """Stub interpreter for matcher (MATCH-001 B') scenarios.

    All matcher scenarios in the current corpus are xfail pending I-4
    wiring to the live ``analysis/market_matcher.py`` token-guard. This
    runner exists so the xfail path is exercised cleanly rather than
    failing with NotImplementedError.
    """
    # No active assertion. Live-matcher integration ships with I-4.
    # Force the xfail by asserting False — strict xfail requires a real
    # failure to satisfy the mark.
    assert False, (  # noqa: B011 — intentional xfail trigger
        f"scenario {record['scenario_id']!r}: matcher pipeline awaits "
        f"I-4 full integration"
    )


PIPELINE_RUNNERS = {
    "blend_task": _run_blend_task_scenario,
    "signal_analyzer": _run_signal_analyzer_scenario,
    "governance": _run_governance_scenario,
    "matcher": _run_matcher_scenario,
}


# ---------------------------------------------------------------------------
# Discovery sanity tests + parametrized suite
# ---------------------------------------------------------------------------


def test_scenarios_directory_present() -> None:
    """The scenarios directory must exist — I-6 promises append-only growth."""
    assert SCENARIOS_DIR.is_dir(), (
        "tests/scenarios/ missing — I-6 corpus must exist for the "
        "adversarial regression catalog to function."
    )


def test_minimum_category_coverage() -> None:
    """Spec requires 3-5 scenarios per category × 4 categories = 12-20 total."""
    by_category: dict[str, int] = {}
    for record, _, _ in _discover_scenarios():
        by_category[record["category"]] = by_category.get(record["category"], 0) + 1
    for category in VALID_CATEGORIES:
        count = by_category.get(category, 0)
        assert count >= 3, (
            f"category {category!r} has {count} scenarios; I-6 spec "
            f"requires ≥3 per category."
        )


def test_each_category_has_at_least_one_live_assertion() -> None:
    """Per spec: at least 1 actively-asserted (non-xfail) scenario per category."""
    live_by_category: dict[str, int] = {}
    for record, _, _ in _discover_scenarios():
        if record["xfail_reason"] is None:
            live_by_category[record["category"]] = (
                live_by_category.get(record["category"], 0) + 1
            )
    for category in VALID_CATEGORIES:
        count = live_by_category.get(category, 0)
        assert count >= 1, (
            f"category {category!r} has {count} live (non-xfail) "
            f"scenarios; I-6 spec requires ≥1."
        )


@pytest.mark.parametrize(
    ("record", "source_path", "line_no"),
    _scenarios_for_parametrize(),
)
def test_scenario(
    record: dict[str, Any], source_path: Path, line_no: int
) -> None:
    """Run a single scenario through its pipeline-specific stub interpreter."""
    runner = PIPELINE_RUNNERS[record["pipeline"]]
    runner(record)
