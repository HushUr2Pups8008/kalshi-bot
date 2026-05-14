"""Executor (E1–E12) validation simulation against the canonical 5 events.

Constructs a paper-mode :class:`trading.executor.TradeExecutor` with empty
state (no portfolio, no cooldowns, fresh notional bankroll) and runs the
actual ``_validate()`` method against synthetic
:class:`analysis.SignalAnalysis` objects matching each canonical event.

Two passes:

*  **Independent** — each event evaluated on a fresh executor, isolating
   intrinsic-pass / fail behaviour.
*  **Sequential** — all events evaluated in firing order on the same
   executor, exercising cooldown + opposing-position interactions
   (e.g. Event 5 KXTRUMPIRAN within 4 h of Event 3 must be cooldown-suppressed).

Origin: PROFIT-EDGE-002 follow-up simulation, formalised from
``/tmp/executor_simulation_v0.29.58.py`` on 2026-04-26.

Usage
-----
::

    .venv/bin/python scripts/simulations/executor_validate.py
    .venv/bin/python scripts/simulations/executor_validate.py --pass independent
    .venv/bin/python scripts/simulations/executor_validate.py --pass sequential

The simulation never mutates state — the executor is constructed with a
mock paper-trader.
"""
from __future__ import annotations

import argparse
import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis import SignalAnalysis  # noqa: E402
from feeds import NewsItem  # noqa: E402
from config import (  # noqa: E402
    cfg,
    PAPER_BLOCK_SAME_SIDE_DUPLICATE,
    PAPER_FLAT_CONTRACTS,
    PAPER_MIN_EDGE,
)
from scripts.simulations._common import (  # noqa: E402
    LLM_POSITIVE_EVENTS_2026_04_26,
    LLMPositiveEvent,
    synthetic_market,
)
from trading.executor import TradeExecutor  # noqa: E402


@dataclass
class ValidationResult:
    event_name: str
    ticker: str
    edge: float
    effective_min_edge: float
    side: str
    skip_reason: Optional[str]


def _news_item(headline: str, source: str = "ReliefWeb") -> NewsItem:
    return NewsItem(
        headline=headline,
        url="https://example.invalid/news",
        source=source,
        published=datetime.now(timezone.utc),
    )


def _signal_analysis_from_event(
    event: LLMPositiveEvent,
    *,
    market_price_cents: float = 50.0,
) -> SignalAnalysis:
    market = synthetic_market(event, yes_price_cents=market_price_cents)
    estimated_prob = event.fast_p
    edge = estimated_prob - market_price_cents / 100.0
    capped = PAPER_FLAT_CONTRACTS * max(1, min(99, int(market_price_cents))) / 100.0
    return SignalAnalysis(
        news_item=_news_item(f"news for {event.ticker}"),
        market=market,
        estimated_probability=estimated_prob,
        executed_price_cents=int(round(market_price_cents)),  # F-16: canonical post-P0; __post_init__ mirrors to market_yes_price
        edge=edge,
        side=event.side,
        kelly_fraction=0.10,
        kelly_dollars=2.50,
        capped_dollars=capped,
        confidence=event.fast_conf,
        match_score=0.50,
        signal_type="news",
        llm_direction=event.side,
        llm_magnitude="small",
        llm_confidence=event.fast_conf,
        signal_meta={},  # no readiness_gate_min_edge_override
    )


def _make_paper_executor() -> TradeExecutor:
    """Construct a paper-mode TradeExecutor with empty mock state.

    Mocks PaperTrader + Portfolio + RestClient with the minimal surface
    ``_validate()`` reads. Stays well short of touching the real database.
    """
    portfolio = MagicMock()
    portfolio.open_positions = MagicMock(return_value=[])
    portfolio.is_concentration_ok = MagicMock(return_value=True)
    portfolio.exposure = MagicMock(return_value=0.0)

    paper = MagicMock()
    paper.portfolio = portfolio
    paper.get_notional_bankroll = MagicMock(return_value=1000.0)
    paper.cooldowns = {}
    paper.credibility = MagicMock()
    paper.credibility.get_multiplier = MagicMock(return_value=1.0)

    e = TradeExecutor.__new__(TradeExecutor)
    e._paper = paper
    e._rest = MagicMock()
    # CR-D re-fetch: return None so executor falls back to the candidate
    # snapshot, which simulations populate with full post-P0 cents fields.
    e._rest.get_market.return_value = None
    e._is_paper = True
    e._last_traded = {}
    e._live_halted = False
    e._session_start_balance = 0.0
    return e


