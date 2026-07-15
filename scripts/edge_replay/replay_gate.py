"""I-4 — Replay-as-CI gate runner.

Single entry point invoked from pytest or CI per
``docs/superpowers/specs/2026-05-23-paper-mode-rapid-learning-framework-design.md``
§3 I-4 and §4 §16.7 Rule 4 schema.

Public API
----------

``run_replay_gate(*, changed_files, corpora="all_diverse", gate_spec,
                  commit_sha=None, config_diff=False, ...) -> GateVerdict``

Behavior summary
----------------

* **Tier-aware** (per I-5 ``classify_tier``):

    - **T0**: pass immediately. No corpus, no cache check, no scenarios.
      Rule 2 explicitly exempts T0 from EV-on-resolved-markets gating.
    - **T1** / **T2**: require (a) scenario suite passing, (b) verified LLM
      cache coverage at or above ``gate_spec.coverage_threshold``, and
      (c) prospectively registered OOS evidence with exact provenance.
      T2 additionally flags a memo-required note (T2 operator-approval is
      out of scope for I-4).
    - **T3**: full Rule 1 — ``trade_count >= gate_spec.min_trades`` and
      ``ev_ci_95_lo > gate_spec.min_ev_ci_95_lo``.

* **Default ``corpora="all_diverse"``** auto-discovers every candidate
  ``logs/edge_replay/corpus_*.jsonl`` with at least two actual market
  families, then requires every selected corpus to match the prospective
  OOS registry exactly (per blocker C / safeguard D anti-gaming).
  An explicit ``corpora=[paths]`` override triggers a notes-entry warning
  if no operator memo file exists in ``docs/governance/``.

* **Output** (always written, even on failure):
  ``logs/edge_replay/ci_runs/<commit_sha>/`` containing
  ``verdict.json``, ``rule4_table.json``, ``coverage_report.json``,
  ``scenario_report.txt``, ``notes.md``.

* **Exit codes** (CLI):
  - ``0`` — pass
  - ``1`` — fail (any tier check failed)
  - ``2`` — infrastructure error (corpus DB missing, etc.)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from scripts.edge_replay.llm_cache import (
    CoverageReport,
    LLMReplayCache,
    compute_cache_coverage,
)
from scripts.edge_replay.oos_registry import (
    DEFAULT_REGISTRY_PATH,
    OOSRegistration,
    OOSRegistryError,
    load_oos_registry,
)
from scripts.edge_replay.tier_classifier import classify_tier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

Tier = Literal["T0", "T1", "T2", "T3"]

DEFAULT_CORPUS_DIR: Path = Path("logs/edge_replay")
DEFAULT_CI_RUN_DIR: Path = Path("logs/edge_replay/ci_runs")
DEFAULT_GOVERNANCE_DIR: Path = Path("docs/governance")
DEFAULT_SCENARIO_SUITE: Path = Path("tests/test_scenario_suite.py")

# Coverage check threshold default — per IC §16.7 pre-gate requirement.
_COVERAGE_DEFAULT: float = 0.95

# Rule 1 default minimum-trades floor per IC §16 Rule 1.
_MIN_TRADES_DEFAULT: int = 30

# Normal-approximation 95% CI z-score. We use a paper-mode static factor;
# t-distribution adjustments do not materially change the verdict at
# n>=30 (CLT regime) and the gate spec is decision-grade not academic.
_Z_95: float = 1.96


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GateError(Exception):
    """Base exception for replay-gate failures."""


class InsufficientCorpusError(GateError):
    """No corpora available, or all corpora are IN_PERIOD_VALIDATION_ONLY.

    Raised when corpus auto-discovery returns zero non-IN_PERIOD candidates
    (or, with ``allow_in_period_only=False``, zero diverse corpora). The
    deploy is structurally not gateable and must not proceed.
    """


class CacheCoverageError(GateError):
    """Pre-gate LLM cache coverage check failed (< threshold).

    Distinct from a cache-miss-at-read-time error: this is the bulk
    coverage check that fires BEFORE any EV is computed. A corpus with
    < 95% extended-key hit rate cannot produce gate-quality EV evidence
    because some rows would have to re-call live LLMs (forbidden per I-2).
    """


class ScenarioFailureError(GateError):
    """One or more scenario-suite scenarios failed (regression detected).

    The I-6 scenario suite is the adversarial regression catalog. A
    failure here means a known bad path got reintroduced — gate refuses
    to advance even if Rule 4 EV is positive.
    """


# ---------------------------------------------------------------------------
# Dataclasses (IC §16.7 Rule 4 schema)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule4Table:
    """IC §16 Rule 4 schema — required EV evidence per behavioral deploy.

    All dollar / unit values reflect persisted fee-net settled PnL.
    ``sharpe`` is None when trade_count < 2 (variance undefined). The
    Sharpe ratio is NOT annualized for paper-mode replay (the window
    timescale is the experiment's window, not a year); operators reading
    the value should treat it as a unitless per-trade signal-to-noise
    ratio, not a finance-textbook annualized Sharpe.
    """

    trade_count: int
    win_rate: float
    per_trade_ev: float
    ev_ci_95_lo: float
    ev_ci_95_hi: float
    realized_pnl: float
    sharpe: float | None
    window_start_utc: str
    window_end_utc: str
    corpora_used: list[str]


@dataclass(frozen=True)
class GateSpec:
    """Operator-chosen gate parameters."""

    min_trades: int = _MIN_TRADES_DEFAULT
    min_ev_ci_95_lo: float = 0.0
    coverage_threshold: float = _COVERAGE_DEFAULT
    require_scenarios_pass: bool = True
    allow_in_period_only: bool = False


@dataclass(frozen=True)
class GateVerdict:
    """Verdict returned by ``run_replay_gate``. Always serializable to JSON.

    ``pass_`` uses a trailing underscore because ``pass`` is a reserved
    Python keyword and cannot be a dataclass field. CLI surfaces the
    value as ``pass`` in JSON output.
    """

    pass_: bool
    tier: Tier
    rule4: Rule4Table | None
    coverage_report_path: Path
    rule4_table_path: Path
    scenario_report_path: Path
    notes: list[str]
    failure_reason: str | None
    output_dir: Path


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now_iso_z() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Corpus discovery + diversity check
# ---------------------------------------------------------------------------


def _scan_corpus_diversity(corpus_path: Path) -> tuple[int, bool]:
    """Return (row_count, in_period_validation_only) for a JSONL corpus.

    A corpus is IN_PERIOD_VALIDATION_ONLY when its rows touch fewer than
    two distinct ``market_families``. This recomputes the same flag I-1
    surfaces on ``BuildResult`` so the gate can audit ANY corpus on disk
    without requiring the original BuildResult to be present (corpora
    outlive their build sessions and routinely move between machines).

    A corrupted JSONL line raises ``GateError`` rather than being silently
    skipped — a corpus you can't fully parse is a corpus you can't trust
    for a gate verdict.
    """
    families_seen: set[str] = set()
    row_count = 0
    with corpus_path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GateError(
                    f"corpus {corpus_path} line {line_no} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise GateError(
                    f"corpus {corpus_path} line {line_no} must be a JSON object"
                )
            row_count += 1
            fams = row.get("market_families") or []
            if isinstance(fams, list):
                for f in fams:
                    if isinstance(f, str) and f:
                        families_seen.add(f)
    in_period = len(families_seen) < 2
    return row_count, in_period


def _discover_all_diverse_corpora(
    corpus_dir: Path,
) -> tuple[list[Path], list[Path], list[str]]:
    """Auto-discover I-1-built corpora under ``corpus_dir``.

    Returns ``(diverse, in_period_only, notes)``:

    - ``diverse``: corpus files with rows from >=2 market_families.
    - ``in_period_only``: corpus files that exist but are IN_PERIOD.
    - ``notes``: human-readable strings to be appended to verdict notes.

    Discovery glob is ``corpus_*.jsonl`` directly under ``corpus_dir``
    (matches I-1 ``_default_output_path`` naming). Subdirectories are
    NOT recursed — those contain legacy / hand-built artifacts that
    pre-date the I-1 contract.
    """
    diverse: list[Path] = []
    in_period_only: list[Path] = []
    notes: list[str] = []
    if not corpus_dir.exists():
        notes.append(f"corpus_dir {corpus_dir} does not exist")
        return diverse, in_period_only, notes
    for candidate in sorted(corpus_dir.glob("corpus_*.jsonl")):
        try:
            _row_count, is_in_period = _scan_corpus_diversity(candidate)
        except GateError as exc:
            notes.append(f"skipping {candidate}: {exc}")
            continue
        if is_in_period:
            in_period_only.append(candidate)
        else:
            diverse.append(candidate)
    return diverse, in_period_only, notes


# ---------------------------------------------------------------------------
# Rule 4 EV computation
# ---------------------------------------------------------------------------


def _load_corpus_rows(corpus_paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Read every JSONL row across the given corpora into a single list."""
    rows: list[dict[str, Any]] = []
    for path in corpus_paths:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise GateError(
                        f"corpus {path} line {line_no} is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(row, dict):
                    raise GateError(
                        f"corpus {path} line {line_no} must be a JSON object"
                    )
                rows.append(row)
    return rows


def _parse_corpus_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise GateError(f"{field_name} must be canonical UTC text")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise GateError(
            f"{field_name} must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc


def _validate_registered_oos_corpus(
    corpus_path: Path,
    registry: Mapping[str, OOSRegistration],
) -> set[str]:
    rows = _load_corpus_rows([corpus_path])
    if not rows:
        raise GateError(f"corpus {corpus_path} is empty")

    registration_values = [row.get("oos_registration_id") for row in rows]
    if any(not isinstance(value, str) or not value for value in registration_values):
        raise GateError(
            f"corpus {corpus_path} has an invalid OOS registration id"
        )
    registration_ids = set(registration_values)
    if len(registration_ids) != 1:
        raise GateError(
            f"corpus {corpus_path} must bind exactly one OOS registration"
        )
    registration_id = next(iter(registration_ids))
    if not isinstance(registration_id, str) or registration_id not in registry:
        raise GateError(
            f"corpus {corpus_path} references unknown OOS registration "
            f"{registration_id!r}"
        )

    registration = registry[registration_id]
    registered_families = set(registration.market_families)
    actual_families: set[str] = set()
    row_ids: set[str] = set()
    window_end = _parse_corpus_utc(
        registration.window_end_utc,
        field_name="registration.window.end_utc",
    )

    for line_no, row in enumerate(rows, start=1):
        prefix = f"corpus {corpus_path} row {line_no}"
        row_id = row.get("row_id")
        if not isinstance(row_id, str) or not row_id:
            raise GateError(f"{prefix} row_id must be a non-empty string")
        if row_id in row_ids:
            raise GateError(f"{prefix} has duplicate row_id {row_id!r}")
        row_ids.add(row_id)
        if row.get("evidence_class") != "registered_oos":
            raise GateError(f"{prefix} evidence_class must be registered_oos")
        if row.get("oos_registration_id") != registration.id:
            raise GateError(f"{prefix} OOS registration id mismatch")
        if row.get("registration_hash") != registration.registration_hash:
            raise GateError(f"{prefix} registration_hash mismatch")
        if (
            row.get("corpus_window_start_utc") != registration.window_start_utc
            or row.get("corpus_window_end_utc") != registration.window_end_utc
        ):
            raise GateError(f"{prefix} corpus window does not match registration")
        if row.get("regime_label") != registration.regime_label:
            raise GateError(f"{prefix} regime_label does not match registration")
        if row.get("contamination_window") is not False:
            raise GateError(f"{prefix} contamination is not excluded")
        if row.get("in_period_validation_only") is not False:
            raise GateError(f"{prefix} is not registered OOS evidence")

        built_at = _parse_corpus_utc(
            row.get("built_at_utc"),
            field_name=f"{prefix} built_at_utc",
        )
        if built_at < window_end:
            raise GateError(f"{prefix} was materialized before the window closed")

        max_age = row.get("llm_capture_max_age_seconds")
        if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
            raise GateError(
                f"{prefix} llm_capture_max_age_seconds must be a positive integer"
            )

        families = row.get("market_families")
        if (
            not isinstance(families, list)
            or not families
            or any(not isinstance(family, str) or not family for family in families)
        ):
            raise GateError(f"{prefix} market_families must be non-empty strings")
        row_families = set(families)
        if len(row_families) != len(families):
            raise GateError(f"{prefix} market_families contains duplicates")
        if not row_families <= registered_families:
            raise GateError(f"{prefix} contains an unregistered market family")
        actual_families.update(row_families)

    if actual_families != registered_families:
        raise GateError(
            f"corpus {corpus_path} actual market-family diversity does not "
            "match its registration"
        )
    return row_ids


def _resolved_pnls(rows: Iterable[dict[str, Any]]) -> list[float]:
    """Extract persisted fee-net PnL from resolved corpus rows.

    Gross ``pnl_dollars`` is deliberately ignored: it cannot establish
    deployable expectancy after venue fees. Missing fee-net settlement is
    uncovered evidence and therefore cannot contribute to Rule 4.
    """
    out: list[float] = []
    for row in rows:
        pnl = row.get("fee_net_pnl_dollars")
        if pnl is None:
            continue
        try:
            out.append(float(pnl))
        except (TypeError, ValueError):
            continue
    return out


def _normal_approx_ci(pnls: list[float], z: float = _Z_95) -> tuple[float, float, float, float | None]:
    """Return (mean, ci_lo, ci_hi, sharpe).

    ``sharpe`` is mean / stdev (sample stdev, not annualized — see
    Rule4Table docstring). When n < 2 or stdev == 0, sharpe is None.
    The 95% CI uses the normal approximation mean +/- z * stdev / sqrt(n);
    at n>=30 (the Rule 1 floor) the CLT regime is appropriate.
    """
    n = len(pnls)
    if n == 0:
        return 0.0, 0.0, 0.0, None
    mean = sum(pnls) / n
    if n < 2:
        return mean, mean, mean, None
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1)
    stdev = math.sqrt(var)
    half = z * stdev / math.sqrt(n)
    ci_lo = mean - half
    ci_hi = mean + half
    sharpe: float | None = mean / stdev if stdev > 0 else None
    return mean, ci_lo, ci_hi, sharpe


def _build_rule4_table(
    rows: list[dict[str, Any]],
    corpora_used: list[Path],
    window_start_utc: str,
    window_end_utc: str,
) -> Rule4Table:
    pnls = _resolved_pnls(rows)
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = (wins / n) if n > 0 else 0.0
    realized_pnl = sum(pnls)
    mean, ci_lo, ci_hi, sharpe = _normal_approx_ci(pnls)
    return Rule4Table(
        trade_count=n,
        win_rate=win_rate,
        per_trade_ev=mean,
        ev_ci_95_lo=ci_lo,
        ev_ci_95_hi=ci_hi,
        realized_pnl=realized_pnl,
        sharpe=sharpe,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        corpora_used=[str(p) for p in corpora_used],
    )


def _corpus_window(rows: list[dict[str, Any]]) -> tuple[str, str]:
    """Min/max of corpus_window_{start,end}_utc across rows."""
    starts = [r.get("corpus_window_start_utc") for r in rows]
    ends = [r.get("corpus_window_end_utc") for r in rows]
    starts_s = [s for s in starts if isinstance(s, str) and s]
    ends_s = [s for s in ends if isinstance(s, str) and s]
    start = min(starts_s) if starts_s else ""
    end = max(ends_s) if ends_s else ""
    return start, end


# ---------------------------------------------------------------------------
# Scenario suite execution
# ---------------------------------------------------------------------------


# Bounds the scenario-suite subprocess. A hung pytest (deadlocked fixture,
# stuck import) would otherwise block CI indefinitely. 300s is generous for
# the current ~22-scenario suite (sub-second runtime today) but cheap to
# raise. Surfaced as a TimeoutExpired -> non-zero return so the gate
# fails-closed rather than masquerading as a pass.
_SCENARIO_SUITE_TIMEOUT_SECONDS = 300


def _run_scenario_suite(
    scenario_path: Path = DEFAULT_SCENARIO_SUITE,
    runner: Sequence[str] | None = None,
    *,
    timeout_seconds: float = _SCENARIO_SUITE_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    """Invoke the I-6 scenario suite via pytest and return (exit_code, captured_output).

    The runner defaults to ``[sys.executable, "-m", "pytest", "-q"]``. Tests
    monkeypatch this function (or override ``runner``) to avoid spawning a
    real subprocess per unit-test.

    A ``TimeoutExpired`` is translated to a non-zero exit code so the gate
    fails-closed on a hung pytest process. Per python-reviewer M1: a hung
    scenario subprocess would otherwise block CI indefinitely.
    """
    cmd = list(runner) if runner is not None else [sys.executable, "-m", "pytest", "-q"]
    cmd.append(str(scenario_path))
    try:
        proc = subprocess.run(  # noqa: S603 — controlled runner list
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        combined = (
            f"scenario suite TIMED OUT after {timeout_seconds}s (gate fail-closed)\n"
            f"cmd: {cmd}\n"
            f"partial_stdout: {(exc.stdout or b'').decode('utf-8', 'replace')}\n"
            f"partial_stderr: {(exc.stderr or b'').decode('utf-8', 'replace')}\n"
        )
        # Exit code 124 mirrors GNU `timeout(1)`'s convention for timed-out
        # commands; any non-zero value triggers the gate's ScenarioFailureError
        # path, so the specific number is operator-readable rather than load-bearing.
        return 124, combined
    combined = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, combined


# ---------------------------------------------------------------------------
# Operator-memo presence check (anti-gaming safeguard D)
# ---------------------------------------------------------------------------


def _has_corpus_override_memo(governance_dir: Path = DEFAULT_GOVERNANCE_DIR) -> bool:
    """Return True iff ANY ``*-corpus-override-memo.md`` exists in governance_dir.

    We do not parse the memo — presence-check only. The gate's job is to
    surface the missing-memo condition; reviewing the memo's content is
    the operator's job (per IC §16 corpus-override workflow).
    """
    if not governance_dir.exists():
        return False
    for candidate in governance_dir.glob("*-corpus-override-memo.md"):
        if candidate.is_file():
            return True
    return False


# ---------------------------------------------------------------------------
# Commit-SHA derivation
# ---------------------------------------------------------------------------


def _git_head_sha(cwd: Path | None = None) -> str:
    """Return ``git rev-parse HEAD`` for ``cwd`` (or current dir).

    On failure (no git, detached state, etc.), return ``"unknown"`` so the
    gate still produces a writable output directory rather than crashing.
    """
    try:
        proc = subprocess.run(  # noqa: S603,S607 — invokes git from PATH
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _dataclass_to_json(obj: Any) -> Any:
    """Recursively convert a dataclass to JSON-friendly types.

    Paths become strings; ``pass_`` becomes ``pass`` for the verdict
    dict. Nested dataclasses are unwrapped.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(obj):
            key = "pass" if f.name == "pass_" else f.name
            out[key] = _dataclass_to_json(getattr(obj, f.name))
        return out
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, list):
        return [_dataclass_to_json(x) for x in obj]
    if isinstance(obj, tuple):
        return [_dataclass_to_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_json(v) for k, v in obj.items()}
    return obj


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


def _write_text(path: Path, text: str) -> None:
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write text to ``path`` via tmp-file + Path.replace().

    Per python-reviewer M2: a process kill between writing
    ``verdict.json`` and ``notes.md`` would otherwise leave the CI
    run directory with some files present and some absent. I-13
    (next Phase 2 deliverable) treats directory presence as a
    completion signal; partial writes would mislead it.

    ``Path.replace()`` is atomic on POSIX (rename(2)) and on NTFS
    when source and destination live on the same volume — which
    they do here, since the .tmp sibling is created in
    ``path.parent`` via ``tempfile.mkstemp(dir=...)``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _write_outputs(
    output_dir: Path,
    verdict: GateVerdict,
    rule4: Rule4Table | None,
    coverage: CoverageReport | None,
    scenario_output: str,
    notes: list[str],
) -> None:
    """Persist verdict.json, rule4_table.json, coverage_report.json,
    scenario_report.txt, notes.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "verdict.json", _dataclass_to_json(verdict))
    _write_json(
        output_dir / "rule4_table.json",
        _dataclass_to_json(rule4) if rule4 is not None else None,
    )
    _write_json(
        output_dir / "coverage_report.json",
        _dataclass_to_json(coverage) if coverage is not None else None,
    )
    _write_text(output_dir / "scenario_report.txt", scenario_output)
    notes_md = "# Replay gate notes\n\n" + "\n".join(
        f"- {n}" for n in notes
    ) + ("\n" if notes else "")
    _write_text(output_dir / "notes.md", notes_md)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_replay_gate(
    *,
    changed_files: list[Path],
    corpora: list[Path] | Literal["all_diverse"] = "all_diverse",
    gate_spec: GateSpec | None = None,
    commit_sha: str | None = None,
    config_diff: bool = False,
    prompt_template_diff: bool = False,
    model_manifest_diff: bool = False,
    schema_migrations: Sequence[Path] = (),
    cache_db_path: Path | None = None,
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    ci_run_dir: Path = DEFAULT_CI_RUN_DIR,
    governance_dir: Path = DEFAULT_GOVERNANCE_DIR,
    oos_registry_path: Path | None = None,
    scenario_path: Path = DEFAULT_SCENARIO_SUITE,
    scenario_runner: Sequence[str] | None = None,
    cache_factory: Any | None = None,
) -> GateVerdict:
    """Run the full replay-as-CI gate per IC §16 amendment §16.7.

    Args:
        changed_files: Repo-relative or absolute paths of files in the
            candidate diff. Drives tier classification.
        corpora: ``"all_diverse"`` (default) auto-discovers all non
            IN_PERIOD_VALIDATION_ONLY corpora; an explicit list cherry-
            picks a subset and requires an operator memo (notes-only
            warning, does not fail the gate by itself).
        gate_spec: Tier-aware thresholds. Defaults match IC §16 Rule 1.
        commit_sha: Defaults to ``git rev-parse HEAD`` from cwd.
        config_diff / prompt_template_diff / model_manifest_diff /
        schema_migrations: Forwarded to I-5 ``classify_tier`` per its
            semantic-scope STAMP.
        cache_db_path: Override for the LLM cache SQLite path. Defaults
            to I-2's ``_DEFAULT_DB_PATH``.
        corpus_dir: Where ``corpora="all_diverse"`` looks for files.
        ci_run_dir: Parent dir for the per-commit output folder.
        governance_dir: Where corpus-override memos live.
        oos_registry_path: Prospective OOS registry. Defaults to
            ``governance_dir / oos-corpus-registry.json``.
        scenario_path: Path to the scenario-suite test file.
        scenario_runner: Override for the subprocess command list.
            Tests inject a no-op echo runner.
        cache_factory: Test injection point — a callable that returns
            an object satisfying the verified ``LLMReplayCache`` status API.
            Defaults to
            ``lambda: LLMReplayCache(cache_db_path)``.

    Returns:
        A ``GateVerdict``. Output files are always written, even on
        failure, so CI logs always have a Rule-4 audit artifact.
    """
    spec = gate_spec or GateSpec()
    sha = (commit_sha or _git_head_sha()).strip() or "unknown"
    output_dir = ci_run_dir / sha
    # Notes accumulate in execution order. Per python-reviewer M3:
    # ordering is INTENTIONALLY insertion-order, not sorted, so the
    # operator can reconstruct the gate's reasoning path from top to
    # bottom. If I-13 ever diffs notes across runs, sort before
    # writing in _write_outputs; today this is acceptable because
    # the order is deterministic for a given code path.
    notes: list[str] = [f"commit_sha={sha}", f"timestamp_utc={_now_iso_z()}"]

    # ----- Tier classification (I-5) -----
    tier: Tier = classify_tier(
        changed_files,
        config_diff=config_diff,
        prompt_template_diff=prompt_template_diff,
        model_manifest_diff=model_manifest_diff,
        schema_migrations=list(schema_migrations),
    )
    notes.append(f"tier={tier}")

    # ----- T0 fast-path -----
    if tier == "T0":
        notes.append("T0: Rule 2 exempt — passing without corpus/scenarios/cache.")
        verdict = GateVerdict(
            pass_=True,
            tier=tier,
            rule4=None,
            coverage_report_path=output_dir / "coverage_report.json",
            rule4_table_path=output_dir / "rule4_table.json",
            scenario_report_path=output_dir / "scenario_report.txt",
            notes=notes,
            failure_reason=None,
            output_dir=output_dir,
        )
        _write_outputs(
            output_dir, verdict, rule4=None, coverage=None,
            scenario_output="T0 fast-path: scenarios not executed.\n",
            notes=notes,
        )
        return verdict

    # ----- Corpus selection -----
    if corpora == "all_diverse":
        diverse, in_period_only, disc_notes = _discover_all_diverse_corpora(
            corpus_dir
        )
        notes.extend(disc_notes)
        if spec.allow_in_period_only:
            selected_corpora = diverse + in_period_only
        else:
            selected_corpora = diverse
        if not selected_corpora:
            notes.append(
                f"InsufficientCorpusError: 0 usable corpora "
                f"(diverse={len(diverse)}, in_period_only={len(in_period_only)}, "
                f"allow_in_period_only={spec.allow_in_period_only})"
            )
            return _fail(
                output_dir, tier, notes,
                failure_reason="insufficient corpus",
                coverage=None, rule4=None, scenario_output="",
            )
        notes.append(
            f"corpora=all_diverse: selected {len(selected_corpora)} corpus file(s) "
            f"({len(diverse)} diverse, {len(in_period_only)} in_period_only)"
        )
    else:
        # Explicit subset — anti-gaming safeguard D requires a memo.
        explicit_paths = [Path(p) for p in corpora]
        if not explicit_paths:
            notes.append(
                "InsufficientCorpusError: explicit corpora list is empty"
            )
            return _fail(
                output_dir, tier, notes,
                failure_reason="insufficient corpus",
                coverage=None, rule4=None, scenario_output="",
            )
        memo_present = _has_corpus_override_memo(governance_dir)
        if not memo_present:
            notes.append(
                "WARNING (safeguard D): explicit corpora subset supplied without "
                f"an operator memo in {governance_dir}/. "
                "Add a *-corpus-override-memo.md file documenting the cherry-pick "
                "rationale before relying on this gate verdict."
            )
        else:
            notes.append(
                "explicit corpora subset supplied; operator memo present in "
                f"{governance_dir}/"
            )
        # Diversity audit each explicit corpus and bail if none survive.
        usable: list[Path] = []
        explicit_in_period: list[Path] = []
        for path in explicit_paths:
            if not path.exists():
                notes.append(f"explicit corpus {path} does not exist; skipping")
                continue
            try:
                _row_count, is_in_period = _scan_corpus_diversity(path)
            except GateError as exc:
                notes.append(f"explicit corpus {path}: {exc}")
                continue
            if is_in_period:
                explicit_in_period.append(path)
                if spec.allow_in_period_only:
                    usable.append(path)
            else:
                usable.append(path)
        if not usable:
            notes.append(
                f"InsufficientCorpusError: 0 usable explicit corpora "
                f"(in_period_only={len(explicit_in_period)}, "
                f"allow_in_period_only={spec.allow_in_period_only})"
            )
            return _fail(
                output_dir, tier, notes,
                failure_reason="insufficient corpus",
                coverage=None, rule4=None, scenario_output="",
            )
        selected_corpora = usable

    # ----- Prospective OOS provenance validation -----
    registry_path = oos_registry_path or (
        governance_dir / DEFAULT_REGISTRY_PATH.name
    )
    try:
        registry = load_oos_registry(registry_path)
        corpus_row_ids: set[str] = set()
        for path in selected_corpora:
            row_ids = _validate_registered_oos_corpus(path, registry)
            duplicate_ids = corpus_row_ids & row_ids
            if duplicate_ids:
                duplicate = sorted(duplicate_ids)[0]
                raise GateError(
                    f"duplicate row_id {duplicate!r} across selected corpora"
                )
            corpus_row_ids.update(row_ids)
    except (GateError, OOSRegistryError) as exc:
        reason = f"registered OOS corpus validation failed: {exc}"
        notes.append(reason)
        return _fail(
            output_dir,
            tier,
            notes,
            failure_reason=reason,
            coverage=None,
            rule4=None,
            scenario_output="",
        )
    notes.append(
        f"validated {len(selected_corpora)} registered OOS corpus file(s) "
        f"against {registry_path}"
    )

    # ----- Load corpus rows once for both coverage + EV -----
    try:
        all_rows = _load_corpus_rows(selected_corpora)
    except GateError as exc:
        notes.append(f"corpus load failed: {exc}")
        return _fail(
            output_dir, tier, notes,
            failure_reason=f"corpus load failed: {exc}",
            coverage=None, rule4=None, scenario_output="",
        )
    notes.append(f"loaded {len(all_rows)} corpus rows across {len(selected_corpora)} file(s)")

    # ----- Pre-gate LLM cache coverage check (I-2) -----
    if cache_factory is not None:
        cache = cache_factory()
    else:
        cache = LLMReplayCache(cache_db_path)
    try:
        coverage = compute_cache_coverage(
            all_rows, cache, threshold=spec.coverage_threshold,
        )
    finally:
        # Cache may or may not be a real LLMReplayCache. Best-effort close.
        close = getattr(cache, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    notes.append(
        f"cache coverage: {coverage.cache_hits}/{coverage.total_rows} "
        f"(ratio={coverage.coverage_ratio:.4f}, threshold={coverage.threshold:.4f}, "
        f"passes={coverage.passes_threshold})"
    )
    if not coverage.passes_threshold:
        notes.append(
            f"CacheCoverageError: coverage {coverage.coverage_ratio:.4f} < "
            f"threshold {coverage.threshold:.4f}"
        )
        return _fail(
            output_dir, tier, notes,
            failure_reason=(
                f"cache coverage {coverage.coverage_ratio:.4f} < threshold "
                f"{coverage.threshold:.4f}"
            ),
            coverage=coverage, rule4=None, scenario_output="",
        )

    # ----- Scenario suite (I-6) -----
    scenario_output = ""
    if spec.require_scenarios_pass:
        rc, scenario_output = _run_scenario_suite(scenario_path, scenario_runner)
        notes.append(f"scenario suite exit code: {rc}")
        if rc != 0:
            notes.append("ScenarioFailureError: I-6 scenario suite did not pass")
            return _fail(
                output_dir, tier, notes,
                failure_reason="scenario suite failed",
                coverage=coverage, rule4=None, scenario_output=scenario_output,
            )
    else:
        scenario_output = "scenarios skipped (require_scenarios_pass=False).\n"
        notes.append("scenarios skipped per gate_spec.require_scenarios_pass=False")

    # ----- Rule 4 EV computation -----
    window_start, window_end = _corpus_window(all_rows)
    rule4 = _build_rule4_table(
        all_rows, selected_corpora, window_start, window_end,
    )

    # ----- Tier-specific gating on Rule 4 -----
    if rule4.trade_count < spec.min_trades:
        reason = (
            f"trade_count {rule4.trade_count} < min {spec.min_trades}"
        )
        notes.append(f"FAIL: {reason}")
        return _fail(
            output_dir, tier, notes,
            failure_reason=reason,
            coverage=coverage, rule4=rule4, scenario_output=scenario_output,
        )

    if tier == "T3" and rule4.ev_ci_95_lo <= spec.min_ev_ci_95_lo:
        reason = (
            f"T3 EV CI 95% lo {rule4.ev_ci_95_lo:.4f} <= min "
            f"{spec.min_ev_ci_95_lo:.4f}"
        )
        notes.append(f"FAIL: {reason}")
        return _fail(
            output_dir, tier, notes,
            failure_reason=reason,
            coverage=coverage, rule4=rule4, scenario_output=scenario_output,
        )

    if tier == "T2":
        notes.append(
            "T2 reminder (out-of-scope for I-4): operator-approval memo required "
            "per IC §16 T2 workflow before merging."
        )

    # ----- Pass -----
    notes.append("PASS")
    verdict = GateVerdict(
        pass_=True,
        tier=tier,
        rule4=rule4,
        coverage_report_path=output_dir / "coverage_report.json",
        rule4_table_path=output_dir / "rule4_table.json",
        scenario_report_path=output_dir / "scenario_report.txt",
        notes=notes,
        failure_reason=None,
        output_dir=output_dir,
    )
    _write_outputs(
        output_dir, verdict,
        rule4=rule4, coverage=coverage,
        scenario_output=scenario_output, notes=notes,
    )
    return verdict


def _fail(
    output_dir: Path,
    tier: Tier,
    notes: list[str],
    *,
    failure_reason: str,
    coverage: CoverageReport | None,
    rule4: Rule4Table | None,
    scenario_output: str,
) -> GateVerdict:
    """Build + persist a FAIL verdict. Shared by every early-exit branch."""
    verdict = GateVerdict(
        pass_=False,
        tier=tier,
        rule4=rule4,
        coverage_report_path=output_dir / "coverage_report.json",
        rule4_table_path=output_dir / "rule4_table.json",
        scenario_report_path=output_dir / "scenario_report.txt",
        notes=notes,
        failure_reason=failure_reason,
        output_dir=output_dir,
    )
    _write_outputs(
        output_dir, verdict,
        rule4=rule4, coverage=coverage,
        scenario_output=scenario_output, notes=notes,
    )
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="replay_gate",
        description="Run the replay-as-CI gate (I-4) for a candidate change.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--changed-files-from-stdin",
        action="store_true",
        help="Read changed-file paths from stdin (one per line).",
    )
    src.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="A changed-file path (repeat for multiple).",
    )
    p.add_argument(
        "--corpora",
        default="all_diverse",
        help=(
            'Either "all_diverse" (default) or a comma-separated list of '
            "JSONL paths."
        ),
    )
    p.add_argument("--min-trades", type=int, default=_MIN_TRADES_DEFAULT)
    p.add_argument("--min-ev-ci-95-lo", type=float, default=0.0)
    p.add_argument("--coverage-threshold", type=float, default=_COVERAGE_DEFAULT)
    p.add_argument(
        "--allow-in-period-only",
        action="store_true",
        help=(
            "Include IN_PERIOD candidates in discovery; prospective OOS "
            "registration validation still applies."
        ),
    )
    p.add_argument("--config-diff", action="store_true")
    p.add_argument("--prompt-template-diff", action="store_true")
    p.add_argument("--model-manifest-diff", action="store_true")
    p.add_argument(
        "--schema-migration",
        action="append",
        default=[],
        help="A schema-migration file path (repeat for multiple).",
    )
    p.add_argument("--commit-sha", default=None)
    p.add_argument(
        "--no-scenarios",
        action="store_true",
        help="Skip the scenario suite (require_scenarios_pass=False).",
    )
    return p


def _parse_corpora_arg(value: str) -> list[Path] | Literal["all_diverse"]:
    if value == "all_diverse":
        return "all_diverse"
    return [Path(p.strip()) for p in value.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    if args.changed_files_from_stdin:
        changed = [Path(line.strip()) for line in sys.stdin if line.strip()]
    else:
        changed = [Path(p) for p in args.changed_file]

    corpora_sel = _parse_corpora_arg(args.corpora)

    spec = GateSpec(
        min_trades=args.min_trades,
        min_ev_ci_95_lo=args.min_ev_ci_95_lo,
        coverage_threshold=args.coverage_threshold,
        require_scenarios_pass=not args.no_scenarios,
        allow_in_period_only=args.allow_in_period_only,
    )

    try:
        verdict = run_replay_gate(
            changed_files=changed,
            corpora=corpora_sel,
            gate_spec=spec,
            commit_sha=args.commit_sha,
            config_diff=args.config_diff,
            prompt_template_diff=args.prompt_template_diff,
            model_manifest_diff=args.model_manifest_diff,
            schema_migrations=[Path(p) for p in args.schema_migration],
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"[replay_gate] infrastructure error: {exc}\n")
        return 2
    except GateError as exc:
        sys.stderr.write(f"[replay_gate] gate error: {exc}\n")
        return 2

    sys.stdout.write(
        json.dumps(_dataclass_to_json(verdict), indent=2, sort_keys=True) + "\n"
    )
    return 0 if verdict.pass_ else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "GateError",
    "InsufficientCorpusError",
    "CacheCoverageError",
    "ScenarioFailureError",
    "GateSpec",
    "GateVerdict",
    "Rule4Table",
    "run_replay_gate",
    "main",
]
