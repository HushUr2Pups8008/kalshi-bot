"""Framework v3 readiness integration test (I-13).

Asserts every Phase 1 + Phase 2 deliverable is importable and exposes
its documented public API. Gates the IC §16 amendment merge per
docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
§3 I-13 + §7 Phase 2.

Tests run from repo root. No external dependencies (no DB, no network,
no subprocess). Pure import-time + signature-introspection assertions.

Per spec §3 I-13, this test asserts for each shipped deliverable:
  (a) the module is importable;
  (b) each public entry point exists with the documented signature;
  (c) the I-5 tier classifier returns T3 for empty/unknown inputs
      (fail-safe verification).

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
# I-1 — corpus builder
# --------------------------------------------------------------------------- #


def test_i1_corpus_builder_readiness() -> None:
    """I-1: build_corpus + BuildResult + UnregisteredRegimeError + load_registered_regimes."""
    mod = _import("scripts.edge_replay.build_corpus")

    _assert_callable_has_params(
        mod.build_corpus,
        {
            "start_utc",
            "end_utc",
            "market_families",
            "cohort_tag",
            "regime_label",
            "output_path",
            "include_contamination",
        },
        func_name="build_corpus",
    )
    _assert_dataclass_fields(
        mod.BuildResult,
        {
            "output_path",
            "row_count",
            "corpus_window_start_utc",
            "corpus_window_end_utc",
            "cohort_tag",
            "regime_label",
            "in_period_validation_only",
        },
        cls_name="BuildResult",
    )
    _assert_exception_subclass(
        mod.UnregisteredRegimeError, ValueError, cls_name="UnregisteredRegimeError"
    )
    _assert_callable_has_params(
        mod.load_registered_regimes,
        {"doc_path"},
        func_name="load_registered_regimes",
    )
    _mark_ready("I-1")


# --------------------------------------------------------------------------- #
# I-2 — LLM replay cache
# --------------------------------------------------------------------------- #


def test_i2_llm_cache_readiness() -> None:
    """I-2: LLMReplayCache class + cache key/hit/coverage dataclasses + error types + helpers."""
    mod = _import("scripts.edge_replay.llm_cache")

    # LLMReplayCache is the primary cache façade — instantiable with no args.
    assert inspect.isclass(mod.LLMReplayCache), "LLMReplayCache is not a class"
    init_sig = inspect.signature(mod.LLMReplayCache)
    assert "db_path" in init_sig.parameters, "LLMReplayCache(__init__) missing db_path"

    _assert_exception_subclass(mod.CacheMissError, Exception, cls_name="CacheMissError")
    _assert_exception_subclass(
        mod.CacheCorruptionError, Exception, cls_name="CacheCorruptionError"
    )

    _assert_dataclass_fields(
        mod.CacheKey,
        {
            "row_id",
            "prompt_template_hash",
            "prompt_filled_hash",
            "model_id",
            "endpoint_type",
            "seed",
            "temperature",
        },
        cls_name="CacheKey",
    )
    _assert_dataclass_fields(
        mod.CacheHit,
        {"key", "response", "captured_at_utc", "repeat_verification"},
        cls_name="CacheHit",
    )
    _assert_dataclass_fields(
        mod.CoverageReport,
        {
            "total_rows",
            "cache_hits",
            "cache_misses",
            "coverage_ratio",
            "threshold",
            "passes_threshold",
            "missing_row_ids",
        },
        cls_name="CoverageReport",
    )

    _assert_callable_has_params(
        mod.compute_cache_coverage,
        {"corpus_rows", "cache", "threshold"},
        func_name="compute_cache_coverage",
    )
    _assert_callable_has_params(
        mod.migrate_jsonl_to_sqlite,
        {"jsonl_path", "db_path", "overwrite"},
        func_name="migrate_jsonl_to_sqlite",
    )
    _mark_ready("I-2")


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
# I-4 — replay-as-CI gate
# --------------------------------------------------------------------------- #


def test_i4_replay_gate_readiness() -> None:
    """I-4: run_replay_gate + GateSpec/Verdict/Rule4Table dataclasses + GateError hierarchy."""
    mod = _import("scripts.edge_replay.replay_gate")

    _assert_callable_has_params(
        mod.run_replay_gate,
        {"changed_files", "corpora", "gate_spec"},
        func_name="run_replay_gate",
    )
    _assert_dataclass_fields(
        mod.GateSpec,
        {
            "min_trades",
            "min_ev_ci_95_lo",
            "coverage_threshold",
            "require_scenarios_pass",
            "allow_in_period_only",
        },
        cls_name="GateSpec",
    )
    _assert_dataclass_fields(
        mod.GateVerdict,
        {
            "pass_",
            "tier",
            "rule4",
            "coverage_report_path",
            "rule4_table_path",
            "scenario_report_path",
        },
        cls_name="GateVerdict",
    )
    _assert_dataclass_fields(
        mod.Rule4Table,
        {
            "trade_count",
            "win_rate",
            "per_trade_ev",
            "ev_ci_95_lo",
            "ev_ci_95_hi",
            "realized_pnl",
            "sharpe",
            "window_start_utc",
            "window_end_utc",
            "corpora_used",
        },
        cls_name="Rule4Table",
    )
    _assert_exception_subclass(mod.GateError, Exception, cls_name="GateError")
    _assert_exception_subclass(
        mod.InsufficientCorpusError, mod.GateError, cls_name="InsufficientCorpusError"
    )
    _assert_exception_subclass(
        mod.CacheCoverageError, mod.GateError, cls_name="CacheCoverageError"
    )
    _assert_exception_subclass(
        mod.ScenarioFailureError, mod.GateError, cls_name="ScenarioFailureError"
    )
    _mark_ready("I-4")


# --------------------------------------------------------------------------- #
# I-5 — tier classifier
# --------------------------------------------------------------------------- #


def test_i5_tier_classifier_readiness() -> None:
    """I-5: classify_tier + tier_of_path + PATH_TIER_RULES."""
    mod = _import("scripts.edge_replay.tier_classifier")

    _assert_callable_has_params(
        mod.classify_tier,
        {
            "changed_paths",
            "config_diff",
            "prompt_template_diff",
            "model_manifest_diff",
            "schema_migrations",
        },
        func_name="classify_tier",
    )
    _assert_callable_has_params(
        mod.tier_of_path,
        {"path"},
        func_name="tier_of_path",
    )
    assert isinstance(mod.PATH_TIER_RULES, list), "PATH_TIER_RULES must be a list"
    assert mod.PATH_TIER_RULES, "PATH_TIER_RULES must be non-empty"
    _mark_ready("I-5")


def test_i5_classifier_fail_safe_returns_t3() -> None:
    """I-5 spec §3(c): empty inputs + no semantic signals must yield T3 (fail-safe)."""
    from scripts.edge_replay.tier_classifier import classify_tier

    assert classify_tier([], False, False, False, []) == "T3"
    _mark_ready("I-5-failsafe")


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
# I-12 — cache integrity scan
# --------------------------------------------------------------------------- #


def test_i12_cache_integrity_readiness() -> None:
    """I-12: scan_integrity + ScanReport dataclass + CachePoisonedError on llm_cache."""
    mod = _import("scripts.edge_replay.llm_cache")

    _assert_exception_subclass(
        mod.CachePoisonedError, Exception, cls_name="CachePoisonedError"
    )
    _assert_callable_has_params(
        mod.scan_integrity,
        {"db_path", "mark_poisoned"},
        func_name="scan_integrity",
    )
    _assert_dataclass_fields(
        mod.ScanReport,
        {
            "total_rows",
            "integrity_mismatches",
            "already_poisoned",
            "newly_poisoned",
            "repeat_verification_nondeterministic",
            "scan_duration_ms",
        },
        cls_name="ScanReport",
    )
    _mark_ready("I-12")


# --------------------------------------------------------------------------- #
# Summary roll-up
# --------------------------------------------------------------------------- #


def test_framework_readiness_summary() -> None:
    """Roll-up: all non-deferred deliverables passed readiness.

    Per framework v3 §7 Phase 2: passing this test in CI is the precondition
    for merging the IC §16 amendment. The summary asserts each non-deferred
    deliverable's readiness sentinel was recorded by the per-deliverable
    tests above.
    """
    expected = {
        "I-1",
        "I-2",
        "I-3",
        "I-4",
        "I-5",
        "I-5-failsafe",
        "I-6",
        "I-8",
        "I-10",
        "I-12",
    }
    missing = expected - set(_READINESS.keys())
    assert not missing, (
        f"framework v3 readiness sentinels not recorded for: {sorted(missing)}. "
        f"IC §16 amendment merge remains BLOCKED."
    )
    # All recorded sentinels must be True.
    failed = {k for k, v in _READINESS.items() if not v}
    assert not failed, (
        f"framework v3 readiness sentinels recorded but failed: {sorted(failed)}. "
        f"IC §16 amendment merge remains BLOCKED."
    )
