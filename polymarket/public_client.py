from __future__ import annotations

import time
from typing import Any

import requests
from requests import HTTPError

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
        wanted = str(market_id).strip()
        filter_keys = ["slug"]
        if wanted.isdigit():
            filter_keys.append("id")
        for filter_key in filter_keys:
            params: dict[str, Any] = {filter_key: wanted, "limit": 5}
            try:
                data = self._request("GET", "/v1/markets", params=params)
            except HTTPError as exc:
                status_code = getattr(
                    getattr(exc, "response", None), "status_code", None
                )
                if filter_key == "id" and status_code == 400:
                    # Filter rejected the identifier -> treat as no match and
                    # fall through to the not-found ValueError contract.
                    continue
                raise
            raw_markets = data.get("markets", []) if isinstance(data, dict) else data
            for payload in raw_markets:
                if not isinstance(payload, dict):
                    continue
                identifiers = {
                    str(payload.get("slug") or "").strip(),
                    str(payload.get("id") or "").strip(),
                }
                if wanted in identifiers:
                    return payload
        raise ValueError(f"Polymarket market {market_id!r} not found")
