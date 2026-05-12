"""Kalshi REST payload normalizer (P-2 implementation).

Converts raw `GET /markets` list entries, single-market detail responses,
and exchange-status payloads into the post-P0 ``KalshiMarket`` /
``ExchangeState`` shapes. Implements operator-locked design decisions:

* LD-3: Decimal arithmetic, ROUND_HALF_EVEN dollars→cents quantization.
* LD-4: spread / complement invariants enforced at the parse boundary.
* LD-16: ``price_method`` provenance + ``raw_payload_hash`` provenance.

DriftCounter / halt-sentinel writer + exchange-open gate land in P-3 /
P-7 respectively; not implemented here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Optional

from . import ExchangeState, KalshiMarket


logger = logging.getLogger(__name__)


_CENTS_QUANTUM = Decimal("1")
_HUNDRED = Decimal("100")

_DOLLAR_PRICE_FIELDS = (
    "yes_bid_dollars",
    "yes_ask_dollars",
    "no_bid_dollars",
    "no_ask_dollars",
)

_LEGACY_CENTS_FIELDS = (
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
)

# Side/price keywords used to detect unrecognized "drift" price contracts.
_SIDE_TOKENS = ("yes", "no")
_PRICE_TOKENS = ("bid", "ask", "price")


class UnsupportedPayloadContractError(ValueError):
    """Raised at the parse boundary when a Kalshi payload carries an
    unrecognized or invalid price contract (LD-4, LD-16).

    Inherits from ``ValueError`` for ergonomic ``pytest.raises`` interop.
    """

    def __init__(
        self,
        message: str,
        *,
        ticker: str = "",
        payload_hash: str = "",
    ) -> None:
        super().__init__(message)
        self.ticker = ticker
        self.payload_hash = payload_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_payload(payload: dict) -> str:
    """SHA-256 of a deterministic JSON serialization (LD-16)."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _to_cents(dollar_value: Any) -> int:
    """Quantize dollars (str/Decimal/number) to integer cents via
    ROUND_HALF_EVEN (LD-3, LD-16). Raises on malformed input."""
    if dollar_value is None or dollar_value == "":
        raise InvalidOperation("empty dollars value")
    if isinstance(dollar_value, Decimal):
        d = dollar_value
    elif isinstance(dollar_value, (int, float)):
        # Route via str() to avoid binary-float artifacts.
        d = Decimal(str(dollar_value))
    elif isinstance(dollar_value, str):
        d = Decimal(dollar_value)
    else:
        raise InvalidOperation(f"unsupported dollars type: {type(dollar_value)!r}")
    quantized = (d * _HUNDRED).quantize(_CENTS_QUANTUM, rounding=ROUND_HALF_EVEN)
    return int(quantized)


def _optional_decimal(payload: dict, key: str) -> Optional[Decimal]:
    raw = payload.get(key)
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _optional_cents(payload: dict, dollar_key: str) -> Optional[int]:
    raw = payload.get(dollar_key)
    if raw is None or raw == "":
        return None
    try:
        return _to_cents(raw)
    except InvalidOperation:
        return None


def _optional_datetime(payload: dict, key: str) -> Optional[datetime]:
    raw = payload.get(key)
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _has_unrecognized_price_drift(payload: dict) -> bool:
    """Return True if the payload carries keys that LOOK like price
    contracts (contain side+price tokens) but none of the recognized
    contracts (LD-4). Used to distinguish "absent" from "drift"."""
    recognized = set(_DOLLAR_PRICE_FIELDS) | set(_LEGACY_CENTS_FIELDS) | {
        "last_price_dollars",
        "previous_price_dollars",
        "previous_yes_bid_dollars",
        "previous_yes_ask_dollars",
    }
    for key in payload.keys():
        if key in recognized:
            continue
        lk = key.lower()
        has_side = any(t in lk for t in _SIDE_TOKENS)
        has_price = any(t in lk for t in _PRICE_TOKENS)
        if has_side and has_price:
            return True
    return False


