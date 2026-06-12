"""Tests for the I-5 tier classifier with semantic scope.

Reference: docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md
§2 (Risk Tier Matrix) and §3 row I-5.

Test discipline: each assertion encodes WHY the behavior matters per
~/.claude/rules/validation.md — a test that cannot fail when the underlying
business logic changes is not a test.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from scripts.edge_replay.tier_classifier import (
    PATH_TIER_RULES,
    classify_tier,
    tier_of_path,
)


# ---------------------------------------------------------------------------
# Path-based reduction
# ---------------------------------------------------------------------------


def test_empty_inputs_default_to_t3(tmp_path, monkeypatch):
    """No changed paths + no semantic diffs → T3 (fail-safe direction).

    Per §2 closing rule: "When in doubt, route to T3." An empty input set is
    the most extreme form of doubt — we have zero evidence about scope.
    Defaulting to T0 here would silently un-gate any future caller that
    forgets to pass changed_paths.
    """
    monkeypatch.chdir(tmp_path)
    assert classify_tier([]) == "T3"


def test_single_t0_path_returns_t0(tmp_path, monkeypatch):
    """Pure docs change → T0. Required for the T0-budget cap (§6.1) to
    correctly identify low-risk commits."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("docs/foo.md")]) == "T0"


def test_single_t1_path_returns_t1(tmp_path, monkeypatch):
    """analysis/market_matcher.py is the canonical T1 path (paper-mode
    behavioral, replay-decidable per §2). Misclassifying this as T0 would
    bypass the replay-as-CI gate (§16.7)."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("analysis/market_matcher.py")]) == "T1"


def test_single_t2_path_returns_t2(tmp_path, monkeypatch):
    """governance/prompts.py changes are T2 (replay-indeterminate per v3
    §3 I-5). Cached LLM outputs cannot be reused after prompt edits, so the
    change cannot be gated by existing replay corpora alone."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("governance/prompts.py")]) == "T2"


def test_single_t3_path_returns_t3(tmp_path, monkeypatch):
    """trading/executor.py owns Kelly logic, hard-cap enforcement, and
    bankroll mutation. Misclassifying this below T3 would skip the IC §16
    full-rigor gate on capital-affecting changes."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("trading/executor.py")]) == "T3"


def test_max_wins_t0_plus_t1_returns_t1(tmp_path, monkeypatch):
    """§2 max-wins rule (v2 STAMP, OVERRIDE NOT ALLOWED): the highest tier
    across changed_paths wins. Mixed T0+T1 PRs must still trigger the T1
    gate — otherwise an attacker / careless contributor could bury a T1
    behavioral change inside a docs commit."""
    monkeypatch.chdir(tmp_path)
    paths = [Path("docs/notes.md"), Path("analysis/market_matcher.py")]
    assert classify_tier(paths) == "T1"


def test_max_wins_t1_plus_t3_returns_t3(tmp_path, monkeypatch):
    """A behavioral edit bundled with a sizing-formula edit must route to T3.
    Per §2 the safety invariant is that the highest tier wins."""
    monkeypatch.chdir(tmp_path)
    paths = [Path("analysis/decision_blender.py"), Path("trading/executor.py")]
    assert classify_tier(paths) == "T3"


def test_max_wins_t0_plus_t2_returns_t2(tmp_path, monkeypatch):
    """Prompt edits bundled with docs must still trip the T2 replay-
    indeterminate gate; otherwise prompt drift sneaks through under cover
    of a docs commit."""
    monkeypatch.chdir(tmp_path)
    paths = [Path("README.md"), Path("governance/prompts.py")]
    assert classify_tier(paths) == "T2"


def test_unknown_path_returns_t3(tmp_path, monkeypatch):
    """§2 closing rule + §3 I-5: 'Unknown paths default to T3.' Without this
    invariant a new top-level directory ships with zero gating."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("brand_new_directory/some_module.py")]) == "T3"


# ---------------------------------------------------------------------------
# Semantic-scope reduction (v3 STAMP per Codex blocker B)
# ---------------------------------------------------------------------------


def test_config_diff_forces_t3(tmp_path, monkeypatch):
    """Per §3 I-5 v3 semantic scope: any unclassified config/env edit →
    T3. A config edit that quietly changes a sizing constant must not be
    gated only as T0 just because the file path happens to be T0-listed."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("docs/foo.md")], config_diff=True) == "T3"


def test_prompt_template_diff_forces_t3(tmp_path, monkeypatch):
    """Per §3 I-5 v3: prompt-template diffs route to T3 because the cache
    key (I-2 v3 13-field tuple) is invalidated; existing replay corpora
    cannot gate the change. Until the classifier can definitively map
    prompts to T2 with cache-coverage evidence (future work), T3 is the
    fail-safe."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("docs/foo.md")], prompt_template_diff=True) == "T3"


