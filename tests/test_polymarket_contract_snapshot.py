from __future__ import annotations

import json
from pathlib import Path


SNAPSHOT = Path("tests/fixtures/polymarket_us/contract_snapshot.json")
DOC = Path(".hermes/api_contracts/polymarket_us_retail_contract.md")


def test_polymarket_us_contract_snapshot_is_present_and_currently_reviewed() -> None:
    data = json.loads(SNAPSHOT.read_text())

    assert data["reviewed_utc"].startswith("2026-06-07T")
    assert data["docs"]["authentication"] == "https://docs.polymarket.us/api-reference/authentication"
    assert data["docs"]["markets_get_markets"] == "https://docs.polymarket.us/api-reference/markets/get-markets"
    assert data["docs"]["account_get_account_balances"] == (
        "https://docs.polymarket.us/api-reference/account/get-account-balances"
    )
    assert data["public_market_data"]["base_url"] == "https://gateway.polymarket.us"
    assert data["public_market_data"]["get_markets_path"] == "/v1/markets"
    assert data["authenticated"]["base_url"] == "https://api.polymarket.us"
    assert data["authenticated"]["portfolio_positions_path"] == "/v1/portfolio/positions"
    assert data["authenticated"]["account_balances_path"] == "/v1/account/balances"
    assert data["authenticated"]["orders_path"] == "/v1/orders"
    assert data["auth"]["signature_message"] == "timestamp + method + path"
    assert data["auth"]["timestamp_unit"] == "milliseconds"
    assert data["auth"]["timestamp_skew_seconds"] == 30
    assert data["rate_limits"]["public_unauthenticated"] == "20 req/sec per IP"
    assert data["rate_limits"]["trading_rest"] == "100 req/sec per firm averaged over 1 minute"
    assert data["rate_limits"]["query_report_endpoints"]["GetBBO"] == "12 req/min"
    assert data["rate_limits"]["query_report_endpoints"]["ListPositionValuations"] == "~0.5 req/min"


def test_polymarket_us_contract_markdown_matches_snapshot_urls() -> None:
    text = DOC.read_text()
    data = json.loads(SNAPSHOT.read_text())

    for url in data["docs"].values():
        assert url in text
    assert "Do not use Global CLOB (`clob.polymarket.com`) for this operator" in text
