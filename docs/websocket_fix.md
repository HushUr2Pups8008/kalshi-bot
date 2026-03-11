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
| 12.0+              | `extra_headers` (renamed back) |

The bot **requires `websockets==12.0`** (pinned in `requirements.txt`).
On that version the correct call is:

```python
async with websockets.connect(
    url,
    extra_headers=auth_headers,   # ← correct for websockets 12.0
    ping_interval=30,
    ping_timeout=10,
) as ws:
```

If you accidentally install a different version (e.g. the latest 13.x or an older
11.x), the kwarg name may not match and the auth headers will be silently dropped,
causing a 401 or connection reset.

**Always install the pinned version:**

```bash
pip install -r requirements.txt
```

or explicitly:

```bash
pip install "websockets==12.0"
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
4. Verify `websockets` is exactly 12.0:
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
- `"401"` or `"403"` in the connection error → auth headers not sent; verify `websockets==12.0`.
- `"ConnectionClosed"` immediately after connect → usually a key or header issue.
