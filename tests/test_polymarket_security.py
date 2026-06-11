from __future__ import annotations

import config as config_module
from polymarket.security import redact_polymarket_secret
from tests.test_polymarket_config import _clear_polymarket_env, _valid_rsa_pem


def test_redact_polymarket_secret_removes_key_material():
    secret = "base64-secret-value"
    message = f"failed auth with {secret}"

    redacted = redact_polymarket_secret(message, secret)

    assert secret not in redacted
    assert "[REDACTED_POLYMARKET_SECRET]" in redacted


def test_redact_polymarket_secret_noops_when_secret_missing():
    message = "failed auth without local key material"

    assert redact_polymarket_secret(message, None) == message
    assert redact_polymarket_secret(message, "") == message


def test_enabled_runtime_no_longer_requires_eligibility_ack(monkeypatch):
    # Operator decision 2026-06-11: the daily same-day eligibility-ack gate is
    # removed. Enabling Polymarket no longer requires
    # POLYMARKET_US_ELIGIBILITY_ACK_DATE — config builds without it, like Kalshi.
    # WHY this stays a test: it pins the regression so the daily-.env-edit
    # requirement cannot silently return.
    _clear_polymarket_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "key")
    monkeypatch.setenv("POLYMARKET_US_SECRET", "base64-secret-value")
    monkeypatch.delenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", raising=False)

    cfg = config_module.BotConfig(
        api_key_id="kalshi-key",
        api_key_secret=_valid_rsa_pem(),
    )
    assert cfg.polymarket_us_enabled is True
