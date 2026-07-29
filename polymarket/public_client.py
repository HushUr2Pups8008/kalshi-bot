from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import requests
from requests import HTTPError

from config import cfg
from polymarket.models import PolymarketMarket
from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue
from trading.orderbook import (
    BinaryMarketBook,
    BookLevel,
    decimal_value,
    payload_sha256,
)
from utils.bounded_https import fetch_bounded_https_ipv4
from utils.logger import get_logger

log = get_logger("polymarket_public")

_POLYMARKET_AUTHORITATIVE_HOST = "gateway.polymarket.us"
_AUTHORITATIVE_POLYMARKET_MAX_BYTES = 256 * 1024
_AUTHORITATIVE_POLYMARKET_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class PolymarketMarketPage:
    markets: list[PolymarketMarket]
    cursor: str | None
    raw_count: int


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


def _authoritative_timeout_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Polymarket authoritative settlement timeout must be finite and positive")
    timeout_seconds = float(value)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Polymarket authoritative settlement timeout must be finite and positive")
    return timeout_seconds


def _authoritative_slug(value: str) -> str:
    if not isinstance(value, str) or _AUTHORITATIVE_POLYMARKET_SLUG.fullmatch(value) is None:
        raise ValueError("Polymarket authoritative settlement slug is invalid")
    return value


def _authoritative_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Polymarket authoritative settlement base must be a string")
    canonical_base = f"https://{_POLYMARKET_AUTHORITATIVE_HOST}"
    if value != canonical_base:
        raise ValueError(
            "Polymarket authoritative settlement requires exact gateway.polymarket.us HTTPS base"
        )
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "Polymarket authoritative settlement base has an invalid port"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != _POLYMARKET_AUTHORITATIVE_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Polymarket authoritative settlement requires exact gateway.polymarket.us HTTPS base"
        )
    return canonical_base