def test_model_manifest_diff_forces_t3(tmp_path, monkeypatch):
    """Per §3 I-5 v3: model_digest pin changes or ollama version bumps
    invalidate cache determinism (Codex blocker D) and route to T3."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier([Path("docs/foo.md")], model_manifest_diff=True) == "T3"


def test_schema_migration_forces_t3(tmp_path, monkeypatch):
    """Per §3 I-5 v3: DB schema migrations are runtime-affecting artifacts
    that the classifier cannot definitively map to a lower tier. Default
    to T3."""
    monkeypatch.chdir(tmp_path)
    migration = Path("data/migrations/0001_add_cohort_field.sql")
    assert classify_tier([Path("docs/foo.md")], schema_migrations=[migration]) == "T3"


def test_semantic_diff_overrides_lower_path_tier(tmp_path, monkeypatch):
    """Even with a pure T0 path set, any truthy semantic signal forces T3.
    The two reduction layers are unioned, not min'd — per §2 v3 STAMP the
    semantic detection is the outer step and path max-wins is the inner."""
    monkeypatch.chdir(tmp_path)
    result = classify_tier(
        [Path("docs/x.md"), Path("README.md")],
        config_diff=True,
    )
    assert result == "T3"


# ---------------------------------------------------------------------------
# Logging side-effect contract
# ---------------------------------------------------------------------------


def _read_jsonl_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def test_logging_writes_jsonl_with_full_fingerprint(tmp_path, monkeypatch):
    """§6.5 + §3 I-5: 'Append-only ledger at
    logs/edge_replay/tier_classifications.jsonl records the full input
    fingerprint, not just file paths.' Without this audit trail, post-hoc
    review of tier classifications is impossible."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs" / "edge_replay").mkdir(parents=True)
    paths = [Path("analysis/market_matcher.py"), Path("tests/test_market_matcher.py")]
    classify_tier(paths)

    log_path = tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl"
    assert log_path.exists(), "audit log must be created on first classify call"

    lines = _read_jsonl_lines(log_path)
    assert len(lines) == 1
    entry = lines[0]
    # ts_utc must be ISO-8601 UTC ('Z' suffix) per ~/.claude/rules/portability.md
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", entry["ts_utc"])
    assert entry["ts_utc"].endswith("Z")
    # The fingerprint must capture every input axis the classifier branches on,
    # so an auditor can reconstruct WHY a tier was assigned.
    assert entry["changed_paths"] == [
        "analysis/market_matcher.py",
        "tests/test_market_matcher.py",
    ]
    assert entry["config_diff"] is False
    assert entry["prompt_template_diff"] is False
    assert entry["model_manifest_diff"] is False
    assert entry["schema_migrations"] == []
    assert entry["classified_tier"] == "T1"
    assert entry["max_tier_path"] == "analysis/market_matcher.py"
    assert entry["rule_matched"].startswith("path:")


