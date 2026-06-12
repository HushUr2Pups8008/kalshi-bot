from __future__ import annotations


def redact_polymarket_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[REDACTED_POLYMARKET_SECRET]")
