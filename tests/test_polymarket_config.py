from datetime import date

import pytest

import config as config_module


def _valid_rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _clear_polymarket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "POLYMARKET_US_ENABLED",
        "POLYMARKET_US_KEY_ID",
        "POLYMARKET_US_SECRET",
        "POLYMARKET_US_ELIGIBILITY_ACK_DATE",
        "POLYMARKET_US_PUBLIC_BASE_URL",
        "POLYMARKET_US_API_BASE_URL",
        "POLYMARKET_US_LIVE_TRADING_ENABLED",
        "POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND",
        "POLYMARKET_US_ORDER_REQUESTS_PER_SECOND",
        "POLYMARKET_US_BBO_REQUESTS_PER_MINUTE",
        "POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE",
    ):
        monkeypatch.delenv(key, raising=False)


def _bot_config() -> config_module.BotConfig:
    return config_module.BotConfig(
        api_key_id="kalshi-key",
        api_key_secret=_valid_rsa_pem(),
    )


def test_polymarket_config_defaults_disabled(monkeypatch):
    _clear_polymarket_env(monkeypatch)

    cfg = _bot_config()

    assert cfg.polymarket_us_enabled is False
    assert cfg.polymarket_us_live_trading_enabled is False
    assert cfg.polymarket_us_key_id == ""
    assert cfg.polymarket_us_secret == ""
    assert cfg.polymarket_us_eligibility_ack_date == ""
    assert cfg.polymarket_us_public_base_url == "https://gateway.polymarket.us"
    assert cfg.polymarket_us_api_base_url == "https://api.polymarket.us"
    assert cfg.polymarket_us_public_requests_per_second == 20
    assert cfg.polymarket_us_order_requests_per_second == 100
    assert cfg.polymarket_us_bbo_requests_per_minute == 12
    assert cfg.polymarket_us_position_valuation_requests_per_minute == 0.5


def test_polymarket_enabled_requires_credentials(monkeypatch):
    _clear_polymarket_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")

    with pytest.raises(SystemExit):
        _bot_config()


def test_polymarket_enabled_accepts_credentials_but_live_trading_stays_separate(
    monkeypatch,
):
    _clear_polymarket_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "pm-key-id")
    monkeypatch.setenv("POLYMARKET_US_SECRET", "pm-secret")
    monkeypatch.setenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", date.today().isoformat())

    cfg = _bot_config()

    assert cfg.polymarket_us_enabled is True
    assert cfg.polymarket_us_live_trading_enabled is False
    assert cfg.polymarket_us_key_id == "pm-key-id"
    assert cfg.polymarket_us_secret == "pm-secret"


def test_polymarket_startup_status_reports_paper_execution_mode(
    monkeypatch,
):
    _clear_polymarket_env(monkeypatch)
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "pm-key-id")
    monkeypatch.setenv("POLYMARKET_US_SECRET", "pm-secret")
    monkeypatch.setenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", date.today().isoformat())

    cfg = _bot_config()

    status = cfg.polymarket_us_startup_status()

    assert "enabled=true" in status
    assert "paper_execution=blend" in status
    assert "live_trading=false" in status
    assert "pm-key-id" not in status
    assert "pm-secret" not in status


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND", "0"),
        ("POLYMARKET_US_ORDER_REQUESTS_PER_SECOND", "-1"),
        ("POLYMARKET_US_BBO_REQUESTS_PER_MINUTE", "0"),
        ("POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE", "0"),
    ],
)
def test_polymarket_rate_limits_must_be_positive(monkeypatch, env_name, env_value):
    _clear_polymarket_env(monkeypatch)
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(SystemExit):
        _bot_config()
