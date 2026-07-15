"""Smoke tests for I-4 ``scripts/edge_replay/replay_gate.py``.

Covers the tier-aware gating contract, corpus auto-discovery, cache
coverage failure, scenario failure, Rule 4 EV computation, output files,
CLI exit codes, and commit-sha derivation.

External systems are mocked:
  * ``scenario_runner`` injects a fake pytest invocation.
  * ``cache_factory`` injects an in-memory cache stub satisfying the
    coverage-check API.
  * Tests run in tmp_path with monkeypatched cwd to keep real
    ``logs/edge_replay/`` clean.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

from scripts.edge_replay.llm_cache import CacheKey
from scripts.edge_replay.oos_registry import canonical_registration_hash
from scripts.edge_replay.replay_gate import (
    GateError,
    GateSpec,
    _build_rule4_table,
    _scan_corpus_diversity,
    main,
    run_replay_gate,
)


_TEST_REGISTRATION = {
    "id": "test-oos-window",
    "declared_at_utc": "2026-04-01T00:00:00Z",
    "window": {
        "start_utc": "2026-05-01T00:00:00Z",
        "end_utc": "2026-05-08T00:00:00Z",
    },
    "regime_label": "test-oos-regime",
    "universe_policy": {
        "type": "market_families",
        "market_families": ["KXFAMA", "KXFAMB"],
    },
    "exclude_contamination": True,
}
_TEST_REGISTRATION_HASH = canonical_registration_hash(_TEST_REGISTRATION)


# ---------------------------------------------------------------------------
# Fixtures + stubs
# ---------------------------------------------------------------------------


class _StubCache:
    """In-memory stand-in for ``LLMReplayCache`` used by ``compute_cache_coverage``.

    Exposes the verified-only status used by the replay gate. We default to
    "verified" and override per test to drive a coverage miss.
    """

    def __init__(self, hit_predicate=lambda key: True) -> None:
        self._predicate = hit_predicate

    def has(self, key: CacheKey) -> bool:
        return bool(self._predicate(key))

    def verified_status(self, key: CacheKey) -> str:
        return "verified" if self._predicate(key) else "missing"

    def is_poisoned(self, key: CacheKey) -> bool:
        # All misses are "absent" not "poisoned" in this stub. The gate's
        # behavioral contract does not branch on poisoned vs absent — it
        # only branches on coverage ratio — so this is sufficient.
        return False

    def close(self) -> None:  # pragma: no cover — exercised by run_replay_gate
        return None


def _row_with_keys(**overrides: Any) -> dict[str, Any]:
    """Build a minimal corpus row carrying all 13 cache-key fields + family.

    ``market_families`` is a list (diversity-check input).
    ``pnl_dollars`` is set per-test as the EV input.
    """
    base = {
        "row_id": "r1",
        "prompt_template_hash": "tpl-h",
        "prompt_filled_hash": "filled-h",
        "model_id": "m",
        "model_digest": None,
        "ollama_version": None,
        "endpoint_type": "openai_compat",
        "seed": None,
        "temperature": 0.0,
        "num_ctx": None,
        "sampler_options_hash": "samp-h",
        "hardware_backend_class": None,
        "response_hash": "resp-h",
        "market_families": ["KXFAMA"],
        "corpus_window_start_utc": "2026-05-01T00:00:00Z",
        "corpus_window_end_utc": "2026-05-08T00:00:00Z",
        "regime_label": "test-oos-regime",
        "built_at_utc": "2026-05-09T00:00:00Z",
        "evidence_class": "registered_oos",
        "oos_registration_id": "test-oos-window",
        "registration_hash": _TEST_REGISTRATION_HASH,
        "in_period_validation_only": False,
        "contamination_window": False,
        "llm_capture_max_age_seconds": 3600,
        "pnl_dollars": None,
        "fee_net_pnl_dollars": None,
    }
    base.update(overrides)
    if "pnl_dollars" in overrides and "fee_net_pnl_dollars" not in overrides:
        base["fee_net_pnl_dollars"] = overrides["pnl_dollars"]
    return base


def _write_corpus(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def _scenario_runner_pass(cmd_in: Sequence[str] | None = None):
    """Inject a runner that always returns exit 0."""

    def runner(scenario_path, runner_cmd):
        # Mimic the internal subprocess shape used by _run_scenario_suite,
        # but we are passed via the optional ``scenario_runner`` kwarg so
        # the public function still appends scenario_path to runner_cmd.
        return 0, "scenarios stubbed: pass\n"

    return runner


def _fake_scenario_subprocess(returncode: int, output: str):
    """Return a callable suitable for monkeypatch on subprocess.run."""

    def fake_run(cmd, **kwargs):
        completed = subprocess.CompletedProcess(
            cmd, returncode=returncode, stdout=output, stderr=""
        )
        return completed

    return fake_run


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated dir with corpus_dir + ci_run_dir + governance_dir scaffolding."""
    corpus_dir = tmp_path / "logs" / "edge_replay"
    corpus_dir.mkdir(parents=True)
    (tmp_path / "logs" / "edge_replay" / "ci_runs").mkdir()
    (tmp_path / "docs" / "governance").mkdir(parents=True)
    registration = dict(_TEST_REGISTRATION)
    registration["registration_hash"] = _TEST_REGISTRATION_HASH
    (tmp_path / "docs" / "governance" / "oos-corpus-registry.json").write_text(
        json.dumps({"schema_version": 1, "registrations": [registration]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _common_kwargs(
    tmp_workspace: Path,
    *,
    scenario_returncode: int = 0,
    cache_hit: bool = True,
) -> dict[str, Any]:
    """Standard injected dependencies. Overridable per-test."""

    def fake_runner(scenario_path, runner_cmd):
        return scenario_returncode, "stub-scenario-output\n"

    def factory() -> _StubCache:
        return _StubCache(lambda key: cache_hit)

    return {
        "corpus_dir": tmp_workspace / "logs" / "edge_replay",
        "ci_run_dir": tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        "governance_dir": tmp_workspace / "docs" / "governance",
        "scenario_runner": ["unused"],  # exercised only when no fake passed
        "cache_factory": factory,
        "commit_sha": "deadbeef",
    }


def _patch_scenario_runner(monkeypatch: pytest.MonkeyPatch, rc: int, out: str = "stub\n") -> None:
    """Monkeypatch the module-level scenario runner to skip real pytest."""
    import scripts.edge_replay.replay_gate as mod

    def fake(scenario_path, runner_cmd):
        return rc, out

    monkeypatch.setattr(mod, "_run_scenario_suite", fake)


# ---------------------------------------------------------------------------
# T0 fast-path
# ---------------------------------------------------------------------------


def test_t0_changed_files_pass_immediately(tmp_workspace: Path) -> None:
    # README is T0 per tier_classifier.
    verdict = run_replay_gate(
        changed_files=[Path("README.md")],
        commit_sha="t0sha",
        ci_run_dir=tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        corpus_dir=tmp_workspace / "logs" / "edge_replay",
        governance_dir=tmp_workspace / "docs" / "governance",
    )
    assert verdict.pass_ is True
    assert verdict.tier == "T0"
    assert verdict.rule4 is None
    assert verdict.failure_reason is None
    # Output files written even on fast-path (so CI logs are uniform).
    assert (verdict.output_dir / "verdict.json").exists()
    assert (verdict.output_dir / "notes.md").exists()


# ---------------------------------------------------------------------------
# Insufficient corpus
# ---------------------------------------------------------------------------


def test_t1_with_no_corpora_fails_insufficient(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.pass_ is False
    assert verdict.tier in {"T1", "T2", "T3"}
    assert "insufficient corpus" in (verdict.failure_reason or "")


def test_t1_with_absent_corpus_directory_fails_closed(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "logs" / "edge_replay"
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        corpus_dir=corpus_dir,
        ci_run_dir=tmp_path / "ci_runs",
        governance_dir=tmp_path / "governance",
        commit_sha="absent-corpus",
        cache_factory=lambda: _StubCache(),
    )

    assert verdict.pass_ is False
    assert verdict.rule4 is None
    assert "insufficient corpus" in (verdict.failure_reason or "")


def test_t1_in_period_only_fails_without_allow_flag(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    # Single-family corpus -> in_period_validation_only.
    _write_corpus(
        corpus_dir / "corpus_demo_X_2026-05-01_2026-05-08.jsonl",
        [_row_with_keys(market_families=["KXFAMA"], pnl_dollars=1.0)],
    )
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.pass_ is False
    assert "insufficient corpus" in (verdict.failure_reason or "")


@pytest.mark.parametrize(
    ("row_override", "expected"),
    [
        ({"evidence_class": "historical_diagnostic"}, "evidence_class"),
        ({"registration_hash": "0" * 64}, "registration_hash"),
        ({"contamination_window": True}, "contamination"),
        ({"oos_registration_id": []}, "registration"),
        ({"market_families": ["KXFAMC"]}, "market family"),
        (
            {"corpus_window_start_utc": "2026-05-02T00:00:00Z"},
            "window",
        ),
    ],
)
def test_registered_oos_corpus_provenance_mismatch_fails_closed(
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_override: dict[str, Any],
    expected: str,
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_path = (
        tmp_workspace
        / "logs"
        / "edge_replay"
        / "corpus_test_oos_2026-05-01_2026-05-08.jsonl"
    )
    rows = [
        _row_with_keys(row_id="a", market_families=["KXFAMA"], pnl_dollars=1.0),
        _row_with_keys(row_id="b", market_families=["KXFAMB"], pnl_dollars=1.0),
    ]
    rows[0].update(row_override)
    _write_corpus(corpus_path, rows)

    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=1),
        **_common_kwargs(tmp_workspace),
    )

    assert verdict.pass_ is False
    assert "registered OOS corpus validation failed" in (verdict.failure_reason or "")
    assert expected in (verdict.failure_reason or "")


def test_registered_oos_corpus_rejects_duplicate_trade_rows(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_path = (
        tmp_workspace
        / "logs"
        / "edge_replay"
        / "corpus_duplicate_2026-05-01_2026-05-08.jsonl"
    )
    _write_corpus(
        corpus_path,
        [
            _row_with_keys(
                row_id="duplicate", market_families=["KXFAMA"], pnl_dollars=1.0
            ),
            _row_with_keys(
                row_id="duplicate", market_families=["KXFAMB"], pnl_dollars=1.0
            ),
        ],
    )

    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=1),
        **_common_kwargs(tmp_workspace),
    )

    assert verdict.pass_ is False
    assert "duplicate row_id" in (verdict.failure_reason or "")


# ---------------------------------------------------------------------------
# Happy-path T1 pass (sufficient diversity + coverage + scenarios + EV)
# ---------------------------------------------------------------------------


def _build_diverse_corpus(corpus_path: Path, n_per_family: int, pnl_each: float) -> None:
    rows: list[dict[str, Any]] = []
    for i in range(n_per_family):
        rows.append(
            _row_with_keys(
                row_id=f"r-a-{i}",
                market_families=["KXFAMA"],
                pnl_dollars=pnl_each,
            )
        )
        rows.append(
            _row_with_keys(
                row_id=f"r-b-{i}",
                market_families=["KXFAMB"],
                pnl_dollars=pnl_each,
            )
        )
    _write_corpus(corpus_path, rows)


def test_t1_all_pass_with_diverse_corpus_full_coverage(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    _build_diverse_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl",
        n_per_family=20,
        pnl_each=2.0,
    )
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=10),
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.pass_ is True, verdict.failure_reason
    assert verdict.rule4 is not None
    assert verdict.rule4.trade_count == 40
    assert verdict.rule4.realized_pnl == pytest.approx(80.0)
    assert verdict.rule4.per_trade_ev == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Cache coverage failure
# ---------------------------------------------------------------------------


def test_cache_coverage_below_threshold_fails(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    _build_diverse_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl",
        n_per_family=15,
        pnl_each=1.0,
    )
    # Force coverage stub to miss every key.
    kw = _common_kwargs(tmp_workspace, cache_hit=False)
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=10),
        **kw,
    )
    assert verdict.pass_ is False
    assert "coverage" in (verdict.failure_reason or "")


