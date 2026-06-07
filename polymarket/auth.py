from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class PolymarketAuth:
    def __init__(self, *, key_id: str, secret_b64: str):
        self._key_id = key_id.strip()
        if not self._key_id:
            raise ValueError("Polymarket US key_id is required")

        try:
            raw = base64.b64decode(secret_b64, validate=True)[:32]
            self._private_key = Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise ValueError("invalid Polymarket US Ed25519 secret") from exc

    def headers(self, method: str, path: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
        signature = self._private_key.sign(message)
        signature_b64 = base64.b64encode(signature).decode("ascii")

        return {
            "X-PM-Access-Key": self._key_id,
            "X-PM-Timestamp": timestamp,
            "X-PM-Signature": signature_b64,
            "Content-Type": "application/json",
        }
