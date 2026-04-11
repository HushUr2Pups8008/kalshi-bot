"""
Kalshi WebSocket client.

Maintains a persistent connection to the Kalshi WS feed, subscribes to
orderbook_delta channels for the tickers it is told to watch, and calls
registered price-update callbacks when quotes change.

Usage:
    ws = KalshiWebSocketClient()
    ws.on_price_update(my_callback)   # async def my_callback(ticker, yes_bid, yes_ask)
    await ws.watch(["KXUKR-25APR01-T0.5", ...])
    await ws.run()
"""

import asyncio
import base64
import json
import logging
import time
from typing import Callable, Awaitable, Any

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
    _WS_AVAILABLE = True
    # extra_headers was renamed to additional_headers in websockets 14.0
    _ws_ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
    _WS_HEADER_KWARG = "additional_headers" if _ws_ver >= (14, 0) else "extra_headers"
except ImportError:
    _WS_AVAILABLE = False
    _WS_HEADER_KWARG = "extra_headers"

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

from config import cfg
from utils.logger import get_logger

log = get_logger("kalshi_ws")

PriceCallback = Callable[[str, float, float], Awaitable[None]]

# Reconnect delays (seconds)
_INITIAL_RECONNECT_DELAY = 2
_MAX_RECONNECT_DELAY     = 60
_PING_INTERVAL           = 30

def _normalize_pem(raw: str | bytes) -> bytes:
    """
    Normalize a private key to a valid PEM byte string.

    Handles:
      - Literal \\n escape sequences (common in .env files)
      - Missing -----BEGIN/END RSA PRIVATE KEY----- headers
    """
    if isinstance(raw, bytes):
        raw = raw.decode()
    raw = raw.replace("\\n", "\n").strip()
    if "BEGIN" not in raw:
        raw = f"-----BEGIN RSA PRIVATE KEY-----\n{raw}\n-----END RSA PRIVATE KEY-----"
    return raw.encode()


