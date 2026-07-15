"""Credential-free, GET-only access to Kalshi public market data."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

import aiohttp

from weather.shadow_models import Fingerprints, RetrievedEvent, RetrievedMarket


_ORIGIN = "https://api.elections.kalshi.com"
_API_PREFIX = "/trade-api/v2"
_EVENTS_URL = f"{_ORIGIN}{_API_PREFIX}/events"
_MARKETS_URL = f"{_ORIGIN}{_API_PREFIX}/markets"
_IDENTIFIER = re.compile(r"[A-Z0-9][A-Z0-9._-]*")
_MARKET_SUFFIX = re.compile(r"-(?P<kind>[TB])(?P<strike>-?\d+(?:\.5)?)$")


class KalshiPublicMarketDataError(ValueError):
    """Raised when public market data cannot be trusted."""


def is_safe_kalshi_identifier(value: object) -> bool:
    """Return whether value matches the public API's identifier grammar."""
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


class PublicMarketDataReader(Protocol):
    async def list_active_events(
        self, *, series_ticker: str
    ) -> tuple[RetrievedEvent, ...]: ...

    async def get_event(self, *, event_ticker: str) -> RetrievedEvent: ...


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise KalshiPublicMarketDataError("payload is not canonical JSON") from exc


def _hash(payload: Any) -> str:
    return sha256(_canonical_json(payload).encode()).hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise KalshiPublicMarketDataError(f"{label} must be an object")
    return value


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise KalshiPublicMarketDataError(f"{key} must be a non-empty string")
    return value


