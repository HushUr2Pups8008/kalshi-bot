from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

from observer.events import ObserverEvent, is_externally_sendable_event
from observer.formatters import format_telegram_html


@dataclass(frozen=True)
class TelegramObserverConfig:
    token: str | None
    chat_id: str | None
    enabled: bool = False

    @classmethod
    def from_env(cls) -> TelegramObserverConfig:
        token = os.getenv("KALSHI_OBSERVER_TELEGRAM_TOKEN")
        chat_id = os.getenv("KALSHI_OBSERVER_CHAT_ID")
        return cls(
            token=token,
            chat_id=chat_id,
            enabled=os.getenv("KALSHI_OBSERVER_ENABLED", "false").strip().lower() == "true",
        )


class TelegramObserverClient:
    """Small Telegram sender, inert unless explicitly enabled and configured."""

    def __init__(self, config: TelegramObserverConfig | None = None) -> None:
        self.config = config or TelegramObserverConfig.from_env()

    def can_send(self) -> bool:
        return bool(self.config.enabled and self.config.token and self.config.chat_id)

    def send_html(self, message: str) -> dict:
        if not self.can_send():
            return {"ok": False, "skipped": True, "reason": "observer_disabled_or_unconfigured"}

        assert self.config.token is not None
        assert self.config.chat_id is not None
        url = f"https://api.telegram.org/bot{self.config.token}/sendMessage"
        body = urllib.parse.urlencode(
            {
                "chat_id": self.config.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def send_event(self, event: ObserverEvent) -> dict:
        if not is_externally_sendable_event(event):
            return {"ok": False, "skipped": True, "reason": "observer_event_not_approved_for_external_send"}
        return self.send_html(format_telegram_html(event))

