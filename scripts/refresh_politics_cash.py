#!/usr/bin/env python3
"""Refresh logs/state/politics_cash_cents.json from a balance GET.

Runs only when the file timestamp is older than 2 hours. A failed or
non-positive read does not overwrite the file. This script does not call
an order endpoint.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from kalshi.rest_client import KalshiRestClient
from utils.event_news_session_halt import CASH_STALE_SECONDS, refresh_stale_cash


def balance_cents(client: KalshiRestClient) -> int | None:
    """Integer cents from GET /portfolio/balance. Anything else is unusable."""
    data = client._request("GET", "/portfolio/balance")
    if not isinstance(data, dict):
        return None
    raw = data.get("balance")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        cents = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        cents = int(raw.strip())
    else:
        return None
    if cents <= 0:
        return None
    return cents


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    path = root / "logs" / "state" / "politics_cash_cents.json"
    client = KalshiRestClient()

    def read_cents() -> int | None:
        return balance_cents(client)

    status = refresh_stale_cash(
        read_cents=read_cents,
        path=path,
        now=datetime.now(timezone.utc),
        max_age_seconds=CASH_STALE_SECONDS,
    )
    print(f"cash_refresh={status}")
    return 0 if status in {"fresh", "refreshed"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"cash_refresh=not_measured error_type={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
