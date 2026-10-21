from __future__ import annotations

from typing import Any

import requests

from config import cfg
from kalshi import OrderResult
from polymarket.auth import PolymarketAuth
from polymarket.rate_limit import call_once
from polymarket.read_only import assert_polymarket_read_only


class PolymarketAccountClient:
    def __init__(
        self,
        *,
        key_id: str | None = None,
        secret_b64: str | None = None,
        base_url: str | None = None,
    ):
        self._base = (base_url or cfg.polymarket_us_api_base_url).rstrip("/")
        self._auth = PolymarketAuth(
            key_id=key_id or cfg.polymarket_us_key_id,
            secret_b64=secret_b64 or cfg.polymarket_us_secret,
        )
        self._session = requests.Session()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert_polymarket_read_only(method, endpoint)

        def send() -> requests.Response:
            headers = {"Accept": "application/json"}
            headers.update(self._auth.headers(method, endpoint))
            return self._session.request(
                method,
                self._base + endpoint,
                headers=headers,
                params=params,
                timeout=10,
            )

        response = call_once(send)
        response.raise_for_status()
        return response.json() if response.text else {}

    def get_positions(
        self,
        *,
        market: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if market:
            params["market"] = market
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/portfolio/positions", params=params)

    def get_balance(self) -> float:
        data = self._request("GET", "/v1/account/balances")
        balances = data.get("balances") or []
        for balance in balances:
            if balance.get("currency") == "USD":
                return float(
                    balance.get("buyingPower") or balance.get("currentBalance") or 0.0
                )
        return 0.0

    def place_limit_order(
        self,
        *,
        ticker: str,
        side: str,
        count: int,
        limit_price: int,
        expiration_ts: int | None = None,
    ) -> OrderResult:
        return OrderResult(
            order_id="",
            ticker=ticker,
            side=side,
            contracts=count,
            price_cents=limit_price,
            status="error",
            error="Polymarket live order placement is not enabled in this phase",
        )
