"""
Kalshi REST API v2 client.

Handles authentication (API key + RSA signature per Kalshi's latest auth scheme),
market listing/lookup, balance queries, and order placement.

Kalshi API docs: https://trading-api.kalshi.com/trade-api/v2/openapi.json
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import cfg
from kalshi import KalshiMarket, OrderResult
from utils.logger import get_logger

log = get_logger("kalshi_rest")

# Rate-limit guard: max 10 requests/second
_MIN_REQUEST_INTERVAL = 0.12   # seconds


class KalshiRestClient:
    """
    Thin wrapper around the Kalshi REST API.

    Authentication uses HMAC-SHA256 over the canonical request string that
    Kalshi specifies for API key auth. If the private key is not configured
    the client still works for public endpoints (market data).
    """

    def __init__(self):
        self._base   = cfg.rest_base_url
        self._key_id = cfg.api_key_id
        self._secret = cfg.api_key_secret  # raw string; may be PEM or hex
        self._token: Optional[str] = None
        self._last_req_time = 0.0

        # Session with retry logic
        self._session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """
        Build the HMAC-SHA256 Authorization header required by Kalshi.

        Kalshi uses:
          timestamp  = milliseconds since epoch (string)
          message    = timestamp + method.upper() + path + body
          signature  = HMAC-SHA256(secret, message), base64-encoded
          header     = "Kalshi <key_id>:<signature>"
        """
        ts = str(int(time.time() * 1000))
        message = ts + method.upper() + path + body
        try:
            secret_bytes = self._secret.encode() if isinstance(self._secret, str) else self._secret
            sig = hmac.new(secret_bytes, message.encode(), hashlib.sha256).digest()
            sig_b64 = base64.b64encode(sig).decode()
        except Exception as exc:
            log.warning("Could not sign request: %s — proceeding unsigned", exc)
            return {"KALSHI-ACCESS-TIMESTAMP": ts}

        return {
            "KALSHI-ACCESS-KEY":       self._key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
        }

    def _headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        h = {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }
        if self._key_id and self._secret:
            h.update(self._sign(method, path, body))
        return h

    # ── Raw request ───────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> dict[str, Any]:
        # Primitive rate-limit guard
        elapsed = time.monotonic() - self._last_req_time
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)

        path        = "/trade-api/v2" + endpoint   # used for signing
        url         = self._base + endpoint
        body_str    = json.dumps(payload) if payload else ""
        headers     = self._headers(method, path, body_str)

        try:
            resp = self._session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=body_str if body_str else None,
                timeout=10,
            )
            self._last_req_time = time.monotonic()
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except requests.HTTPError as exc:
            log.error("HTTP %s %s → %s: %s", method, endpoint, exc.response.status_code,
                      exc.response.text[:300])
            raise
        except requests.RequestException as exc:
            log.error("Request error %s %s: %s", method, endpoint, exc)
            raise

    # ── Public market data ────────────────────────────────────────────────────

    def get_markets(
        self,
        status: str = "open",
        cursor: str | None = None,
        limit: int = 200,
        series_ticker: str | None = None,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> tuple[list[KalshiMarket], str | None]:
        """
        Fetch a page of markets.

        Returns (markets_list, next_cursor).
        next_cursor is None when there are no more pages.
        """
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if min_close_ts:
            params["min_close_ts"] = min_close_ts
        if max_close_ts:
            params["max_close_ts"] = max_close_ts

        try:
            data = self._request("GET", "/markets", params=params)
        except Exception:
            return [], None

        markets = []
        for m in data.get("markets", []):
            try:
                yes_bid   = float(m.get("yes_bid",   m.get("yes_ask", 50)) or 50)
                yes_ask   = float(m.get("yes_ask",   m.get("yes_bid", 50)) or 50)
                yes_price = (yes_bid + yes_ask) / 2.0
                markets.append(KalshiMarket(
                    ticker=m["ticker"],
                    title=m.get("title", m.get("subtitle", m["ticker"])),
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    yes_price=yes_price,
                    volume=int(m.get("volume", 0) or 0),
                    open_interest=int(m.get("open_interest", 0) or 0),
                    close_time=m.get("close_time", ""),
                    status=m.get("status", "open"),
                    series_ticker=m.get("series_ticker", ""),
                    subtitle=m.get("subtitle", ""),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                log.debug("Skipping malformed market entry: %s", exc)

        next_cursor = data.get("cursor") or None
        return markets, next_cursor

    def get_market(self, ticker: str) -> Optional[KalshiMarket]:
        """Fetch a single market by ticker."""
        try:
            data = self._request("GET", f"/markets/{ticker}")
            m = data.get("market", data)
            yes_bid   = float(m.get("yes_bid",   m.get("yes_ask", 50)) or 50)
            yes_ask   = float(m.get("yes_ask",   m.get("yes_bid", 50)) or 50)
            yes_price = (yes_bid + yes_ask) / 2.0
            return KalshiMarket(
                ticker=m["ticker"],
                title=m.get("title", m.get("subtitle", ticker)),
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                yes_price=yes_price,
                volume=int(m.get("volume", 0) or 0),
                open_interest=int(m.get("open_interest", 0) or 0),
                close_time=m.get("close_time", ""),
                status=m.get("status", "open"),
                series_ticker=m.get("series_ticker", ""),
                subtitle=m.get("subtitle", ""),
            )
        except Exception as exc:
            log.warning("get_market(%s) failed: %s", ticker, exc)
            return None

    def get_all_open_markets(self, max_pages: int = 10) -> list[KalshiMarket]:
        """Paginate through all open markets (up to max_pages pages)."""
        all_markets: list[KalshiMarket] = []
        cursor = None
        for _ in range(max_pages):
            page, cursor = self.get_markets(cursor=cursor)
            all_markets.extend(page)
            if not cursor:
                break
        log.info("Fetched %d open markets from Kalshi", len(all_markets))
        return all_markets

    # ── Account data ──────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        """Returns available balance in dollars."""
        try:
            data = self._request("GET", "/portfolio/balance")
            cents = data.get("balance", 0) or 0
            return float(cents) / 100.0
        except Exception as exc:
            log.warning("get_balance failed: %s", exc)
            return 0.0

    def get_open_positions(self) -> list[dict]:
        """Return list of current open positions."""
        try:
            data = self._request("GET", "/portfolio/positions")
            return data.get("market_positions", [])
        except Exception as exc:
            log.warning("get_open_positions failed: %s", exc)
            return []

    # ── Order placement ───────────────────────────────────────────────────────

    def place_limit_order(
        self,
        ticker: str,
        side: str,           # "yes" | "no"
        count: int,          # number of contracts
        limit_price: int,    # cents (1–99)
        expiration_ts: Optional[int] = None,
    ) -> OrderResult:
        """
        Place a limit order on Kalshi.

        count × limit_price cents = total risk (for yes orders).
        """
        payload: dict[str, Any] = {
            "ticker":   ticker,
            "action":   "buy",
            "type":     "limit",
            "side":     side,
            "count":    count,
            "yes_price" if side == "yes" else "no_price": limit_price,
        }
        if expiration_ts:
            payload["expiration_ts"] = expiration_ts

        try:
            data = self._request("POST", "/portfolio/orders", payload=payload)
            order = data.get("order", data)
            cost = count * limit_price / 100.0
            return OrderResult(
                order_id=order.get("order_id", "unknown"),
                ticker=ticker,
                side=side,
                contracts=count,
                price_cents=limit_price,
                status=order.get("status", "resting"),
                filled=int(order.get("count_filled", 0) or 0),
                error=None,
            )
        except Exception as exc:
            return OrderResult(
                order_id="",
                ticker=ticker,
                side=side,
                contracts=count,
                price_cents=limit_price,
                status="error",
                error=str(exc),
            )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        try:
            self._request("DELETE", f"/portfolio/orders/{order_id}")
            return True
        except Exception as exc:
            log.warning("cancel_order(%s) failed: %s", order_id, exc)
            return False