def run_independent() -> list[ValidationResult]:
    """Each event on a fresh executor — surfaces intrinsic gate behaviour."""
    out: list[ValidationResult] = []
    for ev in LLM_POSITIVE_EVENTS_2026_04_26:
        e = _make_paper_executor()
        analysis = _signal_analysis_from_event(ev)
        out.append(ValidationResult(
            event_name=ev.name,
            ticker=ev.ticker,
            edge=analysis.edge,
            effective_min_edge=e._min_edge_threshold(analysis),
            side=ev.side,
            skip_reason=e._validate(analysis),
        ))
    return out


def run_sequential() -> list[ValidationResult]:
    """All events on the same executor in firing order — exercises cooldown
    + opposing-position guards."""
    e = _make_paper_executor()
    open_positions: dict[str, list] = {}
    e._paper.portfolio.open_positions = lambda ticker: open_positions.get(ticker, [])

    out: list[ValidationResult] = []
    for ev in LLM_POSITIVE_EVENTS_2026_04_26:
        analysis = _signal_analysis_from_event(ev)
        skip = e._validate(analysis)
        out.append(ValidationResult(
            event_name=ev.name,
            ticker=ev.ticker,
            edge=analysis.edge,
            effective_min_edge=e._min_edge_threshold(analysis),
            side=ev.side,
            skip_reason=skip,
        ))
        if skip is None:
            # Simulate trade record — cooldown + open position update
            e._last_traded[ev.ticker] = _time.monotonic()
            open_positions.setdefault(ev.ticker, []).append(SimpleNamespace(
                side=ev.side,
                estimated_prob=analysis.estimated_probability,
                # F-11 P1-A: alias-removal; mirror executed-side price under
                # the canonical name (Position.entry_price_cents).
                entry_price_cents=analysis.executed_price_cents,
            ))
    return out


def _print_pass(label: str, results: list[ValidationResult]) -> None:
    print("=" * 100)
    print(f"Pass: {label}")
    print("=" * 100)
    for r in results:
        status = "PASS  → would record paper trade" if r.skip_reason is None \
            else f"SKIP  → {r.skip_reason}"
        print(f"\n  {r.event_name}")
        print(f"    ticker={r.ticker}  side={r.side.upper()}  edge={r.edge:+.4f}  "
              f"effective_min_edge={r.effective_min_edge:.4f}")
        print(f"    {status}")
    n_pass = sum(1 for r in results if r.skip_reason is None)
    print(f"\n  Summary: {n_pass}/{len(results)} events accepted by executor")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", 1)[0],
    )
    parser.add_argument(
        "--pass",
        dest="pass_kind",
        choices=("independent", "sequential", "both"),
        default="both",
        help="which simulation pass(es) to run (default: both)",
    )
    args = parser.parse_args(argv)

    print(f"Constants:  PAPER_MIN_EDGE={PAPER_MIN_EDGE}  PAPER_FLAT_CONTRACTS={PAPER_FLAT_CONTRACTS}")
    print(f"            PAPER_BLOCK_SAME_SIDE_DUPLICATE={PAPER_BLOCK_SAME_SIDE_DUPLICATE}")
    print(f"            paper_ticker_cooldown={cfg.paper_ticker_cooldown}s "
          f"({cfg.paper_ticker_cooldown/3600:.1f}h)")
    print(f"            max_ticker_exposure_pct={cfg.max_ticker_exposure_pct}\n")

    if args.pass_kind in ("independent", "both"):
        _print_pass("independent (each event on a fresh executor)", run_independent())
        print()
    if args.pass_kind in ("sequential", "both"):
        _print_pass("sequential (events evaluated in firing order, shared state)", run_sequential())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
