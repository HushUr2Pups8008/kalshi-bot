from unittest.mock import MagicMock

import pytest
import requests

from polymarket.public_client import PolymarketPublicClient
from trading.venue import Venue


def _market_payload(slug: str = "m1") -> dict:
    return {
        "slug": slug,
        "title": "Will X?",
        "status": "open",
        "outcomes": [
            {"name": "Yes", "bestAsk": {"value": "0.40"}},
            {"name": "No", "bestAsk": {"value": "0.61"}},
        ],
    }


def _response(payload: dict, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.text = "{}"
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    else:
        response.raise_for_status.return_value = None
    return response


def test_get_market_settlement_calls_slug_endpoint():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    client._session.request = MagicMock(
        return_value=_response({"slug": "will-example-happen", "settlement": "1"})
    )

    payload = client.get_market_settlement("will-example-happen")

    assert payload == {"slug": "will-example-happen", "settlement": "1"}
    assert client._session.request.call_args.args[1].endswith(
        "/v1/markets/will-example-happen/settlement"
    )


@pytest.mark.parametrize(
    "slug",
    ["will/example", "will?region=us", "will#result", ".", ".."],
)
def test_get_market_settlement_rejects_noncanonical_slug(slug):
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    client._session.request = MagicMock()

    with pytest.raises(ValueError, match="invalid canonical slug"):
        client.get_market_settlement(slug)

    client._session.request.assert_not_called()


def test_get_market_settlement_resolves_numeric_id_to_slug():
    client = PolymarketPublicClient()
    client.get_market_payload = MagicMock(
        return_value={"id": "123", "slug": "canonical-slug"}
    )
    client._request = MagicMock(
        return_value={"slug": "canonical-slug", "settlement": 0}
    )

    payload = client.get_market_settlement("123")

    assert payload["settlement"] == 0
    client._request.assert_called_once_with(
        "GET", "/v1/markets/canonical-slug/settlement"
    )


def test_get_market_settlement_rejects_slug_mismatch():
    client = PolymarketPublicClient()
    client._request = MagicMock(
        return_value={"slug": "different", "settlement": 1}
    )

    with pytest.raises(ValueError, match="slug mismatch"):
        client.get_market_settlement("expected")


def test_get_market_settlement_rejects_nonobject_response():
    client = PolymarketPublicClient()
    client._request = MagicMock(return_value=[])

    with pytest.raises(ValueError, match="must be an object"):
        client.get_market_settlement("expected")


def test_get_market_settlement_translates_only_http_404_to_not_found():
    client = PolymarketPublicClient()
    not_found_response = MagicMock(status_code=404)
    client._request = MagicMock(
        side_effect=requests.HTTPError(response=not_found_response)
    )

    with pytest.raises(ValueError, match="not found"):
        client.get_market_settlement("expected")

    server_error_response = MagicMock(status_code=503)
    client._request.side_effect = requests.HTTPError(response=server_error_response)
    with pytest.raises(requests.HTTPError):
        client.get_market_settlement("expected")


def test_get_markets_uses_public_gateway_and_normalizes():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[{"slug":"m1"}]}'
    response.json.return_value = {"markets": [_market_payload()], "cursor": None}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=1)

    assert client.venue == Venue.POLYMARKET_US
    assert len(markets) == 1
    assert markets[0].market_id == "m1"
    assert cursor is None
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets",
    )
    kwargs = client._session.request.call_args.kwargs
    assert kwargs["params"] == {"limit": 1, "closed": "false"}
    headers = kwargs["headers"]
    assert headers["Accept"] == "application/json"
    assert "X-PM-Access-Key" not in headers
    assert "X-PM-Signature" not in headers


def test_get_markets_passes_cursor_and_returns_cursor():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us/")
    response = MagicMock()
    response.text = '{"markets":[],"cursor":"next"}'
    response.json.return_value = {"markets": [], "cursor": "next"}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=25, cursor="abc")

    assert markets == []
    assert cursor == "next"
    assert client._session.request.call_args.kwargs["params"] == {
        "limit": 25,
        "cursor": "abc",
        "closed": "false",
    }


