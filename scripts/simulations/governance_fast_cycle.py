"""Governance Phase 2 fast-cycle simulation — Task E.

Drives :func:`governance.agent.run_cycle` for one fast-cadence pass
against a temp filesystem (overrides YAML, audit log dir), a
``FakeLLM`` seeded with deterministic responses, and an
``audit_data_override`` payload that yields three Reddit-derived
``disable_source`` candidates (the same shape the integration tests
use, scoped to the canonical "all_stale / no_matches" classifications
the agent acts on).

Pins three contracts the Phase 2 shadow soak (PROFIT-PHASE2-001) is
otherwise dependent on the live launchd cadence to expose:

*   Shadow-mode invariant — every emitted ``GOVERNANCE_DECISION``
    record has ``applied=False`` regardless of LLM confidence.
*   Audit JSONL append-only — running two consecutive cycles strictly
    grows the log; previously-written records are not rewritten.
*   Kill-switch (read-only) — flipping ``GOVERNANCE_READONLY=true``
    blocks the apply path even if the overrides file declares
    ``mode='real'``.

Origin: PROFIT-EDGE-004 — Task E in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only contract: writes only to a tempdir owned by the harness;
nothing under ``data/runtime_overrides.yaml`` or
``logs/governance/`` is touched.

Usage
-----
::

    .venv/bin/python scripts/simulations/governance_fast_cycle.py
    .venv/bin/python scripts/simulations/governance_fast_cycle.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from governance.adapter import KalshiGovernanceAdapter  # noqa: E402
from governance.agent import load_state, run_cycle  # noqa: E402
from governance.audit import AuditLogger  # noqa: E402
from governance.evidence import (  # noqa: E402
    Candidate,
    compose_evidence_for_candidate,
    select_candidates_for_cadence,
)
from governance.llm import (  # noqa: E402
    FakeLLM,
    canned_response_for_action,
    prompt_hash,
)
from governance.prompts import render_prompt  # noqa: E402
from utils.runtime_overrides import (  # noqa: E402
    OverridesState,
    RuntimeOverridesReader,
    atomic_write_state,
)


# ── Synthetic audit data ──────────────────────────────────────────────────────
#
# Three Reddit subs with explicit "all_stale" / "no_matches" classifications
# above the ingestion floor (>= 20). select_candidates_for_cadence("fast")
# converts these to three disable_source Candidates.

_AUDIT_DATA_FAST_CYCLE = {
    "alignment": {"pairs": [], "overall_anchor_rate": 0.0, "overall_n": 0},
    "keywords": {"no_keyword_misses": 0, "candidate_phrases": []},
    "reddit": {
        "subs": [
            {
                "source": "r/Turkey",
                "ingestion": 408,
                "fresh_passes": 7,
                "matches": 0,
                "classification": "all_stale",
            },
            {
                "source": "r/pakistan",
                "ingestion": 80,
                "fresh_passes": 5,
                "matches": 0,
                "classification": "no_matches",
            },
            {
                "source": "r/Syria",
                "ingestion": 100,
                "fresh_passes": 0,
                "matches": 0,
                "classification": "all_stale",
            },
        ],
    },
    "freshness": {"sources": {}},
}


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CycleReport:
    cycle_label: str
    overrides_mode: str
    governance_readonly: bool
    governance_disabled: bool
    proposed_count: int
    applied_count: int
    audit_records_emitted: int
    decision_records: int
    cycle_start_records: int
    cycle_end_records: int
    audit_log_size_bytes: int
    kill_switch_tripped_during_cycle: bool


@dataclass(frozen=True)
class FastCycleRunReport:
    shadow_first_cycle: CycleReport
    shadow_second_cycle: CycleReport
    readonly_real_mode_cycle: CycleReport
    audit_log_size_grew: bool
    pre_existing_records_preserved: bool


# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_overrides(path: Path, *, mode: str) -> None:
    atomic_write_state(
        OverridesState(
            version=1,
            updated_at=datetime(2026, 5, 2, 14, 0, tzinfo=timezone.utc),
            updated_by="governance-fast-cycle-sim",
            mode=mode,
            applied_disabled_sources=[],
        ),
        path,
    )


def _build_adapter(*, root: Path) -> KalshiGovernanceAdapter:
    trade_log = root / "trades.jsonl"
    trade_log.write_text("", encoding="utf-8")
    paper_db = root / "paper.db"
    return KalshiGovernanceAdapter(
        trade_log_path=trade_log,
        paper_db_path=paper_db,
        market_provider=lambda: [],
    )


def _build_canned_llm(
    candidates: Iterable[Candidate],
    *,
    audit_data: dict,
    adapter: KalshiGovernanceAdapter,
) -> FakeLLM:
    canned: dict[str, str] = {}
    for cand in candidates:
        evidence = compose_evidence_for_candidate(cand, audit_data, adapter)
        sys_p, user_p = render_prompt(cand.action, evidence)
        canned[prompt_hash(sys_p, user_p)] = canned_response_for_action(
            cand.action, target=cand.target,
        )
    return FakeLLM(canned=canned)


def _read_audit_records(decisions_dir: Path) -> list[dict]:
    log = decisions_dir / "decisions.jsonl"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@contextmanager
def _kill_switch_env(*, disabled: bool = False, readonly: bool = False):
    """Set GOVERNANCE_DISABLED / GOVERNANCE_READONLY for the duration of
    a cycle; restore on exit. Each call to ``KillSwitch.is_*`` re-reads
    os.environ, so this is the only synchronization needed."""
    saved = {
        "GOVERNANCE_DISABLED": os.environ.get("GOVERNANCE_DISABLED"),
        "GOVERNANCE_READONLY": os.environ.get("GOVERNANCE_READONLY"),
    }
    if disabled:
        os.environ["GOVERNANCE_DISABLED"] = "true"
    else:
        os.environ.pop("GOVERNANCE_DISABLED", None)
    if readonly:
        os.environ["GOVERNANCE_READONLY"] = "true"
    else:
        os.environ.pop("GOVERNANCE_READONLY", None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _drive_one_cycle(
    *,
    label: str,
    decisions_dir: Path,
    overrides_path: Path,
    adapter: KalshiGovernanceAdapter,
    candidates: list[Candidate],
    audit_data: dict,
    readonly: bool = False,
) -> CycleReport:
    pre_existing = len(_read_audit_records(decisions_dir))

    with _kill_switch_env(readonly=readonly):
        loaded = load_state(overrides_path=overrides_path)
        llm = _build_canned_llm(
            candidates, audit_data=audit_data, adapter=adapter,
        )
        rc = run_cycle(
            cadence="fast",
            loaded_state=loaded,
            adapter=adapter,
            llm=llm,
            audit_logger=AuditLogger(log_dir=decisions_dir),
            overrides_path=overrides_path,
            candidate_override=candidates,
            audit_data_override=audit_data,
        )
        if rc != 0:
            raise RuntimeError(f"run_cycle exit={rc} for label={label}")

    records = _read_audit_records(decisions_dir)
    new_records = records[pre_existing:]
    decisions = [r for r in new_records if r.get("type") == "GOVERNANCE_DECISION"]
    starts = [r for r in new_records if r.get("type") == "GOVERNANCE_CYCLE_START"]
    ends = [r for r in new_records if r.get("type") == "GOVERNANCE_CYCLE_END"]
    proposed = sum(1 for d in decisions if d.get("applied") is False)
    applied = sum(1 for d in decisions if d.get("applied") is True)
    log_size = (decisions_dir / "decisions.jsonl").stat().st_size

    return CycleReport(
        cycle_label=label,
        overrides_mode=loaded.mode,
        governance_readonly=loaded.kill_switch_readonly,
        governance_disabled=False,  # if disabled, load_state would have raised
        proposed_count=proposed,
        applied_count=applied,
        audit_records_emitted=len(new_records),
        decision_records=len(decisions),
        cycle_start_records=len(starts),
        cycle_end_records=len(ends),
        audit_log_size_bytes=log_size,
        kill_switch_tripped_during_cycle=readonly,
    )


# ── Top-level runner ──────────────────────────────────────────────────────────


def run(*, work_dir: Optional[Path] = None) -> FastCycleRunReport:
    """Run three cycles: shadow×2 (append-only check) then real+readonly."""

    def _all(root: Path) -> FastCycleRunReport:
        decisions_dir = root / "logs" / "governance"
        overrides_shadow = root / "overrides_shadow.yaml"
        overrides_real = root / "overrides_real.yaml"
        _seed_overrides(overrides_shadow, mode="shadow")
        _seed_overrides(overrides_real, mode="real")

        adapter = _build_adapter(root=root)
        candidates = list(
            select_candidates_for_cadence(_AUDIT_DATA_FAST_CYCLE, cadence="fast")
        )
        if not candidates:
            raise RuntimeError("synthetic audit_data produced zero candidates")

        first = _drive_one_cycle(
            label="shadow-cycle-1",
            decisions_dir=decisions_dir,
            overrides_path=overrides_shadow,
            adapter=adapter,
            candidates=candidates,
            audit_data=_AUDIT_DATA_FAST_CYCLE,
        )

        # Snapshot the audit log content right before the second cycle so we
        # can verify cycle 2 strictly appended.
        log_path = decisions_dir / "decisions.jsonl"
        before_second = log_path.read_bytes()

        second = _drive_one_cycle(
            label="shadow-cycle-2",
            decisions_dir=decisions_dir,
            overrides_path=overrides_shadow,
            adapter=adapter,
            candidates=candidates,
            audit_data=_AUDIT_DATA_FAST_CYCLE,
        )

        after_second = log_path.read_bytes()
        pre_existing_preserved = after_second.startswith(before_second)
        size_grew = len(after_second) > len(before_second)

        readonly_real = _drive_one_cycle(
            label="real-mode + GOVERNANCE_READONLY=true",
            decisions_dir=decisions_dir,
            overrides_path=overrides_real,
            adapter=adapter,
            candidates=candidates,
            audit_data=_AUDIT_DATA_FAST_CYCLE,
            readonly=True,
        )

        # Final invariant: applied list on overrides files unchanged.
        for path in (overrides_shadow, overrides_real):
            reader = RuntimeOverridesReader(path=path)
            reader.reload()
            assert reader.snapshot().applied_disabled_sources == [], (
                f"{path.name}: shadow-mode invariant violated — applied list grew"
            )

        return FastCycleRunReport(
            shadow_first_cycle=first,
            shadow_second_cycle=second,
            readonly_real_mode_cycle=readonly_real,
            audit_log_size_grew=size_grew,
            pre_existing_records_preserved=pre_existing_preserved,
        )

    if work_dir is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        return _all(work_dir)

    with tempfile.TemporaryDirectory(prefix="governance-fast-cycle-") as tmp:
        return _all(Path(tmp))


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_cycle(report: CycleReport) -> None:
    print(f"\n• {report.cycle_label}")
    print(f"  overrides mode      : {report.overrides_mode}")
    print(f"  GOVERNANCE_READONLY : {report.governance_readonly}")
    print(
        f"  decisions emitted   : {report.decision_records}  "
        f"(proposed={report.proposed_count}, applied={report.applied_count})"
    )
    print(
        f"  audit records       : {report.audit_records_emitted}  "
        f"(start={report.cycle_start_records}, end={report.cycle_end_records})"
    )
    print(f"  log size            : {report.audit_log_size_bytes} bytes")


def _print_report(report: FastCycleRunReport) -> None:
    print("=" * 90)
    print("Governance Phase 2 fast-cycle simulation")
    print("=" * 90)
    _print_cycle(report.shadow_first_cycle)
    _print_cycle(report.shadow_second_cycle)
    _print_cycle(report.readonly_real_mode_cycle)
    print("\n" + "-" * 90)
    print(
        f"Append-only contract     : size_grew={report.audit_log_size_grew}  "
        f"prefix_preserved={report.pre_existing_records_preserved}"
    )
    print("-" * 90)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive three governance fast cycles for shadow-mode + kill-switch contracts.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="optional persistent work dir (default: tempdir, deleted on exit)",
    )
    args = parser.parse_args(argv)

    report = run(work_dir=args.work_dir)
    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
