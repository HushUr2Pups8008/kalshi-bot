"""The runtime fill log is for real fills. Tests must not append to it."""

from trading.executor import RUNTIME_FILL_LOG_PATH, append_live_fill_log


def test_a_test_cannot_append_to_the_runtime_fill_log(tmp_path):
    """A mock row and a valid row both leave logs/state/live-fill-log.md untouched."""

    before = RUNTIME_FILL_LOG_PATH.read_bytes() if RUNTIME_FILL_LOG_PATH.exists() else None
    append_live_fill_log(
        ticker="KXTRUTHSOCIAL-26OCT-A",
        side="no",
        price_cents=63,
        contracts=7,
        client_order_id="client-1",
        http_result="<MagicMock name='mock.place_limit_order().http_status'>",
        path=RUNTIME_FILL_LOG_PATH,
        filled_count=None,
        order_id="mock-order",
    )
    append_live_fill_log(
        ticker="KXTRUMPSAY-26OCT19-GOLF",
        side="yes",
        price_cents=89,
        contracts=8,
        client_order_id="client-1",
        http_result="201",
        path=RUNTIME_FILL_LOG_PATH,
        filled_count=8,
        order_id="order-1",
    )
    after = RUNTIME_FILL_LOG_PATH.read_bytes() if RUNTIME_FILL_LOG_PATH.exists() else None
    assert after == before

    target = tmp_path / "fills.md"
    append_live_fill_log(
        ticker="KXTRUMPSAY-26OCT19-GOLF",
        side="yes",
        price_cents=89,
        contracts=8,
        client_order_id="client-1",
        http_result="201",
        path=target,
        filled_count=8,
        order_id="order-1",
    )
    assert target.read_text(encoding="utf-8").strip().split(", ")[1:] == [
        "KXTRUMPSAY-26OCT19-GOLF",
        "yes",
        "89",
        "8",
        "client-1",
        "201",
    ]


def test_default_fill_log_path_during_a_test_is_not_the_runtime_log():
    import trading.executor as executor

    assert executor.LIVE_FILL_LOG_PATH.resolve() != executor.RUNTIME_FILL_LOG_PATH.resolve()
