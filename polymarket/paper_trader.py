from __future__ import annotations

from analysis import SignalAnalysis
from trading.paper_trader import PaperTrader
from trading.venue import Venue
from utils.logger import get_logger

log = get_logger("polymarket.paper_trader")


class PolymarketPaperTrader:
    """Paper-only Polymarket recorder backed by the shared paper trade store."""

    venue = Venue.POLYMARKET_US

    def __init__(self, paper_trader: PaperTrader):
        self._paper_trader = paper_trader

    def record_trade(self, analysis: SignalAnalysis) -> str:
        if analysis.executed_price_cents is None:
            log.warning(
                "[POLYMARKET_PAPER] skip ticker=%s reason=executed_price_unavailable",
                getattr(analysis.market, "ticker", "<unknown>"),
            )
            return ""
        setattr(analysis, "venue", self.venue.value)
        return self._paper_trader.record_trade(analysis)
