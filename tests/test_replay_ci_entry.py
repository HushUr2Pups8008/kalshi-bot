"""Tests for `scripts/edge_replay/ci_entry.py` (Phase 3 activation).

The CI entry script is the single integration point between
`.github/workflows/replay-ci-gate.yml` and `run_replay_gate`. These
tests pin:

  - semantic-flag detection (which file paths trigger config/prompt/
    model-manifest/schema-migration flags into the I-5 tier classifier)
  - operator override mechanism (REPLAY_GATE_OVERRIDE=1 forces exit 0)
  - exit code mapping (pass → 0, fail → 1, override → 0)
"""
from __future__ import annotations

from pathlib import Path


from scripts.edge_replay.ci_entry import (
    _detect_semantic_diffs,
    main,
)


class TestSemanticDiffDetection:
    """`_detect_semantic_diffs` derives the I-5 semantic-scope flags from
    a changed-file list. Load-bearing for correct tier classification."""

    def test_config_py_change_sets_config_diff(self):
        flags = _detect_semantic_diffs([Path("config.py")])
        assert flags["config_diff"] is True
        assert flags["prompt_template_diff"] is False
        assert flags["model_manifest_diff"] is False
        assert flags["schema_migrations"] == []

    def test_governance_prompts_py_sets_prompt_template_diff(self):
        flags = _detect_semantic_diffs([Path("governance/prompts.py")])
        assert flags["prompt_template_diff"] is True

    def test_any_filename_containing_prompt_triggers_prompt_diff(self):
        flags = _detect_semantic_diffs([Path("analysis/signal_prompt.py")])
        assert flags["prompt_template_diff"] is True

    def test_model_registry_json_sets_model_manifest_diff(self):
        flags = _detect_semantic_diffs([Path("model_registry.json")])
        assert flags["model_manifest_diff"] is True

    def test_schema_migration_file_path_is_collected(self):
        flags = _detect_semantic_diffs([Path("schema/0042_add_column.sql")])
        assert len(flags["schema_migrations"]) == 1

    def test_migration_in_filename_is_collected(self):
        flags = _detect_semantic_diffs([Path("analysis/migration_helper.py")])
        assert any(
            "migration" in p.name.lower() for p in flags["schema_migrations"]
        )

    def test_no_special_files_means_all_flags_false(self):
        flags = _detect_semantic_diffs([
            Path("analysis/market_matcher.py"),
            Path("tests/test_market_matcher.py"),
            Path("CHANGELOG.md"),
        ])
        assert flags["config_diff"] is False
        assert flags["prompt_template_diff"] is False
        assert flags["model_manifest_diff"] is False
        assert flags["schema_migrations"] == []


class TestOperatorOverride:
    """Operator override forces exit 0 regardless of verdict. Pin per
    IC §16 §16.7 — override decisions are logged and reviewed at merge."""

    def _patch_gate_to_fail(self):
        """Return a mock that makes `run_replay_gate` produce a failing verdict."""
        from scripts.edge_replay.replay_gate import GateVerdict
        fake = GateVerdict(
            pass_=False,
            tier="T1",
            rule4=None,
            coverage_report_path=Path("/tmp/cov.json"),
            rule4_table_path=Path("/tmp/rule4.json"),
            scenario_report_path=Path("/tmp/scen.txt"),
            notes=["forced-fail-for-test"],
            failure_reason="forced-fail-for-test",
            output_dir=Path("/tmp"),
        )
        return fake

    def test_override_forces_exit_zero_on_failing_verdict(self, monkeypatch):
        fake_verdict = self._patch_gate_to_fail()
        monkeypatch.setenv("REPLAY_GATE_OVERRIDE", "1")
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry.run_replay_gate",
            lambda **kw: fake_verdict,
        )
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry._git_changed_files",
            lambda *a, **kw: [Path("analysis/market_matcher.py")],
        )
        exit_code = main(["--base", "origin/main"])
        assert exit_code == 0, (
            "REPLAY_GATE_OVERRIDE=1 must force exit 0 even when the "
            "verdict pass_=False"
        )

    def test_no_override_propagates_failing_verdict(self, monkeypatch):
        fake_verdict = self._patch_gate_to_fail()
        monkeypatch.delenv("REPLAY_GATE_OVERRIDE", raising=False)
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry.run_replay_gate",
            lambda **kw: fake_verdict,
        )
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry._git_changed_files",
            lambda *a, **kw: [Path("analysis/market_matcher.py")],
        )
        exit_code = main(["--base", "origin/main"])
        assert exit_code == 1, (
            "without override, a failing verdict must propagate exit 1 "
            "to fail the CI check"
        )

    def test_print_only_mode_always_exits_zero(self, monkeypatch):
        fake_verdict = self._patch_gate_to_fail()
        monkeypatch.delenv("REPLAY_GATE_OVERRIDE", raising=False)
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry.run_replay_gate",
            lambda **kw: fake_verdict,
        )
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry._git_changed_files",
            lambda *a, **kw: [Path("analysis/market_matcher.py")],
        )
        exit_code = main(["--print-only"])
        assert exit_code == 0, (
            "--print-only is a debug mode and must always exit 0"
        )


class TestPassingVerdictPropagates:
    """Passing verdict + no override → exit 0."""

    def test_passing_verdict_exit_zero(self, monkeypatch):
        from scripts.edge_replay.replay_gate import GateVerdict
        fake = GateVerdict(
            pass_=True,
            tier="T0",
            rule4=None,
            coverage_report_path=Path("/tmp/cov.json"),
            rule4_table_path=Path("/tmp/rule4.json"),
            scenario_report_path=Path("/tmp/scen.txt"),
            notes=["T0: Rule 2 exempt"],
            failure_reason=None,
            output_dir=Path("/tmp"),
        )
        monkeypatch.delenv("REPLAY_GATE_OVERRIDE", raising=False)
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry.run_replay_gate",
            lambda **kw: fake,
        )
        monkeypatch.setattr(
            "scripts.edge_replay.ci_entry._git_changed_files",
            lambda *a, **kw: [Path("CHANGELOG.md")],
        )
        exit_code = main(["--base", "origin/main"])
        assert exit_code == 0
