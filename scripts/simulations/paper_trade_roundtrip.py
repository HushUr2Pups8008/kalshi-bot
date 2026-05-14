"""Paper-trade INSERT path simulation — Task C of the pipeline buildout.

Drives a *real* :class:`trading.paper_trader.PaperTrader` against a temp
SQLite database, wired to a paper-mode :class:`trading.executor.TradeExecutor`,
and pushes each canonical LLM-positive event through ``execute()``.

Each event runs on a freshly-instantiated executor + DB. That isolates
ticker-cooldown / opposing-position interactions (which already have
their own coverage in ``executor_validate.py``) and makes the contract
under test purely the INSERT path:

    1. ``executor.execute(SignalAnalysis)``
    2. → ``executor._validate(...)`` (paper gates)
    3. → ``executor._execute_paper(...)``
    4. → ``paper_trader.record_trade(analysis)``
    5. → SQLite INSERT into ``paper_trades`` + bankroll debit

Production ran for 8 days at v0.29.54 with zero rows in ``paper_trades``
because the readiness gate killed every signal upstream of this path.
EDGE-001/002/003 reopened the gate in v0.29.58, so this harness pins
the *next* link in the chain — the row write itself.

Origin: PROFIT-EDGE-004 — Task C in
``docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md``.

Read-only contract is upheld by writing to a tempdir; nothing under
``data/`` or ``logs/`` is touched.

Usage
-----
::

    .venv/bin/python scripts/simulations/paper_trade_roundtrip.py
    .venv/bin/python scripts/simulations/paper_trade_roundtrip.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import SignalAnalysis  # noqa: E402
from config import PAPER_FLAT_CONTRACTS  # noqa: E402
from feeds import NewsItem  # noqa: E402
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENT_HEADLINES_2026_04_26,
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    synthetic_market,
)
from trading.executor import TradeExecutor  # noqa: E402
from trading.paper_trader import PaperTrader  # noqa: E402


_DEFAULT_BANKROLL = 1000.0


# ── Reports ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoundtripReport:
    event_name: str
    ticker: str
    accepted: bool
    skip_reason: Optional[str]
    inserted_trade_id: Optional[str]
    inserted_row: Optional[dict]
    bankroll_before: float
    bankroll_after: float
    bankroll_delta: float
    expected_cost_dollars: Optional[float]
    source_multiplier_persisted: Optional[float]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _signal_analysis(event: LLMPositiveEvent, headline: str) -> SignalAnalysis:
    market = synthetic_market(event)
    estimated_prob = event.fast_p
    edge = estimated_prob - market.yes_price / 100.0
    # capped_dollars not used by paper INSERT (paper uses PAPER_FLAT_CONTRACTS)
    # but the live path branches on it, so populate something realistic.
    price_cents = max(1, min(99, int(market.yes_price)))
    capped = PAPER_FLAT_CONTRACTS * price_cents / 100.0
    # P-6 / LD-10: paper recorder consumes executed_price_cents; map to the
    # chosen side's executable ask so the simulation continues to record
    # trades the same way it did pre-P-6.
    if event.side == "yes":
        executed_cents = market.yes_ask_cents or price_cents
    else:
        executed_cents = market.no_ask_cents or max(1, min(99, 100 - price_cents))
    return SignalAnalysis(
        news_item=NewsItem(
            headline=headline,
            url="https://example.invalid/roundtrip",
            source="paper-trade-roundtrip",
            published=datetime(2026, 4, 26, tzinfo=timezone.utc),
        ),
        market=market,
        estimated_probability=estimated_prob,
        executed_price_cents=int(executed_cents),  # F-16: canonical post-P0; __post_init__ mirrors to market_yes_price
        edge=edge,
        side=event.side,
        kelly_fraction=0.10,
        kelly_dollars=2.50,
        capped_dollars=capped,
        keywords_matched=[],
        confidence=event.fast_conf,
        match_score=0.50,
        signal_type="news",
        llm_direction=event.side,
        llm_magnitude="small",
        llm_confidence=event.fast_conf,
        signal_meta={},
    )


def _make_real_paper_executor(db_path: Path, *, bankroll: float = _DEFAULT_BANKROLL) -> TradeExecutor:
    """Build a paper-mode TradeExecutor over a real PaperTrader.

    The PaperTrader is constructed with ``startup_context="test"`` so the
    process-wide runtime guard does not fire, the credibility tracker is
    real (auto-instantiated by ``initialize()``), and the bankroll is
    seeded explicitly so the row-write captures a deterministic
    ``notional_bankroll_before`` value.
    """
    paper = PaperTrader(db_path=db_path, startup_context="test")
    paper._set_state("notional_bankroll", str(round(bankroll, 4)))
    rest = MagicMock(name="rest_client_unused_in_paper")
    return TradeExecutor(rest_client=rest, paper_trader=paper)


def _read_inserted_row(db_path: Path, trade_id: str) -> Optional[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _expected_paper_cost(market, side: str) -> float:
    """P-6 / LD-10: paper recorder consumes ``executed_price_cents`` on the
    chosen side, so the expected debit mirrors that executable ask rather
    than the legacy midpoint synthesis."""
    if side == "yes":
        price_cents = market.yes_ask_cents
    else:
        price_cents = market.no_ask_cents
    if price_cents is None:
        # Fallback shouldn't fire under synthetic_market but keep the math
        # defined rather than raising in a harness path.
        yes_int = max(1, min(99, int(market.yes_price)))
        price_cents = yes_int if side == "yes" else max(1, min(99, 100 - yes_int))
    price_cents = max(1, min(99, int(price_cents)))
    return PAPER_FLAT_CONTRACTS * price_cents / 100.0


# ── Per-event simulation ──────────────────────────────────────────────────────


async def _run_event(
    event: LLMPositiveEvent,
    *,
    root_dir: Path,
    slot: int,
) -> RoundtripReport:
    headline = LLM_POSITIVE_EVENT_HEADLINES_2026_04_26.get(event.name, event.title)
    analysis = _signal_analysis(event, headline)

    # Slot index disambiguates events 3 + 5, which share KXTRUMPIRAN-26MAY01
    # and would otherwise reuse the same DB file across the loop.
    db_path = root_dir / f"paper_trades-{slot:02d}-{event.ticker}.db"
    executor = _make_real_paper_executor(db_path)

    bankroll_before = executor._paper.get_notional_bankroll()
    trade_id = await executor.execute(analysis)
    bankroll_after = executor._paper.get_notional_bankroll()

    skip_reason: Optional[str] = None if trade_id else executor._validate(analysis)
    inserted_row = _read_inserted_row(db_path, trade_id) if trade_id else None
    source_mult = (
        float(inserted_row["source_multiplier"])
        if inserted_row and "source_multiplier" in inserted_row
        and inserted_row["source_multiplier"] is not None
        else None
    )

    expected_cost = (
        _expected_paper_cost(analysis.market, analysis.side)
        if trade_id
        else None
    )

    return RoundtripReport(
        event_name=event.name,
        ticker=event.ticker,
        accepted=trade_id is not None,
        skip_reason=skip_reason,
        inserted_trade_id=trade_id,
        inserted_row=inserted_row,
        bankroll_before=bankroll_before,
        bankroll_after=bankroll_after,
        bankroll_delta=bankroll_after - bankroll_before,
        expected_cost_dollars=expected_cost,
        source_multiplier_persisted=source_mult,
    )


def run(*, db_root: Optional[Path] = None) -> list[RoundtripReport]:
    """Run the round-trip sim for every canonical event.

    If ``db_root`` is None, a tempdir is created for the duration of the
    run and torn down on exit.
    """
    async def _all(root: Path) -> list[RoundtripReport]:
        return [
            await _run_event(ev, root_dir=root, slot=i)
            for i, ev in enumerate(LLM_POSITIVE_EVENTS_2026_04_26)
        ]

    if db_root is not None:
        db_root.mkdir(parents=True, exist_ok=True)
        return asyncio.run(_all(db_root))

    with tempfile.TemporaryDirectory(prefix="paper-trade-roundtrip-") as tmp:
        return asyncio.run(_all(Path(tmp)))


# ── CLI rendering ─────────────────────────────────────────────────────────────


def _print_per_event(reports: list[RoundtripReport]) -> None:
    print("=" * 100)
    print("Paper-trade round-trip sim — five canonical LLM-positive events")
    print("=" * 100)
    for r in reports:
        flag = "  ✓" if r.accepted else "  ✗"
        print(f"\n{flag} {r.event_name}")
        print(f"  ticker          : {r.ticker}")
        if r.accepted:
            print(f"  trade_id        : {r.inserted_trade_id}")
            print(
                f"  bankroll        : ${r.bankroll_before:.2f} → ${r.bankroll_after:.2f}  "
                f"(Δ={r.bankroll_delta:+.4f})  expected cost=${r.expected_cost_dollars:.4f}"
            )
            print(f"  source_mult     : {r.source_multiplier_persisted}")
            row = r.inserted_row or {}
            print(
                f"  row preview     : "
                f"side={row.get('side')!r:<7} "
                f"contracts={row.get('contracts')} "
                f"price_cents={row.get('price_cents')} "
                f"cost_dollars={row.get('cost_dollars')} "
                f"edge={row.get('edge')} "
                f"signal_type={row.get('signal_type')!r}"
            )
        else:
            print(f"  skipped: {r.skip_reason}")


def _print_summary(reports: list[RoundtripReport]) -> None:
    accepted = sum(1 for r in reports if r.accepted)
    inserted = sum(1 for r in reports if r.inserted_trade_id)
    print("\n" + "-" * 100)
    print(
        f"Summary: {accepted}/{len(reports)} accepted, "
        f"{inserted} rows inserted into paper_trades"
    )
    print("-" * 100)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the paper-trade round-trip sim against a temp SQLite DB.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSONL to stdout")
    parser.add_argument(
        "--db-root",
        type=Path,
        default=None,
        help="optional persistent DB directory (default: tempdir, deleted on exit)",
    )
    args = parser.parse_args(argv)

    reports = run(db_root=args.db_root)

    if args.json:
        for r in reports:
            print(json.dumps(asdict(r), separators=(",", ":"), default=str))
    else:
        _print_per_event(reports)
        _print_summary(reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