def _detect_contract(payload: dict) -> str:
    """Detect payload price contract. Returns one of:
    ``"dollars_fixed_point"``, ``"legacy_cents"``, ``"none"``.

    Raises ``UnsupportedPayloadContractError`` when the payload carries a
    drift contract (e.g. ``yes_price_basis_points``).
    """
    has_dollars = any(k in payload for k in _DOLLAR_PRICE_FIELDS)
    has_legacy = any(k in payload for k in _LEGACY_CENTS_FIELDS)
    if has_dollars:
        return "dollars_fixed_point"
    if has_legacy:
        return "legacy_cents"
    if _has_unrecognized_price_drift(payload):
        raise UnsupportedPayloadContractError(
            f"unrecognized price contract for {payload.get('ticker', '')!r}: "
            f"no recognized bid/ask fields, drift keys present",
            ticker=str(payload.get("ticker", "")),
            payload_hash=_hash_payload(payload),
        )
    return "none"


def _parse_dollars_quad(payload: dict) -> tuple[Optional[int], Optional[int],
                                                 Optional[int], Optional[int]]:
    """Parse the four ``*_dollars`` price fields to cents. Returns
    ``(yes_bid, yes_ask, no_bid, no_ask)`` cents. Any malformed field
    short-circuits to all-None so the caller can mark price_available=False
    (per P0-REST-004 fail-closed contract).
    """
    try:
        return (
            _to_cents(payload["yes_bid_dollars"]),
            _to_cents(payload["yes_ask_dollars"]),
            _to_cents(payload["no_bid_dollars"]),
            _to_cents(payload["no_ask_dollars"]),
        )
    except (KeyError, InvalidOperation, TypeError):
        return (None, None, None, None)


def _parse_legacy_quad(payload: dict) -> tuple[Optional[int], Optional[int],
                                                Optional[int], Optional[int]]:
    """Parse the four legacy integer-cent price fields."""
    try:
        return (
            int(payload["yes_bid"]),
            int(payload["yes_ask"]),
            int(payload["no_bid"]),
            int(payload["no_ask"]),
        )
    except (KeyError, ValueError, TypeError):
        return (None, None, None, None)


def _invariants_hold(
    yes_bid: int, yes_ask: int, no_bid: int, no_ask: int,
) -> bool:
    """Spread + complement invariants (LD-4 / D3)."""
    for v in (yes_bid, yes_ask, no_bid, no_ask):
        if v < 0 or v > 100:
            return False
    if yes_bid > yes_ask:
        return False
    if no_bid > no_ask:
        return False
    if yes_bid + no_ask > 100:
        return False
    return True


