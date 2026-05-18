from __future__ import annotations

from observer.events import ObserverEvent, is_externally_sendable_event
from observer.formatters import format_telegram_html
from observer.readers import events_from_app_log, events_from_trade_log
from observer.sanitizer import sanitize_event
from observer.telegram import TelegramObserverConfig, TelegramObserverClient
from tests._helpers import cleanup_tmp_dir, make_tmp_dir, write_jsonl


def test_sanitizer_removes_secret_and_account_sensitive_details() -> None:
    event = ObserverEvent(
        source="unit",
        event_type="diagnostic",
        severity="warning",
        title="Sensitive payload",
        details={
            "token": "kalshi-token-123",
            "private_key": "-----BEGIN PRIVATE KEY-----abc",
            "api_secret": "secret-value",
            "balance": 12345,
            "positions": [{"ticker": "KXTEST", "qty": 2}],
            "fills": [{"order_id": "fill-1"}],
            "order_history": [{"order_id": "order-1"}],
            "market_status": "open",
            "signal": "YES",
        },
    )

    sanitized = sanitize_event(event)

    assert sanitized is not event
    assert sanitized.details == {
        "market_status": "open",
        "signal": "YES",
    }


def test_formatter_brands_observer_without_impersonating_kalshi() -> None:
    event = ObserverEvent(
        source="trade_log",
        event_type="signal",
        severity="info",
        title="Signal observed",
        market_ticker="KXTEST-26MAY16",
        environment="paper",
        action="watch",
        details={"status": "open", "confidence": "0.62"},
    )

    rendered = format_telegram_html(event)
    lowered = rendered.lower()

    assert "Kalshi-bot Observer" in rendered
    assert "KXTEST-26MAY16" in rendered
    assert "open" in lowered
    assert "paper" in lowered
    assert "watch" in lowered
    assert "official kalshi" not in lowered
    assert "kalshi alert" not in lowered


def test_app_log_reader_converts_error_line_to_observer_event() -> None:
    tmp = make_tmp_dir("observer")
    try:
        log_path = tmp / "bot.log"
        log_path.write_text(
            "2026-05-16T12:34:56Z ERROR order_router failed risk check\n",
            encoding="utf-8",
        )

        events = list(events_from_app_log(log_path))

        assert len(events) == 1
        assert events[0].source == "app_log"
        assert events[0].severity == "error"
        assert events[0].event_type == "error"
        assert "risk check" in events[0].title or "risk check" in str(events[0].details)
    finally:
        cleanup_tmp_dir(tmp)


def test_trade_log_reader_converts_signal_record_to_observer_event() -> None:
    tmp = make_tmp_dir("observer")
    try:
        log_path = tmp / "trades.jsonl"
        write_jsonl(
            log_path,
            [
                {
                    "type": "SIGNAL",
                    "timestamp": "2026-05-16T12:35:00Z",
                    "market_ticker": "KXTEST-26MAY16",
                    "action": "watch",
                    "confidence": 0.62,
                }
            ],
        )

        events = list(events_from_trade_log(log_path))

        assert len(events) == 1
        assert events[0].source == "trade_log"
        assert events[0].event_type == "signal"
        assert events[0].market_ticker == "KXTEST-26MAY16"
    finally:
        cleanup_tmp_dir(tmp)


def test_trade_log_reader_parses_paper_trade_ts_for_formatting() -> None:
    tmp = make_tmp_dir("observer")
    try:
        log_path = tmp / "trades.jsonl"
        write_jsonl(
            log_path,
            [
                {
                    "type": "PAPER_TRADE",
                    "ts": "2026-05-16T12:35:00Z",
                    "market_ticker": "KXTEST-26MAY16",
                    "action": "buy_yes",
                    "title": "Paper trade placed",
                    "quantity": 1,
                }
            ],
        )

        events = list(events_from_trade_log(log_path))
        rendered = format_telegram_html(events[0])

        assert len(events) == 1
        assert events[0].event_type == "paper_trade"
        assert "ts=2026-05-16T12:35:00+00:00" in rendered
        assert "KXTEST-26MAY16" in rendered
    finally:
        cleanup_tmp_dir(tmp)


