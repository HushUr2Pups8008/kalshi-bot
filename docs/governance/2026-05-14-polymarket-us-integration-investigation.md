# Polymarket US Integration Investigation

**Last research update: 2026-05-14 — Q1-Q3 resolution pass (see § 10.1).**

**Date:** 2026-05-14 (UTC)
**Repo HEAD:** `1314da7` (current local `main`)
**VERSION:** `0.30.1` (operative; `v0.30.0` tag is published-broken — see `CLAUDE.md` Kalshi-API gotcha)
**Bot:** PID `92951`, untouched during investigation
**Mode:** read-only research; single deliverable artifact; **no code, DB, credential, or runtime mutations**
**Authority of this artifact:** scoped planning artifact under `docs/governance/`. Per `CLAUDE.md` R-10 (One Document), tracking content lands in `docs/profit_path_debt_log.md` only when the operator decides to integrate; this file proposes a debt-log pointer but does not author one.
**Doc fetches grounding this artifact:** all citations resolve under `docs.polymarket.us/...` (verified 2026-05-14). No `api.polymarket.us` or `gateway.polymarket.us` calls were made.

---

## 1. Executive Summary

This investigation evaluates whether the kalshi-bot codebase can be extended to also trade Polymarket US, and how much of the existing Kalshi-coupled stack can be reused versus replaced.

### Scope correction (important for any future reader)

"Polymarket US" referenced in this artifact is the **US-regulated centralized REST exchange** documented at `https://docs.polymarket.us/`. It is **not** the international, on-chain Polymarket built on Polygon with USDC collateral, EIP-712 signatures, and UMA-oracle resolution. The two share a brand and a market vocabulary but are wholly different integration surfaces. Specifically, Polymarket US:

- Exposes a public REST gateway at `https://gateway.polymarket.us` (market discovery, no auth) and an authenticated trading API at `https://api.polymarket.us` (orders, portfolio, balances) per `https://docs.polymarket.us/api-reference/introduction`.
- Uses **Ed25519** signatures with the request headers `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature` and a 30-second clock-skew window per `https://docs.polymarket.us/api-reference/authentication.md`.
- Provides two WebSocket channels: `wss://api.polymarket.us/v1/ws/private` and `wss://api.polymarket.us/v1/ws/markets` per `https://docs.polymarket.us/api-reference/introduction` and the per-page WebSocket docs.
- Has no on-chain dependency, no USDC collateral path, and no UMA oracle. There is no documented sandbox/testnet/paper environment for the public-developer (Ed25519) track (see § 10 open questions).

A second, **institutional** authentication track is documented at `https://docs.polymarket.us/trader-guide/authentication.md` using **Private Key JWT (RS256)** against an Auth0 OAuth2 token endpoint, with three named environments (`api.dev01.polymarketexchange.com`, `api.preprod.polymarketexchange.com`, `api.prod.polymarketexchange.com`) per `https://docs.polymarket.us/trader-guide/connection-issues.md`. The institutional track also offers gRPC streaming (`https://docs.polymarket.us/streaming-endpoints/grpc-overview.md`) and FIX (`https://docs.polymarket.us/trader-guide/connection-options.md`). For a single-operator bot the public-developer Ed25519 track is the natural target; the institutional track is mentioned only to flag that some doc URLs returned during fetch belong to it and have a different shape.

### Reuse classification distribution

Per § 4 below, the 25 modules called out in the user prompt classify as:

**`6 (a) / 12 (b) / 6 (c) / 1 (d)`**

Legend: **(a)** reusable as-is, **(b)** reusable behind a thin abstraction, **(c)** Kalshi-specific behavior that needs an exchange-specific replacement, **(d)** delete or replace wholesale (only `analysis/fade_signal.py` qualifies — its `@Kalshi` tweet patterns and Kalshi-WS-fed price fade are tightly bound to the Kalshi venue surface and have no Polymarket analog).

### Headline conclusion

The codebase is more reusable than expected for the *belief and decision* layers (`analysis/decision_blender.py`, `analysis/structural_prior.py`, `analysis/source_credibility.py`, `analysis/regime_classifier.py`, `governance/*`, `tasks/trade_readiness_gate.py`, `tasks/blend_task.py` orchestration with a `KalshiMarket`-equivalent input) and the *ingestion* layer (`feeds/*` is venue-agnostic). It is least reusable in the *exchange-touching* layers (`kalshi/*` REST/WS/normalizer must be cloned per exchange; `trading/portfolio.py` and `trading/paper_trader.py` carry Kalshi-specific column names and lack an `exchange` discriminator on every persisted row). The hierarchy inversion (Polymarket Series → Events → Markets vs Kalshi Series → Markets → Events with Series at the top) requires a discovery-layer adapter, not a deep refactor.

The single largest pre-launch risk is the **same-class trap that produced PROFIT-API-001 silent-50**: Polymarket's trading-endpoint `price` field is `int64`-string with no documented scaling, while the same logical price appears as a decimal-dollar string (`"0.55"`) on the WebSocket and a `decimal` on `get-markets`. Mixing the two encodings is a foreseeable defect class with the exact shape of the Kalshi cents-vs-dollars contamination the v0.30.x P0 work resolved.

### Update 2026-05-14 — Q1-Q3 resolution pass

- **Q1 resolved (CONFIRMED ABSENT):** No sandbox / testnet / paper environment exists for the public-developer Ed25519 track. The only documented multi-environment matrix (`https://docs.polymarket.us/trader-guide/environments.md`) lists three Auth0/JWT hosts (`api.dev01.polymarketexchange.com`, `api.preprod.polymarketexchange.com`, `api.prod.polymarketexchange.com`) all bound to the institutional track. The public-developer quickstart (`https://docs.polymarket.us/getting-started/quickstart.md`) and authentication reference (`https://docs.polymarket.us/api-reference/authentication.md`) reference only the live host `api.polymarket.us`. PAPER-ONLY posture from day one (§ 7.1) becomes a **structural necessity, not a preference**.
- **Q2 resolved (DOWNGRADED FROM HEADLINE RISK):** On the public-developer Ed25519 track, trading prices are submitted as a money-object `{ value: <decimal-dollar string>, currency: "USD" }`, **not** as an undocumented-scaling `int64`-string. Source: SDK quickstarts (`https://docs.polymarket.us/api-reference/sdks/python/quickstart.md`, `https://docs.polymarket.us/api-reference/sdks/typescript/quickstart.md`) both show verbatim `"price": {"value": "0.55", "currency": "USD"}`. The `int64`-string `price` field in the OpenAPI fragment under `https://docs.polymarket.us/api-reference/trading/insert-order.md` belongs to the **institutional `trading-schema.json` declared server `https://api.prod.polymarketexchange.com`** (Auth0/JWT track), not to the public-developer endpoint at `api.polymarket.us`. The "trading-int64-vs-decimal-dollar pricing trap" is therefore **contained to the institutional track** — the public-developer integration target uses a single decimal-dollar envelope end-to-end (REST market-data, WebSocket market-data, WebSocket private, REST trading via SDK). The PROFIT-API-001-class lesson is still load-bearing on principle (one `Price` value type, fail closed on unrecognized shape, never blend representations) but the specific silent-50 contamination class for Polymarket is materially smaller than the original headline suggested.
- **Q3 resolved (HANDSHAKE-HEADERS, NOT POST-CONNECT-AUTH):** WebSocket authentication is via **signed X-PM-* headers on the HTTP upgrade**, identical-in-shape to Kalshi's RSA-PSS handshake (just with different headers and Ed25519 instead of RSA-PSS). Source: `https://docs.polymarket.us/api-reference/websocket/overview.md` ("WebSocket connections use the same API key authentication as the REST API. Include these headers in the connection handshake: `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature`"). Both per-stream WS pages (`/private.md`, `/markets.md`) repeat the warning "This WebSocket endpoint requires API key authentication in the connection handshake." There is no post-connect auth-message step. Subscribe is a post-connect JSON command `{"subscribe": {"requestId": ..., "subscriptionType": ..., ...}}`. Heartbeats are server-sent and clients reconnect with exponential backoff if heartbeats stop; the numeric heartbeat interval is not stated in the public docs.

**Net effect on architectural posture:** The headline conclusion does not regress. Q3 confirms the Kalshi WS-auth pattern (signed-headers-on-upgrade plus `_WS_HEADER_KWARG` library-kwarg detection per § 6) ports directly to Polymarket — only the signature primitive and header names change. Q2 narrows the highest-risk defect class. Q1 hardens the safety boundary in § 7 (paper-only is now a structural requirement, not a soft preference). The remaining § 10 open questions are operationally smaller than Q1-Q3 were before resolution.

---

## 2. Polymarket US API Findings

Every claim in this section cites the URL it was fetched from. Quoted strings are verbatim from the doc page.

### 2.1 Authentication model (public-developer track)

- **Algorithm:** Ed25519 over a canonical request string. Source: `https://docs.polymarket.us/api-reference/authentication.md` shows the Python example
  `private_key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode("YOUR_SECRET_KEY")[:32])`
  and the signing function constructs `message = f"{timestamp}{method}{path}"` then `signature = base64.b64encode(private_key.sign(message.encode())).decode()`.
- **Headers (verbatim):** `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature` — same source. The portfolio schema (`https://docs.polymarket.us/api-reference/portfolio/get-user-positions.md`) repeats the same `securitySchemes` block ("All endpoints require Ed25519 signature authentication.").
- **Timestamp encoding:** "Unix timestamp in milliseconds. Must be within 30 seconds of server time" — verbatim from `securitySchemes.X-PM-Timestamp.description` in `https://docs.polymarket.us/api-reference/account/get-account-balances.md`.
- **Secret key format:** base64-encoded; the `[:32]` slice in the example confirms the raw seed is 32 bytes (canonical Ed25519 private-key seed length).
- **Sign-in-method coupling:** "Always sign in with the same method (Apple, Google, or email). Switching between sign-in methods may break your API key access." — verbatim Warning at `https://docs.polymarket.us/api-reference/authentication.md`. **There is no Kalshi analog for this gotcha class.** The bot's existing operator runbooks have nothing to say about identity-provider continuity, and a silent break of API-key access via account migration would surface as 401s with no semantic clue (similar shape to the RSA-PSS-vs-PKCS1v15 false-401 trap in `CLAUDE.md`).
- **Key rotation:** The page `https://docs.polymarket.us/trader-guide/authentication.md` contains a "Key Rotation" heading on the institutional (JWT) track. The public-developer page does not document a rotation procedure beyond regenerating keys at `polymarket.us/developer`. Treated as an open question (§ 10).
- **Body inclusion:** The example signing string is `f"{timestamp}{method}{path}"` — **the request body is not included**. This is a difference from Kalshi where the body is appended (see `kalshi/rest_client.py:107`).

