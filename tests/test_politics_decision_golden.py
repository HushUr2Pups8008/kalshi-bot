"""Desk decision grid. No orders, no Grok, no secret output.

The hash is the live 13 plus the paper series, the alien series, and the
fee-net edge cases. A gate change has to change this hash on purpose.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

from config import cfg
from trading.executor import TradeExecutor
from utils.event_news_research import (
    EVENT_NEWS_COHORT_ID,
    event_news_fee_net_edge,
    event_news_forecast_refresh_series,
    event_news_gate_chain_reason,
    event_news_score_edge,
    event_news_settle_statuses,
    routing_yes_probability,
)
from utils.series_universe import (
    effective_mode,
    event_news_live_post_block_reason,
    event_news_prefix_blocked,
    get_universe,
    reset_series_universe,
)

# Captured on v0.36.7 after the politics-only desk, before this PR's
# fee-admission deletion. That deletion is paper-legacy only and is outside
# this grid. Re-record the hash only when a desk decision change is intended.
POLITICS_DECISION_SHA256 = (
    "dc34d539ad0aecb2623ea10996d0de5985f42aded4f23cebcfd0b2034a8045ae"
)

POLITICS = SimpleNamespace(
    paper_cohort_id=EVENT_NEWS_COHORT_ID,
    event_news_live_allow=True,
    research_prewarm_sourceable_series_fallback=(),
)
ENVIRON = {
    "EVENT_NEWS_YES_FLOOR_CENTS": "55",
    "EVENT_NEWS_EXCLUDE_EXACT_35": "true",
}
ASKS = (0, 1, 12, 20, 35, 36, 54, 55, 60, 90, 91, 99)
SIDES = ("yes", "no")
PURPOSES = ("order", "score")
LIVES = (True, False)
_CLEARED_FLAGS = (
    "EVENT_NEWS_UNIFIED_EDGE",
    "EVENT_NEWS_EQUITY_LOSS_HALT",
    "EVENT_NEWS_LIVE_TRADABLE_RESEARCH",
    "EVENT_NEWS_SESSION_HALT",
    "EVENT_NEWS_SERIES_RESERVE",
    "EVENT_NEWS_ONE_PER_FAMILY",
)


class Market:
    def __init__(self, series: str, yes_cents: int) -> None:
        self.series_ticker = series
        self.ticker = f"{series}-26OCT19-G"
        self.yes_ask_cents = yes_cents
        self.no_ask_cents = 100 - yes_cents
        self.yes_ask_size = 100.0
        self.no_ask_size = 100.0
        self.open_interest_fp = 1000.0
        self.volume_24h_fp = 1000.0
        self.last_price_cents = yes_cents if 1 <= yes_cents <= 99 else 50
        self.yes_prob = 0.42
        self.yes_price = 0.42


def _dec(value: Decimal) -> str:
    return format(value, "f")


def _decision_rows() -> tuple[list[dict], int]:
    reset_series_universe()
    universe = get_universe()
    assert universe.failed is False
    series = [entry.series for entry in universe.entries]
    rows: list[dict] = []
    for name in series:
        entry = next(item for item in universe.entries if item.series == name)
        market = Market(name, 60)
        rows.append(
            {
                "kind": "series",
                "series": name,
                "file_mode": entry.mode,
                "effective_mode": effective_mode(entry),
                "post_block": event_news_live_post_block_reason(market, config=POLITICS),
                "paper_score": entry.paper_score,
                "transcript_skip": entry.transcript_skip,
            }
        )
    alien = Market("KXTEST", 60)
    rows.append(
        {
            "kind": "series",
            "series": "KXTEST",
            "file_mode": None,
            "effective_mode": None,
            "post_block": event_news_live_post_block_reason(alien, config=POLITICS),
            "paper_score": None,
            "transcript_skip": None,
        }
    )
    for name in series + ["KXTEST"]:
        for ask in ASKS:
            market = Market(name, ask)
            probability, source = routing_yes_probability(market, config=POLITICS)
            rows.append(
                {
                    "kind": "route",
                    "series": name,
                    "ask": ask,
                    "p": probability,
                    "source": source,
                }
            )
            for side in SIDES:
                for purpose in PURPOSES:
                    for live in LIVES:
                        reason = event_news_gate_chain_reason(
                            market,
                            side=side,
                            config=POLITICS,
                            environ=ENVIRON,
                            live=live,
                            purpose=purpose,
                            open_tickers=(),
                        )
                        rows.append(
                            {
                                "kind": "gate",
                                "series": name,
                                "ask": ask,
                                "side": side,
                                "purpose": purpose,
                                "live": live,
                                "reason": reason,
                            }
                        )
    locked = Market("KXTRUTHSOCIAL", 60)
    rows.append(
        {
            "kind": "family",
            "series": "KXTRUTHSOCIAL",
            "reason": event_news_gate_chain_reason(
                locked,
                side="yes",
                config=POLITICS,
                environ=ENVIRON,
                live=True,
                purpose="order",
                open_tickers=("KXTRUTHSOCIAL-26OCT19-OTHER",),
            ),
        }
    )
    rows.append(
        {
            "kind": "prefix",
            "series": "KXTRUMPSAY",
            "blocked": event_news_prefix_blocked("KXTRUMPSAY", ("KXTRUMPSAY",)),
        }
    )
    rows.append(
        {
            "kind": "prefix",
            "series": "KXTRUMPSAYCOUNTRY",
            "blocked": event_news_prefix_blocked("KXTRUMPSAYCOUNTRY", ("KXTRUMPSAY",)),
        }
    )
    rows.append({"kind": "settle", "statuses": list(event_news_settle_statuses(config=POLITICS))})
    rows.append(
        {
            "kind": "forecast",
            "series": list(event_news_forecast_refresh_series(("KXHIGHNY",), config=POLITICS)),
        }
    )
    fee_cases = (
        (Decimal("0.50"), 50, 1),
        (Decimal("0.60"), 55, 1),
        (Decimal("0.94"), 85, 1),
        (Decimal("0.94"), 85, 4),
    )
    for probability, ask, contracts in fee_cases:
        gross, fee, net = event_news_fee_net_edge(probability, ask, contracts)
        assert gross is not None and fee is not None and net is not None
        rows.append(
            {
                "kind": "fee",
                "p": _dec(probability),
                "ask": ask,
                "contracts": contracts,
                "gross": _dec(gross),
                "fee": _dec(fee),
                "net": _dec(net),
            }
        )
    edge_series = ("KXAPRPOTUS", "KXHORMUZWEEKLY")
    edge_asks = (50, 55, 60)
    edge_ps = (0.50, 0.60, 0.90)
    for name in edge_series:
        for ask in edge_asks:
            for side in SIDES:
                for probability in edge_ps:
                    for use_new in (False, True):
                        for old_take in (False, True):
                            market = Market(name, ask)
                            analysis = SimpleNamespace(
                                side=side,
                                estimated_probability=probability,
                                market=market,
                                edge=0.05 if old_take else 0.01,
                            )
                            skip = TradeExecutor._event_news_edge_skip(
                                SimpleNamespace(_is_paper=use_new),
                                analysis,
                                ask_cents=ask,
                                gross_edge=float(analysis.edge),
                                old_take=old_take,
                                contracts=1,
                                use_new=use_new,
                                shadow=False,
                            )
                            scored = event_news_score_edge(
                                market,
                                yes_probability=probability,
                                side=side,
                                ask_cents=ask,
                                contracts=1,
                                config=POLITICS,
                            )
                            rows.append(
                                {
                                    "kind": "edge",
                                    "series": name,
                                    "ask": ask,
                                    "side": side,
                                    "p": probability,
                                    "use_new": use_new,
                                    "old_take": old_take,
                                    "skip": skip,
                                    "new_take": None if scored is None else scored["new_take"],
                                    "fee_net": (
                                        None
                                        if scored is None or scored["fee_net"] is None
                                        else _dec(scored["fee_net"])
                                    ),
                                }
                            )
    return rows, len(series)


def test_politics_decision_grid_matches_the_desk(monkeypatch) -> None:
    """A paper series still cannot POST, and the 0-35c / 55c / 91c gates stay."""
    monkeypatch.setattr(cfg, "paper_cohort_id", EVENT_NEWS_COHORT_ID)
    for name in _CLEARED_FLAGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EVENT_NEWS_YES_FLOOR_CENTS", "55")
    monkeypatch.setenv("EVENT_NEWS_EXCLUDE_EXACT_35", "true")
    monkeypatch.setenv("ENABLE_FEE_NET_PAPER_ACCOUNTING", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("EVENT_NEWS_LIVE_ALLOW", "false")

    rows, series_count = _decision_rows()
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    assert series_count == 23
    assert len(rows) == 2769
    assert digest == POLITICS_DECISION_SHA256
