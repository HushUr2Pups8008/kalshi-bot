# Polymarket US Retail API Contract Snapshot

Reviewed: 2026-06-07

Purpose: pin the current official Polymarket US Retail API assumptions before runtime integration code is written. Update this file and `tests/fixtures/polymarket_us/contract_snapshot.json` in the same commit whenever official docs, account behavior, or endpoint semantics drift.

Sources:

- https://docs.polymarket.us/api-reference/introduction
- https://docs.polymarket.us/api-reference/authentication
- https://docs.polymarket.us/api-reference/markets/get-markets
- https://docs.polymarket.us/api-reference/account/get-account-balances
- https://docs.polymarket.us/api-reference/orders/overview
- https://docs.polymarket.us/trader-guide/rate-limits

Implementation rules:

- Do not use Global CLOB (`clob.polymarket.com`) for this operator.
- Use `https://gateway.polymarket.us` for public market/event data that does not require credentials.
- Use `https://api.polymarket.us` for authenticated trading, portfolio, account, and WebSocket endpoints.
- Raw request auth signs `timestamp + method + path` with Ed25519. Do not include request body in the signature unless official docs change and this snapshot is updated.
- `X-PM-Timestamp` is milliseconds and must be within 30 seconds of server time.
- Decode `POLYMARKET_US_SECRET` from base64 and pass the first 32 bytes to `Ed25519PrivateKey.from_private_bytes`.
- Public unauthenticated calls are capped at 20 req/sec per IP.
- Authenticated trading REST is capped at 100 req/sec per firm averaged over 1 minute, with lower query/report endpoint caps.
- Orders endpoints require authentication and must remain hard-gated until a separate operator-approved live branch.
- Same-day eligibility/account/API access must be re-confirmed before any runtime enablement flag changes.