def test_logging_is_append_only(tmp_path, monkeypatch):
    """Successive classifications must NOT truncate the ledger. A
    truncating writer would silently destroy prior audit evidence."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs" / "edge_replay").mkdir(parents=True)
    classify_tier([Path("docs/a.md")])
    classify_tier([Path("trading/executor.py")])

    log_path = tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl"
    lines = _read_jsonl_lines(log_path)
    assert len(lines) == 2
    assert lines[0]["classified_tier"] == "T0"
    assert lines[1]["classified_tier"] == "T3"


def test_logging_failure_does_not_raise(tmp_path, monkeypatch):
    """Per spec: 'If write fails (e.g., disk full), log a warning and
    continue — classification result is still returned.' The classifier is
    a routing primitive on the deploy path; throwing on an audit-log write
    failure would convert an audit-trail incident into a deploy-pipeline
    outage."""
    monkeypatch.chdir(tmp_path)
    # Pre-create the audit-log target as a DIRECTORY so open(..., 'a') fails
    # with IsADirectoryError. This simulates a corrupted log mount without
    # needing root permissions or a real read-only FS.
    bogus = tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl"
    bogus.parent.mkdir(parents=True)
    bogus.mkdir()  # path now resolves to a dir, not a file
    # Must not raise; classification result still returned.
    assert classify_tier([Path("trading/executor.py")]) == "T3"


# ---------------------------------------------------------------------------
# Path-normalization correctness
# ---------------------------------------------------------------------------


def test_tier_of_path_handles_string_and_path(tmp_path, monkeypatch):
    """Boundary contract: callers may pass either str or Path. Mishandling
    str at the boundary would produce false T3 classifications on tooling
    that lists changed files as strings (git diff --name-only)."""
    monkeypatch.chdir(tmp_path)
    assert tier_of_path("analysis/market_matcher.py") == "T1"
    assert tier_of_path(Path("analysis/market_matcher.py")) == "T1"


def test_tier_of_path_handles_absolute_paths(tmp_path, monkeypatch):
    """Git tooling often emits repo-relative paths but operators run
    classifications from absolute paths. Absolute paths matching the
    repo-rooted patterns must classify identically — otherwise the same
    file gets two different tiers depending on how it was invoked."""
    monkeypatch.chdir(tmp_path)
    repo_root = tmp_path
    abs_path = repo_root / "trading" / "executor.py"
    # Path matching is done by repo-relative suffix matching; the helper
    # must reduce absolute paths back to their repo-relative form.
    assert tier_of_path(abs_path, repo_root=repo_root) == "T3"


def test_tier_of_path_absolute_path_outside_repo_root_returns_t3(tmp_path):
    """An absolute path that does NOT live under the configured repo_root
    cannot be reduced to a repo-relative path and so matches no rule.
    The classifier must fail-safe to T3 in that branch (per project rule
    'when in doubt, route to T3'). Without this test, the first time the
    branch fires in production — e.g. a tooling bug passes a stray /tmp
    path — the classification would silently default but the gap would
    not have been exercised at build time, weakening the fail-safe
    guarantee for the I-13 framework readiness integration test."""
    outside_abs = Path("/tmp/totally_outside_the_repo/some_module.py")
    assert tier_of_path(outside_abs, repo_root=tmp_path) == "T3"


def test_tier_of_path_specific_known_files(tmp_path, monkeypatch):
    """Spot-check the canonical mappings from §2 / §3 I-5 to catch
    accidental rule reordering. Each entry here corresponds to a load-
    bearing CLAUDE.md gotcha or a specific tier-row example."""
    monkeypatch.chdir(tmp_path)
    # T0
    assert tier_of_path("scripts/bothealth.sh") == "T0"
    assert tier_of_path("scripts/launchd/foo.plist.template") == "T0"
    assert tier_of_path(".githooks/pre-commit") == "T0"
    assert tier_of_path("CHANGELOG.md") == "T0"
    assert tier_of_path("VERSION") == "T0"
    assert tier_of_path("tests/test_anything.py") == "T0"
    # T1
    assert tier_of_path("analysis/signal_analyzer.py") == "T1"
    # PROFIT-EDGE-014: the rule previously named analysis/blender.py, which
    # does not exist; the real blender file is decision_blender.py.
    assert tier_of_path("analysis/decision_blender.py") == "T1"
    assert tier_of_path("analysis/evidence_scorer.py") == "T1"
    assert tier_of_path("tasks/trade_readiness_gate.py") == "T1"
    assert tier_of_path("feeds/reddit_source.py") == "T1"
    # T2
    assert tier_of_path("governance/prompts.py") == "T2"
    # T3
    assert tier_of_path("trading/executor.py") == "T3"
    assert tier_of_path("trading/paper_trader.py") == "T3"
    assert tier_of_path("config.py") == "T3"


# ---------------------------------------------------------------------------
# Constants surface (operator-readable mapping)
# ---------------------------------------------------------------------------


def test_path_tier_rules_constant_is_introspectable(tmp_path):
    """§3 I-5 + spec: 'Document the per-tier allowlist + denylist explicitly
    in the module docstring AND as module-level dicts/sets so tests can
    introspect them. This is the operator-readable mapping.' Without this
    the only way to audit the routing table is reading function bodies."""
    assert isinstance(PATH_TIER_RULES, list)
    assert len(PATH_TIER_RULES) > 0
    # Every rule must declare a tier and a matcher representation; otherwise
    # introspection tooling cannot render an operator-readable table.
    for rule in PATH_TIER_RULES:
        assert "tier" in rule
        assert rule["tier"] in {"T0", "T1", "T2", "T3"}
        assert "pattern" in rule


def test_relative_path_str_input_classifies(tmp_path, monkeypatch):
    """End-to-end check that passing strings (the git diff --name-only
    output shape) through the top-level classify_tier API works without
    explicit Path() conversion. Many CI tooling layers will hand us raw
    strings."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier(["analysis/market_matcher.py"]) == "T1"  # type: ignore[list-item]