def _bounded_json_object(raw: bytes, *, provider_name: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError(f"{provider_name} response must be bytes")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{provider_name} response must be a JSON object")
    return payload


def _require_exact_slug(
    payload: dict[str, Any], *, expected_slug: str, provider_name: str
) -> None:
    returned_slug = payload.get("slug")
    if not isinstance(returned_slug, str) or returned_slug != expected_slug:
        raise ValueError(
            f"{provider_name} slug mismatch: expected {expected_slug!r}, "
            f"got {returned_slug!r}"
        )


class PolymarketPublicClient:
    venue = Venue.POLYMARKET_US
    _EXACT_FILTER_PAGE_SIZE = 200
    _EXACT_FILTER_MAX_PAGES = 100

    def __init__(self, *, base_url: str | None = None):
        self._authoritative_base = (
            cfg.polymarket_us_public_base_url if base_url is None else base_url
        )
        self._base = self._authoritative_base.rstrip("/")
        self._session = requests.Session()
        self._last_req_time = 0.0
        self._min_interval = 1.0 / max(
            1, cfg.polymarket_us_public_requests_per_second
        )

    async def _get_authoritative_object(
        self,
        endpoint: str,
        *,
        timeout_seconds: float,
        fetcher: BoundedHttpsFetcher | None,
        provider_name: str,
    ) -> dict[str, Any] | None:
        timeout_seconds = _authoritative_timeout_seconds(timeout_seconds)
        base_url = _authoritative_base_url(self._authoritative_base)
        bounded_fetch = fetcher or fetch_bounded_https_ipv4
        url = f"{base_url}{endpoint}"
        try:
            raw = await bounded_fetch(
                url,
                canonical_host=_POLYMARKET_AUTHORITATIVE_HOST,
                provider_name=provider_name,
                user_agent="kalshi-bot/authoritative-settlement-v1",
                timeout=timeout_seconds,
                max_bytes=_AUTHORITATIVE_POLYMARKET_MAX_BYTES,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        return _bounded_json_object(raw, provider_name=provider_name)

    async def get_market_settlement_exact_bounded(
        self,
        slug: str,
        *,
        timeout_seconds: float,
        fetcher: BoundedHttpsFetcher | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one exact official settlement without legacy client fallback."""
        slug = _authoritative_slug(slug)
        payload = await self._get_authoritative_object(
            f"/v1/markets/{urllib.parse.quote(slug, safe='')}/settlement",
            timeout_seconds=timeout_seconds,
            fetcher=fetcher,
            provider_name="Polymarket US authoritative settlement",
        )
        if payload is None:
            return None
        _require_exact_slug(
            payload,
            expected_slug=slug,
            provider_name="Polymarket US authoritative settlement",
        )
        if "settlement" not in payload:
            raise ValueError(
                "Polymarket US authoritative settlement response is missing settlement"
            )
        settlement = payload["settlement"]
        if (
            isinstance(settlement, bool)
            or not isinstance(settlement, (int, float))
            or not math.isfinite(float(settlement))
        ):
            raise ValueError(
                "Polymarket US authoritative settlement response has invalid settlement"
            )
        return payload

    async def get_market_by_slug_exact_bounded(
        self,
        slug: str,
        *,
        timeout_seconds: float,
        fetcher: BoundedHttpsFetcher | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the documented exact market-by-slug envelope without fallback."""
        slug = _authoritative_slug(slug)
        response = await self._get_authoritative_object(
            f"/v1/market/slug/{urllib.parse.quote(slug, safe='')}",
            timeout_seconds=timeout_seconds,
            fetcher=fetcher,
            provider_name="Polymarket US authoritative market",
        )
        if response is None:
            return None
        market = response.get("market")
        if not isinstance(market, dict):
            raise ValueError(
                "Polymarket US authoritative market response must contain market object"
            )
        _require_exact_slug(
            market,
            expected_slug=slug,
            provider_name="Polymarket US authoritative market",
        )
        market_id = market.get("id")
        if (
            not isinstance(market_id, str)
            or not market_id
            or market_id != market_id.strip()
        ):
            raise ValueError(
                "Polymarket US authoritative market response has invalid id"
            )
        return market

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
        page = self.get_market_page(**kwargs)
        return page.markets, page.cursor

    def get_market_page(self, **kwargs: Any) -> PolymarketMarketPage:
        params = {"limit": kwargs.get("limit", 100), "closed": "false"}
        cursor = kwargs.get("cursor")
        if cursor:
            params["cursor"] = cursor
        offset = kwargs.get("offset")
        if offset is not None:
            params["offset"] = offset

        data = self._request("GET", "/v1/markets", params=params)
        raw_markets = data.get("markets", []) if isinstance(data, dict) else data
        raw_count = len(raw_markets)
        markets = []
        for raw in raw_markets:
            try:
                markets.append(normalize_polymarket_market(raw))
            except ValueError as exc:
                log.debug("Skipping unsupported Polymarket market: %s", exc)
        return PolymarketMarketPage(
            markets=markets,
            cursor=data.get("cursor") if isinstance(data, dict) else None,
            raw_count=raw_count,
        )

    def get_market(self, market_id: str) -> PolymarketMarket:
        # PROFIT-DRAWDOWN-001b: delegate to get_market_payload so a slug-style
        # identifier falls back to a slug/id lookup over the markets list on a
        # 404 instead of raising. The bot persists market_id = slug|id
        # (normalize_polymarket_market sets it from payload["slug"] or ["id"]),
        # and GET /v1/markets/{id} only resolves the numeric id -- so a stored
        # slug 404s. This 404'd every open Polymarket position in
        # scripts/mark_open_positions.py (and any other get_market caller passing
        # a slug). The settlement path already used get_market_payload; this
        # aligns get_market with it. List-endpoint payloads normalize the same
        # way (see get_markets), so the fallback result is safe to normalize.
        payload = self.get_market_payload(market_id)
        return normalize_polymarket_market(payload)

    def get_market_payload(self, market_id: str) -> dict[str, Any]:
        try:
            data = self._request("GET", f"/v1/markets/{market_id}")
        except HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code != 404:
                raise
            return self._find_market_payload_by_slug_or_id(market_id)
        if not isinstance(data, dict):
            raise ValueError("Polymarket market response must be an object")
        payload = data.get("market", data)
        if not isinstance(payload, dict):
            raise ValueError("Polymarket market payload must be an object")
        return payload

    def get_market_book(self, market_id: str) -> BinaryMarketBook:
        requested = str(market_id).strip()
        slug = requested
        if requested.isdigit():
            market = self.get_market_payload(requested)
            slug = str(market.get("slug") or "").strip()
        if not slug or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug) is None:
            raise ValueError(f"Polymarket market {market_id!r} has invalid canonical slug")

        data = self._request("GET", f"/v1/markets/{slug}/book")
        market_data = data.get("marketData") if isinstance(data, dict) else None
        if not isinstance(market_data, dict):
            raise ValueError("Polymarket book response missing marketData")
        returned_slug = str(market_data.get("marketSlug") or "").strip()
        if returned_slug != slug:
            raise ValueError(
                f"Polymarket book slug mismatch: expected {slug!r}, "
                f"got {returned_slug!r}"
            )
        transact_time = market_data.get("transactTime")
        if not isinstance(transact_time, str) or not transact_time:
            raise ValueError("Polymarket book missing transactTime")
        try:
            as_of = datetime.fromisoformat(transact_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Polymarket book transactTime is invalid") from exc

        def levels_for(key: str) -> tuple[BookLevel, ...]:
            raw_levels = market_data.get(key)
            if not isinstance(raw_levels, list):
                raise ValueError(f"Polymarket book {key} must be a list")
            levels = []
            for raw_level in raw_levels:
                if not isinstance(raw_level, dict):
                    raise ValueError(f"Polymarket book {key} level is malformed")
                price_obj = raw_level.get("px")
                if not isinstance(price_obj, dict):
                    raise ValueError(f"Polymarket book {key} price is malformed")
                levels.append(
                    BookLevel(
                        price=decimal_value(price_obj.get("value")),
                        quantity=decimal_value(raw_level.get("qty")),
                    )
                )
            return tuple(sorted(levels, key=lambda level: level.price, reverse=True))

        yes_bids = levels_for("bids")
        yes_offers = levels_for("offers")
        no_bids = tuple(
            sorted(
                (
                    BookLevel(
                        price=Decimal("1") - level.price,
                        quantity=level.quantity,
                    )
                    for level in yes_offers
                ),
                key=lambda level: level.price,
                reverse=True,
            )
        )
        return BinaryMarketBook(
            venue=Venue.POLYMARKET_US,
            venue_market_id=slug,
            yes_bids=yes_bids,
            no_bids=no_bids,
            as_of=as_of,
            raw_payload_hash=payload_sha256(data),
        )

    def get_market_settlement(self, market_id: str) -> dict[str, Any]:
        """Return the authoritative settlement payload for a market slug."""
        requested = str(market_id).strip()
        slug = requested
        if requested.isdigit():
            market = self.get_market_payload(requested)
            slug = str(market.get("slug") or "").strip()
        if not slug:
            raise ValueError(f"Polymarket market {market_id!r} has no canonical slug")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug) is None:
            raise ValueError(
                f"Polymarket market {market_id!r} has invalid canonical slug"
            )

        try:
            data = self._request("GET", f"/v1/markets/{slug}/settlement")
        except HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 404:
                raise ValueError(
                    f"Polymarket market {market_id!r} settlement not found"
                ) from exc
            raise

        if not isinstance(data, dict):
            raise ValueError("Polymarket settlement response must be an object")
        returned_slug = str(data.get("slug") or "").strip()
        if returned_slug != slug:
            raise ValueError(
                f"Polymarket settlement slug mismatch: expected {slug!r}, "
                f"got {returned_slug!r}"
            )
        return data

    def _find_market_payload_by_slug_or_id(self, market_id: str) -> dict[str, Any]:
        # FIX-1 (PM feed-drop durable handling): resolve via the server-side
        # exact-match filter on /v1/markets instead of a cursor-paginated scan.
        #
        # WHY the old cursor scan was structurally broken: it paged closed=false
        # then closed=true at limit=500, but the closed=true listing terminates
        # at the oldest ~500-1000 ids (cursor=None at every page), so a resolved
        # HIGH-id market was never reached. Live-probed: id=8594
        # (slug aqc-cbb-f4-2026-04-06-kan) raised 'not found' under the scan but
        # the ?slug= filter returns it instantly. The held election positions sit
        # at id 40542/44051 -- far beyond the scan's reach -- so by-slug auto-
        # settlement would SettlementNotFound forever once they resolved.
        #
        # The filter MUST NOT pass a closed= param: it crosses the closed
        # boundary on its own (live-probed: ?slug=...kan returns closed=true with
        # no closed param), which is exactly what settlement needs at resolution.
        #
        # We DO NOT trust the filter blindly on this money/state path: every
        # returned payload is re-confirmed to actually match the wanted
        # slug-or-id before returning, and if neither filter yields a confirmed
        # match we raise the SAME ValueError('... not found') as before so
        # settlement_reconciler's SettlementNotFound translation and reconcile()'s
        # per-ticker isolation (P2, #149) keep handling a transiently-absent
        # market unchanged.
        #
        # The ?id= filter is ONLY issued when the stored identifier is numeric.
        # Live-probed: ?slug=<absent> returns an empty list (clean miss), but
        # ?id=<non-numeric> returns HTTP 400. The bot persists market_id =
        # slug|id, so the held election positions are slug-keyed (non-numeric) --
        # firing ?id= on a slug would 400 every cycle and, worse, escape this
        # method as an HTTPError instead of the documented not-found ValueError,
        # breaking the transient-drop contract. So a non-numeric wanted only ever
        # tries ?slug=; a numeric wanted tries ?slug= then ?id=. A defensive 400
        # guard on the id call (belt-and-suspenders) still degrades to a clean
        # miss rather than leaking an HTTPError.
        try:
            candidates = self.find_market_payloads_by_slug_or_id(market_id)
        except HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if str(market_id).strip().isdigit() and status_code == 400:
                raise ValueError(f"Polymarket market {market_id!r} not found") from exc
            raise
        if candidates:
            return candidates[0]
        raise ValueError(f"Polymarket market {market_id!r} not found")

    def find_market_payloads_by_slug_or_id(
        self,
        market_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return every exact slug or numeric-ID match across all filter pages."""
        wanted = str(market_id).strip()
        filter_keys = ["slug"]
        if wanted.isdigit():
            filter_keys.append("id")
        matches: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for filter_key in filter_keys:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for _page_number in range(self._EXACT_FILTER_MAX_PAGES):
                params: dict[str, Any] = {
                    filter_key: wanted,
                    "limit": self._EXACT_FILTER_PAGE_SIZE,
                }
                if cursor is not None:
                    params["cursor"] = cursor
                data = self._request("GET", "/v1/markets", params=params)
                if not isinstance(data, dict):
                    raise ValueError("Polymarket exact-filter response must be an object")
                if "markets" not in data:
                    raise ValueError(
                        "Polymarket exact-filter response must include markets"
                    )
                raw_markets = data["markets"]
                if not isinstance(raw_markets, list):
                    raise ValueError("Polymarket exact-filter markets must be a list")
                cursor_present = "cursor" in data
                next_cursor = data.get("cursor")
                if next_cursor is not None and not isinstance(next_cursor, str):
                    raise ValueError(
                        "Polymarket exact-filter cursor must be a string or null"
                    )
                for payload in raw_markets:
                    if not isinstance(payload, dict):
                        raise ValueError(
                            "Polymarket exact-filter market payload must be an object"
                        )
                    identifiers = {
                        str(payload.get("slug") or "").strip(),
                        str(payload.get("id") or "").strip(),
                    }
                    if wanted in identifiers:
                        identity = (
                            str(payload.get("slug") or "").strip(),
                            str(payload.get("id") or "").strip(),
                        )
                        if identity not in seen:
                            seen.add(identity)
                            matches.append(payload)
                if not cursor_present:
                    if len(raw_markets) >= self._EXACT_FILTER_PAGE_SIZE:
                        raise ValueError(
                            "Polymarket exact-filter response missing cursor on "
                            "full page; pagination is ambiguous"
                        )
                    break
                if not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    raise ValueError("Polymarket exact-filter cursor cycle detected")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                raise ValueError("Polymarket exact-filter page limit exceeded")
        return tuple(matches)