### 2.2 Authentication model (institutional track, for completeness)

- **Algorithm:** Private Key JWT, RS256 (RSA). Source: `https://docs.polymarket.us/trader-guide/authentication.md` and `https://docs.polymarket.us/streaming-endpoints/authentication.md`.
- **Required JWT claims:** `iss`, `sub`, `aud` (must be `https://pmx-{env}.us.auth0.com/oauth/token`), `iat`, `exp`, `jti` — per the troubleshooting page `https://docs.polymarket.us/trader-guide/authentication-troubleshooting.md` ("Missing required claims" / "Reused jti").
- **Environments:** `https://docs.polymarket.us/trader-guide/connection-issues.md` lists three: Development (`api.dev01.polymarketexchange.com`), Pre-production (`api.preprod.polymarketexchange.com`), Production (`api.prod.polymarketexchange.com`). All trading-endpoint OpenAPI snippets (`/v1/trading/orders`, `/v1/trading/orders/list`, etc.) declare their server as `https://api.prod.polymarketexchange.com`, confirming the trading-API endpoints in the institutional documentation track exist on the Auth0/RS256 surface, not the public-developer Ed25519 surface.

This artifact assumes the public-developer Ed25519 track is the bot's integration target. Using the institutional track requires firm onboarding and has no path forward for a single-operator user.

### 2.3 Market discovery and hierarchy

- **Hierarchy:** "Every prediction on Polymarket US is structured around three levels: **series**, **events**, and **markets**." — `https://docs.polymarket.us/concepts/events-and-markets.md`. The functional ordering described there, and reflected in the API surface, is **Series → Events → Markets**.
- **Comparison to Kalshi:** The Kalshi codebase treats Series as a categorization above Markets and Markets as the tradeable instrument; "Events" in Kalshi vocabulary is closer to Polymarket's Markets (a single resolvable contract). The `KalshiMarket` dataclass at `kalshi/__init__.py` carries `series_ticker` as a foreign-key-ish field and `ticker` as the primary tradeable identifier. Polymarket's tradeable identifier is `slug` (preferred for human use) or `id` (UUID-stable); both are first-class per `https://docs.polymarket.us/api-reference/markets/get-market-by-slug.md` and `https://docs.polymarket.us/api-reference/markets/get-market-by-id.md`.
- **Discovery endpoints documented in `llms.txt`:**
  - `GET /v1/series` and `GET /v1/series/id/{id}` (`https://docs.polymarket.us/api-reference/series/get-series.md` etc.)
  - `GET /v1/events`, `GET /v1/events/{id}`, `GET /v1/events/slug/{slug}`
  - `GET /v1/markets`, `GET /v1/market/id/{id}`, `GET /v1/market/slug/{slug}`
  - `GET /v1/search` (`https://docs.polymarket.us/api-reference/search/search.md`)
  - `GET /v1/markets/{slug}/book`, `/v1/markets/{slug}/bbo`, `/v1/markets/{slug}/settlement`
  - Subjects and Tags trees (`https://docs.polymarket.us/api-reference/subjects/...` and `https://docs.polymarket.us/api-reference/tags/...`)

### 2.4 Market metadata fields

The `v1Market` schema in `https://docs.polymarket.us/api-reference/markets/get-market-by-slug.md` (and identically in `get-market-by-id.md` and `get-markets.md`) carries the following load-bearing fields:

- `id: string` (UUID-stable) and `slug: string` (URL-safe)
- `question: string` (the market question, equivalent in role to Kalshi's `title`)
- `active: boolean` ("Whether market is active", nullable)
- `closed: boolean` ("Whether market is closed", nullable)
- `archived: boolean`
- `outcomes: string` ("Outcomes JSON", nullable) and `outcomePrices: string`
- `marketSides: array of v1MarketSide` (with `MARKET_SIDE_TYPE_ERC1155` or `MARKET_SIDE_TYPE_INSTRUMENT` enum)
- `bestBid: number (decimal)` and `bestAsk: number (decimal)` — quoted in dollars between 0 and 1 (see § 2.6)
- `orderPriceMinTickSize: number (decimal)` — per-market tick size
- `feeCoefficient: number (decimal)` — per-market fee coefficient (nullable)
- `subject: v1Subject` and `subjectId: int32`
- `gameStartTime: string` (sports-context only)
- `createdAt`, `updatedAt: string` timestamps

**Two-status-field analog to Kalshi:** Polymarket has both `active: boolean` and `closed: boolean` fields, which together encode the tradeable state. There is no documented overloaded request-vs-response status filter analogous to Kalshi's `status="open"` request / `status="active"` response asymmetry. The risk shape is different but exists: `active=true` and `closed=true` could in principle co-exist and downstream code must pick one as the canonical tradeable predicate.

### 2.5 Order books

`GET /v1/markets/{slug}/book` (`https://docs.polymarket.us/api-reference/markets/get-market-book.md`) returns full book depth; `GET /v1/markets/{slug}/bbo` (`https://docs.polymarket.us/api-reference/markets/get-market-bbo.md`) returns only best bid/offer in a lightweight format. The Markets WebSocket page (`https://docs.polymarket.us/api-reference/websocket/markets.md`) notes "Order book levels are sorted best-to-worst (highest bid first, lowest ask first)."

### 2.6 Pricing units (the contamination risk)

**Resolved 2026-05-14 (Q2 — see § 10.1).** On the public-developer Ed25519 track this risk is materially smaller than originally flagged. The public-developer surface uses a **single decimal-dollar money-object envelope** end-to-end; the undocumented-scaling `int64` representation is **scoped to the institutional Auth0 track** and not reachable from the public-developer path the bot is integrating with.

- **WebSocket market-data prices** are money objects with decimal-dollar string `value`. Verbatim from `https://docs.polymarket.us/api-reference/websocket/markets.md`: `"price": {"value": "0.55", "currency": "USD"}`.
- **WebSocket private (orders, fills) prices** use the same money-object envelope per `https://docs.polymarket.us/api-reference/websocket/private.md` (order updates carry `price.value/currency`, `quantity.value/currency`).
- **REST market-data fields** (`bestBid`, `bestAsk`, `orderPriceMinTickSize`, `feeCoefficient`) are typed `number / decimal` on the `v1Market` schema. Dollar-fixed-point in `[0, 1]`.
- **REST trading endpoints (public-developer track, via the SDKs) use the same money-object envelope.** Verbatim from `https://docs.polymarket.us/api-reference/sdks/python/quickstart.md`:
  ```python
  order = client.orders.create({
      "marketSlug": "btc-100k-2025",
      "intent": "ORDER_INTENT_BUY_LONG",
      "type": "ORDER_TYPE_LIMIT",
      "price": {"value": "0.55", "currency": "USD"},
      "quantity": 100,
      "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
  })
  ```
  Verbatim from `https://docs.polymarket.us/api-reference/sdks/typescript/quickstart.md`:
  ```typescript
  const order = await client.orders.create({
    marketSlug: 'btc-100k-2025',
    intent: 'ORDER_INTENT_BUY_LONG',
    type: 'ORDER_TYPE_LIMIT',
    price: { value: '0.55', currency: 'USD' },
    quantity: 100,
    tif: 'TIME_IN_FORCE_GOOD_TILL_CANCEL',
  });
  ```
  Both SDK quickstarts are explicitly authored against the public-developer (`api.polymarket.us`, Ed25519, `X-PM-*`) track — they reference `polymarket.us/developer` as the key-issue surface and use the `polymarket_us` / `polymarket-us` package binding. The encoding is **decimal-dollar string in `value`** with the explicit `currency` field — same shape on every public-developer surface (REST trading, WS markets, WS private).
- **The `int64`-string `price` field documented in OpenAPI fragments under `https://docs.polymarket.us/api-reference/trading/insert-order.md` and `.../preview-order.md` belongs to the institutional track, not this integration target.** The OpenAPI block on both pages opens with the literal header `````yaml /institutional/oapi-schemas/trading-schema.json post /v1/trading/orders````` and declares `servers: - url: https://api.prod.polymarketexchange.com` — the institutional Auth0/JWT host, not the public-developer `api.polymarket.us` host. The institutional track exposes its trading API as raw integer encodings (`price: { type: string, format: int64 }` titled "Integer price representation (for limit, stop limit)") because its server-side wire format is gRPC + FIX-style integer pricing. The public-developer surface wraps that wire format in the money-object envelope at the SDK / public REST boundary. Per § 1's "Scope correction" the bot is not integrating with the institutional track, so the `int64` representation is not on the bot's code path.
- **Tick size, minimum order size, and price-validity bands are still per-market.** The `orderPriceMinTickSize: number (decimal)` on `v1Market` carries the per-market tick. The minimum order notional in dollars is not stated in the public docs (still open — see § 10 Q4).

**Reduced-risk class:** PROFIT-API-001-style silent-50 contamination is **possible only if a future implementation accidentally crosses the public/institutional boundary** — e.g., by importing an institutional-track SDK code sample whose `price: "55"` string-int format reaches the public-developer endpoint and is interpreted as `$55`. The mitigation in § 8 (one `Price` value type, fail closed on unrecognized shape, never blend representations) is still appropriate and load-bearing in principle; the headline-class blast radius from § 1 is contained to that specific cross-track-import defect rather than being an unavoidable integration hazard.

**Mitigation summary (unchanged in principle, narrowed in surface):** `Price` value type with two constructors only — `Price.from_polymarket_money_object({"value": "0.55", "currency": "USD"}) → Price(cents=55)` and `Price.from_kalshi_dollar_field(...) → Price(cents=55)`. Any other shape (e.g., a bare `int64` string) hard-fails — fail closed per `~/.claude/rules/risk_review.md`. No institutional-track adapter is built unless the operator explicitly approves a separate scope expansion.

### 2.7 Order placement

`https://docs.polymarket.us/api-reference/trading/insert-order.md` documents `POST /v1/trading/orders` with the `InsertOrderRequest` schema:

- `type`: enum `ORDER_TYPE_MARKET_TO_LIMIT | ORDER_TYPE_LIMIT | ORDER_TYPE_STOP | ORDER_TYPE_STOP_LIMIT`
- `side`: enum `SIDE_BUY | SIDE_SELL`
- `timeInForce`: enum `TIME_IN_FORCE_DAY | TIME_IN_FORCE_GOOD_TILL_CANCEL | TIME_IN_FORCE_IMMEDIATE_OR_CANCEL | TIME_IN_FORCE_GOOD_TILL_TIME | TIME_IN_FORCE_FILL_OR_KILL`
- `clordId: string` — the FIX-style **client order ID**, the documented idempotency surface
- `selfMatchPreventionId`, `selfMatchPreventionInstruction` — anti-self-trade controls
- `account: string`, `clientAccountId`, `clientParticipantId` — multi-account / give-up routing
- `goodTillTime: date-time` — required when `timeInForce = TIME_IN_FORCE_GOOD_TILL_TIME`
- `manualOrderIndicator`, `bestLimit`, `immediatelyExecutableLimit`, `strictLimit` — order-shape modifiers
- `ignorePriceValidityChecks: boolean` — explicit override of price-band validation

**Side semantics differ from Kalshi.** Polymarket sends `SIDE_BUY` or `SIDE_SELL` (a directional side per market), where the YES/NO distinction is encoded by the chosen `marketSides[].id` (or via the `intent` field documented in WS messages: `ORDER_INTENT_BUY_LONG`, `ORDER_INTENT_SELL_LONG`). Kalshi sends `side="yes"` or `side="no"` on a single market `ticker`. Any abstraction must capture both encodings; collapsing on either loses information.

**Preview Order** (`https://docs.polymarket.us/api-reference/trading/preview-order.md`) returns full order economics including `makerCommissionsBasisPoints` without inserting. This is a first-class pre-trade EV check — Kalshi has no equivalent.

**Batch order list** (`https://docs.polymarket.us/api-reference/trading/insert-order-list.md`) accepts up to 20 orders per request: "Maximum batch size is 20 orders; requests exceeding this limit will be rejected."

### 2.8 Cancel and modify

- `POST /v1/trading/orders/cancel` (`https://docs.polymarket.us/api-reference/trading/cancel-order.md`) cancels a working order.
- `POST /v1/trading/orders/list/cancel` cancels up to 20 orders per request.
- A modify-order endpoint is referenced in `llms.txt` but `https://docs.polymarket.us/api-reference/trading/modify-order.md` returned **HTTP 404** during this investigation — flagged in § 10.

### 2.9 Positions, balances, history

- `GET /v1/portfolio/positions` (`https://docs.polymarket.us/api-reference/portfolio/get-user-positions.md`) — positions across all markets or filtered by market.
- `GET /v1/account/balances` (`https://docs.polymarket.us/api-reference/account/get-account-balances.md`) — current account balances "including buying power, asset values, and pending transactions". `securitySchemes` block on this page is the most explicit X-PM-* documentation.
- `POST /v1/report/orders/search` and `POST /v1/report/trades/search` (`https://docs.polymarket.us/api-reference/report/search-orders.md`, `search-trades.md`) — order and trade history search. Trade records carry `makerCommissionsBasisPoints` and `ExecutionType` enum (`EXECUTION_TYPE_PARTIAL_FILL | EXECUTION_TYPE_FILL | EXECUTION_TYPE_CANCELED | EXECUTION_TYPE_REPLACE | EXECUTION_TYPE_REJECTED | EXECUTION_TYPE_EXPIRED | EXECUTION_TYPE_DONE_FOR_DAY`).

### 2.10 Settlement and resolution

`GET /v1/markets/{slug}/settlement` (`https://docs.polymarket.us/api-reference/markets/get-market-settlement.md`) returns the settlement price for a specific market by its slug. Notable: it carries an optional `fromEp3: boolean` query parameter ("Whether to get settlement from EP3" — internal acronym, undefined in the docs; flagged in § 10) and returns 404 when "Market not found or not settled".

This is a **dedicated resolution endpoint**, structurally different from Kalshi's pattern of putting `result` directly on each market in `GET /markets`. Kalshi resolution flows through `KalshiMarket.result` parsed by `kalshi/normalizer.py`. Polymarket needs a separate `SettlementSource` protocol (§ 5).

### 2.11 Rate limits

The most explicit rate-limit statement in the fetched corpus is in the gRPC overview (institutional track): "Client-to-server messages are limited to **100 messages per second** across all streams per firm, averaged over a 1-minute window." (`https://docs.polymarket.us/streaming-endpoints/grpc-overview.md`). The public REST track does **not** publish a numeric rate limit on the pages fetched in this investigation — the error-handling page (`https://docs.polymarket.us/trader-guide/error-handling.md`) advises "Use pagination for large result sets" and "Break large operations into smaller requests" without a per-second number. Flagged in § 10.

### 2.12 WebSocket private and markets streams

- **Private:** `wss://api.polymarket.us/v1/ws/private`. Per `https://docs.polymarket.us/api-reference/websocket/private.md`, subscription types are `SUBSCRIPTION_TYPE_ORDER`, `SUBSCRIPTION_TYPE_ORDER_SNAPSHOT`, `SUBSCRIPTION_TYPE_POSITION`, `SUBSCRIPTION_TYPE_ACCOUNT_BALANCE`. Subscribe payload shape: `{"subscribe": {"requestId": "...", "subscriptionType": "..."}}`. Order updates carry `marketSlug`, `side: ORDER_SIDE_BUY|SELL`, `intent: ORDER_INTENT_BUY_LONG|SELL_LONG`, `state: ORDER_STATE_PENDING_NEW|...`, `tif`, `price` (decimal-dollar `value`/`currency` object), `quantity`, `leavesQuantity`.
- **Markets:** `wss://api.polymarket.us/v1/ws/markets`. Per `https://docs.polymarket.us/api-reference/websocket/markets.md`, trade messages carry `marketSlug`, `price.value/currency`, `quantity.value/currency`, `tradeTime`, and both `maker` and `taker` blocks with `side` and `intent`. Markets-side subscribe payload shape: `{"subscribe": {"requestId": "...", "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA", "marketSlugs": ["..."]}}`.
- **Auth (resolved 2026-05-14 — Q3, see § 10.1):** **Authentication happens at HTTP-upgrade time with signed `X-PM-*` headers — identical-in-shape to Kalshi's RSA-PSS handshake, differing only in signature primitive (Ed25519 vs RSA-PSS) and header names.** Verbatim from `https://docs.polymarket.us/api-reference/websocket/overview.md`:
  > WebSocket connections use the same API key authentication as the REST API.
  > Include these headers in the connection handshake:
  > ```
  > X-PM-Access-Key: <your-api-key-id>
  > X-PM-Timestamp: <timestamp-in-milliseconds>
  > X-PM-Signature: <base64-encoded-signature>
  > ```
  > See Polymarket's Authentication docs for details on request signing.

  Both per-stream pages repeat the same authoritative statement verbatim:
  > This WebSocket endpoint requires API key authentication in the connection handshake. See the Authentication guide for details.
  (Sources: `https://docs.polymarket.us/api-reference/websocket/private.md`, `https://docs.polymarket.us/api-reference/websocket/markets.md`.)

  **Signing-string construction:** "the same API key authentication as the REST API" — per § 2.1, the canonical request string is `f"{timestamp}{method}{path}"` with `method="GET"` and `path="/v1/ws/private"` or `/v1/ws/markets`. The request body is **not** included (consistent with the REST surface). The Ed25519 signature is base64-encoded and placed in `X-PM-Signature`. The 30-second clock-skew window applies — laptops/VMs with drift need NTP discipline. There is no post-connect auth-message step.
- **Heartbeats:** "The server sends periodic heartbeat messages to keep the connection alive." The numeric interval is **not stated** in the public docs — a minor remaining gap, but operationally a client just needs to (a) tolerate server-sent heartbeat messages without treating them as a protocol error and (b) reconnect if heartbeats stop. Per `https://docs.polymarket.us/api-reference/websocket/overview.md` best-practices: "Use unique request IDs", "Handle reconnection — Implement automatic reconnection with exponential backoff", "Process messages in order", "Monitor heartbeats — Reconnect if heartbeats stop", "Limit subscriptions — Only subscribe to markets you need".
- **Reconnect / resubscribe:** Client must (i) re-establish the WS connection with fresh signed headers (timestamp must be ≤30s old at handshake), (ii) re-send subscribe payloads for each `subscriptionType` previously held, (iii) treat any in-flight `requestId` as potentially lost. The reconnect protocol is therefore stateless on the server side — no resume-token semantics; the client owns subscription state.

**Net effect:** the Kalshi WS-auth implementation (handshake-time signed headers + `_WS_HEADER_KWARG` library-kwarg detection per `kalshi/websocket_client.py:25-27`) ports directly. Only the signing primitive (`Ed25519` not `RSA-PSS`), the header names (`X-PM-*` not `KALSHI-ACCESS-*`), and the canonical-string excluded-body convention change. The body-inclusion difference at the REST layer (§ 2.1) applies here too: the WS-upgrade is `GET` with no body, so this is a no-op on the WS path, but the per-venue `_sign()` helpers must keep the bodies straight at the REST layer.

### 2.13 Sandbox / testnet / paper

**Resolved 2026-05-14 (Q1 — see § 10.1): CONFIRMED ABSENT for the public-developer Ed25519 track.** A targeted sweep of the docs.polymarket.us pages most likely to disclose a sandbox confirmed there is no equivalent for the public track. The evidence is uniform across three independent surfaces:

1. **The sole documented multi-environment matrix is institutional-only.** `https://docs.polymarket.us/trader-guide/environments.md` lists exactly three environments — Dev, Preprod, Prod — each on a `polymarketexchange.com` host paired with an `auth0.com` auth domain:

   | Environment | REST API | gRPC | Auth Domain |
   |---|---|---|---|
   | **Dev** | `https://api.dev01.polymarketexchange.com` | `grpc-dev01.polymarketexchange.com:443` | `pmx-dev01.us.auth0.com` |
   | **Preprod** | `https://api.preprod.polymarketexchange.com` | `grpc-preprod.polymarketexchange.com:443` | `pmx-preprod.us.auth0.com` |
   | **Prod** | `https://api.prod.polymarketexchange.com` | `grpc-prod.polymarketexchange.com:443` | `pmx-prod.us.auth0.com` |

   None of these hosts is `api.polymarket.us`. Every entry pairs an Auth0 OAuth2 issuer with the API host, confirming the matrix scopes the Private-Key-JWT/Auth0 institutional track. The page is filed under `/trader-guide/` (the institutional trader guide), not under `/api-reference/` (the public-developer reference).

2. **The public-developer reference pages never mention any non-`api.polymarket.us` host.** `https://docs.polymarket.us/api-reference/introduction.md` lists only `https://gateway.polymarket.us` (public market-data) and `wss://api.polymarket.us/v1/ws/{private,markets}` (authenticated WS) as endpoints. `https://docs.polymarket.us/api-reference/authentication.md` has its lone live-host example point to `https://api.polymarket.us/v1/portfolio/positions`. No `dev.`, `preprod.`, `test.`, `sandbox.`, or `staging.` subdomain appears anywhere in the public-developer reference corpus fetched.

3. **The public-developer quickstart has no environment-selector step.** `https://docs.polymarket.us/getting-started/quickstart.md` walks through (1) Download the app, (2) Generate an API key at `polymarket.us/developer`, (3) Make a request — with the request example pointing at `https://api.polymarket.us` directly. The quickstart says "No authentication required for public endpoints" but never offers a non-production environment. The SDK quickstarts (`/sdks/python/quickstart.md`, `/sdks/typescript/quickstart.md`) similarly instantiate the client without an environment selector.

**Operator-side implication.** Because the operator cannot first-touch live in a no-money-at-risk environment, the first live Polymarket REST POST is by construction a live order against real bankroll. This makes the PAPER-ONLY posture from day one (§ 7.1) a **structural necessity, not a preference**, and forces the first-touch protocol to be operationally conservative.

**First-touch protocol (recommended, since no sandbox exists).** The operator's safest path from PAPER-ONLY to LIVE-READY is:

1. **Authenticated read-only first.** First live call is `GET /v1/health/check` (no auth) → then `GET /v1/portfolio/positions` (authenticated; no side effects). Verifies Ed25519 signing end-to-end against the live host without touching the order book.
2. **Single-contract BUY on a high-liquidity ~50¢ market.** The first live order is the minimum quantity (per the per-market `orderQty` minimum — see § 10 Q4) on a market with `bestBid` between 0.45 and 0.55 (where YES and NO are roughly symmetric and decimal-dollar contamination would jump out by an order of magnitude — a `$0.50` order misinterpreted at any other scale would either fail validation immediately or be obviously wrong). Choose a venue-popular market with deep two-sided liquidity so the BUY fills near-instantly and the operator can observe the order state transition end-to-end. **One contract, BUY-only.** Do not place SELL orders on the first touch.
3. **Verify the fill round-trips through `paper_trades`-equivalent persistence with `exchange='polymarket'`** and through `GET /v1/portfolio/positions` showing the new position before authorizing a second live order.
4. **Pause for operator review.** No second live order placed without operator confirmation that the first round-tripped cleanly. This is consistent with the `~/.claude/rules/agent_collaboration.md` "agent-executed cutover" prohibition.

Because Step 2 spends real money, **the entire first-touch sequence is operator-executed at the keyboard with the agent in advisory mode only**. Per `CLAUDE.md`'s agent-collaboration policy, this is squarely in the high-assurance / dual-agent-audit / operator-gated zone.

### 2.14 Fees and incentives

- **Per-market fee coefficient** is exposed on the market schema (`feeCoefficient` field, § 2.4).
- **Per-trade maker commissions** in basis points are returned on `Preview Order` (`makerCommissionsBasisPoints`) and on the `search-trades` execution records.
- **Incentives endpoint:** `GET /v1/incentives/earnings` (`https://docs.polymarket.us/api-reference/incentives/get-incentive-earnings.md`) — "Returns reward records grouped by market and date (Eastern Time). Dates are bucketed by ET midnight boundaries." The ET-midnight bucketing is a small-but-load-bearing surface (UTC mismatch with the rest of the bot per `~/.claude/rules/portability.md`).

---

## 3. Comparison to Current Kalshi Architecture

Point-by-point below; "Kalshi" refers to current behavior of the kalshi-bot codebase (cited at file:line where load-bearing).

| Concern | Kalshi (today) | Polymarket US | Gap |
|---|---|---|---|
| Auth algorithm | RSA-PSS / SHA-256 / `salt_length=DIGEST_LENGTH` (`kalshi/rest_client.py:107-115`) | Ed25519 (`https://docs.polymarket.us/api-reference/authentication.md`) | Different primitive, identical *shape* of "sign canonical request string and pass headers" |
| Signing string | `ts + METHOD + path + body` (`kalshi/rest_client.py:107`) | `ts + METHOD + path` — body **excluded** | Polymarket has narrower replay surface; importing Kalshi's body-included signing logic verbatim would over-include and produce 401s |
| Headers | `KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP` | `X-PM-Access-Key/Signature/Timestamp` | Mechanical rename; identical role |
| Clock-skew window | Not formally documented in code; effective tolerance set server-side | 30 seconds (verbatim) | Polymarket is stricter than typical exchanges; matters for laptops/VMs that drift |
| Body inclusion | Yes (Kalshi) | No (Polymarket) | Code-reuse trap: copying `_sign()` verbatim produces wrong signatures |
| Secret key format | RSA PEM as single line with literal `\n` (`_normalize_pem` in `kalshi/rest_client.py:34` and `kalshi/websocket_client.py:51` — two identical copies per `CLAUDE.md`) | base64-encoded 32-byte Ed25519 seed | Different store/handling. The "two identical `_normalize_pem()` copies" gotcha argues for a shared `_load_pem()` helper before adding a third `_load_ed25519()` helper |
| Sign-in-method coupling | N/A | Apple/Google/email lock-in (verbatim Warning) | New gotcha class; surface in operator runbook |
| WebSocket auth | RSA-PSS over `ts + "GET" + "/trade-api/ws/v2"` in HTTP upgrade; library-version-sensitive `extra_headers` vs `additional_headers` (`kalshi/websocket_client.py:25-27`) per `CLAUDE.md` | X-PM-* headers; exact handshake protocol not fully documented in fetched corpus (§ 10) | Auth-on-upgrade vs auth-on-message ambiguity to resolve in design |
| WebSocket library kwarg | Detected at import (`_WS_HEADER_KWARG`) per the documented `CLAUDE.md` gotcha | Same library, same risk | The detection helper carries forward unchanged |
| Hierarchy direction | `Series.ticker → Markets[].ticker` (Series above) | `Series.id → Events[].id → Markets[].slug` (Markets at the bottom of a 3-level tree) | Inversion is in **discovery only**, not in the tradeable identifier; once a `Market` is selected the downstream pipeline is identical in shape |
| Tradeable identifier | `ticker: str` (string, e.g., `KXTRUMPIRAN-26JUN01`) | `slug: str` and `id: UUID` (both first-class) | The bot's pervasive `ticker:` parameter must become a polymorphic `market_id` carrying exchange tag |
| Status / tradeability | `status="active"` on response, `?status=open` request filter (PROFIT-API-001 P-7 trap documented in `CLAUDE.md`); enforced at `analysis/market_matcher.py:440,490` | `active: boolean` AND `closed: boolean` — both must be evaluated; `?status=...` filter not documented as overloaded | Different overlap shape; the "two-contract" lesson does not apply identically but the predicate-discipline lesson does |
| Pricing units | Cents (post-P0 normalized via `kalshi/normalizer.py` — `dollars_fixed_point` or `legacy_cents`); strict invariant `yes_bid + no_ask ≤ 100` enforced at `_invariants_hold` | Decimal dollars (0..1) on REST/WS market-data surfaces; **`int64`-string with undocumented multiplier on trading endpoints** (§ 2.6) | This is the load-bearing PROFIT-API-001-class risk; explicit `Price` value type required on Polymarket side |
| Side encoding | `side="yes"\|"no"` over a single market `ticker` (`analysis/side_selection.py`) | `SIDE_BUY/SIDE_SELL` over a chosen `marketSides[].id`; `intent: ORDER_INTENT_BUY_LONG/SELL_LONG` on WS | YES vs NO selection happens at marketSide-id selection time, not as an order field — a different abstraction shape |
| Order types | Single `place_limit_order` (`kalshi/rest_client.py:329`) | LIMIT / MARKET-to-LIMIT / STOP / STOP-LIMIT with full TIF and SMP control | Polymarket is richer; not all surfaces need to be exposed initially |
| Idempotency | None documented in `kalshi/rest_client.py` | `clordId: string` (FIX-style client-order-ID) | Polymarket gives us a documented retry-safety field that Kalshi does not; the existing executor's `_is_retryable_error` plus exponential backoff logic gains real safety with `clordId` populated |
| Pre-trade preview | None | `POST /v1/trading/orders/preview` returns full economics including `makerCommissionsBasisPoints` | New optional capability; could replace the bot's local Kelly-based EV check at the boundary, or run alongside as a sanity rail |
| Settlement / resolution | `KalshiMarket.result` field, parsed by normalizer; orchestrated by `_resolve_market_sync()` (per `CLAUDE.md` atomicity gotcha) | Dedicated `GET /v1/markets/{slug}/settlement` endpoint | Need a `SettlementSource` protocol (§ 5) and explicit polling cadence on Polymarket side |
| Rate limits | Implicit; retry-with-backoff in `_request` `status_forcelist=[429, 500, 502, 503, 504]` (`kalshi/rest_client.py:91`) | Not numerically documented for REST track; gRPC track 100 msg/s averaged over 1 min | Same retry posture is appropriate; rate-budget telemetry is a new operational ask |
| Sandbox | Demo (`KALSHI_DEMO_REST = "https://demo-api.kalshi.co/trade-api/v2"`, `config.py:82`) | None documented for public track | PAPER-ONLY posture is therefore not a preference but a necessity until § 10 Q1 is answered |
| Notable extra surfaces on Polymarket | n/a | `Subjects` and `Tags` trees; `Search` endpoint; per-market `feeCoefficient` and `orderPriceMinTickSize`; ET-midnight `incentives/earnings` | Capability surplus, not a gap |

---

## 4. Per-Module Reuse Classification Table

Classification legend:

- **(a) Reuse as-is.** No exchange-specific assumptions; can be imported by a Polymarket pipeline unchanged.
- **(b) Reuse behind a thin abstraction.** The module's contract is venue-neutral but currently typed against `KalshiMarket` (or imports `kalshi.*`); switching it to a `Market` protocol unlocks dual-venue use without semantic change.
- **(c) Kalshi-specific behavior; needs an exchange-specific replacement.** The module encodes Kalshi REST shapes, signing, status semantics, ticker conventions, or persistence schemas.
- **(d) Delete or replace wholesale.** Strategy / surface is venue-bound and has no Polymarket analog.

For every justification I cite a Kalshi-specific line of evidence in the file or a doc URL difference. Where uncertain I bias toward the conservative (less-sharing) classification per the user prompt.

| Module | Class | Justification |
|---|---|---|
| `feeds/rss_monitor.py` | (a) | Pure RSS ingestion. No `from kalshi` imports. Emits venue-neutral `NewsItem` (`feeds/__init__.py`). News content is identical regardless of which venue's market it later matches. |
| `feeds/reddit_monitor.py` | (a) | Same shape as `rss_monitor.py`; Reddit content is venue-neutral. The `CLAUDE.md` Reddit-403 gotcha (one external IP at a time) is operator policy, not exchange-coupled. |
| `feeds/dedup.py` | (a) | Pure hash-based dedup over `NewsItem.item_id`. No exchange coupling. |
| `feeds/__init__.py` | (a) | Defines `NewsItem` dataclass (`feeds/__init__.py:1-13`). Already venue-neutral. |
| `feeds/bluesky_monitor.py` | n/a — **file does not exist in the current tree** (verified `ls feeds/bluesky_monitor.py` returned not-found). The user prompt listed it under § 2.5; treating as a no-op for this audit. |
| `analysis/signal_analyzer.py` | (b) | Imports `KalshiMarket` (`analysis/signal_analyzer.py:23`) and uses `market.yes_prob`, `market.ticker`, `market.title` for prompt construction and circuit-breaker keys. The LLM prompt logic and Ollama-circuit-breaker behavior is venue-neutral; what's coupled is the dataclass type and the `yes_prob` accessor (a Kalshi-cents-derived property on `KalshiMarket`). A `Market` protocol with `tradeable_id`, `question_text`, and `yes_probability` (already a 0..1 float) replaces the coupling without behavior change. **Carries the `JSONDecoder.raw_decode()` LLM-extraction gotcha from `CLAUDE.md` — must propagate to any Polymarket-side prompt builder.** |
| `analysis/market_matcher.py` | (c) | Hard-coded to Kalshi market discovery: imports `KalshiRestClient` (`market_matcher.py:26`), calls `_client.get_all_series()` and `_client.get_markets(status="open", series_ticker=...)` (`market_matcher.py:417-450`). Carries the **PROFIT-API-001 P-7 hotfix** at `:440,490` (request filter `status="open"`). Polymarket has no series-prefix blocklist analog and a different discovery surface (`Series → Events → Markets`). Needs a Polymarket-specific matcher implementation backed by `gateway.polymarket.us` discovery; the Jaccard-similarity scoring, `_pre_llm_gate_reason`, and quality-flag logic is reusable as a pure helper module. |
| `analysis/decision_blender.py` | (a) | Pure function layer (`analysis/decision_blender.py:3` "No I/O, no DB access, no LLM calls"). No `kalshi` imports. `LaneInput`, `BlendResult`, `blend()`, DER-1..DER-4 logic is fully venue-neutral. |
| `analysis/regime_classifier.py` | (b) | Imports `KalshiMarket` (`regime_classifier.py:18`). The series-prefix table (`_SERIES_PRIORS` keyed by `KXTRUMP*`, `KXSBUDGETRES*`, etc., per the `regime_classifier.py:30-160` block) is Kalshi-specific. The classifier's *algorithm* (prefix-prior + time-to-close + keyword nudge) is venue-neutral and gives the right shape for Polymarket once a Polymarket-specific prefix table replaces the Kalshi one. A `Market` protocol with `series_id`, `tradeable_id`, `close_time` lets the existing function compile against either venue. |
| `analysis/structural_prior.py` | (a) | Pure function layer (`structural_prior.py:3` "No I/O, no DB access, no network calls"). Operates on a `context: dict`. No exchange-specific assumption inside. |
| `analysis/fade_signal.py` | (d) | The `_BULLISH_PATTERNS` regexes (`fade_signal.py:_BULLISH_PATTERNS`) target **`@Kalshi` Twitter hype patterns** and the WS-driven price-fade scaffolding consumes the Kalshi WS feed. There is no Polymarket equivalent of the `@Kalshi` tweet feed; the price-fade detector itself could in principle be venue-neutral, but the module as a whole encodes a Kalshi-marketing-feed-specific fade strategy. Replace wholesale on Polymarket side; do not inherit. |
| `analysis/kelly.py` | (b) | Docstring says "On Kalshi, each contract pays $1 if it resolves YES and $0 otherwise." (`kelly.py:4`). The math is identical for Polymarket (binary YES contract pays $1 per `https://docs.polymarket.us/concepts/market-data.md`). The `contracts_from_dollars()` helper takes `price_cents` — needs to either accept a venue-neutral `Price` value type or be wrapped per-venue to convert from Polymarket's decimal-dollar or trading-int64 representation. |
| `analysis/source_credibility.py` | (a) | File is venue-neutral; checked import surface. (Note: lives in `tasks/stats/` per the actual layout — the user prompt's path may be slightly off; the credibility scoring logic itself is venue-neutral.) |
| `analysis/side_selection.py` | (b) | Imports `KalshiMarket` (`side_selection.py:17`) and reads `market.yes_ask_cents`, `market.no_ask_cents`. The two-sided executable-EV algorithm (LD-10 / P-5) is venue-neutral. The `cents` integer encoding is Kalshi-specific (post-P0); on Polymarket the equivalent comes from decimal-dollar best-ask values multiplied to integer cents (or the trading-API int64 with proper scaling resolved per § 2.6). A `Market` protocol with `yes_ask` and `no_ask` returning a `Price` value type unlocks reuse without changing the algorithm. |
| `tasks/blend_task.py` | (b) | Imports `KalshiMarket` (`tasks/blend_task.py:22`) and uses `market.ticker` as `market_ticker` throughout (`tasks/blend_task.py:115,167-195`). Orchestration logic (lane-meeting, dossier reads, readiness evaluation) is venue-neutral. The `market_ticker:` parameter naming should become `market_id:` in the abstraction. |
| `tasks/structural_task.py` | (b) | Imports `KalshiMarket` (`structural_task.py:18`). Orchestration is venue-neutral; `compute_structural_prior` is already pure. |
| `tasks/trade_readiness_gate.py` | (a) | Module docstring: "stateless...evaluates G1–G6 readiness predicates" (`trade_readiness_gate.py:3`). No `kalshi` imports. Operates on a `Mapping`-shaped readiness context. |
| `tasks/evidence_store.py` | (b) | Persists evidence and dossier records keyed by `market_ticker: str` throughout (`evidence_store.py:40,58,80,97,130,...`). Schema is venue-coupled in the column-name sense (`market_ticker`) but the storage shape is venue-neutral. Either rename to `market_id` and add `exchange` column, or fork into per-exchange databases. The single-DB-with-`exchange`-column option keeps cross-venue analytics simple but requires a schema migration. |
| `trading/executor.py` | (c) | Imports `KalshiRestClient` (`executor.py:23`), wires `place_limit_order` via the Kalshi REST shape (`executor.py:538`), uses Kalshi-cents `price_cents` and `contracts` semantics throughout. Kill-switch logic (`_check_live_loss_limit`, `_handle_go_live` reference, `LIVE_TRADING_ENABLED`-gated path) is venue-neutral and must extend not weaken (§ 7). Needs an `ExchangeClient` protocol injected at construction time, with the Kalshi and Polymarket implementations selected by the candidate's exchange tag. |
| `trading/paper_trader.py` | (c) | SQLite schema `paper_trades` table (`paper_trader.py:55-86`) uses Kalshi-specific column names: `ticker`, `side` (yes/no), `price_cents`, `market_yes_price`. No `exchange` column. Per `CLAUDE.md`, the `_resolve_market_sync()` atomicity gotcha is load-bearing here. A direct port to Polymarket needs (i) `exchange` column on every row, (ii) a polymorphic `market_id`, (iii) a `Price` value type that can carry decimal-dollar or sub-cent representations, (iv) per-exchange settlement-fetch routing. **This is the highest-risk single module on the workoff path.** |
| `trading/portfolio.py` | (c) | `Position` dataclass (`portfolio.py:20-46`) uses `ticker`, `side`, `price_cents`, `entry_price_cents`. `Portfolio._positions: dict[str, list[Position]]` is keyed by `ticker`. No exchange tag. Cross-exchange position confusion is structurally possible today (would not happen because only one exchange is wired) but adding Polymarket without an `exchange` discriminator on the key would create ambiguity (a `KXTRUMPIRAN-...` ticker and a Polymarket slug could co-exist as keys, but the readers downstream do not expect this). |
| `kalshi/rest_client.py` | (c) | Whole-file Kalshi-specific: `KalshiSigningError`, `_sign()` (RSA-PSS, body-included), `KALSHI-ACCESS-*` headers, `/trade-api/v2` path prefix, `?status=open` filter. **Do not adapt; reimplement as `polymarket/rest_client.py` with the Ed25519 signer and `X-PM-*` headers, and extract the shared HTTP-retry/error-redaction primitives into a `exchange_http.py` helper that both clients consume.** |
| `kalshi/websocket_client.py` | (c) | Whole-file Kalshi-specific: RSA-PSS handshake auth, `orderbook_delta` channel, `subscribe` cmd shape, `KALSHI-ACCESS-*` upgrade headers. The `_WS_HEADER_KWARG` library-version-detection helper (`websocket_client.py:25-27`) is the one piece worth extracting and sharing — it mitigates a documented `CLAUDE.md` gotcha that bites any websockets-library consumer regardless of venue. |
| `kalshi/normalizer.py` | (c) | Whole-file Kalshi-specific: `_DOLLAR_PRICE_FIELDS = ("yes_bid_dollars", ...)`, `_LEGACY_CENTS_FIELDS = ("yes_bid", ...)`, `_invariants_hold` checking `yes_bid + no_ask ≤ 100` (cents), `UnsupportedPayloadContractError`. The *invariant-based, fail-closed* design pattern is the single most important reusable lesson — Polymarket needs an analogous `polymarket/normalizer.py` with its own `_DECIMAL_DOLLAR_FIELDS = ("bestBid", "bestAsk", ...)` and its own (more restrictive) invariants on the trading-API int64 path. |
| `kalshi/__init__.py` | (b) | Defines `KalshiMarket` and `OrderResult` dataclasses. The dataclass shape is venue-neutral after the field renames listed in (c) for `paper_trader`/`portfolio`; the Kalshi-specific bits are the legacy `yes_bid`/`yes_ask`/`yes_price` accessors and the `__getattribute__` guard. A shared `Market` protocol is achievable; a single shared dataclass that fits both venues without leaking is harder and not worth the complexity. Recommend two dataclasses (`KalshiMarket`, `PolymarketMarket`) plus a `Market` Protocol that downstream modules type against. |
| `governance/agent.py` | (a) | Decision-policy authority; no exchange-specific I/O. Carries the load-bearing real-mode flip-authority surface called out in `~/.claude/rules/domain_constraints.md` — adding Polymarket doesn't change the live-flip authority, but the kill-switch must keep covering both exchanges (see § 7). |
| `governance/adapter.py` | (a) | Adapter between the rest of the system and the governance LLM call path. Not exchange-coupled. |
| `governance/decision.py` | (a) | Decision dataclass and policy. Not exchange-coupled. |
| `governance/llm.py` | (a) | LLM-client wrapper with the `think: False` qwen3 gotcha (`CLAUDE.md` PROFIT-GOV-001). Exchange-neutral. The `governance/prompts.py:27-31` anchor_rate polarity block (PROFIT-GOV-002) is exchange-neutral and stays untouched per `~/.claude/rules/domain_constraints.md`. |
| `config.py` | (b) | Today carries `KALSHI_PROD_REST`, `KALSHI_DEMO_REST`, `KALSHI_API_KEY_ID`, `KALSHI_API_KEY_SECRET`, `KALSHI_ENV`, `BANKROLL`, `MAX_BET_HARD_CAP` (per `CLAUDE.md` env gotcha), `LIVE_TRADING_ENABLED`. Polymarket adds `POLYMARKET_API_KEY_ID`, `POLYMARKET_API_KEY_SECRET` (base64), and per-exchange enable flags (`POLYMARKET_LIVE_TRADING_ENABLED`). The `dynamic_max_bet(notional)` helper stays venue-neutral. **Crucially, `LIVE_TRADING_ENABLED` must remain a global hard-off; per-exchange enables stack on top, not replace.** |
| `main.py` | (b) | Wires `KalshiRestClient` and `KalshiWebSocketClient` (`main.py:435-436`). The startup-flow, `--go-live` handler (`main.py:2017,2082`), `LIVE_TRADING_ENABLED` gate (`main.py:2093`), and the typed `CONFIRM` prompt (`main.py:2136`) are venue-neutral safety scaffolding. Polymarket integration adds parallel client instantiation behind `if cfg.polymarket_enabled:` and per-exchange routing, with the existing kill-switch semantics extended (not relaxed). |
| `utils/logger.py` | (a) | The structured-log canonical fields list (`utils/logger.py:50,61,75,84,103`) already uses `market_ticker`. Adding `exchange` to the canonical fields is the only change required and is purely additive. |

**Class-distribution counts:** (a) = 6, (b) = 12, (c) = 6, (d) = 1. Total = 25 (the `feeds/bluesky_monitor.py` entry from the user prompt is excluded as the file does not exist).

**Class-of-risk callouts mapped to lessons:**

- **PROFIT-API-001 silent-50 contamination (status-field two-contract trap, fixed-point pricing trap):** maps to (c)-classified `kalshi/normalizer.py`, `kalshi/rest_client.py`, `analysis/market_matcher.py`. The Polymarket equivalent is the **trading-int64-vs-decimal-dollar pricing trap** in § 2.6 and the `active`+`closed` two-boolean tradeability shape in § 2.4.
- **PROFIT-SEC-001 sign-failure auth bypass (closed; preserve fail-fast posture):** maps to (c)-classified `kalshi/rest_client.py` and `kalshi/websocket_client.py`. The Polymarket equivalent is the Ed25519 signing failure path in any new `polymarket/rest_client.py`; **must raise on signing failure, not return unsigned headers**.
- **P-7 status-filter regression (`?status="active"` 2726-error 400 storm):** maps to (c)-classified `analysis/market_matcher.py`. Polymarket's discovery filter shape needs to be confirmed against live behavior before any analogous filter is set in code; the safer first move is to fetch unfiltered and filter client-side.
- **WS library kwarg drift (`extra_headers` vs `additional_headers`):** maps to (c)-classified `kalshi/websocket_client.py`. The detection helper at `kalshi/websocket_client.py:25-27` is the one shared piece worth extracting; any new Polymarket WS client must use the same helper.
- **Sign-in-method coupling (NEW — no Kalshi analog):** Polymarket-specific operator-runbook risk; surface at the start of any deploy plan and require operator confirmation that Apple/Google/email sign-in choice has not changed since last key-issue. There is no code mitigation; this is purely an out-of-band operator discipline class.

---

## 5. Proposed Architecture Approach

The core organizing principle is **per-exchange adapters behind a venue-neutral Market and ExchangeClient protocol**, with persistence carrying an `exchange` discriminator on every row. Decision logic, evidence store, governance, news ingestion, and risk gates remain shared. Per CLAUDE.md's discipline of fail-closed value types and provenance-tagged data, both adapters fail closed at the contract boundary; nothing leaks raw payload structure into shared logic.

High-level layering (from input to action):

1. **Ingestion (shared):** `feeds/*` emits `NewsItem`. Unchanged.
2. **Discovery (per-exchange):** `kalshi.MarketCache` and a new `polymarket.MarketCache` produce `list[Market]` against the `Market` protocol. Series/event hierarchy differences are absorbed here.
3. **Matching (mostly shared):** scoring/quality/Jaccard logic lifts into `analysis/match_scoring.py` (pure helpers); discovery-side calls remain per-exchange.
4. **Belief and decision (shared):** `analysis/signal_analyzer.py`, `analysis/decision_blender.py`, `analysis/structural_prior.py`, `analysis/regime_classifier.py`, `analysis/kelly.py`, `analysis/side_selection.py`, `tasks/trade_readiness_gate.py`, `tasks/blend_task.py`, `tasks/structural_task.py` — all consume the `Market` protocol.
5. **Governance (shared):** `governance/*` is unchanged.
6. **Persistence (shared schema, exchange-discriminator column):** `paper_trades`, `evidence_store` schemas grow an `exchange TEXT NOT NULL` column. `Position`/`Portfolio` keys grow from `ticker` to `(exchange, market_id)`.
7. **Execution (per-exchange):** `kalshi.RestClient.place_limit_order` and a new `polymarket.RestClient.insert_order` — invoked through an `ExchangeClient` protocol that the executor types against. The executor is the single venue-neutral router.
8. **Settlement (per-exchange via `SettlementSource` protocol):** Kalshi reads `KalshiMarket.result`; Polymarket polls `GET /v1/markets/{slug}/settlement`. The protocol presents a unified "is this market resolved? what side won?" interface to the resolution loop.

What stays Kalshi-specific (not shared):

- `kalshi/rest_client.py`, `kalshi/websocket_client.py`, `kalshi/normalizer.py` — venue-bound implementations.
- The `KXxxx`-prefixed series-prior table in `analysis/regime_classifier.py` — Polymarket gets its own Polymarket-prefix table beside it.
- The `@Kalshi` tweet-fade strategy (`analysis/fade_signal.py`) — unchanged for Kalshi; not ported.
- The `?status=open` request filter and series-blocklist plumbing in `analysis/market_matcher.py:417-450`.

What gets extracted (not invented anew):

- An `exchange_http.py` shared helper for retry-with-backoff (`status_forcelist=[429, 500, 502, 503, 504]`), header redaction, and 401-error logging hygiene (lifted from `kalshi/rest_client.py:170-180`).
- A shared `_load_pem_or_b64()` credentials helper (factor out the duplicate `_normalize_pem` per the `CLAUDE.md` "two identical copies" gotcha; add an `ed25519_seed_from_b64` sibling).
- The `_WS_HEADER_KWARG` websockets-library-version detection from `kalshi/websocket_client.py:25-27` into a shared `exchange_ws.py` helper.

What gets a `SettlementSource` protocol:

```python
class SettlementSource(Protocol):
    async def get_settlement(self, market_id: str) -> Optional[SettlementResult]: ...
    # SettlementResult carries: resolved: bool, winning_side: Literal["yes","no"]|None, settled_ts: datetime
```

What gets exchange-tagged on the persistence layer:

- Every row in `paper_trades`, `evidence`, `dossiers`, `dossier_updates`, `structural_priors` gains `exchange TEXT NOT NULL`.
- Every emitted log line that references a market gains `exchange=` in the canonical fields enumerated at `utils/logger.py:50-103`.
- Every in-memory `Position` carries `exchange: str`; every `Portfolio` lookup keys on `(exchange, market_id)` not `ticker`.

---

## 6. Exchange-Specific vs Shared Abstractions

Explicit list. Each shared abstraction is named with its two implementations.

**Shared protocols and helpers:**

- `Market` (Protocol) — minimum surface: `tradeable_id: str`, `exchange: str`, `question_text: str`, `series_id: str | None`, `close_time: datetime | None`, `yes_probability: float`, `yes_ask_cents: int | None`, `no_ask_cents: int | None`, `is_tradeable() -> bool`, `regime_weights: dict[str, float]`. Implementations: `KalshiMarket` (existing dataclass; minor field-name additions for `exchange` and `tradeable_id`), `PolymarketMarket` (new dataclass).
- `ExchangeClient` (Protocol) — minimum surface: `get_balance()`, `get_open_positions()`, `place_limit_order(market_id, side, count, limit_price)`, `cancel_order(order_id)`, `get_market(market_id)`. Implementations: `KalshiRestClient` (existing), `PolymarketRestClient` (new; per-call routing to Polymarket's `POST /v1/trading/orders` with `clordId` populated for idempotency).
- `MarketCache` (Protocol) — minimum surface: `async get_markets() -> list[Market]`, `async get_all_markets() -> list[Market]`. Implementations: `KalshiMarketCache` (existing), `PolymarketMarketCache` (new; absorbs the Series → Events → Markets hierarchy).
- `WebSocketClient` (Protocol) — minimum surface: `watch(market_ids)`, `unwatch(market_ids)`, `on_price_update(callback)`, `run()`. Implementations: `KalshiWebSocketClient` (existing), `PolymarketWebSocketClient` (new).
- `SettlementSource` (Protocol) — see § 5.
- `Price` (value type / NewType) — explicit `Price.from_polymarket_money_object({"value": "0.55", "currency": "USD"}) → Price(cents=55)` and `Price.from_kalshi_dollar_field(...) → Price(cents=55)`. **Both constructors validate the source contract and fail closed on unrecognized shapes — same pattern as `kalshi/normalizer.py:_detect_contract` / `_invariants_hold`.** Per Q3 resolution (§ 10.1), the institutional-track `int64`-string price representation (`Price.from_polymarket_trading_int64`) is **not built** unless the operator explicitly approves a scope expansion to the Auth0/JWT track; the public-developer surface uses a single money-object envelope end-to-end (REST trading via SDK, WS markets, WS private), so a third constructor is unnecessary and would invite the cross-track-import defect class described in § 2.6.
- `exchange_http.py` shared helper (retry, redaction, 401-logging).
- `exchange_ws.py` shared helper (`_WS_HEADER_KWARG` library-kwarg detection).

**Exchange-specific (no shared abstraction):**

- `kalshi/__init__.py` `KalshiMarket` and `kalshi/normalizer.py` (Kalshi-side); their Polymarket counterparts are `polymarket/__init__.py` `PolymarketMarket` and `polymarket/normalizer.py`.
- `kalshi/rest_client.py` `_sign()` (RSA-PSS with body-included canonical request) vs the new `polymarket/rest_client.py` `_sign()` (Ed25519 with body-excluded canonical request).
- `kalshi/websocket_client.py` `_build_ws_auth_headers()` vs the new Polymarket WS-auth implementation (handshake protocol details to be confirmed per § 10).
- `analysis/fade_signal.py` Kalshi-tweet strategy (no Polymarket sibling).
- `analysis/regime_classifier.py` series-prior tables — one table per venue, lookup function shared.

---

## 7. Safety and Operator-Gated Boundaries

The bot's existing safety posture is a **defense in depth** stack: paper-by-default, dual-trigger to live (`LIVE_TRADING_ENABLED=true` env AND `--go-live` CLI AND typed `CONFIRM` prompt at `main.py:2093,2017,2136`), per-session loss limit (`_check_live_loss_limit` in `trading/executor.py:60`), per-ticker concentration cap (`max_ticker_exposure_pct`), dynamic per-bet cap (`cfg.dynamic_max_bet(notional)` per `CLAUDE.md` env gotcha), and the global `MAX_BET_HARD_CAP` ceiling.

**Polymarket integration must extend, not weaken, every layer of this stack.** Concretely:

1. **PAPER-ONLY posture from day one — now structural, per Q1 resolution.** Per § 10.1 (Q1 resolved 2026-05-14: CONFIRMED ABSENT), no sandbox exists on the public-developer track. The first live Polymarket REST POST is by construction a live order against real bankroll. `PolymarketRestClient.insert_order` should raise `NotImplementedError` until the operator authorizes Live-readiness phase (§ 9); the paper trader simulates Polymarket fills against captured book snapshots. The original gate ("until Q1 is answered") collapses to "until the operator decides to authorize Live-readiness," since Q1 cannot be answered any other way. Q3 (price-scaling) is also now resolved (§ 10.1: trading prices on the public-developer track use the money-object envelope, not undocumented `int64` scaling) and is therefore no longer a precondition.
2. **Hard kill-switch extends, not splits.** Today's `LIVE_TRADING_ENABLED=true` continues to gate live trading globally. A new `POLYMARKET_LIVE_TRADING_ENABLED=true` adds an additional layer **only on the Polymarket route**. To trade Polymarket live, both must be true. There is no "paper Kalshi + live Polymarket" mode that bypasses the global gate.
3. **`--go-live` and typed `CONFIRM` extend.** The `_handle_go_live()` flow at `main.py:2082` becomes per-exchange: typing `CONFIRM` answers the prompt for the exchange whose name is displayed; the user must `CONFIRM POLYMARKET` (literal token) when going live on Polymarket, separately from `CONFIRM` for Kalshi. This forces operator-side intent at the exchange level.
4. **Cross-exchange position confusion structurally prevented.** Every persisted row gains an `exchange` column (§ 5 / § 6). Every executor read keys on `(exchange, market_id)` not `ticker`. The executor ingests an exchange-tagged `Candidate` and routes accordingly; an unset or unknown exchange tag hard-fails (raises) rather than defaults — fail-closed per `~/.claude/rules/risk_review.md`.
5. **Kill-switch and drift-halt sentinels are per-exchange.** `data/runtime/kalshi_drift_halt.json` (existing) and a new `data/runtime/polymarket_drift_halt.json` block their respective venues independently. A drift-halt on one venue does not halt the other; cross-exchange contagion stays an explicit operator decision.
6. **Sign-in-method coupling check at startup.** Operator runbook gains a one-line check: "have you signed in to polymarket.us via the same method (Apple/Google/email) since last key issue?" — surfaced as a bot-startup `INFO` log, not gated on. This is operator discipline, not code policy.
7. **Anchor_rate polarity block, real-mode flip authority, positive-EV gating, paper-trading support are preserved verbatim.** Per `~/.claude/rules/domain_constraints.md` triggers for `governance/`, `trading/`, and execution criteria. The Polymarket addition does not change `governance/prompts.py:27-31` and does not relax positive-EV gating in `tasks/trade_readiness_gate.py`.

---

## 8. Testing and Validation Strategy

The project's existing replay-cohort sentinel pattern (`bot_state.p0_price_fix_deployed_ts` cited in `docs/_archive/governance/2026-05-13-v030x-data-runtime-alignment-audit.md`) is the template to apply to Polymarket onboarding. The cohort sentinel is the unique tool the codebase has to keep pre-onboarding evidence from contaminating post-onboarding analytics.

**Per-exchange fixtures:** `tests/fixtures/polymarket/` carries captured payloads for each REST shape (events, markets, market-by-slug, market-book, bbo, settlement, balances, positions, trade history, preview-order, insert-order success and reject) and each WebSocket message shape. Fixtures are committed alongside their fetch URL and capture timestamp in a sidecar `.meta.json`.

**P0-gate CI pattern as template:** the existing `kalshi/normalizer.py` test suite (which validates the `dollars_fixed_point` vs `legacy_cents` contract detection and the `_invariants_hold` predicate) is mirrored in `polymarket/normalizer.py` tests with an explicit assertion that the trading-API `int64` price representation has been resolved to a known multiplier — fixtures must include the multiplier in the meta sidecar; the test reads the meta and refuses unknown multipliers.

**Signed-request tests:** offline tests construct an Ed25519 keypair, sign a known canonical request string, and assert the resulting `X-PM-Signature` round-trips through verification with the public key. This catches the body-included-vs-excluded trap (§ 3) and the timestamp-encoding-millis-vs-seconds trap (§ 2.1).

**Normalizer tests:** for every Polymarket REST response shape the test corpus exercises both the happy path and a deliberately-malformed payload (missing `bestBid`, conflicting `active=true closed=true`, `outcomePrices` JSON parse failure). Fail-closed on each.

**Paper-mode soak before live readiness:** mirror the existing 14-day Phase-2 shadow-soak posture (`PROFIT-PHASE2-001`) for Polymarket. No live Polymarket trading is authorized until the Polymarket paper trader has run continuously for at least the same 14-day window the Kalshi cycle uses, with cohort-clean post-fix-deployed-ts sentinels established and verified.

**Replay-cohort sentinel pattern applied to Polymarket onboarding boundary:** add `bot_state.polymarket_onboarding_ts` written exactly once at the moment Polymarket support lands in production. Every replay/analytics tool that exists today (`scripts/edge_replay/build_replay_dataset.main()`, `score_counterfactual_pnl`, `performance_analysis`) gains a `--exchange polymarket` filter and refuses to operate on rows that span the sentinel boundary unless explicitly asked.

**No live API calls in tests.** All Polymarket interaction in test code is fixture-driven; there are no integration tests against `api.polymarket.us`. This is consistent with the Kalshi side, which has no live integration tests in CI.

---

## 9. Implementation Roadmap

Each row references a per-module classification from § 4 in the "Class" column and an explicit `Why this assignment` cell. Phases proceed in dependency order — each phase blocks the next.

| Phase | Task | Class | Primary agent | Second-agent review required | Operator gate required | Recommended workflow | Why this assignment | Safe while bot running | Recommended execution mode | Acceptance criteria |
|---|---|---|---|---|---|---|---|---|---|---|
| Research-only | This artifact (read-only investigation; no code) | n/a | Either | No | Yes (operator decides whether to integrate roadmap) | One-agent + operator review | Already complete; deliverable is this file | Yes | Read-only | This document exists at the documented path and the operator has reviewed § 11 |
| Design | Author `PolymarketMarket` dataclass spec, `Market` Protocol, `ExchangeClient` Protocol, `Price` value type, `SettlementSource` Protocol — design doc only, no code | (b),(c) per § 4 | Either | Yes (independent design review) | Yes (operator approves protocol shape before scaffolding) | High-assurance — design changes touch persistence layout | Persistence-shape decisions are foundational and hard to undo; cross-exchange ambiguity in the persistence layer is the single largest blast-radius defect class | Yes | Read-only | Design doc landed in `docs/governance/`, second-agent independent review reconciled, operator signs off on `Market` Protocol surface |
| Safe scaffolding (config, dataclass scaffolds, no runtime path) | Add `POLYMARKET_*` env vars to `config.py`; create `polymarket/__init__.py` with the `PolymarketMarket` dataclass scaffold; add `exchange TEXT` columns to schemas guarded by feature-flag (no live writes); no executor wiring | (b)`config.py`, (b)`kalshi/__init__.py` analog | Either | Yes (cross-check no live path exists) | Yes (operator confirms feature-flag default off; reviews schema migration plan) | High-assurance — schema migration | Schema migration is irreversible without rollback work; needs operator gate on the migration window. Code path stays inert under feature flag | Yes (under feature-flag-off) | Read-only mutation of `data/paper_trades.db` schema is operator-gated | New columns exist with `NULL` allowed; no Polymarket code path is reachable from `main.py`'s default startup; existing tests still pass; Kalshi behavior bit-identical |
| Test-only (fixtures, signed-request tests, normalizer tests) | Capture `tests/fixtures/polymarket/` from doc pages (no live calls); write Ed25519 signing round-trip tests for both REST (`ts+method+path`) and WS-upgrade (`ts+"GET"+/v1/ws/private` or `/v1/ws/markets`); write `polymarket/normalizer.py` tests against fixtures asserting the money-object envelope `{value, currency}` is the only accepted trading-price shape and that bare-int64 strings fail closed (per Q3 resolution, § 10.1) | (c)`kalshi/normalizer.py` analog | Either | Yes (independence on cryptographic correctness) | No | High-assurance for the signing test; standard for fixture work | Cryptographic-primitive correctness is high-blast-radius if wrong (PROFIT-SEC-001 lesson); independent verification reduces risk | Yes | Read-only / test-only | All tests green; fixtures cover every documented REST shape; normalizer test rejects any non-money-object trading-price input |
| Paper-mode (Polymarket paper trader, exchange-tagged ledger) | Implement `polymarket/rest_client.py` with `insert_order` raising `NotImplementedError`; implement `polymarket/normalizer.py`; implement `polymarket/market_cache.py`; route a Polymarket `Candidate` through the existing `tasks/blend_task.py` path with all decision logic shared; record paper trades into the exchange-tagged ledger | (c)`kalshi/rest_client.py` analog, (c)`kalshi/normalizer.py` analog, (b)`tasks/blend_task.py`, (c)`trading/paper_trader.py`, (c)`trading/portfolio.py` | Codex if Claude Code did Design; otherwise Claude Code | Yes (independence; persistence-layer changes) | Yes (operator schedules bot bounce for the schema migration) | High-assurance — first runtime path that exercises the new schema | Live-path-adjacent code; touches portfolio bookkeeping; the silent-attrition debugging from F-06 (per debt log §2.0) underlines how easily a bookkeeping defect goes unnoticed | Requires bounce | Operator-gated (bot bounce) | Polymarket paper trades visible in `paper_trades` with `exchange='polymarket'`; existing Kalshi rows untouched; replay tools refuse to mix cohorts |
| Live-readiness (kill-switch wiring, two-sided EV equivalence) | Wire `POLYMARKET_LIVE_TRADING_ENABLED` gate; extend `--go-live` and `CONFIRM POLYMARKET` flow; replace `insert_order` `NotImplementedError` with the real Ed25519-signed implementation; verify two-sided EV equivalence between Kalshi (cents) and Polymarket (money-object decimal-dollar to `Price` value type) on identical synthetic candidates | (c)`trading/executor.py`, (b)`main.py`, (c)`kalshi/rest_client.py` analog | Codex if Claude Code did Paper-mode; otherwise Claude Code | Yes (independence — money-movement-adjacent) | Yes (operator approval and explicit live-flag flip) | High-assurance + dual-agent audit per `~/.claude/rules/agent_collaboration.md` "moves system from paper into live" trigger | Money-movement-adjacent code path; this is the canonical case for two-agent independent verification + operator gate. **No sandbox exists per Q1 resolution (§ 10.1), so the operator-executed first-touch protocol in § 2.13 replaces the conventional sandbox-or-equivalent verification step.** | Requires bounce | Operator-gated, full kill-switch in place | Live path is gated behind both `LIVE_TRADING_ENABLED=true` AND `POLYMARKET_LIVE_TRADING_ENABLED=true` AND typed `CONFIRM POLYMARKET`; operator-executed first-touch protocol (§ 2.13) has been walked through; two-sided EV equivalence proven on synthetic candidates |
| Operator-gated (paper-to-live cutover; never agent-executed) | Operator flips `POLYMARKET_LIVE_TRADING_ENABLED=true`, runs `python main.py --go-live`, types `CONFIRM POLYMARKET`. First Polymarket live order is placed only with operator at the keyboard. | n/a — operator action | Operator | n/a | Yes (operator owns) | Operator-only | Per `~/.claude/rules/agent_collaboration.md` trigger "scheduled jobs, service behavior, external side effects, or safety-critical outcomes" — agent-executed cutover is explicitly forbidden | Bot is running normally; cutover happens during a scheduled window | Operator interactive | First Polymarket live order resolves cleanly; Kalshi behavior bit-identical pre/post cutover; rollback path verified |

---

## 10. Open Questions and Unknowns

### 10.1 Resolved on 2026-05-14

The following bullets were resolved by the Q1-Q3 resolution pass on 2026-05-14. They are retained here in their final form so a future reader can audit what was learned and where.

- **Q1 (was: sandbox / testnet / paper) — RESOLVED: CONFIRMED ABSENT.** No sandbox, testnet, or paper environment is documented for the public-developer Ed25519 track. The only multi-environment matrix (`https://docs.polymarket.us/trader-guide/environments.md`) is institutional-only (Auth0/JWT, on `polymarketexchange.com` hosts). The public-developer reference (`https://docs.polymarket.us/api-reference/introduction.md`), authentication page (`https://docs.polymarket.us/api-reference/authentication.md`), and quickstart (`https://docs.polymarket.us/getting-started/quickstart.md`) all reference only the live host `https://api.polymarket.us`. The body of § 2.13 is fully rewritten with the supporting evidence and a recommended first-touch protocol. Architectural consequence: PAPER-ONLY posture from day one (§ 7.1) becomes a structural necessity, and § 7's first-touch protocol must be operator-executed at the keyboard.
- **Q3 (was: trading int64 price scaling) — RESOLVED: DOWNGRADED FROM HEADLINE RISK.** On the public-developer Ed25519 track, trading prices use the same `{value, currency}` money-object envelope as the WebSocket market-data and WebSocket private streams. The Python and TypeScript SDK quickstarts both show verbatim `"price": {"value": "0.55", "currency": "USD"}` (sources: `https://docs.polymarket.us/api-reference/sdks/python/quickstart.md`, `https://docs.polymarket.us/api-reference/sdks/typescript/quickstart.md`). The `int64`-string price in the OpenAPI fragment under `https://docs.polymarket.us/api-reference/trading/insert-order.md` belongs to the institutional `trading-schema.json` with declared server `https://api.prod.polymarketexchange.com` (Auth0 track), not to the public-developer host `api.polymarket.us`. The silent-50 contamination class is therefore contained to the institutional track. The body of § 2.6 is fully rewritten with the SDK code snippets, the institutional-track scoping evidence, and a narrowed mitigation summary.
- **Q7 (was: WebSocket auth handshake protocol) — RESOLVED: HANDSHAKE-HEADERS, NOT POST-CONNECT-AUTH.** Verbatim from `https://docs.polymarket.us/api-reference/websocket/overview.md`:
  ```
  X-PM-Access-Key: <your-api-key-id>
  X-PM-Timestamp: <timestamp-in-milliseconds>
  X-PM-Signature: <base64-encoded-signature>
  ```
  and the page text "Include these headers in the connection handshake" plus "WebSocket connections use the same API key authentication as the REST API." Both `https://docs.polymarket.us/api-reference/websocket/private.md` and `https://docs.polymarket.us/api-reference/websocket/markets.md` repeat the warning "This WebSocket endpoint requires API key authentication in the connection handshake." There is no post-connect auth-message step. Subscribe is a post-connect JSON command; heartbeats are server-sent; reconnect is exponential-backoff with client-owned subscription state. The body of § 2.12 is fully rewritten with the verbatim header list, the inherited 30-second clock-skew constraint, and a port-from-Kalshi note (signature primitive and header names change; handshake topology is identical).

### 10.2 Confirmed-blocked on 2026-05-14

The Q1-Q3 resolution pass also touched the remaining open questions. None of Q2, Q4, Q5, Q6, Q8, Q9, Q10 was resolved by this pass; they are retained in § 10.3 as still-open and reaffirmed as gap-only-resolvable.

### 10.3 Still open

- Q2 (CONFIRMED-BLOCKED 2026-05-14). What is the documented numeric per-second / per-minute REST rate limit for the public-developer track? `https://docs.polymarket.us/trader-guide/error-handling.md` mentions pagination guidance but no number; the gRPC overview gives 100 msg/s but only for the institutional streaming track. **Gap:** the public-developer reference (`/api-reference/`) does not publish a numeric limit on any page in the fetched corpus. **Resolution evidence needed:** a vendor confirmation, a documented 429 retry-after header value in a worked example, or a numeric figure on a future revision of the error-handling or rate-limits page.
- Q4 (CONFIRMED-BLOCKED 2026-05-14). What is the documented minimum order size in dollars? `https://docs.polymarket.us/concepts/orders.md` discusses limit vs market without a minimum-notional figure. **Gap:** no `minSize` / `minNotional` / `minOrderValue` field appears on the `v1Market` schema or on the public-developer order-create surface (the `minQty` in the SDK quickstart is per-order minimum-quantity for IOC, not a venue-wide minimum). **Resolution evidence needed:** a numeric figure in concepts/orders, in a per-market schema field, or in a "Trading Limits" page.
- Q5 (CONFIRMED-BLOCKED 2026-05-14). Is there a documented idempotency-key header beyond the `clordId` body field, and what is the server's idempotency-window TTL? `https://docs.polymarket.us/api-reference/trading/insert-order.md` exposes `clordId: string` but does not state the dedupe window. **Gap:** the institutional OpenAPI fragment surfaces `clordId` without a TTL; the public-developer-track SDK orders pages do not surface an `Idempotency-Key` header. **Resolution evidence needed:** an explicit dedupe-window value or a documented header.
- Q6 (CONFIRMED-BLOCKED 2026-05-14). What does the `fromEp3: boolean` query parameter on `GET /v1/markets/{slug}/settlement` do? `https://docs.polymarket.us/api-reference/markets/get-market-settlement.md` defines it as "Whether to get settlement from EP3" without expanding the acronym. **Gap:** "EP3" is undefined in the docs glossary (`https://docs.polymarket.us/getting-started/glossary.md` per llms.txt index — not exhaustively walked in this pass). **Resolution evidence needed:** a glossary entry or a worked example showing the difference between `fromEp3=true` and the default.
- Q8 (CONFIRMED-BLOCKED 2026-05-14). Is the modify-order endpoint live or removed? `https://docs.polymarket.us/api-reference/trading/modify-order.md` returned HTTP 404 on fetch; the `llms.txt` index does not list it under `trading/`. **Gap:** there is no canonical "deprecated endpoints" or "removed endpoints" listing. **Resolution evidence needed:** a changelog entry, a docs link, or a vendor confirmation that modify-order is replaced by cancel-and-replace.
- Q9 (CONFIRMED-BLOCKED 2026-05-14). What is the documented key-rotation procedure for the public-developer track? `https://docs.polymarket.us/trader-guide/authentication.md` documents rotation for the institutional JWT path; `https://docs.polymarket.us/api-reference/authentication.md` does not. **Gap:** the public-developer page documents revoke + regenerate at `polymarket.us/developer` without a zero-downtime rotation pattern. **Resolution evidence needed:** a documented rotation procedure (e.g., "previous key remains valid for N hours after a new key is issued") or a vendor statement that rotation is hard-cutover.
- Q10 (CONFIRMED-BLOCKED 2026-05-14). What is the policy on simultaneous active API keys per account, and the activation lag after key issue? Neither `https://docs.polymarket.us/api-reference/authentication.md` nor `https://docs.polymarket.us/trader-guide/authentication-troubleshooting.md` says explicitly. **Gap:** no figure for "max concurrent keys" or "key activation propagation time". **Resolution evidence needed:** a documented limit or a vendor statement.

---

## 11. Recommended Next Steps

Three-bullet operator-decision summary.

- **Accept or reject the roadmap shape.** The shape proposed in § 5 — per-exchange adapters behind shared `Market` and `ExchangeClient` protocols, with `exchange` discriminator on every persisted row — is the recommendation. Rejection alternatives include (i) "fork the codebase per venue", which trades complexity for duplicated bug surface, or (ii) "wait until Polymarket US publishes a sandbox", which is now (per Q1 resolution, § 10.1) explicitly an **indefinite-wait option**: no public-developer sandbox is documented, and no roadmap entry in `https://docs.polymarket.us/changelog.md` indicates one is imminent. The operator must choose between waiting indefinitely and adopting the first-touch protocol in § 2.13. Operator decision needed before any Design-phase work begins.
- **Authorize the first research-only task.** That task is the **Design** phase row of § 9: independent dual-agent design review of the `Market` Protocol, `ExchangeClient` Protocol, `Price` value type, and `SettlementSource` Protocol. No code changes; the deliverable is a design document landed in `docs/governance/`. Estimated cost: one design pass, one independent review pass, one reconciliation pass.
- **Authorize the debt-log pointer.** Recommended pointer text for the operator to add to `docs/profit_path_debt_log.md` Current Status section if and when this investigation graduates from research to active work:

  > **Polymarket US integration investigation (2026-05-14):** scoped planning artifact in this file. Reuse classification distribution: 6 (a) / 12 (b) / 6 (c) / 1 (d) across 25 modules. Q1-Q3 resolution pass (2026-05-14): no public-developer sandbox exists (§ 2.13 / § 10.1 Q1); public-developer trading prices use a money-object envelope, not undocumented `int64` scaling (§ 2.6 / § 10.1 Q3); WebSocket auth is signed `X-PM-*` headers on the HTTP upgrade (§ 2.12 / § 10.1 Q7). Remaining gating risk: operator-executed first-touch protocol on live host since no sandbox exists. Status: research-only; awaiting operator decision on Design-phase authorization.

  Per `CLAUDE.md` R-10, the artifact above is the sanctioned single landing surface for this work; the debt-log line is a pointer, not a parallel tracker. The pointer is added by the operator, not the agent.