def test_get_market_normalizes_single_market_payload():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"market":{"slug":"m2"}}'
    response.json.return_value = {"market": _market_payload("m2")}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    market = client.get_market("m2")

    assert market.market_id == "m2"
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets/m2",
    )


def test_get_market_falls_back_to_slug_lookup_on_404():
    # PROFIT-DRAWDOWN-001b: a stored slug-style id (the form the bot persists)
    # 404s on GET /v1/markets/{slug}. get_market must fall back to the markets-
    # list slug/id lookup and normalize, so mark_open_positions can price open
    # Polymarket positions instead of leaving them unpriced. WHY this matters:
    # the prior get_market raised on 404, so every open Polymarket position was
    # reported as value-unknown, inflating the apparent paper drawdown.
    slug = "ewc-usse-me-2026-11-03-dem"
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    not_found = MagicMock()
    not_found.text = "{}"
    not_found.status_code = 404
    not_found.raise_for_status.side_effect = requests.HTTPError(response=not_found)
    listed = MagicMock()
    listed.text = '{"markets":[]}'
    listed.json.return_value = {"markets": [_market_payload(slug)], "cursor": None}
    listed.raise_for_status.return_value = None
    client._session.request = MagicMock(side_effect=[not_found, listed])

    market = client.get_market(slug)

    assert market.market_id == slug
    # First call: the direct (404'd) GET. Second: the server-side ?slug= filter.
    assert client._session.request.call_args_list[0].args[1].endswith(
        f"/v1/markets/{slug}"
    )
    # FIX-1: the fallback now uses the exact-match ?slug= filter on the same
    # /v1/markets listing (one filtered call), NOT a cursor-paginated scan. The
    # filter MUST NOT send a closed= param -- it crosses the closed boundary by
    # itself (live-probed), which is what settlement needs at resolution.
    second_params = client._session.request.call_args_list[1].kwargs["params"]
    assert second_params == {"slug": slug, "limit": 5}
    assert "closed" not in second_params
    # The held side is now priceable for mark-to-market (was unpriced before).
    assert market.yes_ask_cents is not None and market.yes_ask_cents > 0


def test_get_market_payload_falls_back_to_open_market_slug_lookup_on_404():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    not_found = MagicMock()
    not_found.text = "{}"
    not_found.status_code = 404
    not_found.raise_for_status.side_effect = requests.HTTPError(
        response=not_found
    )
    listed = MagicMock()
    listed.text = '{"markets":[]}'
    listed.json.return_value = {"markets": [_market_payload("m2")], "cursor": None}
    listed.raise_for_status.return_value = None
    client._session.request = MagicMock(side_effect=[not_found, listed])

    payload = client.get_market_payload("m2")

    assert payload["slug"] == "m2"
    # FIX-1: exact-match ?slug= filter on the listing surface, no closed= param.
    second_params = client._session.request.call_args_list[1].kwargs["params"]
    assert second_params == {"slug": "m2", "limit": 5}
    assert "closed" not in second_params


def test_get_market_payload_falls_back_to_closed_market_slug_lookup_for_settlement():
    # FIX-1: a resolved (closed=true) market must be reachable for settlement via
    # the SAME ?slug= filter -- the filter crosses the closed boundary on its own
    # (live-probed: ?slug=aqc-cbb-f4-2026-04-06-kan -> id=8594 closed=true with no
    # closed param). No separate closed=true page is issued anymore.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    not_found = MagicMock()
    not_found.text = "{}"
    not_found.status_code = 404
    not_found.raise_for_status.side_effect = requests.HTTPError(
        response=not_found
    )
    filtered = MagicMock()
    filtered.text = '{"markets":[]}'
    closed_payload = {
        **_market_payload("m2"),
        "closed": True,
        "resolvedOutcome": "YES",
    }
    filtered.json.return_value = {"markets": [closed_payload], "cursor": None}
    filtered.raise_for_status.return_value = None
    client._session.request = MagicMock(side_effect=[not_found, filtered])

    payload = client.get_market_payload("m2")

    assert payload["slug"] == "m2"
    assert payload["resolvedOutcome"] == "YES"
    second_params = client._session.request.call_args_list[1].kwargs["params"]
    assert second_params == {"slug": "m2", "limit": 5}
    assert "closed" not in second_params


