from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observer.events import ObserverEvent
from observer.formatters import format_telegram_html
from observer.readers import events_from_app_log, events_from_trade_log
from observer.telegram import TelegramObserverClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview Kalshi-bot Observer Telegram messages.")
    parser.add_argument("--app-log", type=Path, help="Path to a bot.log file or app log directory.")
    parser.add_argument("--trade-log", type=Path, help="Path to a trades.jsonl file or trade log root.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum events to render per source.")
    parser.add_argument("--sample", action="store_true", help="Render a built-in non-live sample observer event.")
    parser.add_argument("--send-telegram", action="store_true", help="Send rendered messages through the observer Telegram client.")
    return parser.parse_args()


def _sample_event() -> ObserverEvent:
    return ObserverEvent(
        source="sample",
        event_type="signal",
        severity="info",
        title="Signal observed",
        market_ticker="KXTEST-26MAY16",
        environment="paper",
        action="watch",
        details={
            "status": "open",
            "yes_price": "42 cents",
            "no_price": "59 cents",
            "confidence": "0.62",
            "decision": "observe_only",
        },
    )


def main() -> int:
    args = _parse_args()
    events: list[ObserverEvent] = []

    if args.sample:
        events.append(_sample_event())
    if args.app_log:
        events.extend(events_from_app_log(args.app_log, limit=args.limit))
    if args.trade_log:
        events.extend(events_from_trade_log(args.trade_log, limit=args.limit))
    rendered = [format_telegram_html(event) for event in events]

    if args.send_telegram:
        client = TelegramObserverClient()
        for event in events:
            result = client.send_event(event)
            if not result.get("ok"):
                reason = result.get("reason") or result.get("description") or "telegram_send_failed"
                print(f"Telegram send skipped/failed: {reason}")
                return 1
        print(f"Telegram sent: {len(rendered)} message(s)")
        return 0

    for index, message in enumerate(rendered, start=1):
        if index > 1:
            print()
            print("---")
        print(message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
