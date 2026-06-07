import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from polymarket.auth import PolymarketAuth


def _secret_b64() -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii"), key


def test_auth_headers_sign_timestamp_method_and_path(monkeypatch):
    secret, private_key = _secret_b64()
    monkeypatch.setattr("polymarket.auth.time.time", lambda: 1710000000.123)

    auth = PolymarketAuth(key_id="key-123", secret_b64=secret)
    headers = auth.headers("get", "/orders?market=btc-100k-2025")

    assert headers["X-PM-Access-Key"] == "key-123"
    assert headers["X-PM-Timestamp"] == "1710000000123"
    assert headers["Content-Type"] == "application/json"

    message = b"1710000000123GET/orders?market=btc-100k-2025"
    signature = base64.b64decode(headers["X-PM-Signature"], validate=True)
    private_key.public_key().verify(signature, message)


def test_auth_uses_first_32_secret_bytes(monkeypatch):
    secret, private_key = _secret_b64()
    extended_secret = base64.b64encode(base64.b64decode(secret) + b"ignored").decode(
        "ascii"
    )
    monkeypatch.setattr("polymarket.auth.time.time", lambda: 1710000000.0)

    auth = PolymarketAuth(key_id="key-123", secret_b64=extended_secret)
    headers = auth.headers("POST", "/orders")

    message = b"1710000000000POST/orders"
    signature = base64.b64decode(headers["X-PM-Signature"], validate=True)
    private_key.public_key().verify(signature, message)


def test_auth_rejects_blank_key_id_without_leaking_secret():
    secret, _ = _secret_b64()

    with pytest.raises(ValueError) as excinfo:
        PolymarketAuth(key_id=" ", secret_b64=secret)

    assert secret not in str(excinfo.value)
    assert "Polymarket US key_id is required" in str(excinfo.value)


def test_auth_rejects_invalid_secret_without_leaking_secret():
    secret = "not-base64-secret"

    with pytest.raises(ValueError) as excinfo:
        PolymarketAuth(key_id="key-123", secret_b64=secret)

    assert secret not in str(excinfo.value)
    assert "invalid Polymarket US Ed25519 secret" in str(excinfo.value)