def test_paper_trade_record_has_observer_safe_lifecycle_event() -> None:
    tmp = make_tmp_dir("observer")
    try:
        log_path = tmp / "trades.jsonl"
        write_jsonl(
            log_path,
            [
                {
                    "type": "PAPER_TRADE",
                    "ts": "2026-05-16T12:35:00Z",
                    "trade_id": "trade-1",
                    "ticker": "KXTEST-26MAY16",
                    "market_title": "Test market",
                    "side": "yes",
                    "contracts": 5,
                    "price_cents": 42,
                    "cost_dollars": 2.10,
                    "estimated_probability": 0.62,
                    "entry_price_cents": 42.0,
                    "edge": 0.20,
                    "kelly_dollars": 8.50,
                    "reasoning": "Long rationale should stay out of observer details",
                    "signal_headline": "Test headline",
                    "signal_source": "Reuters",
                    "signal_meta": {"positions": [{"ticker": "KXTEST"}], "token": "secret"},
                    "bankroll_delta_dollars": -2.10,
                }
            ],
        )

        events = list(events_from_trade_log(log_path))
        rendered = format_telegram_html(events[0])

        assert len(events) == 1
        assert events[0].event_type == "paper_trade"
        assert events[0].environment == "paper"
        assert events[0].action == "paper_trade_opened"
        assert events[0].title == "Paper trade opened: Test headline"
        assert events[0].details == {
            "trade_id": "trade-1",
            "side": "yes",
            "contracts": 5,
            "price_cents": 42,
            "estimated_probability": 0.62,
            "edge": 0.2,
            "signal_source": "Reuters",
            "signal_headline": "Test headline",
            "simulated_notional_delta_dollars": -2.1,
            "status": "opened",
            "trade_id_description": "paper source event identity only; not a live order or fill id",
        }
        assert is_externally_sendable_event(events[0]) is True
        assert rendered.splitlines()[0].startswith("<b>PAPER / SIMULATED lifecycle update")
        assert "not a live Kalshi order" in rendered
        assert "not live p&amp;l" in rendered.lower()
        assert "cost_dollars" not in rendered
        assert "kelly_dollars" not in rendered
        assert "positions" not in rendered
        assert "Long rationale" not in rendered
    finally:
        cleanup_tmp_dir(tmp)


def test_readers_apply_limit_in_source_order() -> None:
    tmp = make_tmp_dir("observer")
    try:
        app_log_path = tmp / "bot.log"
        app_log_path.write_text(
            "\n".join(
                [
                    "2026-05-16T12:00:00Z ERROR first app error",
                    "2026-05-16T12:01:00Z ERROR second app error",
                    "2026-05-16T12:02:00Z ERROR third app error",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        trade_log_path = tmp / "trades.jsonl"
        write_jsonl(
            trade_log_path,
            [
                {
                    "type": "SIGNAL",
                    "timestamp": "2026-05-16T12:00:00Z",
                    "market_ticker": "KXFIRST",
                },
                {
                    "type": "SIGNAL",
                    "timestamp": "2026-05-16T12:01:00Z",
                    "market_ticker": "KXSECOND",
                },
                {
                    "type": "SIGNAL",
                    "timestamp": "2026-05-16T12:02:00Z",
                    "market_ticker": "KXTHIRD",
                },
            ],
        )

        app_events = list(events_from_app_log(app_log_path, limit=2))
        trade_events = list(events_from_trade_log(trade_log_path, limit=2))

        assert len(app_events) == 2
        assert "first app error" in app_events[0].title or "first app error" in str(app_events[0].details)
        assert "second app error" in app_events[1].title or "second app error" in str(app_events[1].details)
        assert [event.market_ticker for event in trade_events] == ["KXFIRST", "KXSECOND"]
    finally:
        cleanup_tmp_dir(tmp)


def test_telegram_config_prefers_observer_specific_env(monkeypatch) -> None:
    monkeypatch.setenv("KALSHI_OBSERVER_TELEGRAM_TOKEN", "observer-token")
    monkeypatch.setenv("KALSHI_OBSERVER_CHAT_ID", "observer-chat")
    monkeypatch.setenv("KALSHI_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "default-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111,222")

    config = TelegramObserverConfig.from_env()

    assert config.token == "observer-token"
    assert config.chat_id == "observer-chat"
    assert config.enabled is True


def test_telegram_config_requires_observer_specific_destination(monkeypatch) -> None:
    monkeypatch.delenv("KALSHI_OBSERVER_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("KALSHI_OBSERVER_CHAT_ID", raising=False)
    monkeypatch.setenv("KALSHI_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "default-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111, 222")

    config = TelegramObserverConfig.from_env()

    assert config.token is None
    assert config.chat_id is None
    assert config.enabled is True


def test_telegram_client_stays_inert_when_disabled_even_with_default_env(monkeypatch) -> None:
    monkeypatch.delenv("KALSHI_OBSERVER_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("KALSHI_OBSERVER_CHAT_ID", raising=False)
    monkeypatch.setenv("KALSHI_OBSERVER_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "default-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "111")

    client = TelegramObserverClient()

    assert client.can_send() is False
    assert client.send_html("preview") == {
        "ok": False,
        "skipped": True,
        "reason": "observer_disabled_or_unconfigured",
    }


def test_telegram_client_blocks_non_paper_event_before_network(monkeypatch) -> None:
    def fail_urlopen(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("network should not be used for non-paper observer events")

    monkeypatch.setattr("observer.telegram.urllib.request.urlopen", fail_urlopen)
    client = TelegramObserverClient(
        TelegramObserverConfig(token="observer-token", chat_id="observer-chat", enabled=True)
    )
    event = ObserverEvent(
        source="trade_log",
        event_type="signal",
        severity="info",
        title="Generic signal",
        environment=None,
        action="watch",
    )

    assert is_externally_sendable_event(event) is False
    assert client.send_event(event) == {
        "ok": False,
        "skipped": True,
        "reason": "observer_event_not_approved_for_external_send",
    }
