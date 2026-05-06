from scripts.edge_replay.fetch_historical_prices import normalize_trade_prices


def test_normalize_trade_prices_extracts_yes_price_rows():
    rows = normalize_trade_prices(
        {
            "trades": [
                {"created_time": "2026-05-01T00:00:00Z", "yes_price": 44},
                {"trade_time": "2026-05-01T00:01:00Z", "price": 46},
                {"created_time": None, "yes_price": 50},
            ]
        }
    )

    assert rows == [
        {"ts": "2026-05-01T00:00:00Z", "yes_price": 44.0},
        {"ts": "2026-05-01T00:01:00Z", "yes_price": 46.0},
    ]
