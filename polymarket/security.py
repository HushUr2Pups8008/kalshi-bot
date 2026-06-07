from __future__ import annotations

from datetime import date


def redact_polymarket_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[REDACTED_POLYMARKET_SECRET]")


def require_polymarket_enablement_preflight(
    *,
    enabled: bool,
    eligibility_ack_date: str,
    today: str | None = None,
) -> None:
    if not enabled:
        return

    expected = today or date.today().isoformat()
    if eligibility_ack_date != expected:
        raise ValueError(
            "POLYMARKET_US_ELIGIBILITY_ACK_DATE must equal today's date after "
            "same-day state/account eligibility re-check"
        )
