"""Framework v3 readiness integration test (I-13).

Asserts every Phase 1 + Phase 2 deliverable is importable and exposes
its documented public API. Gates the IC §16 amendment merge per
docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
§3 I-13 + §7 Phase 2.

Tests run from repo root. No external dependencies (no DB, no network,
no subprocess). Pure import-time + signature-introspection assertions.

Per spec §3 I-13, this test asserts for each shipped deliverable:
  (a) the module is importable;
  (b) each public entry point exists with the documented signature.

Deferred deliverables (I-7 variance gate, I-11 T1 retrospective) carry
explicit ``pytest.skip`` markers documenting the deferral citation
rather than asserting against unshipped APIs.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _import(module_path: str) -> Any:
    """Import a module by dotted path, surfacing ImportError verbatim."""
    return importlib.import_module(module_path)


def _assert_callable_has_params(
    func: Any, required_params: set[str], *, func_name: str
) -> None:
    """Assert ``func`` is callable and its signature contains ``required_params``.

    We check membership (required params are present) rather than exact-match
    so additive parameter changes (the documented surface keeps growing) do
    not silently break the gate. The "what changed" assertion catches regressions
    where a named parameter is removed or renamed.
    """
    assert callable(func), f"{func_name} is not callable"
    sig = inspect.signature(func)
    present = set(sig.parameters.keys())
    missing = required_params - present
    assert not missing, (
        f"{func_name} signature missing required params: {sorted(missing)}; "
        f"present: {sorted(present)}"
    )


def _assert_dataclass_fields(cls: Any, required_fields: set[str], *, cls_name: str) -> None:
    """Assert ``cls`` is a dataclass with all ``required_fields`` declared."""
    assert dataclasses.is_dataclass(cls), f"{cls_name} is not a dataclass"
    present = {f.name for f in dataclasses.fields(cls)}
    missing = required_fields - present
    assert not missing, (
        f"{cls_name} dataclass missing required fields: {sorted(missing)}; "
        f"present: {sorted(present)}"
    )


def _assert_exception_subclass(cls: Any, base: type, *, cls_name: str) -> None:
    """Assert ``cls`` is a class derived from ``base``."""
    assert inspect.isclass(cls), f"{cls_name} is not a class"
    assert issubclass(cls, base), (
        f"{cls_name} is not a subclass of {base.__name__}; MRO: "
        f"{[c.__name__ for c in cls.__mro__]}"
    )


# Mutable readiness ledger consulted by the roll-up summary test.
_READINESS: dict[str, bool] = {}


def _mark_ready(deliverable: str) -> None:
    _READINESS[deliverable] = True


# --------------------------------------------------------------------------- #
# I-3 — LLM capture
# --------------------------------------------------------------------------- #


def test_i3_llm_capture_readiness() -> None:
    """I-3: capture_llm_response single public entry point."""
    mod = _import("scripts.edge_replay.llm_capture")

    _assert_callable_has_params(
        mod.capture_llm_response,
        {
            "row_id",
            "prompt_template",
            "prompt_filled",
            "model_id",
            "endpoint_type",
            "request_payload",
            "response",
        },
        func_name="capture_llm_response",
    )
    _mark_ready("I-3")


# --------------------------------------------------------------------------- #
# I-6 — scenario suite
# --------------------------------------------------------------------------- #


def test_i6_scenario_suite_readiness() -> None:
    """I-6: at least 4 scenario JSONL files + test_scenario_suite module importable."""
    scenarios_dir = REPO_ROOT / "tests" / "scenarios"
    assert scenarios_dir.is_dir(), f"missing scenarios dir: {scenarios_dir}"
    jsonl_files = sorted(scenarios_dir.glob("*.jsonl"))
    assert len(jsonl_files) >= 4, (
        f"expected >=4 scenario JSONL files (one per category); "
        f"found {len(jsonl_files)}: {[p.name for p in jsonl_files]}"
    )

    # Module must import (test collection sanity).
    suite_mod = _import("tests.test_scenario_suite")
    assert suite_mod is not None
    _mark_ready("I-6")


# --------------------------------------------------------------------------- #
# I-7 — variance gate (DEFERRED to Phase 4)
# --------------------------------------------------------------------------- #


def test_i7_variance_gate_deferred() -> None:
    """I-7 is deferred to Phase 4 per framework v3 §7."""
    pytest.skip(
        "I-7 (variance gate) deferred to Phase 4 per "
        "docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md §7"
    )


# --------------------------------------------------------------------------- #
# I-8 — wave-1 automated closure
# --------------------------------------------------------------------------- #


def test_i8_wave1_closure_readiness() -> None:
    """I-8: tests/conftest.py exposes a callable pytest_collection_modifyitems hook."""
    conftest = _import("tests.conftest")
    hook = getattr(conftest, "pytest_collection_modifyitems", None)
    assert callable(hook), (
        "tests/conftest.py must expose a callable pytest_collection_modifyitems hook"
    )
    sig = inspect.signature(hook)
    params = set(sig.parameters.keys())
    # pytest hook contract requires both `config` and `items`.
    assert {"config", "items"}.issubset(params), (
        f"pytest_collection_modifyitems missing required pytest params; "
        f"present: {sorted(params)}"
    )
    _mark_ready("I-8")


# --------------------------------------------------------------------------- #
# I-10 — contamination corpus manager
# --------------------------------------------------------------------------- #


def test_i10_contamination_marker_readiness() -> None:
    """I-10: open/close window + cohort tagging + export + dataclass + error type."""
    mod = _import("scripts.edge_replay.contamination_corpus_manager")

    _assert_callable_has_params(
        mod.get_active_window,
        {"sentinel_path"},
        func_name="get_active_window",
    )
    _assert_callable_has_params(
        mod.open_window,
        {"change_id", "duration_seconds", "sentinel_path"},
        func_name="open_window",
    )
    _assert_callable_has_params(
        mod.close_active_window,
        {"sentinel_path"},
        func_name="close_active_window",
    )
    _assert_callable_has_params(
        mod.cohort_extension_tag,
        {"window"},
        func_name="cohort_extension_tag",
    )
    _assert_callable_has_params(
        mod.export_contamination_corpus,
        {"change_id", "paper_trades_db", "output_path"},
        func_name="export_contamination_corpus",
    )
    _assert_dataclass_fields(
        mod.ContaminationWindow,
        {"change_id", "window_id", "opened_at_utc", "closes_at_utc"},
        cls_name="ContaminationWindow",
    )
    _assert_exception_subclass(
        mod.ContaminationWindowAlreadyOpenError,
        RuntimeError,
        cls_name="ContaminationWindowAlreadyOpenError",
    )
    _mark_ready("I-10")


# --------------------------------------------------------------------------- #
# I-11 — T1 retrospective (DEFERRED to Phase 3 activation)
# --------------------------------------------------------------------------- #


def test_i11_t1_retrospective_deferred() -> None:
    """I-11 is deferred to Phase 3 activation per framework v3 §7."""
    pytest.skip(
        "I-11 (T1 retrospective) deferred to Phase 3 activation per "
        "docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md §7"
    )


# --------------------------------------------------------------------------- #
# Summary roll-up
# --------------------------------------------------------------------------- #


def test_framework_readiness_summary() -> None:
    """Roll-up: all non-deferred deliverables passed readiness.

    Per framework v3 §7 Phase 2: passing this test in CI is the precondition
    for merging the IC §16 amendment. The summary asserts each non-deferred
    deliverable's readiness sentinel was recorded by the per-deliverable
    tests above.

    Order assumption (per python-reviewer M1): pytest collects tests in
    file order by default. The summary relies on every per-deliverable
    test running BEFORE this summary. If a future run uses
    ``pytest-randomly`` or ``--randomly-seed``, the summary may report
    false-missing sentinels. Mitigation if/when that happens: add
    ``@pytest.mark.order(last=True)`` here via the ``pytest-ordering``
    plugin, or restructure the summary to re-introspect each module
    inline instead of reading shared state.

    Missing-vs-failed-vs-skipped (per python-reviewer M2): a missing
    sentinel means the per-deliverable test EITHER failed before its
    ``_mark_ready`` call OR was skipped by a plugin / xfail. The error
    message below cannot distinguish the two — operators must scroll
    up to the per-deliverable test's status to disambiguate.
    """
    expected = {
        "I-3",
        "I-6",
        "I-8",
        "I-10",
    }
    missing = expected - set(_READINESS.keys())
    assert not missing, (
        f"framework v3 readiness sentinels not recorded for: {sorted(missing)}. "
        f"IC §16 amendment merge remains BLOCKED. "
        f"(A missing sentinel means the per-deliverable test either failed "
        f"or was skipped — check the per-deliverable status above.)"
    )
    # All recorded sentinels must be True.
    failed = {k for k, v in _READINESS.items() if not v}
    assert not failed, (
        f"framework v3 readiness sentinels recorded but failed: {sorted(failed)}. "
        f"IC §16 amendment merge remains BLOCKED."
    )