def _build_market(
    payload: dict,
    *,
    price_source: str,
) -> KalshiMarket:
    """Shared core for list-entry and detail normalization."""
    payload_hash = _hash_payload(payload)
    contract = _detect_contract(payload)

    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    no_bid_cents: Optional[int]
    no_ask_cents: Optional[int]
    price_available = False
    price_method = "none"

    if contract == "dollars_fixed_point":
        yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents = (
            _parse_dollars_quad(payload)
        )
        if (
            yes_bid_cents is not None
            and yes_ask_cents is not None
            and no_bid_cents is not None
            and no_ask_cents is not None
        ):
            if not _invariants_hold(
                yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents
            ):
                # Fail-closed rather than raising — matches P0-REST-006
                # acceptable-outcomes contract.
                yes_bid_cents = yes_ask_cents = None
                no_bid_cents = no_ask_cents = None
                price_available = False
                price_method = "none"
            else:
                price_available = True
                price_method = "dollars_fixed_point"
    elif contract == "legacy_cents":
        yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents = (
            _parse_legacy_quad(payload)
        )
        if (
            yes_bid_cents is not None
            and yes_ask_cents is not None
            and no_bid_cents is not None
            and no_ask_cents is not None
        ):
            if not _invariants_hold(
                yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents
            ):
                yes_bid_cents = yes_ask_cents = None
                no_bid_cents = no_ask_cents = None
                price_available = False
                price_method = "none"
            else:
                price_available = True
                price_method = "legacy_cents"
    else:
        yes_bid_cents = yes_ask_cents = no_bid_cents = no_ask_cents = None

    last_price_cents = _optional_cents(payload, "last_price_dollars")
    previous_price_cents = _optional_cents(payload, "previous_price_dollars")

    if price_available:
        # Legacy mirror fields stay in float-cent units so existing readers
        # see unchanged shape.
        legacy_yes_bid = float(yes_bid_cents)  # type: ignore[arg-type]
        legacy_yes_ask = float(yes_ask_cents)  # type: ignore[arg-type]
        legacy_yes_price = float(
            round((yes_bid_cents + yes_ask_cents) / 2)  # type: ignore[operator]
        )
    else:
        legacy_yes_bid = 0.0
        legacy_yes_ask = 0.0
        legacy_yes_price = 0.0

    volume_fp = _optional_decimal(payload, "volume_fp")
    volume_24h_fp = _optional_decimal(payload, "volume_24h_fp")
    open_interest_fp = _optional_decimal(payload, "open_interest_fp")

    if volume_fp is not None:
        try:
            legacy_volume = int(volume_fp)
        except (InvalidOperation, ValueError):
            legacy_volume = 0
    else:
        legacy_volume = 0
    if open_interest_fp is not None:
        try:
            legacy_open_interest = int(open_interest_fp)
        except (InvalidOperation, ValueError):
            legacy_open_interest = 0
    else:
        legacy_open_interest = 0

    market = KalshiMarket(
        ticker=str(payload.get("ticker", "")),
        title=str(payload.get("title", "")),
        yes_bid=legacy_yes_bid,
        yes_ask=legacy_yes_ask,
        yes_price=legacy_yes_price,
        volume=legacy_volume,
        open_interest=legacy_open_interest,
        close_time=str(payload.get("close_time", "")),
        status=str(payload.get("status", "")),
        series_ticker=str(payload.get("series_ticker", "")),
        subtitle=str(payload.get("subtitle", "")),
        result=str(payload.get("result", "")),
        yes_bid_cents=yes_bid_cents,
        yes_ask_cents=yes_ask_cents,
        no_bid_cents=no_bid_cents,
        no_ask_cents=no_ask_cents,
        yes_bid_size=_optional_decimal(payload, "yes_bid_size_fp"),
        yes_ask_size=_optional_decimal(payload, "yes_ask_size_fp"),
        no_bid_size=_optional_decimal(payload, "no_bid_size_fp"),
        no_ask_size=_optional_decimal(payload, "no_ask_size_fp"),
        last_price_cents=last_price_cents,
        previous_price_cents=previous_price_cents,
        volume_fp=volume_fp,
        volume_24h_fp=volume_24h_fp,
        open_interest_fp=open_interest_fp,
        liquidity_dollars=_optional_decimal(payload, "liquidity_dollars"),
        notional_value_dollars=_optional_decimal(payload, "notional_value_dollars"),
        price_available=price_available,
        price_source=price_source if price_available else "unavailable",
        price_method=price_method,
        price_retrieved_at=datetime.now(timezone.utc),
        raw_payload_hash=payload_hash,
        market_type=str(payload.get("market_type", "")),
        price_level_structure=str(payload.get("price_level_structure", "")),
        fractional_trading_enabled=bool(payload.get("fractional_trading_enabled", False)),
        created_time=_optional_datetime(payload, "created_time"),
        updated_time=_optional_datetime(payload, "updated_time"),
    )
    return market


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def normalize_market_list_entry(payload: dict) -> KalshiMarket:
    """Normalize a single market entry from the ``GET /markets`` list
    response into a post-P0 ``KalshiMarket`` (LD-3, LD-4, LD-16).

    Raises ``UnsupportedPayloadContractError`` only when the payload
    carries an unrecognized price contract (e.g.
    ``yes_price_basis_points``). Malformed values within a recognized
    contract fail-closed to ``price_available=False`` rather than raising,
    per P0-REST-004.
    """
    return _build_market(payload, price_source="rest_list")


