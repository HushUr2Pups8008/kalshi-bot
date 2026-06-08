from __future__ import annotations

import time
from typing import Any

import requests

from config import cfg
from polymarket.models import PolymarketMarket
from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue
from utils.logger import get_logger

log = get_logger("polymarket_public")


class PolymarketPublicClient:
    venue = Venue.POLYMARKET_US

    def __init__(self, *, base_url: str | None = None):
        self._base = (base_url or cfg.polymarket_us_public_base_url).rstrip("/")
        self._session = requests.Session()
        self._last_req_time = 0.0
        self._min_interval = 1.0 / max(
            1, cfg.polymarket_us_public_requests_per_second
        )

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        elapsed = time.monotonic() - self._last_req_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        response = self._session.request(
            method,
            self._base + endpoint,
            headers=headers,
            params=params,
            timeout=10,
        )
        self._last_req_time = time.monotonic()
        response.raise_for_status()
        return response.json() if response.text else {}

    def get_markets(self, **kwargs: Any) -> tuple[list[PolymarketMarket], str | None]:
        params = {"limit": kwargs.get("limit", 100), "closed": "false"}
        cursor = kwargs.get("cursor")
        if cursor:
            params["cursor"] = cursor

        data = self._request("GET", "/v1/markets", params=params)
        raw_markets = data.get("markets", []) if isinstance(data, dict) else data
        markets = []
        for raw in raw_markets:
            try:
                markets.append(normalize_polymarket_market(raw))
            except ValueError as exc:
                log.debug("Skipping unsupported Polymarket market: %s", exc)
        return markets, data.get("cursor") if isinstance(data, dict) else None

    def get_market(self, market_id: str) -> PolymarketMarket:
        data = self._request("GET", f"/v1/markets/{market_id}")
        if not isinstance(data, dict):
            raise ValueError("Polymarket market response must be an object")
        return normalize_polymarket_market(data.get("market", data))