def test_find_market_payload_filter_returns_high_id_closed_market():
    # WHY: the held election positions sit at id 40542/44051, far beyond the old
    # cursor-scan's reach (it terminated at the oldest ~500-1000 closed ids and
    # raised 'not found'). A resolved high-id market MUST be reachable so
    # settlement completes -- this is the exact failure the cursor-scan caused
    # (live-probed against id=8594, which the scan could not find). The ?slug=
    # filter returns it instantly.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    high_id_closed = {
        **_market_payload("us-pres-2026-some-contest-dem"),
        "id": 44051,
        "closed": True,
        "resolvedOutcome": "NO",
    }
    filtered = MagicMock()
    filtered.text = '{"markets":[]}'
    filtered.json.return_value = {"markets": [high_id_closed], "cursor": None}
    filtered.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=filtered)

    payload = client._find_market_payload_by_slug_or_id(
        "us-pres-2026-some-contest-dem"
    )

    assert payload["id"] == 44051
    assert payload["closed"] is True
    # One filtered call, ?slug=, no closed= param.
    assert client._session.request.call_count == 1
    params = client._session.request.call_args.kwargs["params"]
    assert params == {"slug": "us-pres-2026-some-contest-dem", "limit": 5}
    assert "closed" not in params


def test_find_market_payload_id_filter_fallback():
    # WHY: the bot persists market_id = slug|id (normalize sets it from
    # payload['slug'] or payload['id']), so the stored identifier can be a
    # NUMERIC id rather than a slug. When ?slug= returns no match, the ?id=
    # filter must recover it -- otherwise an id-keyed position SettlementNotFounds
    # forever.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    by_slug_empty = MagicMock()
    by_slug_empty.text = '{"markets":[]}'
    by_slug_empty.json.return_value = {"markets": [], "cursor": None}
    by_slug_empty.raise_for_status.return_value = None
    by_id_hit = MagicMock()
    by_id_hit.text = '{"markets":[]}'
    id_payload = {**_market_payload("some-slug"), "id": 8594}
    by_id_hit.json.return_value = {"markets": [id_payload], "cursor": None}
    by_id_hit.raise_for_status.return_value = None
    client._session.request = MagicMock(side_effect=[by_slug_empty, by_id_hit])

    payload = client._find_market_payload_by_slug_or_id("8594")

    assert str(payload["id"]) == "8594"
    # First call ?slug=, second ?id= -- both exact-match filters, no closed param.
    first = client._session.request.call_args_list[0].kwargs["params"]
    second = client._session.request.call_args_list[1].kwargs["params"]
    assert first == {"slug": "8594", "limit": 5}
    assert second == {"id": "8594", "limit": 5}
    assert "closed" not in first and "closed" not in second


def test_find_market_payload_still_raises_not_found_when_absent():
    # WHY: a market TRANSIENTLY DROPPED from the listing entirely (the
    # 2026-06-17 election-category collapse) matches neither filter. It MUST
    # still raise the identical ValueError('... not found') so settlement_
    # reconciler's SettlementNotFound translation + reconcile()'s per-ticker
    # isolation (P2, #149) keep handling it -- the position stays open and is
    # retried next cycle, never silently "succeeds" with a wrong/empty market.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    empty = MagicMock()
    empty.text = '{"markets":[]}'
    empty.json.return_value = {"markets": [], "cursor": None}
    empty.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=empty)

    try:
        client._find_market_payload_by_slug_or_id("ewc-usse-me-2026-11-03-dem")
        raised = False
    except ValueError as exc:
        raised = True
        assert "not found" in str(exc).lower()
        assert "ewc-usse-me-2026-11-03-dem" in str(exc)
    assert raised, "transient-drop must raise not-found, never silently succeed"
    # The wanted id is a non-numeric slug, so ONLY the ?slug= filter is issued
    # (see below: firing ?id= on a slug 400s and would break the contract).
    assert client._session.request.call_count == 1
    assert client._session.request.call_args.kwargs["params"] == {
        "slug": "ewc-usse-me-2026-11-03-dem",
        "limit": 5,
    }


