# WebSocket Connection Fix Notes

## Problem

On some environments (notably Windows), the WebSocket connection to Kalshi fails or
behaves unexpectedly. The most common root cause is a **version mismatch** in the
`websockets` library combined with use of the wrong keyword argument for passing
custom headers during the HTTP upgrade handshake.

---

## The `extra_headers` vs `additional_headers` issue

`websockets` renamed the header-injection parameter across versions:

| websockets version | correct kwarg |
|--------------------|---------------|
| < 10.0             | `extra_headers` |
| 10.x – 11.x        | `additional_headers` |
| 12.0 – 13.x        | `extra_headers` (renamed back) |
| 14.0+              | `additional_headers` (renamed again) |

The bot requires `websockets>=13.0`. Since either 13.x (`extra_headers`) or 14.0+
(`additional_headers`) may be installed, the code detects the version at import time
and uses the correct kwarg automatically:

```python
_ws_ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
_WS_HEADER_KWARG = "additional_headers" if _ws_ver >= (14, 0) else "extra_headers"

async with websockets.connect(
    url,
    **{_WS_HEADER_KWARG: auth_headers},
    ping_interval=30,
    ping_timeout=10,
) as ws:
```

**Always install from the requirements file:**

```bash
pip install -r requirements.txt
```

---

## Auth Header Format

The WS handshake uses RSA-PSS signing (not PKCS1v15). Three custom headers are
injected during the HTTP upgrade:

| Header | Value |
|--------|-------|
| `KALSHI-ACCESS-KEY` | Your API key ID (UUID) |
| `KALSHI-ACCESS-TIMESTAMP` | Unix timestamp in **milliseconds** (string) |
| `KALSHI-ACCESS-SIGNATURE` | Base64-encoded RSA-PSS SHA-256 signature |

The message that gets signed is:

```
<timestamp_ms_string> + "GET" + "/trade-api/ws/v2"
```

Signing parameters: RSA-PSS, SHA-256, `salt_length=DIGEST_LENGTH`.

---

## PEM Key in `.env` (Windows gotcha)

Windows `.env` files often cannot store multi-line values. The bot handles this by
accepting a single-line PEM with literal `\n` escape sequences:

```
KALSHI_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----
```

The `_normalize_pem()` function in `kalshi/websocket_client.py` replaces `\n` with
real newlines before loading the key.  Make sure your `.env` value uses `\n` (two
characters: backslash + n), **not** actual newlines or Windows line endings (CRLF).

---

## Checklist for Windows Setup

1. **Python 3.11+** installed and on PATH.
2. Virtual environment created and activated:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Dependencies installed from the pinned file:
   ```
   pip install -r requirements.txt
   ```
4. Verify `websockets` is 12.0 or newer:
   ```
   pip show websockets
   ```
5. `.env` file has `KALSHI_PRIVATE_KEY` as a single line with literal `\n` separators.
6. Run the bot:
   ```
   python main.py
   ```

If the WebSocket still fails, check the logs for:
- `"Could not sign WS handshake"` → PEM key parse error, check `.env` formatting.
- `"401"` or `"403"` in the connection error → auth headers not sent; verify `websockets>=12.0`.
- `"ConnectionClosed"` immediately after connect → usually a key or header issue.