# ---------------------------------------------------------------------------
# Scenario failure
# ---------------------------------------------------------------------------


def test_scenario_failure_blocks_pass(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, rc=1, out="1 scenario failed\n")
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    _build_diverse_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl",
        n_per_family=20,
        pnl_each=1.0,
    )
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=10),
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.pass_ is False
    assert "scenario" in (verdict.failure_reason or "").lower()


# ---------------------------------------------------------------------------
# Insufficient trade count
# ---------------------------------------------------------------------------


def test_trade_count_below_minimum_fails(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    _build_diverse_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl",
        n_per_family=2,
        pnl_each=1.0,
    )
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=30),
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.pass_ is False
    assert "trade_count" in (verdict.failure_reason or "")


# ---------------------------------------------------------------------------
# T3 EV CI 95% lo > 0 enforcement
# ---------------------------------------------------------------------------


def test_t3_negative_ev_ci_fails(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A T3 path with mean PnL ~ 0 and noise yields ci_lo <= 0 -> FAIL."""
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    # 40 trades alternating +1 / -1 -> mean=0, ci_lo<0.
    rows = []
    for i in range(20):
        rows.append(_row_with_keys(row_id=f"a{i}", market_families=["KXFAMA"], pnl_dollars=1.0))
        rows.append(_row_with_keys(row_id=f"b{i}", market_families=["KXFAMB"], pnl_dollars=-1.0))
    _write_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl", rows
    )
    # Force T3 via semantic-scope STAMP (config_diff=True).
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=10, min_ev_ci_95_lo=0.0),
        config_diff=True,
        **_common_kwargs(tmp_workspace),
    )
    assert verdict.tier == "T3"
    assert verdict.pass_ is False
    assert "EV CI" in (verdict.failure_reason or "")


def test_t3_rule4_uses_fee_net_pnl_not_positive_gross_pnl(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_path = (
        tmp_workspace
        / "logs"
        / "edge_replay"
        / "corpus_fee_net_2026-05-01_2026-05-08.jsonl"
    )
    rows = []
    for i in range(20):
        rows.append(
            _row_with_keys(
                row_id=f"a{i}",
                market_families=["KXFAMA"],
                pnl_dollars=2.0,
                fee_net_pnl_dollars=-1.0,
            )
        )
        rows.append(
            _row_with_keys(
                row_id=f"b{i}",
                market_families=["KXFAMB"],
                pnl_dollars=2.0,
                fee_net_pnl_dollars=-1.0,
            )
        )
    _write_corpus(corpus_path, rows)

    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=30, min_ev_ci_95_lo=0.0),
        config_diff=True,
        **_common_kwargs(tmp_workspace),
    )

    assert verdict.tier == "T3"
    assert verdict.pass_ is False
    assert verdict.rule4 is not None
    assert verdict.rule4.realized_pnl == pytest.approx(-40.0)
    assert "EV CI" in (verdict.failure_reason or "")


# ---------------------------------------------------------------------------
# Rule4 fixture computation
# ---------------------------------------------------------------------------


def test_rule4_table_known_fixture() -> None:
    rows = [_row_with_keys(row_id=f"r{i}", pnl_dollars=v) for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 5.0])]
    table = _build_rule4_table(
        rows,
        corpora_used=[Path("c.jsonl")],
        window_start_utc="2026-05-01T00:00:00Z",
        window_end_utc="2026-05-08T00:00:00Z",
    )
    assert table.trade_count == 5
    assert table.per_trade_ev == pytest.approx(3.0)
    assert table.realized_pnl == pytest.approx(15.0)
    assert table.win_rate == pytest.approx(1.0)
    # stdev of [1..5] = sqrt(2.5) ≈ 1.5811
    # ci half = 1.96 * 1.5811 / sqrt(5) ≈ 1.386
    assert table.ev_ci_95_lo == pytest.approx(3.0 - 1.96 * (2.5 ** 0.5) / (5 ** 0.5))
    assert table.ev_ci_95_hi == pytest.approx(3.0 + 1.96 * (2.5 ** 0.5) / (5 ** 0.5))
    assert table.sharpe is not None
    assert table.corpora_used == ["c.jsonl"]


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------


def test_output_files_written_for_pass(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    _build_diverse_corpus(
        corpus_dir / "corpus_demo_diverse_2026-05-01_2026-05-08.jsonl",
        n_per_family=20,
        pnl_each=1.0,
    )
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        gate_spec=GateSpec(min_trades=10),
        commit_sha="cafeb0ba",
        ci_run_dir=tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        corpus_dir=corpus_dir,
        governance_dir=tmp_workspace / "docs" / "governance",
        cache_factory=lambda: _StubCache(),
    )
    out = verdict.output_dir
    assert out.name == "cafeb0ba"
    for name in ("verdict.json", "rule4_table.json", "coverage_report.json", "scenario_report.txt", "notes.md"):
        assert (out / name).exists(), f"missing {name}"
    payload = json.loads((out / "verdict.json").read_text())
    assert payload["pass"] is True
    assert payload["tier"] in {"T1", "T2", "T3"}


# ---------------------------------------------------------------------------
# Explicit corpora subset triggers memo warning when missing
# ---------------------------------------------------------------------------


def test_explicit_corpora_without_memo_emits_warning_note(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    corpus_path = corpus_dir / "explicit_choice.jsonl"
    _build_diverse_corpus(corpus_path, n_per_family=20, pnl_each=1.0)
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        corpora=[corpus_path],
        gate_spec=GateSpec(min_trades=10),
        commit_sha="explicitsha",
        ci_run_dir=tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        corpus_dir=corpus_dir,
        governance_dir=tmp_workspace / "docs" / "governance",
        cache_factory=lambda: _StubCache(),
    )
    assert any(
        "safeguard D" in n and "memo" in n for n in verdict.notes
    ), verdict.notes


def test_explicit_corpora_with_memo_does_not_warn(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    corpus_dir = tmp_workspace / "logs" / "edge_replay"
    corpus_path = corpus_dir / "explicit_choice2.jsonl"
    _build_diverse_corpus(corpus_path, n_per_family=20, pnl_each=1.0)
    memo = tmp_workspace / "docs" / "governance" / "2026-05-23-corpus-override-memo.md"
    memo.write_text("# Cherry-pick memo\n\nReason: ...\n", encoding="utf-8")
    verdict = run_replay_gate(
        changed_files=[Path("analysis/signal_analyzer.py")],
        corpora=[corpus_path],
        gate_spec=GateSpec(min_trades=10),
        commit_sha="explicitsha2",
        ci_run_dir=tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        corpus_dir=corpus_dir,
        governance_dir=tmp_workspace / "docs" / "governance",
        cache_factory=lambda: _StubCache(),
    )
    assert not any("safeguard D" in n for n in verdict.notes)
    assert any("operator memo present" in n for n in verdict.notes)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_t0_returns_zero(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(sys, "stdin", _StdinStub("README.md\n"))
    rc = main(["--changed-files-from-stdin", "--commit-sha", "clit0"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["pass"] is True
    assert payload["tier"] == "T0"


def test_cli_explicit_changed_file_no_corpus_returns_one(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_scenario_runner(monkeypatch, 0)
    # No corpus written -> InsufficientCorpusError path -> exit 1.
    rc = main([
        "--changed-file", "analysis/signal_analyzer.py",
        "--commit-sha", "clinosha",
        "--no-scenarios",
    ])
    assert rc == 1


# ---------------------------------------------------------------------------
# Commit-SHA derivation
# ---------------------------------------------------------------------------


def test_commit_sha_defaults_to_git_head(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.edge_replay.replay_gate as mod
    monkeypatch.setattr(mod, "_git_head_sha", lambda cwd=None: "deadbeef-stub")
    _patch_scenario_runner(monkeypatch, 0)
    verdict = run_replay_gate(
        changed_files=[Path("README.md")],
        ci_run_dir=tmp_workspace / "logs" / "edge_replay" / "ci_runs",
        corpus_dir=tmp_workspace / "logs" / "edge_replay",
        governance_dir=tmp_workspace / "docs" / "governance",
    )
    assert verdict.output_dir.name == "deadbeef-stub"


# ---------------------------------------------------------------------------
# Diversity scan unit
# ---------------------------------------------------------------------------


def test_scan_corpus_diversity_single_family_is_in_period(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    _write_corpus(p, [_row_with_keys(market_families=["KXFAMA"])])
    n, in_period = _scan_corpus_diversity(p)
    assert n == 1
    assert in_period is True


def test_scan_corpus_diversity_two_families_is_diverse(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    _write_corpus(
        p,
        [
            _row_with_keys(market_families=["KXFAMA"]),
            _row_with_keys(market_families=["KXFAMB"]),
        ],
    )
    n, in_period = _scan_corpus_diversity(p)
    assert n == 2
    assert in_period is False


def test_scan_corpus_diversity_rejects_non_object_rows(tmp_path: Path) -> None:
    corpus_path = tmp_path / "non-object.jsonl"
    corpus_path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(GateError, match="JSON object"):
        _scan_corpus_diversity(corpus_path)


# ---------------------------------------------------------------------------
# Helpers — minimal stdin stub for CLI tests
# ---------------------------------------------------------------------------


class _StdinStub:
    def __init__(self, text: str) -> None:
        self._text = text

    def __iter__(self):
        return iter(self._text.splitlines(keepends=True))

    def read(self) -> str:  # pragma: no cover — unused
        return self._text