def test_find_market_payload_non_numeric_slug_never_fires_id_filter():
    # WHY (live-probed regression guard): ?id=<non-numeric> returns HTTP 400, not
    # an empty list. The bot persists slug-keyed identifiers for the held
    # election positions, so firing ?id= on a slug would 400 EVERY cycle and
    # escape this method as an HTTPError instead of the documented not-found
    # ValueError -- breaking the transient-drop contract that feeds
    # SettlementNotFound. A non-numeric wanted must therefore try ONLY ?slug=.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    slug_hit = MagicMock()
    slug_hit.text = '{"markets":[]}'
    slug_hit.json.return_value = {
        "markets": [_market_payload("ewc-usse-me-2026-11-03-dem")],
        "cursor": None,
    }
    slug_hit.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=slug_hit)

    payload = client._find_market_payload_by_slug_or_id(
        "ewc-usse-me-2026-11-03-dem"
    )

    assert payload["slug"] == "ewc-usse-me-2026-11-03-dem"
    # Exactly one call, the ?slug= filter -- the ?id= filter is never attempted.
    assert client._session.request.call_count == 1
    assert "id" not in client._session.request.call_args.kwargs["params"]


def test_find_market_payload_id_filter_400_degrades_to_not_found():
    # WHY: a numeric wanted whose ?slug= misses then triggers a ?id= that the
    # server rejects with 400 must degrade to the SAME not-found ValueError
    # contract, NOT leak an HTTPError. (Defensive belt-and-suspenders: even
    # though we gate ?id= behind .isdigit(), a server-side 400 on the id call
    # still resolves to a clean miss so settlement isolation handles it.)
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    by_slug_empty = MagicMock()
    by_slug_empty.text = '{"markets":[]}'
    by_slug_empty.json.return_value = {"markets": [], "cursor": None}
    by_slug_empty.raise_for_status.return_value = None
    id_400 = MagicMock()
    id_400.text = "{}"
    id_400.status_code = 400
    id_400.raise_for_status.side_effect = requests.HTTPError(response=id_400)
    client._session.request = MagicMock(side_effect=[by_slug_empty, id_400])

    try:
        client._find_market_payload_by_slug_or_id("8594")
        raised = False
    except ValueError as exc:
        raised = True
        assert "not found" in str(exc).lower()
    except requests.HTTPError:
        raise AssertionError(
            "id-filter 400 must degrade to not-found, never leak HTTPError"
        )
    assert raised
    # Both filters were attempted (numeric wanted): ?slug= then ?id=.
    assert client._session.request.call_count == 2


def test_find_market_payload_rejects_server_filter_mismatch():
    # WHY: do not trust the server filter blindly on a money/state path. If the
    # filter echoes a payload whose slug AND id both differ from what we asked
    # for, returning it would MIS-SETTLE the wrong market. The defensive
    # re-confirmation must reject the mismatch and raise not-found instead.
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    wrong = MagicMock()
    wrong.text = '{"markets":[]}'
    mismatch = {**_market_payload("totally-different-slug"), "id": 999}
    wrong.json.return_value = {"markets": [mismatch], "cursor": None}
    wrong.raise_for_status.return_value = None
    # Both ?slug= and ?id= echo the same non-matching payload.
    client._session.request = MagicMock(return_value=wrong)

    try:
        client._find_market_payload_by_slug_or_id("the-slug-we-asked-for")
        raised = False
    except ValueError as exc:
        raised = True
        assert "not found" in str(exc).lower()
    assert raised, "a non-matching filtered payload must not be trusted/returned"


def test_get_markets_skips_unsupported_payloads():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[]}'
    response.json.return_value = {
        "markets": [
            _market_payload("m1"),
            {"slug": "multi", "outcomes": [{"name": "A"}, {"name": "B"}, {"name": "C"}]},
        ]
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets()

    assert [market.market_id for market in markets] == ["m1"]
    assert cursor is None
