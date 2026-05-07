from scripts.edge_replay.endpoint_diagnosis import classify_endpoint_state, select_probe_tickers


def test_classify_endpoint_state_prefers_param_adjustment_when_documented_trades_works():
    probes = [
        {"variant": "legacy_per_ticker_trades", "status_code": 404, "usable_shape": False},
        {"variant": "documented_live_trades", "status_code": 200, "usable_shape": True},
    ]

    result = classify_endpoint_state(probes)

    assert result["classification"] == "solvable_auth_or_param"
    assert "documented_live_trades" in result["rationale"]


def test_classify_endpoint_state_marks_dead_when_all_trade_paths_404_for_five_tickers():
    probes = []
    for idx in range(5):
        probes.extend(
            [
                {"ticker": f"KX{idx}", "variant": "legacy_per_ticker_trades", "status_code": 404, "usable_shape": False},
                {"ticker": f"KX{idx}", "variant": "documented_live_trades", "status_code": 404, "usable_shape": False},
                {"ticker": f"KX{idx}", "variant": "documented_historical_trades", "status_code": 404, "usable_shape": False},
            ]
        )

    result = classify_endpoint_state(probes)

    assert result["classification"] == "permanently_dead"


def test_select_probe_tickers_mixes_resolved_and_open_tickers():
    resolved = ["R1", "R2", "R3", "R4"]
    open_tickers = ["O1", "O2", "O3"]

    result = select_probe_tickers(resolved, open_tickers, limit=6)

    assert len(result) == 6
    assert any(row["source"] == "resolved" for row in result)
    assert any(row["source"] == "open" for row in result)