def normalize_market_detail(payload: dict) -> KalshiMarket:
    """Normalize the ``GET /markets/{ticker}`` response (which is
    wrapped in ``{"market": {...}}``) into a post-P0 ``KalshiMarket``
    (LD-3, LD-4, LD-16). A bare market dict (no envelope) is also
    accepted for symmetry with the list-entry call.
    """
    inner = payload.get("market", payload) if isinstance(payload, dict) else payload
    if not isinstance(inner, dict):
        raise UnsupportedPayloadContractError(
            "market detail payload missing 'market' object",
            ticker="",
            payload_hash="",
        )
    return _build_market(inner, price_source="rest_detail")


def normalize_exchange_status(payload: Any) -> ExchangeState:
    """Normalize ``GET /exchange/status`` payload into an
    ``ExchangeState`` (LD-4). Raises ``UnsupportedPayloadContractError``
    when required keys are missing or non-bool.
    """
    if not isinstance(payload, dict):
        raise UnsupportedPayloadContractError(
            f"exchange_status payload must be dict, got {type(payload).__name__}",
        )
    if "exchange_active" not in payload or "trading_active" not in payload:
        raise UnsupportedPayloadContractError(
            "exchange_status payload missing required keys",
        )
    exchange_active = payload["exchange_active"]
    trading_active = payload["trading_active"]
    if not isinstance(exchange_active, bool) or not isinstance(trading_active, bool):
        raise UnsupportedPayloadContractError(
            "exchange_status flags must be booleans",
        )
    return ExchangeState(
        exchange_active=exchange_active,
        trading_active=trading_active,
    )


# ---------------------------------------------------------------------------
# P-3 surfaces — DriftCounter (halt-sentinel plumbing) + exchange-open gate.
# ---------------------------------------------------------------------------


_DRIFT_SENTINEL_RELPATH = ("data", "runtime", "kalshi_drift_halt.json")


class DriftCounter:
    """In-process drift accounting + halt-sentinel persistence (LD-5/LD-6/LD-6b).

    Halt trips on the FIRST drift event (strict ``>= 1`` threshold).
    Sentinel persists across counter instances; manual clearance =
    deleting ``data/runtime/kalshi_drift_halt.json``.
    """

    def __init__(self, runtime_dir: Path) -> None:
        self._runtime_dir = Path(runtime_dir)
        self._sentinel_path = self._runtime_dir.joinpath(*_DRIFT_SENTINEL_RELPATH)
        self._count = 0
        self._halt = False
        if self._sentinel_path.is_file():
            try:
                with self._sentinel_path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                seeded = data.get("cycle_drift_count", 0)
                self._count = int(seeded) if isinstance(seeded, int) else 0
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                # Sentinel exists but is unreadable; still treat as halted
                # so we fail closed (LD-6b). Surface corruption to operators
                # via warning log (observability write path; do not swallow).
                logger.warning(
                    "drift sentinel unreadable at %s: %s -- treating as halted (fail-closed)",
                    self._sentinel_path,
                    exc,
                )
                self._count = 0
            self._halt = True

    @property
    def count(self) -> int:
        return self._count

    def halt_tripped(self) -> bool:
        return self._halt

    def record_drift(self, *, ticker: str, payload_hash: str) -> None:
        self._count += 1
        first_event = not self._halt
        if first_event:
            self._halt = True
            self._sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "halt_ts": datetime.now(timezone.utc).isoformat(),
                "trigger_ticker": ticker,
                "trigger_payload_hash": payload_hash,
                "cycle_drift_count": self._count,
            }
            # Atomic write: tmp + os.replace so a mid-write crash cannot
            # leave a partially-written sentinel (cross-platform atomic).
            tmp_path = self._sentinel_path.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, self._sentinel_path)
        logger.info(
            "drift: reason=unsupported_payload_contract "
            "payload_hash=%s ticker=%s count=%d",
            payload_hash,
            ticker,
            self._count,
            extra={
                "reason": "unsupported_payload_contract",
                "payload_hash": payload_hash,
                "ticker": ticker,
            },
        )


def check_exchange_open(state: ExchangeState) -> bool:
    """Orchestrator-level fail-closed gate (LD-4, P-7 surface). Returns
    True only when both exchange and trading flags are active. Wrapped
    here (rather than inlined) so the orchestrator imports a single
    canonical gate from ``kalshi.normalizer``."""
    return bool(state.is_open)
