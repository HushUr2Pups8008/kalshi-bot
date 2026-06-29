import logging

import pytest

from kalshi import websocket_client as ws_mod


class _HandshakeTimeoutConnect:
    async def __aenter__(self):
        raise TimeoutError("timed out during opening handshake")

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_reconnectable_ws_handshake_error_logs_warning_not_error(
    monkeypatch, caplog
):
    """Reconnectable WS startup failures must not pollute ERROR failure gates."""
    monkeypatch.setattr(ws_mod, "_WS_AVAILABLE", True)
    monkeypatch.setattr(ws_mod.cfg, "api_key_id", "")
    monkeypatch.setattr(ws_mod.cfg, "api_key_secret", "")
    monkeypatch.setattr(ws_mod, "_build_ws_auth_headers", lambda: {})
    monkeypatch.setattr(
        ws_mod.websockets,
        "connect",
        lambda *args, **kwargs: _HandshakeTimeoutConnect(),
    )

    client = ws_mod.KalshiWebSocketClient()

    async def _stop_after_reconnect_sleep(delay):
        client.stop()

    monkeypatch.setattr(ws_mod.asyncio, "sleep", _stop_after_reconnect_sleep)

    with caplog.at_level(logging.WARNING, logger="kalshi_ws"):
        await client.run()

    assert any(
        record.levelno == logging.WARNING and "WS error" in record.message
        for record in caplog.records
    )
    assert not any(
        record.levelno >= logging.ERROR and "WS error" in record.message
        for record in caplog.records
    )
