from __future__ import annotations

from datetime import date, timedelta

import pytest

import config as config_module
from polymarket.security import (
    redact_polymarket_secret,
    require_polymarket_enablement_preflight,
)
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


def test_disabled_runtime_does_not_require_eligibility_ack():
    assert (
        require_polymarket_enablement_preflight(
            enabled=False,
            eligibility_ack_date="",
            today="2026-06-07",
        )
        is None
    )


def test_enabled_runtime_requires_same_day_eligibility_ack(monkeypatch, capsys):
    _clear_polymarket_env(monkeypatch)
    secret = "base64-secret-value"
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "key")
    monkeypatch.setenv("POLYMARKET_US_SECRET", secret)
    monkeypatch.delenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", raising=False)

    with pytest.raises(SystemExit):
        config_module.BotConfig(
            api_key_id="kalshi-key",
            api_key_secret=_valid_rsa_pem(),
        )

    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "POLYMARKET_US_ELIGIBILITY_ACK_DATE" in captured.err


def test_stale_eligibility_ack_fails_preflight():
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    with pytest.raises(ValueError) as excinfo:
        require_polymarket_enablement_preflight(
            enabled=True,
            eligibility_ack_date=yesterday,
            today=date.today().isoformat(),
        )

    assert "POLYMARKET_US_ELIGIBILITY_ACK_DATE" in str(excinfo.value)


def test_same_day_eligibility_ack_passes_preflight():
    today = date.today().isoformat()

    assert (
        require_polymarket_enablement_preflight(
            enabled=True,
            eligibility_ack_date=today,
            today=today,
        )
        is None
    )
