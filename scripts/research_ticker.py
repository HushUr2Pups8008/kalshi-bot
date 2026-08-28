#!/usr/bin/env python3
"""Replay one Kalshi ticker through the politics research/decision path.

This is the repeatable operator probe for "how will the desk handle this
book?" It calls the same functions the politics process uses:

- favorite-band routing (`event_news_favorite_side`)
- Factbase remaining-time probability (`truth_social_range_probability`)
- both-sides spread-buffer edge (`decide_research_verdict` formula)
- optional live `run_research_gate` (prewarm-equivalent)
- optional concurrent LLM (`estimate_probability`)

It never blends, sizes, or submits orders. The default dossier is a temp
SQLite file so a probe cannot contaminate the sealed politics sample.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.research_gate import run_research_gate
from analysis.signal_analyzer import estimate_probability
from analysis.truth_social_forecast import (
    fetch_truth_social_count,
    truth_social_range_probability,
)
from config import cfg
from feeds import NewsItem
from kalshi.rest_client import KalshiRestClient
from tasks.research_dossier import (
    DEFAULT_RESEARCH_DOSSIER_DB_PATH,
    ResearchDossierStore,
)
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    _TAKER_FEE_COEFFICIENT,
    event_news_favorite_side,
    event_news_min_edge,
    event_news_official_research_kwargs,
    event_news_prewarm_skip_reason,
    is_event_news_paper_cohort,
    snapshot_ask_cents,
)

_DEFAULT_COHORT = EVENT_NEWS_COHORT_ID

_SPREAD_BUFFER = 0.01
_GATE_PAPER_MIN_EDGE = 0.02
_GATE_LIVE_MIN_EDGE = 0.04


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="Kalshi market ticker to replay.")
    parser.add_argument(
        "--yes-ask",
        type=float,
        default=None,
        help="Pin YES ask (0.38 or 38). Defaults to the live book.",
    )
    parser.add_argument(
        "--no-ask",
        type=float,
        default=None,
        help="Pin NO ask (0.63 or 63). Defaults to the live book.",
    )
    parser.add_argument(
        "--cohort",
        default=_DEFAULT_COHORT,
        help="Paper cohort id. Defaults to the politics event-news desk.",
    )
    parser.add_argument(
        "--live-mode",
        action="store_true",
        help="Score with live min-edge (0.04). Default is paper (0.02).",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Run estimate_probability concurrently with the research gate.",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Skip run_research_gate; still score routing + structured p.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=18.0,
        help="End-to-end research-gate timeout. Default matches politics.",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=10,
        help="Research queries for the gate. Default matches politics.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Research dossier SQLite path. Ignored unless --write-dossier.",
    )
    parser.add_argument(
        "--write-dossier",
        action="store_true",
        help=(
            "Persist gate evidence to --db-path. Refuses the live "
            "evidence_store.db. Default uses a throwaway temp DB."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a human summary.",
    )
    return parser


@contextmanager
def activate_cohort(cohort_id: str) -> Iterator[str]:
    """Temporarily switch this process onto the requested desk, then restore."""
    cohort = str(cohort_id or "").strip().lower() or _DEFAULT_COHORT
    prev_env_id = os.environ.get("PAPER_COHORT_ID")
    prev_env_kind = os.environ.get("PAPER_COHORT_KIND")
    prev_cfg_id = cfg.paper_cohort_id
    prev_cfg_kind = cfg.paper_cohort_kind
    os.environ["PAPER_COHORT_ID"] = cohort
    if cohort == EVENT_NEWS_COHORT_ID:
        os.environ["PAPER_COHORT_KIND"] = "active"
        cfg.paper_cohort_kind = "active"
    cfg.paper_cohort_id = cohort
    try:
        yield cohort
    finally:
        if prev_env_id is None:
            os.environ.pop("PAPER_COHORT_ID", None)
        else:
            os.environ["PAPER_COHORT_ID"] = prev_env_id
        if prev_env_kind is None:
            os.environ.pop("PAPER_COHORT_KIND", None)
        else:
            os.environ["PAPER_COHORT_KIND"] = prev_env_kind
        cfg.paper_cohort_id = prev_cfg_id
        cfg.paper_cohort_kind = prev_cfg_kind


def parse_ask(value: float | None) -> tuple[int, float] | None:
    if value is None:
        return None
    number = float(value)
    if number > 1.0:
        cents = int(round(number))
        return cents, cents / 100.0
    cents = int(round(number * 100.0))
    return cents, number


def apply_pinned_asks(market: Any, yes_ask: float | None, no_ask: float | None) -> Any:
    pinned_yes = parse_ask(yes_ask)
    pinned_no = parse_ask(no_ask)
    if pinned_yes is not None:
        cents, prob = pinned_yes
        setattr(market, "yes_ask_cents", cents)
        setattr(market, "yes_ask", prob)
        # KalshiMarket.yes_prob is yes_price/100. Pin the mid so --llm
        # scores the same book the gate sees, not the live print.
        setattr(market, "yes_price", float(cents))
        setattr(market, "last_price_cents", cents)
        setattr(market, "price_available", True)
    if pinned_no is not None:
        cents, prob = pinned_no
        setattr(market, "no_ask_cents", cents)
        setattr(market, "no_ask", prob)
    return market


def _executable_asks(market: Any) -> tuple[float | None, float | None]:
    yes_cents, no_cents = snapshot_ask_cents(market)
    yes_ask = None if yes_cents is None else yes_cents / 100.0
    no_ask = None if no_cents is None else no_cents / 100.0
    return yes_ask, no_ask


def _actionable_ask(value: float | None) -> bool:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return 0.0 < price < 1.0


def _contract_query(market: Any) -> str:
    parts = [
        str(getattr(market, name, "") or "").strip()
        for name in ("title", "subtitle", "question", "rules_primary", "ticker")
    ]
    return " ".join(part for part in parts if part)


def both_sides_edges(
    p_yes: float,
    yes_ask: float | None,
    no_ask: float | None,
    *,
    live_mode: bool,
) -> dict[str, Any]:
    min_edge = _GATE_LIVE_MIN_EDGE if live_mode else _GATE_PAPER_MIN_EDGE
    yes_ok = _actionable_ask(yes_ask)
    no_ok = _actionable_ask(no_ask)
    yes_raw = p_yes - float(yes_ask) if yes_ok else None
    no_raw = (1.0 - p_yes) - float(no_ask) if no_ok else None
    yes_net = yes_raw - _SPREAD_BUFFER if yes_raw is not None else None
    no_net = no_raw - _SPREAD_BUFFER if no_raw is not None else None
    fee_yes = (
        _TAKER_FEE_COEFFICIENT * float(yes_ask) * (1.0 - float(yes_ask))
        if yes_ok
        else None
    )
    fee_no = (
        _TAKER_FEE_COEFFICIENT * float(no_ask) * (1.0 - float(no_ask))
        if no_ok
        else None
    )

    def _safe(ask: float | None) -> bool:
        if ask is None:
            return False
        if not live_mode:
            return True
        return 0.03 < float(ask) < 0.97

    candidates: list[tuple[str, float]] = []
    if yes_net is not None and yes_net >= min_edge and _safe(yes_ask):
        candidates.append(("yes", yes_net))
    if no_net is not None and no_net >= min_edge and _safe(no_ask):
        candidates.append(("no", no_net))
    selected = None
    if candidates:
        selected = max(candidates, key=lambda item: (item[1], item[0] == "no"))[0]
    return {
        "p_yes": p_yes,
        "min_edge": min_edge,
        "spread_buffer": _SPREAD_BUFFER,
        "yes": {
            "ask": yes_ask,
            "raw_edge": yes_raw,
            "net_edge": yes_net,
            "taker_fee": fee_yes,
        },
        "no": {
            "ask": no_ask,
            "raw_edge": no_raw,
            "net_edge": no_net,
            "taker_fee": fee_no,
        },
        "selected_side": selected,
    }


def routing_snapshot(market: Any, *, config: Any = None) -> dict[str, Any]:
    yes_cents, no_cents = snapshot_ask_cents(market)
    favorite_side, favorite_cents = event_news_favorite_side(market, config=config)
    skip_reason = event_news_prewarm_skip_reason(market, config=config)
    min_edge = event_news_min_edge(_GATE_PAPER_MIN_EDGE, market, config=config)
    return {
        "yes_ask_cents": yes_cents,
        "no_ask_cents": no_cents,
        "favorite_side": favorite_side,
        "favorite_cents": favorite_cents,
        "prewarm_skip_reason": skip_reason,
        "fee_adjusted_min_edge": min_edge,
        "score_both_sides": is_event_news_paper_cohort(config),
    }


def _verdict_dict(verdict: Any) -> dict[str, Any]:
    status = getattr(verdict, "status", None)
    return {
        "status": getattr(status, "value", status),
        "skip_reason": getattr(verdict, "skip_reason", None),
        "force_side": getattr(verdict, "force_side", None),
        "estimated_probability": getattr(verdict, "estimated_probability", None),
        "estimated_edge": getattr(verdict, "estimated_edge", None),
        "confidence": getattr(verdict, "confidence", None),
        "summary": getattr(verdict, "summary", None),
        "decision_grade_reasons": list(
            getattr(verdict, "decision_grade_reasons", ()) or ()
        ),
        "evidence_count": len(getattr(verdict, "evidence", ()) or ()),
        "query_count": len(getattr(verdict, "queries", ()) or ()),
        "evidence": [
            {
                "source_name": getattr(item, "source_name", None),
                "source_class": getattr(item, "source_class", None),
                "supports_direction": getattr(item, "supports_direction", None),
                "metric_name": getattr(item, "metric_name", None),
                "metric_value": getattr(item, "metric_value", None),
                "snippet": (getattr(item, "snippet", None) or "")[:240],
            }
            for item in (getattr(verdict, "evidence", ()) or ())[:8]
        ],
    }


def _structured_probability(market: Any) -> dict[str, Any]:
    query = _contract_query(market)
    observation = fetch_truth_social_count(query)
    payload: dict[str, Any] = {
        "query": query,
        "observation": None,
        "p_yes": None,
        "state": None,
        "confidence": None,
    }
    if observation is None:
        return payload
    payload["observation"] = {
        "count": observation.count,
        "deleted_count": observation.deleted_count,
        "window_start": observation.window_start.isoformat(),
        "window_end": observation.window_end.isoformat(),
        "lower": observation.lower,
        "upper": observation.upper,
        "complete": observation.complete,
        "source_url": observation.source_url,
        "observed_at": observation.observed_at,
    }
    result = truth_social_range_probability(observation)
    if result is None:
        return payload
    p_yes, state, confidence = result
    payload["p_yes"] = p_yes
    payload["state"] = state
    payload["confidence"] = confidence
    return payload


def resolve_dossier_store(
    args: argparse.Namespace,
) -> tuple[ResearchDossierStore, tempfile.TemporaryDirectory[str] | None]:
    if not bool(getattr(args, "write_dossier", False)):
        tmp_dir = tempfile.TemporaryDirectory(prefix="research_ticker_")
        return ResearchDossierStore(Path(tmp_dir.name) / "research_dossier.db"), tmp_dir
    raw_path = getattr(args, "db_path", None)
    if raw_path is None:
        raise ValueError("--write-dossier requires --db-path")
    path = Path(raw_path).expanduser().resolve()
    default = Path(DEFAULT_RESEARCH_DOSSIER_DB_PATH).expanduser().resolve()
    if path == default or path.name == "evidence_store.db":
        raise ValueError("refusing to write the live evidence_store.db")
    return ResearchDossierStore(path), None


def _research_news(market: Any) -> SimpleNamespace:
    return SimpleNamespace(
        headline="",
        source="research_prewarm",
        url="",
        research_open_questions=(),
    )


def _llm_news(market: Any) -> NewsItem:
    title = str(getattr(market, "title", "") or getattr(market, "ticker", "") or "")
    rules = str(getattr(market, "rules_primary", "") or "")
    return NewsItem(
        headline=title,
        url="",
        source="research_ticker",
        body=rules,
    )


def _conclusion(
    *,
    routing: dict[str, Any],
    structured: dict[str, Any],
    edges: dict[str, Any] | None,
    gate: dict[str, Any] | None,
    llm: dict[str, Any] | None,
) -> dict[str, Any]:
    gate_status = (gate or {}).get("status")
    gate_side = (gate or {}).get("force_side")
    scoring_side = (edges or {}).get("selected_side")
    admitted = gate_status in {
        "trade_candidate",
        "decision_grade_candidate",
    }
    halt_reasons = {
        "neutral_only_evidence",
        "ambiguous_direction",
    }
    if admitted:
        # Admission is the gate status. A leftover skip_reason must not
        # keep reporting Trade YES/NO as a halt.
        skip = None
        trade_side = gate_side
    elif gate is None:
        skip = routing.get("prewarm_skip_reason")
        trade_side = scoring_side
    else:
        skip = (gate or {}).get("skip_reason") or routing.get("prewarm_skip_reason")
        trade_side = None
    return {
        "scoring_side": scoring_side,
        "admitted": admitted,
        "trade_side": trade_side,
        "p_yes": (gate or {}).get("estimated_probability") or structured.get("p_yes"),
        "favorite_side_is_not_trade_side": (
            routing.get("favorite_side") is not None
            and trade_side is not None
            and routing.get("favorite_side") != trade_side
        ),
        "halted_on_neutral": skip in halt_reasons,
        "prewarm_skip_reason": routing.get("prewarm_skip_reason"),
        "gate_status": gate_status,
        "gate_skip_reason": (gate or {}).get("skip_reason"),
        "llm_direction": (llm or {}).get("direction"),
        "agrees_with_llm": (
            trade_side is not None
            and (llm or {}).get("direction") in {"yes", "no"}
            and trade_side == (llm or {}).get("direction")
        ),
        "placed_orders": 0,
    }


async def evaluate(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    research_gate: Callable[..., Any] | None = None,
    llm_fn: Callable[..., Any] | None = None,
    structured_fn: Callable[[Any], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    client = client or KalshiRestClient()
    ticker = str(args.ticker).strip()
    market = client.get_market(ticker)
    if market is None:
        raise SystemExit(f"market not found: {ticker}")

    live_yes_cents, live_no_cents = snapshot_ask_cents(market)
    live_snapshot = {
        "ticker": ticker,
        "title": getattr(market, "title", None),
        "status": getattr(market, "status", None),
        "yes_ask_cents": live_yes_cents,
        "no_ask_cents": live_no_cents,
        "yes_ask_size": _jsonable(getattr(market, "yes_ask_size", None)),
        "no_ask_size": _jsonable(getattr(market, "no_ask_size", None)),
        "open_interest": _jsonable(getattr(market, "open_interest", None)),
        "volume": _jsonable(getattr(market, "volume", None)),
        "close_time": getattr(market, "close_time", None),
        "rules_primary": getattr(market, "rules_primary", None),
    }
    apply_pinned_asks(market, args.yes_ask, args.no_ask)
    yes_ask, no_ask = _executable_asks(market)
    tmp_dir = None
    try:
        with activate_cohort(getattr(args, "cohort", _DEFAULT_COHORT)) as cohort_id:
            routing = routing_snapshot(market, config=cfg)
            structured = (structured_fn or _structured_probability)(market)
            p_yes = structured.get("p_yes")
            edges = (
                both_sides_edges(
                    float(p_yes), yes_ask, no_ask, live_mode=bool(args.live_mode)
                )
                if p_yes is not None
                else None
            )
            store, tmp_dir = resolve_dossier_store(args)
            jobs: list[Any] = []
            labels: list[str] = []
            if not args.no_gate:
                gate = research_gate or run_research_gate
                jobs.append(
                    gate(
                        _research_news(market),
                        market,
                        model_direction="neutral",
                        model_confidence=0.0,
                        model_reason="research_ticker replay",
                        yes_ask=yes_ask,
                        no_ask=no_ask,
                        live_mode=bool(args.live_mode),
                        dossier_store=store,
                        max_queries=int(args.max_queries),
                        research_timeout_seconds=float(args.timeout_seconds),
                        require_decision_grade=True,
                        **event_news_official_research_kwargs(config=cfg),
                    )
                )
                labels.append("gate")
            if args.llm:
                llm = llm_fn or estimate_probability
                jobs.append(llm(_llm_news(market), market))
                labels.append("llm")
            results = await asyncio.gather(*jobs) if jobs else []
            by_label = dict(zip(labels, results, strict=True))
            gate_payload = None
            if "gate" in by_label:
                gate_payload = _verdict_dict(by_label["gate"])
                if (
                    edges is None
                    and gate_payload.get("estimated_probability") is not None
                ):
                    edges = both_sides_edges(
                        float(gate_payload["estimated_probability"]),
                        yes_ask,
                        no_ask,
                        live_mode=bool(args.live_mode),
                    )
            llm_payload = None
            if "llm" in by_label:
                (
                    prob,
                    confidence,
                    keywords,
                    reasoning,
                    direction,
                    magnitude,
                    llm_conf,
                ) = by_label["llm"]
                llm_payload = {
                    "probability": prob,
                    "confidence": confidence,
                    "direction": direction,
                    "magnitude": magnitude,
                    "llm_confidence": llm_conf,
                    "keywords": list(keywords or []),
                    "reasoning": reasoning,
                }
            return {
                "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "ticker": ticker,
                "cohort_id": cohort_id,
                "live_mode": bool(args.live_mode),
                "pinned": {"yes_ask": args.yes_ask, "no_ask": args.no_ask},
                "live_market": live_snapshot,
                "routing": routing,
                "structured": structured,
                "both_sides": edges,
                "gate": gate_payload,
                "llm": llm_payload,
                "conclusion": _conclusion(
                    routing=routing,
                    structured=structured,
                    edges=edges,
                    gate=gate_payload,
                    llm=llm_payload,
                ),
                "dossier": {
                    "persisted": bool(args.write_dossier),
                    "path": str(store.db_path),
                },
                "placed_orders": 0,
            }
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def _render(report: dict[str, Any], *, json_output: bool) -> str:
    payload = {key: value for key, value in report.items() if key != "_tmp_dir"}
    if json_output:
        return json.dumps(payload, sort_keys=True, default=str)
    conclusion = payload.get("conclusion") or {}
    routing = payload.get("routing") or {}
    edges = payload.get("both_sides") or {}
    structured = payload.get("structured") or {}
    gate = payload.get("gate") or {}
    llm = payload.get("llm") or {}
    yes = edges.get("yes") or {}
    no = edges.get("no") or {}
    lines = [
        f"research_ticker {payload.get('ticker')}",
        f"  favorite={routing.get('favorite_side')} {routing.get('favorite_cents')}c "
        f"skip={routing.get('prewarm_skip_reason')}",
        f"  structured_p_yes={structured.get('p_yes')} "
        f"state={structured.get('state')} "
        f"count={(structured.get('observation') or {}).get('count')}",
        f"  yes_net={yes.get('net_edge')} no_net={no.get('net_edge')} "
        f"selected={edges.get('selected_side')}",
        f"  gate={gate.get('status')} skip={gate.get('skip_reason')} "
        f"side={gate.get('force_side')} p={gate.get('estimated_probability')}",
    ]
    if llm:
        lines.append(
            f"  llm={llm.get('direction')} p={llm.get('probability')} "
            f"conf={llm.get('confidence')}"
        )
    lines.append(
        f"  conclusion scoring={conclusion.get('scoring_side')} "
        f"admitted={conclusion.get('admitted')} "
        f"side={conclusion.get('trade_side')} "
        f"favorite_not_trade={conclusion.get('favorite_side_is_not_trade_side')} "
        f"halted_neutral={conclusion.get('halted_on_neutral')} "
        f"orders={conclusion.get('placed_orders')}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    try:
        report = asyncio.run(evaluate(args))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(_render(report, json_output=bool(args.json)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
