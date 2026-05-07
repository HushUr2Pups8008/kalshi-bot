from scripts.edge_replay.fetch_historical_prices import (
    merge_price_rows,
    normalize_trade_prices,
    price_rows_for_ticker,
)


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


def test_normalize_trade_prices_converts_dollars_to_cents():
    rows = normalize_trade_prices({"trades": [{"created_time": "2026-05-01T00:00:00Z", "yes_price_dollars": "0.4200"}]})

    assert rows == [{"ts": "2026-05-01T00:00:00Z", "yes_price": 42.0}]


def test_merge_price_rows_deduplicates_and_sorts():
    rows = merge_price_rows(
        [
            {"ts": "2026-05-01T00:01:00Z", "yes_price": 46, "source": "live_trades"},
            {"ts": "2026-05-01T00:00:00Z", "yes_price": 44, "source": "historical_trades"},
            {"ts": "2026-05-01T00:01:00Z", "yes_price": 46, "source": "historical_trades"},
        ]
    )

    assert rows == [
        {"ts": "2026-05-01T00:00:00Z", "yes_price": 44.0, "source": "historical_trades"},
        {"ts": "2026-05-01T00:01:00Z", "yes_price": 46.0, "source": "live_trades"},
    ]


def test_price_rows_for_ticker_merges_live_and_historical_endpoints():
    class Client:
        def _request(self, method, endpoint, params=None):
            assert method == "GET"
            if endpoint == "/markets/trades":
                assert params["ticker"] == "KXTEST"
                return {"trades": [{"created_time": "2026-05-02T00:00:00Z", "yes_price_dollars": "0.5500"}]}
            if endpoint == "/historical/trades":
                assert params["ticker"] == "KXTEST"
                return {"trades": [{"created_time": "2026-04-20T00:00:00Z", "yes_price_dollars": "0.3300"}]}
            raise AssertionError(endpoint)

    rows, errors = price_rows_for_ticker(Client(), "KXTEST")

    assert errors == []
    assert rows == [
        {"ts": "2026-04-20T00:00:00Z", "yes_price": 33.0, "source": "historical_trades"},
        {"ts": "2026-05-02T00:00:00Z", "yes_price": 55.0, "source": "live_trades"},
    ]
