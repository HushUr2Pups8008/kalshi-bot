"""
Kalshi REST API v2 client.

Handles authentication (API key + RSA signature per Kalshi's latest auth scheme),
market listing/lookup, balance queries, and order placement.

Kalshi API docs: https://trading-api.kalshi.com/trade-api/v2/openapi.json
"""

import base64
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional, Protocol

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InvalidHeader
from urllib3.util.retry import Retry

from config import cfg
from kalshi import ExchangeState, KalshiMarket, OrderResult
from kalshi.normalizer import (
    UnsupportedPayloadContractError,
    normalize_exchange_status,
    normalize_market_list_entry,
    normalize_market_detail,
)
from kalshi.public_market_data import is_safe_kalshi_identifier
from kalshi.series_metadata import KalshiSeriesMetadata, normalize_series_payload
from trading.venue import Venue
from trading.orderbook import (
    BinaryMarketBook,
    BookLevel,
    decimal_value,
    payload_sha256,
)
from utils.bounded_https import fetch_bounded_https_ipv4
from utils.logger import get_logger

log = get_logger("kalshi_rest")


@dataclass(frozen=True)
class KalshiEventFeeTerms:
    series_ticker: str
    fee_type_override: str | None
    fee_multiplier_override: Decimal | None
    effective_at: datetime | None
    raw_payload_hash: str


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


# Leave scheduler headroom below Kalshi's 10 request/second limit.
_MIN_REQUEST_INTERVAL = 0.15   # seconds (at most 6.67 local dispatches/second)
_TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
_REQUEST_RETRY_TOTAL = 3
_REQUEST_RETRY_BACKOFF_FACTOR = 0.5
_REQUEST_RETRY_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PUT", "TRACE"}
)
_KALSHI_AUTHORITATIVE_HOSTS = frozenset(
    {
        "external-api.kalshi.com",
        "api.elections.kalshi.com",
        "external-api.demo.kalshi.co",
        "demo-api.kalshi.co",
    }
)
_AUTHORITATIVE_MARKET_MAX_BYTES = 256 * 1024


class RequestStartGate:
    """Coordinate local dispatch permits across client sessions.

    The permit is acquired immediately before a blocking HTTP call. It governs
    process-local dispatch timing and shared server cooldowns, not socket-wire
    timestamps after the operating system schedules the caller.
    """

    def __init__(
        self,
        interval_seconds: float = _MIN_REQUEST_INTERVAL,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("request start interval must be positive")
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_slot = 0.0
        self._defer_until = 0.0
        self._lock = threading.Lock()

    def defer_for(self, delay_seconds: float) -> None:
        """Apply a server-requested cooldown to every session sharing this gate."""

        if delay_seconds <= 0:
            return
        with self._lock:
            self._defer_until = max(
                self._defer_until,
                self._monotonic() + delay_seconds,
            )

    def wait_for_slot(self) -> float:
        """Block until this caller's shared local dispatch permit, then return it."""

        while True:
            with self._lock:
                now = self._monotonic()
                slot = max(now, self._next_slot, self._defer_until)
                self._next_slot = slot + self._interval_seconds
            delay = slot - now
            if delay > 0:
                self._sleep(delay)
            with self._lock:
                if self._monotonic() >= self._defer_until:
                    return slot


class BoundedHttpsFetcher(Protocol):
    def __call__(
        self,
        url: str,
        *,
        canonical_host: str,
        provider_name: str,
        user_agent: str,
        timeout: float,
        max_bytes: int,
    ) -> Awaitable[bytes]: ...


def _authoritative_kalshi_market_url(base: str, ticker: str) -> tuple[str, str]:
    if not isinstance(base, str):
        raise ValueError("Kalshi authoritative settlement base must be a string")
    if not is_safe_kalshi_identifier(ticker):
        raise ValueError("Kalshi authoritative settlement ticker is invalid")

    parsed = urllib.parse.urlsplit(base)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "Kalshi authoritative settlement base has an invalid port"
        ) from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host not in _KALSHI_AUTHORITATIVE_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/trade-api/v2"
    ):
        raise ValueError(
            "Kalshi authoritative settlement requires a documented Kalshi HTTPS host "
            "and exact /trade-api/v2 base"
        )

    return (
        f"https://{host}/trade-api/v2/markets/{urllib.parse.quote(ticker, safe='')}",
        host,
    )


