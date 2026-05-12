"""
Kalshi REST API v2 client.

Handles authentication (API key + RSA signature per Kalshi's latest auth scheme),
market listing/lookup, balance queries, and order placement.

Kalshi API docs: https://trading-api.kalshi.com/trade-api/v2/openapi.json
"""

import base64
import json
import time
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import cfg
from kalshi import KalshiMarket, OrderResult
from kalshi.normalizer import (
    UnsupportedPayloadContractError,
    normalize_market_list_entry,
    normalize_market_detail,
)
from utils.logger import get_logger

log = get_logger("kalshi_rest")


def _normalize_pem(raw: str | bytes) -> bytes:
    """
    Normalize a private key to a valid PEM byte string.

    Handles:
      - Literal \\n escape sequences (common in .env files)
      - Missing -----BEGIN/END RSA PRIVATE KEY----- headers
    """
    if isinstance(raw, bytes):
        raw = raw.decode()
    raw = raw.replace("\\n", "\n").strip()
    if "BEGIN" not in raw:
        raw = f"-----BEGIN RSA PRIVATE KEY-----\n{raw}\n-----END RSA PRIVATE KEY-----"
    return raw.encode()


# Rate-limit guard: max 10 requests/second
_MIN_REQUEST_INTERVAL = 0.12   # seconds


class KalshiSigningError(RuntimeError):
    """Raised when RSA-PSS signing fails with credentials configured."""


class KalshiRestClient:
    """
    Thin wrapper around the Kalshi REST API.

    Authentication uses RSA-PSS/SHA-256 (salt_length=DIGEST_LENGTH) over the
    canonical request string that Kalshi specifies for API key auth — see
    _sign() for the exact construction. If the private key is not configured
    the client still works for public endpoints (market data).
    """

    def __init__(self):
        self._base   = cfg.rest_base_url
        self._key_id = cfg.api_key_id
        self._secret = cfg.api_key_secret  # raw string; may be PEM or hex
        self._token: Optional[str] = None
        self._last_req_time = 0.0
        self._private_key = None

        # Parse PEM once at startup: fail fast on bad key rather than at first trade.
        if self._key_id and self._secret:
            try:
                pem = _normalize_pem(self._secret)
                self._private_key = serialization.load_pem_private_key(pem, password=None)
            except Exception as exc:
                raise KalshiSigningError(
                    f"Failed to load Kalshi private key at startup: {exc}"
                ) from exc

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
        Build the RSA-PSS signature headers required by Kalshi v2.

        Kalshi uses:
          timestamp  = milliseconds since epoch (string)
          message    = timestamp + method.upper() + path + body
          signature  = RSA-PSS/SHA-256 (DIGEST_LENGTH salt), base64-encoded
        """
        ts = str(int(time.time() * 1000))
        message = (ts + method.upper() + path + body).encode()
        try:
            sig = self._private_key.sign(
                message,
                asym_padding.PSS(
                    mgf=asym_padding.MGF1(hashes.SHA256()),
                    salt_length=asym_padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
            sig_b64 = base64.b64encode(sig).decode()
        except Exception as exc:
            raise KalshiSigningError(
                f"RSA-PSS signing failed: {exc}"
            ) from exc

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
            # Log status + sanitized body: strip any echoed auth/signature fields.
            status = exc.response.status_code
            body = exc.response.text[:300] if exc.response.text else ""
            for sensitive in ("KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE",
                              "Authorization", "signature", "api_key"):
                if sensitive.lower() in body.lower():
                    body = "(response body redacted -- may contain credentials)"
                    break
            log.error("HTTP %s %s -> %s: %s", method, endpoint, status, body)
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
                markets.append(normalize_market_list_entry(m))
            except UnsupportedPayloadContractError as exc:
                log.warning(
                    "Skipping market with unsupported payload contract: "
                    "ticker=%s reason=%s",
                    getattr(exc, "ticker", "") or m.get("ticker", "<unknown>"),
                    exc,
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.debug("Skipping malformed market entry: %s", exc)

        next_cursor = data.get("cursor") or None
        return markets, next_cursor

    def get_market(self, ticker: str) -> Optional[KalshiMarket]:
        """Fetch a single market by ticker. Uses the P-2 normalizer so
        cents fields, provenance, and payload-hash all flow through one
        canonical parse path (LD-3)."""
        try:
            data = self._request("GET", f"/markets/{ticker}")
            return normalize_market_detail(data)
        except UnsupportedPayloadContractError as exc:
            log.warning(
                "get_market(%s) unsupported payload contract: %s", ticker, exc,
            )
            return None
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

    def get_all_series(self, max_pages: int = 100) -> list[dict]:
        """
        Paginate through the /series endpoint.

        Returns a list of raw series dicts, each containing at minimum
        'ticker' and 'title' keys. Used by the market cache to discover
        geo/political series before fetching their open markets.
        """
        all_series: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self._request("GET", "/series", params=params)
            except Exception as exc:
                log.warning("get_all_series page fetch failed: %s", exc)
                break
            batch = data.get("series", [])
            all_series.extend(batch)
            cursor = data.get("cursor") or None
            if not cursor or not batch:
                break
        log.info("Fetched %d series from Kalshi", len(all_series))
        return all_series

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
