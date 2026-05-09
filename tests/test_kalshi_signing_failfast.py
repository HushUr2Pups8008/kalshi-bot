"""
Tests for PROFIT-SEC-001: silent Kalshi auth bypass remediation.

Verifies that:
- A configured-but-broken key is fatal at init time (REST and WS).
- No-credentials mode leaves public-only operation intact.
- A per-call signing failure raises and never reaches the network.
- _build_ws_auth_headers() raises on bad PEM when creds are set.
- _build_ws_auth_headers() returns {} when no creds are set.
"""

import pytest
from unittest.mock import MagicMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import config as _cfg_module
from kalshi.rest_client import KalshiRestClient, KalshiSigningError
from kalshi.websocket_client import (
    KalshiWebSocketClient,
    KalshiWsSigningError,
    _build_ws_auth_headers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_valid_pem() -> str:
    """Return a fresh RSA-2048 private key as a PEM string (real newlines)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


_BAD_PEM = "not-a-valid-pem-at-all"


# ---------------------------------------------------------------------------
# REST client tests
# ---------------------------------------------------------------------------

def test_rest_client_init_raises_on_bad_pem(monkeypatch):
    """KalshiRestClient() must raise KalshiSigningError when key is configured but PEM is invalid."""
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "test-key-id")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", _BAD_PEM)

    with pytest.raises(KalshiSigningError):
        KalshiRestClient()


def test_rest_client_init_ok_with_no_creds(monkeypatch):
    """KalshiRestClient() must NOT raise when no credentials are configured (public-only mode)."""
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", "")

    client = KalshiRestClient()  # must not raise
    assert client._private_key is None


def test_rest_client_sign_raises_no_network_call(monkeypatch):
    """
    When _private_key.sign() raises at call time, _sign() must raise KalshiSigningError
    and _session.request must NOT be called.
    """
    valid_pem = _generate_valid_pem()
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "test-key-id")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", valid_pem)

    client = KalshiRestClient()

    # Replace the loaded private key with a mock whose .sign() raises.
    broken_key = MagicMock()
    broken_key.sign.side_effect = ValueError("injected signing failure")
    client._private_key = broken_key

    mock_request = MagicMock()
    client._session.request = mock_request

    with pytest.raises(KalshiSigningError):
        client._sign("GET", "/trade-api/v2/markets")

    mock_request.assert_not_called()


# ---------------------------------------------------------------------------
# WebSocket auth-headers function tests
# ---------------------------------------------------------------------------

def test_ws_auth_headers_raises_on_bad_pem(monkeypatch):
    """_build_ws_auth_headers() must raise KalshiWsSigningError when PEM is invalid."""
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "test-key-id")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", _BAD_PEM)

    with pytest.raises(KalshiWsSigningError):
        _build_ws_auth_headers()


def test_ws_auth_headers_returns_empty_when_no_creds(monkeypatch):
    """_build_ws_auth_headers() must return {} when no credentials are configured."""
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", "")

    result = _build_ws_auth_headers()
    assert result == {}


# ---------------------------------------------------------------------------
# WebSocket client init test
# ---------------------------------------------------------------------------

def test_ws_client_init_raises_on_bad_pem(monkeypatch):
    """KalshiWebSocketClient() must raise KalshiWsSigningError when PEM is invalid."""
    monkeypatch.setattr(_cfg_module.cfg, "api_key_id", "test-key-id")
    monkeypatch.setattr(_cfg_module.cfg, "api_key_secret", _BAD_PEM)

    with pytest.raises(KalshiWsSigningError):
        KalshiWebSocketClient()
