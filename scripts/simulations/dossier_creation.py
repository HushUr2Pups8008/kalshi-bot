"""Evidence → dossier creation simulation — Task G.

Streams synthetic ``analysis.evidence_types.Evidence`` records into
:class:`tasks.accumulation_task.AccumulationTask` against a temp
:class:`tasks.evidence_store.EvidenceStore` and records *when* the
dossier first appears in the store.

Trigger condition (Task G1, traced from
``tasks/accumulation_task.py:_process_evidence_locked``):

* On the first evidence for a ticker, ``store.get_dossier`` returns
  ``None``. The task constructs a baseline dossier with version 0 via
  ``_initial_dossier`` and persists it via
  ``store.update_dossier(_state_from_dossier(initial))``.
* It then writes the evidence record and persists the *next* dossier
  state (version ≥ 1) carrying the scored update.
* Effect: dossier is created **eagerly on N=1** — no minimum-evidence
  threshold gate. PROFIT-EDGE-002's ``12 of 847 active markets had
  dossiers (~1.4%)`` observation is therefore a *coverage* problem
  driven by which markets receive evidence at all, not a creation
  threshold. See ``PROFIT-DOSSIER-001`` in
  ``docs/profit_path_debt_log.md`` for the full re-framing and the
  empirical 100% OPPORTUNITY → dossier alignment as of 2026-05-02.

Origin: PROFIT-EDGE-004 — Task G in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only contract: writes only to a tempdir owned by the harness;
nothing under ``data/evidence_store.db`` is touched.

Usage
-----
::

    .venv/bin/python scripts/simulations/dossier_creation.py
    .venv/bin/python scripts/simulations/dossier_creation.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.evidence_types import Evidence  # noqa: E402
from tasks.accumulation_task import AccumulationTask  # noqa: E402
from tasks.evidence_store import EvidenceStore  # noqa: E402


_DEFAULT_TICKER = "KXTRUMPIRAN-26MAY01"


def _make_evidence(*, ticker: str, index: int, base_ts: datetime) -> Evidence:
    sources = [
        ("Reuters", "news"),
        ("State Department", "official"),
        ("AP", "news"),
        ("White House", "official"),
        ("Times of Israel", "news"),
    ]
    src, src_class = sources[index % len(sources)]
    return Evidence(
        evidence_id=f"sim-ev-{index:03d}",
        market_ticker=ticker,
        source=src,
        source_class=src_class,
        headline=f"Synthetic evidence {index:03d} for dossier_creation sim",
        ingested_ts=(base_ts + timedelta(minutes=index)).isoformat(),
        implied_probability=0.5 + (0.05 * (index % 3 - 1)),
        content_hash=f"hash-sim-{index:03d}",
        published_ts=(base_ts + timedelta(minutes=index)).isoformat(),
    )


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepObservation:
    n: int
    dossier_present: bool
    dossier_version: Optional[int]
    recent_evidence_count: int


@dataclass(frozen=True)
class DossierCreationReport:
    ticker: str
    evidence_count: int
    dossier_created: bool
    creation_threshold_reached_at_n: Optional[int]
    final_dossier_version: Optional[int]
    final_recent_evidence_count: int
    pre_first_evidence_dossier_present: bool
    step_observations: tuple[StepObservation, ...]


# ── Harness ───────────────────────────────────────────────────────────────────


async def _run_async(*, ticker: str, evidence_count: int, db_path: Path) -> DossierCreationReport:
    store = EvidenceStore(db_path=db_path)
    task = AccumulationTask(store=store)

    base_ts = datetime(2026, 4, 26, tzinfo=timezone.utc)

    # Snapshot before any evidence is ingested — confirms the "below threshold"
    # baseline is genuinely empty (no dossier present at N=0).
    pre_dossier = await store.get_dossier(ticker)
    pre_present = pre_dossier is not None

    creation_n: Optional[int] = None
    observations: list[StepObservation] = []

    for i in range(1, evidence_count + 1):
        await task.process_evidence(_make_evidence(ticker=ticker, index=i, base_ts=base_ts))
        dossier = await store.get_dossier(ticker)
        recent = await store.get_recent_evidence(ticker, limit=evidence_count + 5)
        if dossier is not None and creation_n is None:
            creation_n = i
        observations.append(StepObservation(
            n=i,
            dossier_present=dossier is not None,
            dossier_version=dossier.dossier_version if dossier else None,
            recent_evidence_count=len(recent),
        ))

    final = await store.get_dossier(ticker)
    final_recent = await store.get_recent_evidence(ticker, limit=evidence_count + 5)

    return DossierCreationReport(
        ticker=ticker,
        evidence_count=evidence_count,
        dossier_created=final is not None,
        creation_threshold_reached_at_n=creation_n,
        final_dossier_version=final.dossier_version if final else None,
        final_recent_evidence_count=len(final_recent),
        pre_first_evidence_dossier_present=pre_present,
        step_observations=tuple(observations),
    )


def run(
    *,
    ticker: str = _DEFAULT_TICKER,
    evidence_count: int = 5,
    db_root: Optional[Path] = None,
) -> DossierCreationReport:
    """Stream ``evidence_count`` synthetic evidence records and return the
    creation report. ``db_root`` defaults to a tempdir torn down on exit."""
    if evidence_count < 1:
        raise ValueError("evidence_count must be >= 1")

    async def _run(root: Path) -> DossierCreationReport:
        db_path = root / "evidence_store.db"
        return await _run_async(
            ticker=ticker,
            evidence_count=evidence_count,
            db_path=db_path,
        )

    if db_root is not None:
        db_root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(_run(db_root))

    with tempfile.TemporaryDirectory(prefix="dossier-creation-") as tmp:
        return asyncio.run(_run(Path(tmp)))


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_report(report: DossierCreationReport) -> None:
    print("=" * 90)
    print("Dossier creation simulation — evidence → dossier flow")
    print("=" * 90)
    print(f"\n  ticker                         : {report.ticker}")
    print(f"  evidence streamed              : {report.evidence_count}")
    print(f"  dossier present at N=0         : {report.pre_first_evidence_dossier_present}")
    print(f"  dossier created                : {report.dossier_created}")
    print(f"  creation reached at N          : {report.creation_threshold_reached_at_n}")
    print(f"  final dossier version          : {report.final_dossier_version}")
    print(f"  final evidence count in store  : {report.final_recent_evidence_count}")
    print("\n  Per-step observations:")
    print(f"    {'N':>3}  {'dossier?':<8}  {'version':>7}  {'evidence_count':>14}")
    for obs in report.step_observations:
        print(
            f"    {obs.n:>3}  {str(obs.dossier_present):<8}  "
            f"{obs.dossier_version if obs.dossier_version is not None else '—':>7}  "
            f"{obs.recent_evidence_count:>14}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stream synthetic evidence into AccumulationTask and observe dossier creation.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    parser.add_argument("--evidence-count", type=int, default=5)
    parser.add_argument("--ticker", type=str, default=_DEFAULT_TICKER)
    parser.add_argument(
        "--db-root",
        type=Path,
        default=None,
        help="optional persistent DB directory (default: tempdir, deleted on exit)",
    )
    args = parser.parse_args(argv)

    report = run(
        ticker=args.ticker,
        evidence_count=args.evidence_count,
        db_root=args.db_root,
    )
    if args.json:
        json.dump(asdict(report), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