def _build_ws_auth_headers() -> dict:
    """
    Build RSA-PSS auth headers for the WebSocket HTTP upgrade.

    Per the Kalshi docs, auth uses three headers passed during the WS handshake:
      KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE

    Message to sign: timestamp + "GET" + "/trade-api/ws/v2"
    Signing algorithm: RSA-PSS, SHA-256, salt_length=DIGEST_LENGTH
    """
    if not cfg.api_key_id or not cfg.api_key_secret or not _CRYPTO_AVAILABLE:
        return {}
    from urllib.parse import urlparse
    ws_path = cfg.ws_url.replace("wss://", "https://").replace("ws://", "http://")
    path = urlparse(ws_path).path  # e.g. /trade-api/ws/v2
    ts = str(int(time.time() * 1000))
    message = (ts + "GET" + path).encode()
    try:
        pem = _normalize_pem(cfg.api_key_secret)
        private_key = serialization.load_pem_private_key(pem, password=None)
        sig = private_key.sign(
            message,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(sig).decode()
    except Exception as exc:
        log.warning("Could not sign WS handshake: %s -- connecting unsigned", exc)
        return {}
    return {
        "KALSHI-ACCESS-KEY":       cfg.api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
    }


class KalshiWebSocketClient:
    """
    Subscribes to Kalshi orderbook updates via WebSocket.

    Exposes a simple callback interface — callers don't need to deal with
    the raw WS protocol.
    """

    def __init__(self):
        self._subscribed_tickers: set[str]       = set()
        self._price_callbacks:    list[PriceCallback] = []
        self._latest_prices:      dict[str, tuple[float, float]] = {}  # ticker → (bid, ask)
        self._running   = False
        self._ws        = None
        self._cmd_seq   = 0

    def on_price_update(self, callback: PriceCallback) -> None:
        """Register a callback: async def cb(ticker, yes_bid_cents, yes_ask_cents)."""
        self._price_callbacks.append(callback)

    def watch(self, tickers: list[str]) -> None:
        """Add tickers to the watch list (takes effect on next subscription cycle)."""
        for t in tickers:
            self._subscribed_tickers.add(t)

    def unwatch(self, tickers: list[str]) -> None:
        for t in tickers:
            self._subscribed_tickers.discard(t)

    def get_latest_price(self, ticker: str) -> tuple[float, float] | None:
        """Return (yes_bid_cents, yes_ask_cents) or None if unknown."""
        return self._latest_prices.get(ticker)

    def get_yes_price(self, ticker: str) -> float | None:
        """Return mid yes price in cents, or None."""
        prices = self._latest_prices.get(ticker)
        if prices:
            return (prices[0] + prices[1]) / 2.0
        return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _next_seq(self) -> int:
        self._cmd_seq += 1
        return self._cmd_seq

    async def _subscribe_all(self) -> None:
        if not self._ws or not self._subscribed_tickers:
            return
        tickers = list(self._subscribed_tickers)
        # Kalshi WS protocol: subscribe to orderbook_delta channel
        cmd = {
            "id":     self._next_seq(),
            "cmd":    "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": tickers,
            },
        }
        await self._ws.send(json.dumps(cmd))
        log.debug("Subscribed to %d tickers", len(tickers))

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type") or msg.get("msg", {}).get("type", "")

        # Handle both top-level and nested message formats
        if msg_type in ("orderbook_delta", "orderbook_snapshot"):
            await self._handle_orderbook(msg)
        elif msg_type == "subscribed":
            log.debug("WS subscription confirmed: %s", msg.get("msg", {}).get("market_ticker"))
        elif msg_type == "error":
            log.warning("WS error message: %s", msg)

    async def _handle_orderbook(self, msg: dict) -> None:
        """Extract best yes bid/ask from an orderbook update."""
        inner = msg.get("msg", msg)
        ticker = inner.get("market_ticker") or inner.get("ticker")
        if not ticker:
            return

        yes_bids = inner.get("yes", {})
        no_bids  = inner.get("no", {})

        # Extract best prices from the orderbook ladder
        # Kalshi OB format: {"yes": {"50": qty, "48": qty, ...}, "no": {...}}
        yes_bid = _best_bid(yes_bids)
        no_bid  = _best_bid(no_bids)

        # yes_ask ≈ 100 - best no bid
        yes_ask = (100 - no_bid) if no_bid else yes_bid

        if yes_bid is not None:
            self._latest_prices[ticker] = (yes_bid, yes_ask if yes_ask else yes_bid)
            for cb in self._price_callbacks:
                try:
                    await cb(ticker, yes_bid, yes_ask if yes_ask else yes_bid)
                except Exception as exc:
                    log.error("Price callback error: %s", exc)

    async def run(self) -> None:
        """Connect and maintain the WebSocket connection. Runs indefinitely."""
        if not _WS_AVAILABLE:
            log.error("websockets package not installed -- WebSocket feed disabled")
            return

        self._running = True
        delay = _INITIAL_RECONNECT_DELAY

        while self._running:
            try:
                url = cfg.ws_url
                log.info("Connecting to Kalshi WS: %s", url)

                auth_headers = _build_ws_auth_headers()

                async with websockets.connect(
                    url,
                    **{_WS_HEADER_KWARG: auth_headers},
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    delay    = _INITIAL_RECONNECT_DELAY  # reset on success
                    log.info("Kalshi WS connected")
                    await self._subscribe_all()

                    async for message in ws:
                        await self._handle_message(message)

            except ConnectionClosed as exc:
                log.warning("WS connection closed: %s -- reconnecting in %ds", exc, delay)
            except Exception as exc:
                log.error("WS error: %s -- reconnecting in %ds", exc, delay)
            finally:
                self._ws = None

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)

    def stop(self) -> None:
        self._running = False
        if self._ws:
            asyncio.create_task(self._ws.close())


def _best_bid(ladder: dict) -> float | None:
    """Return the highest price key from an orderbook ladder dict."""
    if not ladder:
        return None
    try:
        prices = [float(k) for k in ladder.keys() if float(k) > 0]
        return max(prices) if prices else None
    except (TypeError, ValueError):
        return None