def test_classify_tier_does_not_mutate_inputs(tmp_path, monkeypatch):
    """A routing primitive must not mutate caller state. Mutating
    changed_paths would surprise downstream code that re-uses the list
    (e.g., for logging or commit-message generation)."""
    monkeypatch.chdir(tmp_path)
    paths = [Path("analysis/market_matcher.py"), Path("docs/foo.md")]
    snapshot = list(paths)
    classify_tier(paths)
    assert paths == snapshot


def test_default_log_path_uses_repo_relative_logs_dir(tmp_path, monkeypatch):
    """When invoked from the repo root, the audit log MUST land at
    logs/edge_replay/tier_classifications.jsonl per §6.5. Any other path
    breaks the operator's ability to find the ledger and breaks any
    downstream tooling that consumes it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs" / "edge_replay").mkdir(parents=True)
    classify_tier([Path("analysis/market_matcher.py")])
    assert (tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl").exists()


@pytest.mark.parametrize(
    "paths,expected",
    [
        ([Path("scripts/install.sh")], "T0"),
        ([Path("scripts/governance_monitor.py")], "T0"),
        ([Path(".github/workflows/ci.yml")], "T0"),
        ([Path("feeds/something_new.py")], "T1"),
        ([Path("tasks/blend_task.py")], "T1"),
        ([Path("trading/paper_trader.py")], "T3"),
    ],
)
def test_parametrized_canonical_path_mappings(tmp_path, monkeypatch, paths, expected):
    """Locks down the operator-visible routing table. If any of these
    flip, the framework's deploy-gating behavior changes silently."""
    monkeypatch.chdir(tmp_path)
    assert classify_tier(paths) == expected


def test_self_classification_not_logged_as_t0(tmp_path, monkeypatch):
    """Operator spec: 'DO NOT auto-classify Phase 1 / I-5 itself as T0 in
    the audit log — the classification is for future inputs, not its own
    creation.' This is enforced by NOT invoking classify_tier during
    module import. The smoke check here just confirms the module is
    importable without side effects."""
    monkeypatch.chdir(tmp_path)
    # Importing tier_classifier must not have created a log file.
    expected_log = tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl"
    assert not expected_log.exists(), (
        "tier_classifier module import must not write to the audit log"
    )
    # Tracker: also confirm os.getcwd is the tmp_path (test-isolation
    # sanity check; if this fails subsequent assertions are meaningless).
    assert os.getcwd() == str(tmp_path)


def test_classify_tier_write_ledger_false_skips_audit_log(tmp_path, monkeypatch):
    """T0-budget check (final Phase 2 deliverable) replays classification
    across historical commits as a read-only reporting exercise. Per
    python-reviewer concern #1: those replay calls must NOT pollute the
    runtime ledger with phantom entries indistinguishable from live
    decisions. write_ledger=False suppresses the audit-log write while
    preserving the returned tier."""
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / "logs" / "edge_replay" / "tier_classifications.jsonl"

    # write_ledger=True (default) → log written.
    tier_true = classify_tier(["analysis/market_matcher.py"])
    assert tier_true == "T1"
    assert log_path.exists(), "default write_ledger=True must write"
    lines_after_true = len(log_path.read_text().splitlines())

    # write_ledger=False → no new lines.
    tier_false = classify_tier(
        ["analysis/market_matcher.py"], write_ledger=False
    )
    assert tier_false == "T1", "classification result unchanged by write_ledger"
    lines_after_false = len(log_path.read_text().splitlines())
    assert lines_after_false == lines_after_true, (
        "write_ledger=False must not append to the audit log"
    )


def test_decision_blender_classifies_t1_not_unknown_t3():
    """PROFIT-EDGE-014 review finding: the rule table named analysis/blender.py
    (nonexistent), so the REAL blender file fell through to the unknown-path T3
    fail-safe and blender PRs were misrouted (same class as the tasks/stats
    false positive). The classifier docstring places the blender in T1."""
    from pathlib import Path
    assert tier_of_path(Path("analysis/decision_blender.py")) == "T1"