def _bounded_json_object(raw: bytes, *, provider_name: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError(f"{provider_name} response must be bytes")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{provider_name} response must be a JSON object")
    return payload


def _authoritative_timeout_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Kalshi authoritative settlement timeout must be finite and positive")
    timeout_seconds = float(value)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Kalshi authoritative settlement timeout must be finite and positive")
    return timeout_seconds


def _is_transient_http_status(status: int | None) -> bool:
    return int(status or 0) in _TRANSIENT_HTTP_STATUSES


def _retry_after_seconds(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    try:
        retry_after = Retry().get_retry_after(response)
    except (AttributeError, InvalidHeader, TypeError, ValueError):
        return None
    return max(0.0, float(retry_after)) if retry_after is not None else None


def _is_transient_http_response(
    status: int | None,
    response: requests.Response | None,
) -> bool:
    return _is_transient_http_status(status) or (
        int(status or 0) == 413 and _retry_after_seconds(response) is not None
    )


def _request_exception_status(exc: requests.RequestException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is not None:
        return int(status)

    text = str(exc).lower()
    for transient_status in _TRANSIENT_HTTP_STATUSES:
        if f"too many {transient_status}" in text:
            return transient_status
    return None


def _is_transient_request_exception(exc: requests.RequestException) -> bool:
    if isinstance(exc, requests.Timeout):
        return True
    response = getattr(exc, "response", None)
    status = _request_exception_status(exc)
    if _is_transient_http_response(status, response):
        return True
    text = str(exc).lower()
    if isinstance(exc, requests.ConnectionError):
        return not isinstance(exc, requests.exceptions.SSLError) and not any(
            marker in text
            for marker in ("certificate", "ssl", "tls")
        )
    return any(
        marker in text
        for marker in (
            "too many 429",
            "too many 500",
            "too many 502",
            "too many 503",
            "too many 504",
            "read timed out",
            "connection aborted",
            "connection reset",
            "temporary failure",
        )
    )


def _retry_delay(attempt: int, response: requests.Response | None) -> float:
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return retry_after
    return _REQUEST_RETRY_BACKOFF_FACTOR * (2 ** attempt)


def _require_execution_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Kalshi {field} must be a non-empty string")
    return value.strip()


def _optional_execution_timestamp(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Kalshi {field} must be a non-negative integer")
    return value


def _optional_execution_limit(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000:
        raise ValueError("Kalshi fill limit must be an integer from 1 through 1000")
    return value


def _optional_execution_cursor(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ValueError("Kalshi fill cursor must be a non-empty string")
    return value


def _optional_execution_subaccount(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 63:
        raise ValueError("Kalshi fill subaccount must be an integer from 0 through 63")
    return value


class KalshiSigningError(RuntimeError):
    """Raised when RSA-PSS signing fails with credentials configured."""


class KalshiSeriesDiscoveryError(RuntimeError):
    """Raised when the authoritative Kalshi series inventory is incomplete."""


def _normalize_market_page(data: dict[str, Any]) -> tuple[list[KalshiMarket], str | None]:
    markets = []
    for market in data.get("markets", []):
        try:
            markets.append(normalize_market_list_entry(market))
        except UnsupportedPayloadContractError as exc:
            log.warning(
                "Skipping market with unsupported payload contract: "
                "ticker=%s reason=%s",
                getattr(exc, "ticker", "") or market.get("ticker", "<unknown>"),
                exc,
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.debug("Skipping malformed market entry: %s", exc)
    return markets, data.get("cursor") or None


class KalshiPublicMarketReader:
    """One warmup worker's isolated session for public ``/markets`` reads."""

    _MAX_ATTEMPTS = _REQUEST_RETRY_TOTAL + 1

    def __init__(
        self,
        *,
        base_url: str,
        request_gate: RequestStartGate,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base = base_url
        self._request_gate = request_gate
        self._sleep = sleep
        # A worker owns this session and retries explicitly so every retry reacquires
        # the shared request-start gate.
        self._session = requests.Session()

    def _request_page(self, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._MAX_ATTEMPTS):
            self._request_gate.wait_for_slot()
            try:
                response = self._session.request(
                    "GET",
                    self._base + "/markets",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    params=params,
                    timeout=10,
                )
                if 300 <= response.status_code < 400:
                    raise requests.HTTPError("unexpected redirect response", response=response)
                response.raise_for_status()
                return response.json() if response.text else {}
            except requests.RequestException as exc:
                is_transient = _is_transient_request_exception(exc)
                if is_transient:
                    self._request_gate.defer_for(
                        _retry_delay(attempt, getattr(exc, "response", None))
                    )
                if not is_transient or attempt + 1 >= self._MAX_ATTEMPTS:
                    raise
        raise RuntimeError("unreachable public market request retry state")

    def get_markets(
        self,
        *,
        status: str = "open",
        series_ticker: str | None = None,
        limit: int = 200,
    ) -> tuple[list[KalshiMarket], str | None]:
        params: dict[str, Any] = {"status": status, "limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return _normalize_market_page(self._request_page(params))


class KalshiRestClient:
    """
    Thin wrapper around the Kalshi REST API.

    Authentication uses RSA-PSS/SHA-256 (salt_length=DIGEST_LENGTH) over the
    canonical request string that Kalshi specifies for API key auth — see
    _sign() for the exact construction. If the private key is not configured
    the client still works for public endpoints (market data).
    """
    venue = Venue.KALSHI

    def __init__(self):
        self._base   = cfg.rest_base_url
        self._key_id = cfg.api_key_id
        self._secret = cfg.api_key_secret  # raw string; may be PEM or hex
        self._token: Optional[str] = None
        self._request_gate = RequestStartGate()
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

        # Adapter retries stay disabled. _request applies this exact policy through
        # RequestStartGate so retries share 429/Retry-After cooldowns with warmup.
        self._session = requests.Session()
        self._retry_policy = Retry(
            total=_REQUEST_RETRY_TOTAL,
            backoff_factor=_REQUEST_RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=_REQUEST_RETRY_METHODS,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=0))

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

    def fork_public_market_reader(self) -> KalshiPublicMarketReader:
        """Create one isolated, paced public-market session for cache warmup."""

        return KalshiPublicMarketReader(
            base_url=self._base,
            request_gate=self._request_gate,
        )

    # ── Raw request ───────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        payload: dict | None = None,
        *,
        allow_redirects: bool | None = None,
    ) -> dict[str, Any]:
        path        = "/trade-api/v2" + endpoint   # used for signing
        url         = self._base + endpoint
        body_str    = json.dumps(payload) if payload else ""
        request_kwargs: dict[str, Any] = {
            "params": params,
            "data": body_str if body_str else None,
            "timeout": 10,
        }
        if allow_redirects is not None:
            request_kwargs["allow_redirects"] = allow_redirects

        retry_budget = (
            self._retry_policy.total
            if method.upper() in self._retry_policy.allowed_methods
            else 0
        )
        for attempt in range(retry_budget + 1):
            self._request_gate.wait_for_slot()
            request_kwargs["headers"] = self._headers(method, path, body_str)
            try:
                resp = self._session.request(method, url, **request_kwargs)
                if 300 <= resp.status_code < 400:
                    raise requests.HTTPError("unexpected redirect response", response=resp)
                resp.raise_for_status()
                return resp.json() if resp.text else {}
            except requests.HTTPError as exc:
                # Log status + sanitized body: strip any echoed auth/signature fields.
                response = exc.response
                status = response.status_code
                is_transient = _is_transient_http_response(status, response)
                if is_transient:
                    self._request_gate.defer_for(_retry_delay(attempt, response))
                    if attempt < retry_budget:
                        continue
                if 300 <= status < 400:
                    body = "(redirect response body omitted)"
                else:
                    body = response.text[:300] if response.text else ""
                    for sensitive in ("KALSHI-ACCESS-KEY", "KALSHI-ACCESS-SIGNATURE",
                                      "Authorization", "signature", "api_key"):
                        if sensitive.lower() in body.lower():
                            body = "(response body redacted -- may contain credentials)"
                            break
                log_fn = log.warning if is_transient else log.error
                log_fn(
                    "HTTP request failed method=%s endpoint=%s status=%s "
                    "transient=%s body=%s",
                    method,
                    endpoint,
                    status,
                    str(is_transient).lower(),
                    body,
                )
                raise
            except requests.RequestException as exc:
                is_transient = _is_transient_request_exception(exc)
                if is_transient:
                    self._request_gate.defer_for(
                        _retry_delay(attempt, getattr(exc, "response", None))
                    )
                    if attempt < retry_budget:
                        continue
                status = _request_exception_status(exc)
                log_fn = log.warning if is_transient else log.error
                log_fn(
                    "Request failed method=%s endpoint=%s status=%s "
                    "transient=%s exception=%s",
                    method,
                    endpoint,
                    status if status is not None else "unknown",
                    str(is_transient).lower(),
                    exc,
                )
                raise

        raise RuntimeError("unreachable Kalshi request retry state")

    # ── Public market data ────────────────────────────────────────────────────

    def get_market_orderbook(
        self,
        ticker: str,
        *,
        depth: int = 100,
    ) -> BinaryMarketBook:
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 100:
            raise ValueError("orderbook depth must be an integer in [0, 100]")
        data = self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )
        raw_book = data.get("orderbook_fp") if isinstance(data, dict) else None
        if not isinstance(raw_book, dict):
            raise ValueError("Kalshi orderbook response missing orderbook_fp")

        def levels_for(key: str) -> tuple[BookLevel, ...]:
            raw_levels = raw_book.get(key)
            if not isinstance(raw_levels, list):
                raise ValueError(f"Kalshi orderbook {key} must be a list")
            levels = []
            for raw_level in raw_levels:
                if not isinstance(raw_level, (list, tuple)) or len(raw_level) != 2:
                    raise ValueError(f"Kalshi orderbook {key} level is malformed")
                levels.append(
                    BookLevel(
                        price=decimal_value(raw_level[0]),
                        quantity=decimal_value(raw_level[1]),
                    )
                )
            return tuple(sorted(levels, key=lambda level: level.price, reverse=True))

        return BinaryMarketBook(
            venue=Venue.KALSHI,
            venue_market_id=str(ticker),
            yes_bids=levels_for("yes_dollars"),
            no_bids=levels_for("no_dollars"),
            as_of=datetime.now(timezone.utc),
            raw_payload_hash=payload_sha256(data),
        )

    def get_event_series_ticker(self, event_ticker: str) -> str:
        requested = str(event_ticker).strip()
        if not requested or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", requested) is None:
            raise ValueError("invalid Kalshi event ticker")
        data = self._request("GET", f"/events/{requested}")
        event = data.get("event") if isinstance(data, dict) else None
        if not isinstance(event, dict):
            raise ValueError("Kalshi event response missing event")
        returned = str(event.get("event_ticker") or "").strip()
        if returned != requested:
            raise ValueError(
                f"Kalshi event ticker mismatch: expected {requested!r}, got {returned!r}"
            )
        series_ticker = str(event.get("series_ticker") or "").strip()
        if not series_ticker:
            raise ValueError("Kalshi event response missing series_ticker")
        return series_ticker

    def get_event_fee_terms(
        self,
        event_ticker: str,
        *,
        as_of: datetime,
        max_pages: int = 100,
    ) -> KalshiEventFeeTerms:
        """Resolve the event fee override effective at one report timestamp."""
        if as_of.tzinfo is None:
            raise ValueError("Kalshi event fee as_of must be timezone-aware")
        as_of = as_of.astimezone(timezone.utc)
        requested = str(event_ticker).strip()
        series_ticker = self.get_event_series_ticker(requested)
        pages: list[dict[str, Any]] = []
        changes: list[tuple[datetime, str | None, Decimal | None]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        allowed_fee_types = {"quadratic", "quadratic_with_maker_fees", "flat"}

        for _ in range(max_pages):
            params = {"event_ticker": requested, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/events/fee_changes", params=params)
            if not isinstance(data, dict):
                raise ValueError("Kalshi event fee response must be an object")
            raw_changes = data.get("event_fee_changes")
            next_cursor = data.get("cursor")
            if not isinstance(raw_changes, list) or not isinstance(next_cursor, str):
                raise ValueError("Kalshi event fee response has invalid pagination")
            pages.append(data)

            for raw in raw_changes:
                if not isinstance(raw, dict):
                    raise ValueError("Kalshi event fee change must be an object")
                required = {
                    "id",
                    "event_ticker",
                    "series_ticker",
                    "fee_type_override",
                    "fee_multiplier_override",
                    "scheduled_ts",
                }
                if not required.issubset(raw):
                    raise ValueError("Kalshi event fee change is missing required fields")
                if not str(raw.get("id") or "").strip():
                    raise ValueError("Kalshi event fee change id is required")
                if str(raw.get("event_ticker") or "").strip() != requested:
                    raise ValueError("Kalshi event fee change event identity mismatch")
                if str(raw.get("series_ticker") or "").strip() != series_ticker:
                    raise ValueError("Kalshi event fee change series identity mismatch")

                fee_type_raw = raw.get("fee_type_override")
                fee_type = None if fee_type_raw is None else str(fee_type_raw).strip()
                if fee_type is not None and fee_type not in allowed_fee_types:
                    raise ValueError("Kalshi event fee change has unsupported fee type")
                multiplier_raw = raw.get("fee_multiplier_override")
                if multiplier_raw is None:
                    multiplier = None
                else:
                    try:
                        multiplier = Decimal(str(multiplier_raw))
                    except (InvalidOperation, ValueError) as exc:
                        raise ValueError(
                            "Kalshi event fee change has invalid multiplier"
                        ) from exc
                    if not multiplier.is_finite() or multiplier < 0:
                        raise ValueError("Kalshi event fee change has invalid multiplier")

                scheduled_raw = raw.get("scheduled_ts")
                if not isinstance(scheduled_raw, str) or not scheduled_raw:
                    raise ValueError("Kalshi event fee change has invalid scheduled_ts")
                try:
                    scheduled_at = datetime.fromisoformat(
                        scheduled_raw.replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise ValueError(
                        "Kalshi event fee change has invalid scheduled_ts"
                    ) from exc
                if scheduled_at.tzinfo is None:
                    raise ValueError("Kalshi event fee scheduled_ts must be timezone-aware")
                changes.append(
                    (scheduled_at.astimezone(timezone.utc), fee_type, multiplier)
                )

            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ValueError("Kalshi event fee pagination cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise ValueError("Kalshi event fee pagination exceeded max_pages")

        applicable = [change for change in changes if change[0] <= as_of]
        if applicable:
            effective_at = max(change[0] for change in applicable)
            latest_terms = {
                (fee_type, multiplier)
                for scheduled_at, fee_type, multiplier in applicable
                if scheduled_at == effective_at
            }
            if len(latest_terms) != 1:
                raise ValueError("ambiguous Kalshi event fee changes")
            fee_type_override, fee_multiplier_override = latest_terms.pop()
        else:
            effective_at = None
            fee_type_override = None
            fee_multiplier_override = None

        return KalshiEventFeeTerms(
            series_ticker=series_ticker,
            fee_type_override=fee_type_override,
            fee_multiplier_override=fee_multiplier_override,
            effective_at=effective_at,
            raw_payload_hash=payload_sha256(
                {
                    "event_ticker": requested,
                    "series_ticker": series_ticker,
                    "fee_change_pages": pages,
                }
            ),
        )

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
        return _normalize_market_page(data)

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

    def get_market_exact(self, ticker: str) -> Optional[KalshiMarket]:
        """Fetch and normalize one exact ticker without hiding lookup failures.

        An authoritative HTTP 404 is the only missing-market result. Transport,
        server, JSON, and payload-contract failures remain exceptions so callers
        cannot mistake an inconclusive lookup for confirmed absence.
        """
        try:
            data = self._request("GET", f"/markets/{ticker}")
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                return None
            raise
        return normalize_market_detail(data)

    async def get_market_exact_bounded(
        self,
        ticker: str,
        *,
        timeout_seconds: float,
        fetcher: BoundedHttpsFetcher | None = None,
    ) -> KalshiMarket | None:
        """Fetch one exact market through the bounded authoritative transport.

        Only a received HTTP 404 establishes that the ticker is absent. Transport,
        response, JSON, and market-contract failures remain errors for the caller.
        """
        timeout_seconds = _authoritative_timeout_seconds(timeout_seconds)
        url, host = _authoritative_kalshi_market_url(self._base, ticker)
        bounded_fetch = fetcher or fetch_bounded_https_ipv4
        try:
            raw = await bounded_fetch(
                url,
                canonical_host=host,
                provider_name="Kalshi authoritative settlement",
                user_agent="kalshi-bot/authoritative-settlement-v1",
                timeout=timeout_seconds,
                max_bytes=_AUTHORITATIVE_MARKET_MAX_BYTES,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        market = normalize_market_detail(
            _bounded_json_object(raw, provider_name="Kalshi authoritative settlement")
        )
        if market.ticker != ticker:
            raise ValueError("Kalshi authoritative settlement ticker mismatch")
        return market

    def get_series(self, series_ticker: str) -> KalshiSeriesMetadata | None:
        """Fetch and normalize one series detail payload for shadow metadata."""
        try:
            data = self._request("GET", f"/series/{series_ticker}")
            return normalize_series_payload(data)
        except Exception as exc:
            log.warning("get_series(%s) failed: %s", series_ticker, exc)
            return None

    def get_exchange_status(self) -> Optional[ExchangeState]:
        """Fetch /exchange/status once per cycle and normalize through the
        P-2 normalizer (LD-4 / P-7). Returns None on fetch failure or
        unsupported payload contract — caller must treat None as fail-closed
        (exchange NOT open, skip all market analysis for this cycle).
        """
        try:
            data = self._request("GET", "/exchange/status")
            return normalize_exchange_status(data)
        except UnsupportedPayloadContractError as exc:
            log.warning(
                "get_exchange_status unsupported payload contract: %s", exc,
            )
            return None
        except Exception as exc:
            log.warning("get_exchange_status fetch failed: %s", exc)
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
                raise KalshiSeriesDiscoveryError(
                    "Kalshi series discovery page fetch failed"
                ) from exc
            if not isinstance(data, dict):
                raise KalshiSeriesDiscoveryError(
                    "Kalshi series discovery returned a non-object page"
                )
            batch = data.get("series", [])
            if not isinstance(batch, list):
                raise KalshiSeriesDiscoveryError(
                    "Kalshi series discovery returned a non-list page"
                )
            all_series.extend(batch)
            cursor = data.get("cursor") or None
            if not cursor:
                log.info("Fetched %d series from Kalshi", len(all_series))
                return all_series
            if not batch:
                raise KalshiSeriesDiscoveryError(
                    "Kalshi series discovery returned an empty page with a cursor"
                )
        raise KalshiSeriesDiscoveryError(
            f"Kalshi series discovery exceeded max_pages={max_pages}"
        )

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

    # ── Authenticated execution receipts ─────────────────────────────────────

    def get_order_receipt(self, order_id: str) -> dict[str, Any]:
        """Fetch one explicit official order receipt without following redirects."""
        requested_order_id = _require_execution_id(order_id, field="order_id")
        endpoint = "/portfolio/orders/" + urllib.parse.quote(requested_order_id, safe="")
        data = self._request("GET", endpoint, allow_redirects=False)
        if not isinstance(data, dict):
            raise ValueError("Kalshi order receipt response must be an object")

        order = data.get("order")
        if not isinstance(order, dict):
            raise ValueError("Kalshi order receipt response missing order object")
        returned_order_id = _require_execution_id(order.get("order_id"), field="order receipt order_id")
        if returned_order_id != requested_order_id:
            raise ValueError("Kalshi order receipt order_id mismatch")
        return order

    def get_fills_page(
        self,
        *,
        order_id: str,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        subaccount: int | None = None,
    ) -> tuple[list[object], str | None]:
        """Fetch one receipt page for one explicit order without following redirects."""
        requested_order_id = _require_execution_id(order_id, field="order_id")
        requested_min_ts = _optional_execution_timestamp(min_ts, field="min_ts")
        requested_max_ts = _optional_execution_timestamp(max_ts, field="max_ts")
        if (
            requested_min_ts is not None
            and requested_max_ts is not None
            and requested_min_ts > requested_max_ts
        ):
            raise ValueError("Kalshi fill min_ts must not exceed max_ts")

        params: dict[str, Any] = {"order_id": requested_order_id}
        optional_params = {
            "min_ts": requested_min_ts,
            "max_ts": requested_max_ts,
            "limit": _optional_execution_limit(limit),
            "cursor": _optional_execution_cursor(cursor),
            "subaccount": _optional_execution_subaccount(subaccount),
        }
        params.update({key: value for key, value in optional_params.items() if value is not None})

        data = self._request(
            "GET",
            "/portfolio/fills",
            params=params,
            allow_redirects=False,
        )
        if not isinstance(data, dict):
            raise ValueError("Kalshi fills response must be an object")

        raw_fills = data.get("fills")
        next_cursor = data.get("cursor")
        if not isinstance(raw_fills, list) or not isinstance(next_cursor, str):
            raise ValueError("Kalshi fills response has invalid pagination")

        # Preserve unexpected receipt payloads verbatim so the dedicated ledger
        # can durably quarantine them instead of discarding exchange evidence.
        return raw_fills, next_cursor or None

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
            data = self._request(
                "POST",
                "/portfolio/orders",
                payload=payload,
                allow_redirects=False,
            )
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