def _parse_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise KalshiPublicMarketDataError(f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KalshiPublicMarketDataError(f"{label} is malformed") from exc
    if parsed.tzinfo is None:
        raise KalshiPublicMarketDataError(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc)


def _decimal(value: Any, label: str, *, optional: bool = False) -> Decimal | None:
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise KalshiPublicMarketDataError(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise KalshiPublicMarketDataError(f"{label} must be numeric") from exc
    if not parsed.is_finite() or parsed < 0:
        raise KalshiPublicMarketDataError(f"{label} must be finite and nonnegative")
    return parsed


def _cents(value: Any, label: str, *, optional: bool = False) -> int | None:
    dollars = _decimal(value, label, optional=optional)
    if dollars is None:
        return None
    cents = dollars * 100
    if cents != cents.to_integral_value() or not 0 <= cents <= 100:
        raise KalshiPublicMarketDataError(f"{label} must be an exact cent price")
    return int(cents)


class KalshiPublicMarketDataReader:
    """Read event ladders without configuration, credentials, or auth headers."""

    def __init__(
        self,
        *,
        session_factory: Callable[..., Any] = aiohttp.ClientSession,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not is_safe_kalshi_identifier(value):
            raise KalshiPublicMarketDataError(f"{label} is unsafe")
        return value

    @classmethod
    def _event_url(cls, event_ticker: str) -> str:
        ticker = cls._identifier(event_ticker, "event_ticker")
        return f"{_EVENTS_URL}/{ticker}"

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        allowed_event = parsed.path.startswith(f"{_API_PREFIX}/events/") and bool(
            _IDENTIFIER.fullmatch(parsed.path.removeprefix(f"{_API_PREFIX}/events/"))
        )
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.elections.kalshi.com"
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.path not in {f"{_API_PREFIX}/events", f"{_API_PREFIX}/markets"}
            and not allowed_event
        ):
            raise KalshiPublicMarketDataError("request URL is outside the public market-data boundary")

    async def _request_json(
        self, session: Any, url: str, *, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._validate_url(url)
        try:
            response = await session.get(url, params=dict(params), allow_redirects=False)
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as exc:
            raise KalshiPublicMarketDataError("public Kalshi request failed") from exc
        try:
            if response.status != 200:
                raise KalshiPublicMarketDataError(
                    f"public Kalshi request returned HTTP {response.status}"
                )
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ClientError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise KalshiPublicMarketDataError("public Kalshi response is not JSON") from exc
            return _require_mapping(payload, "public Kalshi response")
        finally:
            response.release()

    @staticmethod
    def _cursor(payload: Mapping[str, Any]) -> str:
        if "cursor" not in payload or not isinstance(payload["cursor"], str):
            raise KalshiPublicMarketDataError("pagination cursor is malformed")
        return payload["cursor"]

    @staticmethod
    def _settlement_sources(event: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
        sources = event.get("settlement_sources")
        if not isinstance(sources, list) or not sources:
            raise KalshiPublicMarketDataError("event settlement sources are missing")
        normalized: list[Mapping[str, str]] = []
        for source in sources:
            item = _require_mapping(source, "settlement source")
            normalized.append(
                {"name": _require_string(item, "name"), "url": _require_string(item, "url")}
            )
        return tuple(normalized)

    @classmethod
    def _validate_nested_markets(
        cls, event: Mapping[str, Any], event_ticker: str
    ) -> tuple[str, ...]:
        markets = event.get("markets")
        if not isinstance(markets, list):
            raise KalshiPublicMarketDataError("nested markets are missing")
        seen: set[str] = set()
        tickers: list[str] = []
        for raw_market in markets:
            market = _require_mapping(raw_market, "nested market")
            ticker = _require_string(market, "ticker")
            if _require_string(market, "event_ticker") != event_ticker:
                raise KalshiPublicMarketDataError("nested market event attribution is invalid")
            if not ticker.startswith(f"{event_ticker}-"):
                raise KalshiPublicMarketDataError("nested market ticker is outside event")
            if ticker in seen:
                raise KalshiPublicMarketDataError("duplicate market ticker")
            seen.add(ticker)
            tickers.append(ticker)
        return tuple(tickers)

    @classmethod
    def _validate_event(
        cls,
        raw_event: Any,
        *,
        expected_series: str | None = None,
        expected_event: str | None = None,
    ) -> tuple[
        Mapping[str, Any],
        str,
        str,
        tuple[Mapping[str, str], ...],
        tuple[str, ...],
    ]:
        event = _require_mapping(raw_event, "event")
        event_ticker = _require_string(event, "event_ticker")
        series_ticker = _require_string(event, "series_ticker")
        if expected_series is not None and series_ticker != expected_series:
            raise KalshiPublicMarketDataError("event series attribution is invalid")
        if expected_event is not None and event_ticker != expected_event:
            raise KalshiPublicMarketDataError("returned event ticker does not match request")
        if not event_ticker.startswith(f"{series_ticker}-"):
            raise KalshiPublicMarketDataError("event ticker is outside returned series")
        nested_tickers = cls._validate_nested_markets(event, event_ticker)
        return (
            event,
            event_ticker,
            series_ticker,
            cls._settlement_sources(event),
            nested_tickers,
        )

    async def _list_markets(
        self,
        session: Any,
        *,
        event_ticker: str,
        settlement_sources: tuple[Mapping[str, str], ...],
        retrieved_at: datetime,
    ) -> tuple[RetrievedMarket, ...]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_tickers: set[str] = set()
        normalized: list[RetrievedMarket] = []
        while True:
            params: dict[str, Any] = {"event_ticker": event_ticker, "limit": 1000}
            if cursor is not None:
                params["cursor"] = cursor
            payload = await self._request_json(session, _MARKETS_URL, params=params)
            markets = payload.get("markets")
            if not isinstance(markets, list):
                raise KalshiPublicMarketDataError("markets page is malformed")
            for raw_market in markets:
                market = _require_mapping(raw_market, "market")
                ticker = _require_string(market, "ticker")
                if ticker in seen_tickers:
                    raise KalshiPublicMarketDataError("duplicate market ticker")
                seen_tickers.add(ticker)
                normalized.append(
                    self._normalize_market(
                        market,
                        event_ticker=event_ticker,
                        settlement_sources=settlement_sources,
                        retrieved_at=retrieved_at,
                    )
                )
            next_cursor = self._cursor(payload)
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise KalshiPublicMarketDataError("pagination cursor repeated")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        if not normalized:
            raise KalshiPublicMarketDataError("event has no markets")
        return tuple(normalized)

    @staticmethod
    def _bounds(market: Mapping[str, Any], ticker: str) -> tuple[int | None, int | None, bool, bool]:
        strike_type = _require_string(market, "strike_type")
        match = _MARKET_SUFFIX.search(ticker)
        if match is None:
            raise KalshiPublicMarketDataError("market ticker has no weather strike")
        strike = Decimal(match.group("strike"))
        if strike_type == "less" and match.group("kind") == "T" and strike == int(strike):
            cap = market.get("cap_strike")
            if Decimal(str(cap)) != strike:
                raise KalshiPublicMarketDataError("lower-tail strike metadata is inconsistent")
            return None, int(strike) - 1, True, False
        if strike_type == "greater" and match.group("kind") == "T" and strike == int(strike):
            cap = market.get("cap_strike")
            if cap is not None and Decimal(str(cap)) != strike:
                raise KalshiPublicMarketDataError("upper-tail strike metadata is inconsistent")
            return int(strike) + 1, None, False, True
        if strike_type == "between" and match.group("kind") == "B" and strike % 1 == Decimal("0.5"):
            lower = int(strike - Decimal("0.5"))
            upper = lower + 1
            if Decimal(str(market.get("cap_strike"))) != upper:
                raise KalshiPublicMarketDataError("bounded strike metadata is inconsistent")
            return lower, upper, False, False
        raise KalshiPublicMarketDataError("weather strike type is unsupported")

    @classmethod
    def _normalize_market(
        cls,
        market: Mapping[str, Any],
        *,
        event_ticker: str,
        settlement_sources: tuple[Mapping[str, str], ...],
        retrieved_at: datetime,
    ) -> RetrievedMarket:
        ticker = _require_string(market, "ticker")
        if _require_string(market, "event_ticker") != event_ticker:
            raise KalshiPublicMarketDataError("market event attribution is invalid")
        if not ticker.startswith(f"{event_ticker}-"):
            raise KalshiPublicMarketDataError("market ticker is outside event")
        status = _require_string(market, "status")
        close_time = _parse_datetime(market.get("close_time"), "close_time")
        lower, upper, is_lower, is_upper = cls._bounds(market, ticker)
        rules_primary = _require_string(market, "rules_primary")
        rules_secondary = _require_string(market, "rules_secondary")
        yes_bid = _cents(market.get("yes_bid_dollars"), "yes_bid_dollars")
        yes_ask = _cents(market.get("yes_ask_dollars"), "yes_ask_dollars")
        no_bid = _cents(market.get("no_bid_dollars"), "no_bid_dollars")
        no_ask = _cents(market.get("no_ask_dollars"), "no_ask_dollars")
        if None in {yes_bid, yes_ask, no_bid, no_ask}:
            raise KalshiPublicMarketDataError("quote is incomplete")
        yes_bid_size = _decimal(market.get("yes_bid_size_fp"), "yes_bid_size_fp")
        yes_ask_size = _decimal(market.get("yes_ask_size_fp"), "yes_ask_size_fp")
        assert isinstance(yes_bid, int) and isinstance(yes_ask, int)
        assert isinstance(no_bid, int) and isinstance(no_ask, int)
        assert isinstance(yes_bid_size, Decimal) and isinstance(yes_ask_size, Decimal)
        result_raw = market.get("result")
        result = None
        if result_raw not in (None, ""):
            if not isinstance(result_raw, str) or result_raw.lower() not in {"yes", "no"}:
                raise KalshiPublicMarketDataError("market result is invalid")
            result = result_raw.lower()
        contract_record = {
            "close_time": close_time.isoformat(),
            "event_ticker": event_ticker,
            "is_lower_tail": is_lower,
            "is_upper_tail": is_upper,
            "lower_bound_f": lower,
            "market_ticker": ticker,
            "strike_type": market["strike_type"],
            "title": _require_string(market, "title"),
            "upper_bound_f": upper,
        }
        return RetrievedMarket(
            market_ticker=ticker,
            event_ticker=event_ticker,
            status=status,
            close_time=close_time,
            lower_bound_f=lower,
            upper_bound_f=upper,
            is_lower_tail=is_lower,
            is_upper_tail=is_upper,
            fingerprints=Fingerprints(
                contract=_hash(contract_record),
                rules_source=_hash(
                    {"rules_primary": rules_primary, "rules_secondary": rules_secondary}
                ),
                settlement_source=_hash(settlement_sources),
            ),
            yes_bid_cents=yes_bid,
            yes_ask_cents=yes_ask,
            no_bid_cents=no_bid,
            no_ask_cents=no_ask,
            yes_bid_size=yes_bid_size,
            yes_ask_size=yes_ask_size,
            no_bid_size=yes_ask_size,
            no_ask_size=yes_bid_size,
            last_price_cents=_cents(
                market.get("last_price_dollars"), "last_price_dollars", optional=True
            ),
            volume=_decimal(market.get("volume_fp"), "volume_fp", optional=True),
            price_retrieved_at=retrieved_at,
            raw_payload_json=_canonical_json(market),
            result=result,
        )

    @staticmethod
    def _normalize_event(
        event_ticker: str,
        markets: tuple[RetrievedMarket, ...],
        retrieved_at: datetime,
        *,
        authoritative_market_tickers: tuple[str, ...],
        requested_open: bool,
    ) -> RetrievedEvent:
        markets = tuple(
            replace(market, status="open") if market.status == "active" else market
            for market in markets
        )
        retrieved_market_tickers = tuple(market.market_ticker for market in markets)
        if (
            not authoritative_market_tickers
            or len(authoritative_market_tickers) != len(set(authoritative_market_tickers))
            or len(retrieved_market_tickers) != len(set(retrieved_market_tickers))
            or set(authoritative_market_tickers) != set(retrieved_market_tickers)
        ):
            raise KalshiPublicMarketDataError(
                "event nested enumeration does not match markets response"
            )
        close_times = {market.close_time for market in markets}
        statuses = {market.status for market in markets}
        if len(close_times) != 1 or len(statuses) != 1:
            raise KalshiPublicMarketDataError("event ladder metadata is inconsistent")
        market_status = next(iter(statuses))
        event_status = "open" if market_status == "active" else market_status
        return RetrievedEvent(
            event_ticker=event_ticker,
            status=event_status,
            close_time=next(iter(close_times)),
            market_tickers=authoritative_market_tickers,
            markets=markets,
            retrieved_at=retrieved_at,
        )

    async def list_active_events(self, *, series_ticker: str) -> tuple[RetrievedEvent, ...]:
        series = self._identifier(series_ticker, "series_ticker")
        timeout = aiohttp.ClientTimeout(total=8)
        async with self._session_factory(timeout=timeout) as session:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            seen_events: set[str] = set()
            event_records: list[
                tuple[str, tuple[Mapping[str, str], ...], tuple[str, ...]]
            ] = []
            while True:
                params: dict[str, Any] = {
                    "series_ticker": series,
                    "status": "open",
                    "with_nested_markets": "true",
                    "limit": 200,
                }
                if cursor is not None:
                    params["cursor"] = cursor
                payload = await self._request_json(session, _EVENTS_URL, params=params)
                raw_events = payload.get("events")
                if not isinstance(raw_events, list):
                    raise KalshiPublicMarketDataError("events page is malformed")
                for raw_event in raw_events:
                    _, event_ticker, _, sources, nested_tickers = self._validate_event(
                        raw_event, expected_series=series
                    )
                    if event_ticker in seen_events:
                        raise KalshiPublicMarketDataError("duplicate event ticker")
                    seen_events.add(event_ticker)
                    event_records.append((event_ticker, sources, nested_tickers))
                next_cursor = self._cursor(payload)
                if not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    raise KalshiPublicMarketDataError("pagination cursor repeated")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            normalized: list[RetrievedEvent] = []
            for event_ticker, sources, nested_tickers in event_records:
                retrieved_at = self._clock().astimezone(timezone.utc)
                markets = await self._list_markets(
                    session,
                    event_ticker=event_ticker,
                    settlement_sources=sources,
                    retrieved_at=retrieved_at,
                )
                normalized.append(
                    self._normalize_event(
                        event_ticker,
                        markets,
                        retrieved_at,
                        authoritative_market_tickers=nested_tickers,
                        requested_open=True,
                    )
                )
            return tuple(normalized)

    async def get_event(self, *, event_ticker: str) -> RetrievedEvent:
        expected = self._identifier(event_ticker, "event_ticker")
        timeout = aiohttp.ClientTimeout(total=8)
        async with self._session_factory(timeout=timeout) as session:
            payload = await self._request_json(
                session,
                self._event_url(expected),
                params={"with_nested_markets": "true"},
            )
            _, returned, _, sources, nested_tickers = self._validate_event(
                payload.get("event"), expected_event=expected
            )
            retrieved_at = self._clock().astimezone(timezone.utc)
            markets = await self._list_markets(
                session,
                event_ticker=returned,
                settlement_sources=sources,
                retrieved_at=retrieved_at,
            )
            return self._normalize_event(
                returned,
                markets,
                retrieved_at,
                authoritative_market_tickers=nested_tickers,
                requested_open=False,
            )
