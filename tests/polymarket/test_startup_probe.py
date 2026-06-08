from __future__ import annotations

import logging

import config as config_module
from polymarket.models import PolymarketMarket
from polymarket.startup_probe import log_polymarket_startup_probe
from trading.venue import Venue


class FakeClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result if result is not None else ([], None)
        self.error = error
        self.calls = []

    def get_markets(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def _market() -> PolymarketMarket:
    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="will-example-happen-2026",
        title="Will example happen in 2026?",
        status="open",
        yes_ask_cents=42,
        no_ask_cents=59,
        volume_dollars=1000.0,
        open_interest_dollars=100.0,
        close_time="2026-12-31T23:59:59Z",
    )


def test_startup_probe_skips_when_polymarket_disabled(monkeypatch, caplog):
    monkeypatch.setattr(config_module.cfg, "polymarket_us_enabled", False)
    client = FakeClient()

    with caplog.at_level(logging.INFO, logger="polymarket.startup_probe"):
        result = log_polymarket_startup_probe(client=client)

    assert result.status == "disabled"
    assert client.calls == []
    assert "[POLYMARKET] enabled=false startup_probe=skipped" in caplog.text


def test_startup_probe_logs_sample_market_when_enabled(monkeypatch, caplog):
    monkeypatch.setattr(config_module.cfg, "polymarket_us_enabled", True)
    monkeypatch.setattr(config_module.cfg, "polymarket_us_live_trading_enabled", False)
    client = FakeClient(result=([_market()], "next-cursor"))

    with caplog.at_level(logging.INFO, logger="polymarket.startup_probe"):
        result = log_polymarket_startup_probe(client=client)

    assert result.status == "ok"
    assert client.calls == [{"limit": 1}]
    assert "startup_probe=ok" in caplog.text
    assert "sample_market=will-example-happen-2026" in caplog.text
    assert "paper_execution=blend" in caplog.text
    assert "live_trading=false" in caplog.text


def test_startup_probe_redacts_secret_on_failure(monkeypatch, caplog):
    secret = "secret-material"
    monkeypatch.setattr(config_module.cfg, "polymarket_us_enabled", True)
    monkeypatch.setattr(config_module.cfg, "polymarket_us_secret", secret)
    client = FakeClient(error=RuntimeError(f"bad signature {secret}"))

    with caplog.at_level(logging.WARNING, logger="polymarket.startup_probe"):
        result = log_polymarket_startup_probe(client=client)

    assert result.status == "error"
    assert secret not in caplog.text
    assert "[REDACTED_POLYMARKET_SECRET]" in caplog.text
