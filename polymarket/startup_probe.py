from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import config as config_module
from polymarket.models import PolymarketMarket
from polymarket.public_client import PolymarketPublicClient
from polymarket.security import redact_polymarket_secret
from utils.logger import get_logger

log = get_logger("polymarket.startup_probe")


class _MarketClient(Protocol):
    def get_markets(self, **kwargs) -> tuple[list[PolymarketMarket], str | None]: ...


@dataclass(frozen=True)
class PolymarketStartupProbeResult:
    status: str
    market_count: int = 0
    sample_market: str | None = None
    error: str | None = None


def log_polymarket_startup_probe(
    *,
    client: _MarketClient | None = None,
) -> PolymarketStartupProbeResult:
    cfg = config_module.cfg
    if not cfg.polymarket_us_enabled:
        log.info("[POLYMARKET] enabled=false startup_probe=skipped")
        return PolymarketStartupProbeResult(status="disabled")

    probe_client = client or PolymarketPublicClient()
    try:
        markets, _cursor = probe_client.get_markets(limit=1)
    except Exception as exc:
        message = redact_polymarket_secret(str(exc), cfg.polymarket_us_secret)
        log.warning("[POLYMARKET] startup_probe=error enabled=true error=%s", message)
        return PolymarketStartupProbeResult(status="error", error=message)

    sample_market = markets[0].market_id if markets else None
    log.info(
        "[POLYMARKET] startup_probe=ok enabled=true public_markets_sampled=%d "
        "sample_market=%s paper_execution=not_wired live_trading=%s",
        len(markets),
        sample_market or "none",
        str(cfg.polymarket_us_live_trading_enabled).lower(),
    )
    return PolymarketStartupProbeResult(
        status="ok",
        market_count=len(markets),
        sample_market=sample_market,
    )
