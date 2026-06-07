# Polymarket Trading Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Polymarket US as a second trading venue in `vscode/kalshi-bot`, paper-first and binary-market-only, while preserving Kalshi behavior and all live-trading safety gates.

**Architecture:** Introduce a venue-neutral execution boundary in `trading/`, keep `kalshi/` unchanged behind an adapter, add a peer `polymarket/` package for Polymarket US Retail API contract fixtures, public gateway market data, authenticated account/order surfaces, and paper-only accounting. The first mergeable increments are contract capture, read-only public market data, secret-safe authenticated account probes, and paper-only venue accounting; live Polymarket order placement remains hard-gated behind a separate operator-approved phase.

**Tech Stack:** Python 3.14 in CI/local venv, `requests`, `websockets`, `cryptography` Ed25519, SQLite, pytest, ruff, existing `KalshiMarket`/`SignalAnalysis` pipeline with new venue-neutral wrappers.

---

## Current Context / Assumptions

- Repo inspected: `/Users/jacobparenti/vscode/kalshi-bot`.
- Existing runtime is paper-first, with live gated by `LIVE_TRADING_ENABLED=false` and explicit `--go-live` flow.
- Existing project safety rules classify `trading/`, executor logic, bankroll, paper/live transitions, DB mutation, and live-state changes as high-risk. Every implementation PR here needs adversarial review and operator approval before merge/restart.
- Existing `.env.example` already documents Polymarket US as the relevant path, not Global CLOB:
  - `POLYMARKET_US_KEY_ID`
  - `POLYMARKET_US_SECRET`
  - Ed25519 request signing headers: `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature`
  - Older archived REST/WebSocket caps that must be replaced by the current official contract fixture before client code is implemented.
- Archived investigation says Polymarket Global is not compliant for US residents and should not be implemented for this operator. This plan targets Polymarket US only.
- `.env.example` says waitlist cleared / account provisioned as of 2026-05-10, but implementation must still fail closed unless credentials are present.
- Code-review-graph MCP tools were requested by the repo-level context, but no `code-review-graph` tool namespace was available in this Hermes session. I used read-only file inspection instead.
- Existing user worktree is dirty (`main.py`, `trading/paper_trader.py`, tests, runtime logs/state). Implementation agents must not overwrite those changes without reviewing ownership.
- Readiness review repaired in this revision: implementation now starts with official API contract capture, public/authenticated client split, paper accounting definitions, venue-aware executor refetch, and explicit secret/eligibility gates.
- Current official Polymarket US docs to capture before code:
  - Authenticated endpoints for trading, portfolio, and WebSocket require API keys; public market data and events do not.
  - Raw auth signs `timestamp + method + path` with Ed25519 and uses millisecond timestamps within 30 seconds of server time.
  - Public market data is served from `https://gateway.polymarket.us`; authenticated account/order endpoints use `https://api.polymarket.us`.
  - Rate limits are not the old single 60 req/min value. Public unauthenticated endpoints are 20 req/sec per IP; trading REST is firm-wide 100 req/sec averaged over 1 minute; query/report endpoints have lower endpoint-specific caps such as 60 req/min, 12 req/min, 6 req/min, and ~0.5 req/min.

## Non-Goals

- Do not implement Polymarket Global / `clob.polymarket.com` trading.
- Do not support multi-outcome markets in the first integration. Filter to binary YES/NO markets only.
- Do not place live Polymarket orders in the initial integration.
- Do not lower readiness gates, freshness gates, match thresholds, or bankroll caps to generate volume.
- Do not restart launchd or change production runtime state during implementation unless the operator explicitly approves.

## Proposed Phases

0. **Contract capture and drift fixtures.** Capture current official Retail API docs into checked-in fixtures/specs before client code.
1. **Venue-neutral foundation, Kalshi-only behavior preserved.** Add abstractions and venue fields with defaults/backfills to `kalshi`.
2. **Polymarket US public read-only client.** Fetch public market data from the gateway without credentials; normalize binary markets only.
3. **Polymarket US authenticated account client.** Add secret-safe auth, balance/positions probes, and hard-gated order methods; no live order placement.
4. **Paper accounting integration.** Persist venue, Polymarket market/side IDs, fees, tick size, settlement source, and net PnL fields before execution smoke tests.
5. **Venue-aware execution path.** Route selected Polymarket candidates into existing gates in paper mode through an explicit venue client/refetch boundary.
6. **Live enable branch.** Separate future branch after paper evidence and operator approval.

---

## Files Likely to Change

### Create

- `.hermes/api_contracts/polymarket_us_retail_contract.md` — hand-captured official endpoint/auth/rate-limit contract.
- `tests/fixtures/polymarket_us/contract_snapshot.json` — machine-readable fixture generated from official docs and developer portal/account checks.
- `trading/venue.py` — venue constants/types and helpers.
- `trading/venue_client.py` — protocol for venue market data / orders / balance.
- `polymarket/__init__.py` — package exports.
- `polymarket/auth.py` — Ed25519 signing helpers for authenticated Retail API paths.
- `polymarket/models.py` — normalized Polymarket market/order dataclasses.
- `polymarket/public_client.py` — unauthenticated gateway market/event data client.
- `polymarket/account_client.py` — authenticated account/order client; order method hard-gated initially.
- `polymarket/normalizer.py` — parse API payloads into normalized binary market models.
- `polymarket/websocket_client.py` — optional market-data WS in later task.
- `tests/test_venue_types.py`
- `tests/test_polymarket_contract_snapshot.py`
- `tests/test_polymarket_auth.py`
- `tests/test_polymarket_normalizer.py`
- `tests/test_polymarket_public_client.py`
- `tests/test_polymarket_account_client.py`
- `tests/test_paper_trader_venue.py`
- `tests/test_polymarket_paper_accounting.py`
- `tests/test_executor_venue.py`

### Modify

- `config.py` — Polymarket env fields, live-gate fields, rate-limit config.
- `.env.example` — move Polymarket vars from “not yet wired” to active optional config.
- `requirements.txt` — only if a new dependency is genuinely needed. Prefer existing `cryptography` Ed25519 first.
- `kalshi/__init__.py` — add venue default field or adapter compatibility if needed.
- `kalshi/rest_client.py` — add explicit no-op/default venue protocol methods when Task 2 tests require them; do not change signing.
- `trading/portfolio.py` — add venue to `Position`; venue-aware open-position queries.
- `trading/paper_trader.py` — add `venue` column, backfill `kalshi`, record/query venue.
- `trading/executor.py` — depend on venue protocol / venue field; keep Kalshi behavior identical.
- `tasks/blend_task.py` — include venue in `TradeCandidate`/signal metadata if candidates can be non-Kalshi.
- `analysis/market_matcher.py` — later phase only: venue-specific market cache or polymarket matcher.
- `utils/logger.py` — include `venue` in relevant trade log records.
- `scripts/daily_review.py`, `scripts/performance_analysis.py`, `tests/test_paper_performance_drilldown.py` — venue dimension in reporting.
- `README.md`, `CHANGELOG.md`, `VERSION` — behavior-changing PR only, per repo rule.

---

## Step-by-Step Implementation Plan

### Task 0: Capture Polymarket US contract and drift fixtures

**Objective:** Freeze the current official Polymarket US Retail API contract before implementing client code, so tests fail when archived assumptions drift from the live docs.

**Files:**
- Create: `.hermes/api_contracts/polymarket_us_retail_contract.md`
- Create: `tests/fixtures/polymarket_us/contract_snapshot.json`
- Test: `tests/test_polymarket_contract_snapshot.py`

- [ ] **Step 1: Write the failing contract snapshot test**

```python
# tests/test_polymarket_contract_snapshot.py
from __future__ import annotations

import json
from pathlib import Path


SNAPSHOT = Path("tests/fixtures/polymarket_us/contract_snapshot.json")
DOC = Path(".hermes/api_contracts/polymarket_us_retail_contract.md")


def test_polymarket_us_contract_snapshot_is_present_and_currently_reviewed():
    data = json.loads(SNAPSHOT.read_text())

    assert data["reviewed_utc"].startswith("2026-06-07T")
    assert data["docs"]["authentication"] == "https://docs.polymarket.us/api-reference/authentication"
    assert data["docs"]["markets_get_markets"] == "https://docs.polymarket.us/api-reference/markets/get-markets"
    assert data["public_market_data"]["base_url"] == "https://gateway.polymarket.us"
    assert data["public_market_data"]["get_markets_path"] == "/v1/markets"
    assert data["authenticated"]["base_url"] == "https://api.polymarket.us"
    assert data["authenticated"]["portfolio_positions_path"] == "/v1/portfolio/positions"
    assert data["auth"]["signature_message"] == "timestamp + method + path"
    assert data["auth"]["timestamp_unit"] == "milliseconds"
    assert data["auth"]["timestamp_skew_seconds"] == 30
    assert data["rate_limits"]["public_unauthenticated"] == "20 req/sec per IP"
    assert data["rate_limits"]["trading_rest"] == "100 req/sec per firm averaged over 1 minute"
    assert data["rate_limits"]["query_report_endpoints"]["GetBBO"] == "12 req/min"
    assert data["rate_limits"]["query_report_endpoints"]["ListPositionValuations"] == "~0.5 req/min"


def test_polymarket_us_contract_markdown_matches_snapshot_urls():
    text = DOC.read_text()
    data = json.loads(SNAPSHOT.read_text())

    for url in data["docs"].values():
        assert url in text
    assert "Do not use Global CLOB (`clob.polymarket.com`) for this operator" in text
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_contract_snapshot.py -v`

Expected: FAIL because the fixture and contract doc do not exist.

- [ ] **Step 3: Add checked-in contract snapshot**

```json
{
  "reviewed_utc": "2026-06-07T00:00:00Z",
  "docs": {
    "introduction": "https://docs.polymarket.us/api-reference/introduction",
    "authentication": "https://docs.polymarket.us/api-reference/authentication",
    "markets_get_markets": "https://docs.polymarket.us/api-reference/markets/get-markets",
    "rate_limits": "https://docs.polymarket.us/trader-guide/rate-limits",
    "orders_overview": "https://docs.polymarket.us/api-reference/orders/overview"
  },
  "public_market_data": {
    "base_url": "https://gateway.polymarket.us",
    "get_markets_path": "/v1/markets",
    "auth_required": false
  },
  "authenticated": {
    "base_url": "https://api.polymarket.us",
    "portfolio_positions_path": "/v1/portfolio/positions",
    "account_balances_path": "/v1/account/balances",
    "orders_path": "/v1/orders",
    "auth_required": true
  },
  "auth": {
    "headers": ["X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature"],
    "signature_message": "timestamp + method + path",
    "timestamp_unit": "milliseconds",
    "timestamp_skew_seconds": 30,
    "secret_decode": "base64 decode secret, use first 32 bytes as Ed25519 private key bytes"
  },
  "rate_limits": {
    "public_unauthenticated": "20 req/sec per IP",
    "trading_rest": "100 req/sec per firm averaged over 1 minute",
    "query_report_endpoints": {
      "GetTradeStats": "60 req/min",
      "ListInstruments": "6 req/min",
      "ListSymbols": "6 req/min",
      "GetOrderBook": "12 req/min",
      "GetBBO": "12 req/min",
      "SearchOrders": "12 req/min",
      "SearchExecutions": "12 req/min",
      "SearchTrades": "12 req/min",
      "ListPositionValuations": "~0.5 req/min"
    },
    "grpc_streaming": "100 ingress msg/sec per firm averaged over 1 minute",
    "fix": "150 ingress msg/sec per session"
  },
  "implementation_rules": [
    "Use public gateway for market/event data without credentials.",
    "Use authenticated API only for portfolio/account/order endpoints.",
    "Never place live Polymarket orders in this plan.",
    "Re-check docs and account eligibility on the day any enablement flag changes."
  ]
}
```

Save as `tests/fixtures/polymarket_us/contract_snapshot.json`.

- [ ] **Step 4: Add human-readable contract note**

```markdown
# Polymarket US Retail API Contract Snapshot

Reviewed: 2026-06-07

Sources:

- https://docs.polymarket.us/api-reference/introduction
- https://docs.polymarket.us/api-reference/authentication
- https://docs.polymarket.us/api-reference/markets/get-markets
- https://docs.polymarket.us/trader-guide/rate-limits
- https://docs.polymarket.us/api-reference/orders/overview

Implementation rules:

- Do not use Global CLOB (`clob.polymarket.com`) for this operator.
- Use `https://gateway.polymarket.us` for public market/event data that does not require credentials.
- Use `https://api.polymarket.us` for authenticated trading, portfolio, and WebSocket endpoints.
- Raw request auth signs `timestamp + method + path` with Ed25519. Do not include request body in the signature unless official docs change and this snapshot is updated.
- `X-PM-Timestamp` is milliseconds and must be within 30 seconds of server time.
- Decode `POLYMARKET_US_SECRET` from base64 and pass the first 32 bytes to `Ed25519PrivateKey.from_private_bytes`.
- Public unauthenticated calls are capped at 20 req/sec per IP.
- Authenticated trading REST is capped at 100 req/sec per firm averaged over 1 minute, with lower query/report endpoint caps.
- Every implementation PR that changes Polymarket API paths, auth, rate limits, or order behavior must update this file and `tests/fixtures/polymarket_us/contract_snapshot.json` in the same commit.
```

Save as `.hermes/api_contracts/polymarket_us_retail_contract.md`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_contract_snapshot.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .hermes/api_contracts/polymarket_us_retail_contract.md tests/fixtures/polymarket_us/contract_snapshot.json tests/test_polymarket_contract_snapshot.py
git commit -m "docs: capture polymarket us api contract"
```

### Task 1: Add venue type helpers

**Objective:** Establish a tiny venue namespace with no behavior change.

**Files:**
- Create: `trading/venue.py`
- Test: `tests/test_venue_types.py`

**Step 1: Write failing test**

```python
# tests/test_venue_types.py
import pytest

from trading.venue import Venue, namespaced_market_id, split_namespaced_market_id


def test_namespaced_market_id_round_trips():
    value = namespaced_market_id(Venue.KALSHI, "KXTEST-25DEC31")

    assert value == "kalshi:KXTEST-25DEC31"
    assert split_namespaced_market_id(value) == (Venue.KALSHI, "KXTEST-25DEC31")


def test_split_rejects_unknown_venue():
    with pytest.raises(ValueError, match="unsupported venue"):
        split_namespaced_market_id("unknown:abc")
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_venue_types.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'trading.venue'`.

**Step 3: Write minimal implementation**

```python
# trading/venue.py
from __future__ import annotations

from enum import StrEnum


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET_US = "polymarket_us"


def normalize_venue(value: str | Venue) -> Venue:
    if isinstance(value, Venue):
        return value
    try:
        return Venue(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(f"unsupported venue: {value!r}") from exc


def namespaced_market_id(venue: str | Venue, market_id: str) -> str:
    venue_value = normalize_venue(venue).value
    market = str(market_id or "").strip()
    if not market:
        raise ValueError("market_id is required")
    return f"{venue_value}:{market}"


def split_namespaced_market_id(value: str) -> tuple[Venue, str]:
    text = str(value or "").strip()
    if ":" not in text:
        raise ValueError(f"namespaced market id must contain ':', got {value!r}")
    venue_text, market_id = text.split(":", 1)
    venue = normalize_venue(venue_text)
    if not market_id:
        raise ValueError("market_id is required")
    return venue, market_id
```

**Step 4: Run test to verify pass**

Run: `.venv/bin/python -m pytest tests/test_venue_types.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add trading/venue.py tests/test_venue_types.py
git commit -m "feat: add venue namespace helpers"
```

---

### Task 2: Add venue protocol without changing executor behavior

**Objective:** Define the shape expected from venue clients while keeping `KalshiRestClient` usable.

**Files:**
- Create: `trading/venue_client.py`
- Test: `tests/test_venue_client_protocol.py`

**Step 1: Write failing test**

```python
# tests/test_venue_client_protocol.py
from typing import runtime_checkable

from kalshi.rest_client import KalshiRestClient
from trading.venue_client import VenueClient


def test_kalshi_rest_client_has_venue_client_methods():
    required = ["get_market", "get_markets", "get_balance", "place_limit_order"]

    for name in required:
        assert hasattr(KalshiRestClient, name)


def test_venue_client_is_runtime_checkable_protocol():
    assert getattr(VenueClient, "_is_runtime_protocol", False) is True
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_venue_client_protocol.py -v`

Expected: FAIL — missing `trading.venue_client`.

**Step 3: Write minimal implementation**

```python
# trading/venue_client.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from kalshi import KalshiMarket, OrderResult
from trading.venue import Venue


@runtime_checkable
class VenueClient(Protocol):
    venue: Venue

    def get_market(self, market_id: str) -> KalshiMarket | None: ...

    def get_markets(self, **kwargs) -> tuple[list[KalshiMarket], str | None]: ...

    def get_balance(self) -> float: ...

    def place_limit_order(
        self,
        *,
        ticker: str,
        side: str,
        count: int,
        limit_price: int,
        expiration_ts: int | None = None,
    ) -> OrderResult: ...
```

**Step 4: Add `venue` property to Kalshi client**

Modify `kalshi/rest_client.py`:

```python
from trading.venue import Venue
```

Inside `KalshiRestClient`:

```python
    venue = Venue.KALSHI
```

Do not alter Kalshi RSA-PSS signing.

**Step 5: Run test to verify pass**

Run: `.venv/bin/python -m pytest tests/test_venue_client_protocol.py tests/test_kalshi_signing_failfast.py -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add trading/venue_client.py kalshi/rest_client.py tests/test_venue_client_protocol.py
git commit -m "refactor: define venue client protocol"
```

---

### Task 3: Add Polymarket config fields, fail-closed by default

**Objective:** Make Polymarket US public/authenticated config readable from `cfg` without enabling runtime behavior.

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Test: `tests/test_polymarket_config.py`

**Step 1: Write failing tests**

```python
# tests/test_polymarket_config.py
import pytest

import config as config_module


def _valid_rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def test_polymarket_config_defaults_disabled():
    cfg = config_module.BotConfig(
        api_key_id="kalshi-key",
        api_key_secret=_valid_rsa_pem(),
    )

    assert cfg.polymarket_us_enabled is False
    assert cfg.polymarket_us_live_trading_enabled is False
    assert cfg.polymarket_us_public_base_url == "https://gateway.polymarket.us"
    assert cfg.polymarket_us_api_base_url == "https://api.polymarket.us"
    assert cfg.polymarket_us_public_requests_per_second == 20
    assert cfg.polymarket_us_order_requests_per_second == 100
    assert cfg.polymarket_us_bbo_requests_per_minute == 12
    assert cfg.polymarket_us_position_valuation_requests_per_minute == 0.5


def test_polymarket_enabled_requires_credentials(monkeypatch):
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
    monkeypatch.delenv("POLYMARKET_US_SECRET", raising=False)

    with pytest.raises(SystemExit):
        config_module.BotConfig(
            api_key_id="kalshi-key",
            api_key_secret=_valid_rsa_pem(),
        )
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_config.py -v`

Expected: FAIL — fields missing.

**Step 3: Implement config fields**

Add to `BotConfig` near existing Kalshi credentials/live fields:

```python
    polymarket_us_enabled: bool = field(
        default_factory=lambda: _parse_bool_env("POLYMARKET_US_ENABLED", "false")
    )
    polymarket_us_key_id: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_KEY_ID", "").strip()
    )
    polymarket_us_secret: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_SECRET", "").strip()
    )
    polymarket_us_public_base_url: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_PUBLIC_BASE_URL", "https://gateway.polymarket.us").rstrip("/")
    )
    polymarket_us_api_base_url: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_API_BASE_URL", "https://api.polymarket.us").rstrip("/")
    )
    polymarket_us_live_trading_enabled: bool = field(
        default_factory=lambda: _parse_bool_env("POLYMARKET_US_LIVE_TRADING_ENABLED", "false")
    )
    polymarket_us_public_requests_per_second: int = field(
        default_factory=lambda: int(os.getenv("POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND", "20"))
    )
    polymarket_us_order_requests_per_second: int = field(
        default_factory=lambda: int(os.getenv("POLYMARKET_US_ORDER_REQUESTS_PER_SECOND", "100"))
    )
    polymarket_us_bbo_requests_per_minute: int = field(
        default_factory=lambda: int(os.getenv("POLYMARKET_US_BBO_REQUESTS_PER_MINUTE", "12"))
    )
    polymarket_us_position_valuation_requests_per_minute: float = field(
        default_factory=lambda: float(os.getenv("POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE", "0.5"))
    )
```

In `__post_init__`, append errors only when relevant:

```python
        if self.polymarket_us_public_requests_per_second <= 0:
            errors.append("POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND must be positive")
        if self.polymarket_us_order_requests_per_second <= 0:
            errors.append("POLYMARKET_US_ORDER_REQUESTS_PER_SECOND must be positive")
        if self.polymarket_us_bbo_requests_per_minute <= 0:
            errors.append("POLYMARKET_US_BBO_REQUESTS_PER_MINUTE must be positive")
        if self.polymarket_us_position_valuation_requests_per_minute <= 0:
            errors.append("POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE must be positive")
        if self.polymarket_us_enabled:
            if not self.polymarket_us_key_id:
                errors.append("POLYMARKET_US_KEY_ID is required when POLYMARKET_US_ENABLED=true")
            if not self.polymarket_us_secret:
                errors.append("POLYMARKET_US_SECRET is required when POLYMARKET_US_ENABLED=true")
```

**Step 4: Update `.env.example`**

Change the Polymarket block from “NOT YET WIRED” to “optional, disabled by default”, keeping credentials commented except the enable flag:

```dotenv
# -- Polymarket US API (OPTIONAL; disabled by default) ------------------------
POLYMARKET_US_ENABLED=false
# POLYMARKET_US_KEY_ID=uuid-from-developer-portal
# POLYMARKET_US_SECRET=base64-encoded-ed25519-private-key
POLYMARKET_US_PUBLIC_BASE_URL=https://gateway.polymarket.us
POLYMARKET_US_API_BASE_URL=https://api.polymarket.us
POLYMARKET_US_LIVE_TRADING_ENABLED=false
POLYMARKET_US_PUBLIC_REQUESTS_PER_SECOND=20
POLYMARKET_US_ORDER_REQUESTS_PER_SECOND=100
POLYMARKET_US_BBO_REQUESTS_PER_MINUTE=12
POLYMARKET_US_POSITION_VALUATION_REQUESTS_PER_MINUTE=0.5
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_config.py tests/test_main_startup.py -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add config.py .env.example tests/test_polymarket_config.py
git commit -m "feat: add disabled polymarket us config"
```

---

### Task 4: Implement Ed25519 request signing helper

**Objective:** Sign Polymarket US requests without touching Kalshi signing.

**Files:**
- Create: `polymarket/__init__.py`
- Create: `polymarket/auth.py`
- Test: `tests/test_polymarket_auth.py`

**Step 1: Write failing tests**

```python
# tests/test_polymarket_auth.py
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from polymarket.auth import PolymarketAuth


def _secret_b64() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


def test_auth_headers_include_required_fields(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1710000000.123)
    auth = PolymarketAuth(key_id="key-123", secret_b64=_secret_b64())

    headers = auth.headers("GET", "/v1/portfolio/positions")

    assert headers["X-PM-Access-Key"] == "key-123"
    assert headers["X-PM-Timestamp"] == "1710000000123"
    assert headers["X-PM-Signature"]


def test_auth_rejects_invalid_secret():
    try:
        PolymarketAuth(key_id="key-123", secret_b64="not-base64")
    except ValueError as exc:
        assert "invalid Polymarket US Ed25519 secret" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_auth_error_does_not_leak_secret():
    secret = "not-base64-secret"
    try:
        PolymarketAuth(key_id="key-123", secret_b64=secret)
    except ValueError as exc:
        assert secret not in str(exc)
        assert "invalid Polymarket US Ed25519 secret" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_auth.py -v`

Expected: FAIL — missing module.

**Step 3: Implement signing helper**

```python
# polymarket/auth.py
from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class PolymarketAuth:
    def __init__(self, *, key_id: str, secret_b64: str):
        self._key_id = key_id.strip()
        try:
            raw = base64.b64decode(secret_b64, validate=True)[:32]
            self._private_key = Ed25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise ValueError("invalid Polymarket US Ed25519 secret") from exc
        if not self._key_id:
            raise ValueError("Polymarket US key_id is required")

    def headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        # Official Retail API raw auth signs timestamp + method + path.
        message = (ts + method.upper() + path).encode("utf-8")
        signature = self._private_key.sign(message)
        return {
            "X-PM-Access-Key": self._key_id,
            "X-PM-Timestamp": ts,
            "X-PM-Signature": base64.b64encode(signature).decode("ascii"),
            "Content-Type": "application/json",
        }
```

```python
# polymarket/__init__.py
"""Polymarket US client package."""
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_auth.py tests/test_kalshi_signing_failfast.py -v`

Expected: PASS. Kalshi signing tests must still pass.

**Step 5: Commit**

```bash
git add polymarket/__init__.py polymarket/auth.py tests/test_polymarket_auth.py
git commit -m "feat: add polymarket us ed25519 auth"
```

---

### Task 4B: Add Polymarket secret hygiene and enablement preflight

**Objective:** Prove Polymarket credentials never leak to logs/reports/errors and that runtime enablement fails closed unless same-day eligibility is acknowledged.

**Files:**
- Modify: `config.py`
- Create: `polymarket/security.py`
- Test: `tests/test_polymarket_security.py`

- [ ] **Step 1: Write failing security tests**

```python
# tests/test_polymarket_security.py
from __future__ import annotations

from datetime import date

import pytest

import config as config_module
from polymarket.security import redact_polymarket_secret, require_polymarket_enablement_preflight
from tests.test_polymarket_config import _valid_rsa_pem


def test_redact_polymarket_secret_removes_key_material():
    secret = "base64-secret-value"
    message = f"failed auth with {secret}"

    redacted = redact_polymarket_secret(message, secret)

    assert secret not in redacted
    assert "[REDACTED_POLYMARKET_SECRET]" in redacted


def test_enabled_runtime_requires_same_day_eligibility_ack(monkeypatch):
    monkeypatch.setenv("POLYMARKET_US_ENABLED", "true")
    monkeypatch.setenv("POLYMARKET_US_KEY_ID", "key")
    monkeypatch.setenv("POLYMARKET_US_SECRET", "secret")
    monkeypatch.delenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", raising=False)

    with pytest.raises(SystemExit):
        config_module.BotConfig(
            api_key_id="kalshi-key",
            api_key_secret=_valid_rsa_pem(),
        )


def test_same_day_eligibility_ack_passes_preflight(monkeypatch):
    today = date.today().isoformat()

    assert require_polymarket_enablement_preflight(
        enabled=True,
        eligibility_ack_date=today,
        today=today,
    ) is None
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_security.py -v`

Expected: FAIL because `polymarket.security` does not exist and config lacks the ack field.

- [ ] **Step 3: Add security helper**

```python
# polymarket/security.py
from __future__ import annotations

from datetime import date


def redact_polymarket_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, "[REDACTED_POLYMARKET_SECRET]")


def require_polymarket_enablement_preflight(*, enabled: bool, eligibility_ack_date: str, today: str | None = None) -> None:
    if not enabled:
        return
    expected = today or date.today().isoformat()
    if eligibility_ack_date != expected:
        raise ValueError(
            "POLYMARKET_US_ELIGIBILITY_ACK_DATE must equal today's date after same-day state/account eligibility re-check"
        )
```

- [ ] **Step 4: Wire config field and validation**

Add to `BotConfig`:

```python
    polymarket_us_eligibility_ack_date: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_US_ELIGIBILITY_ACK_DATE", "").strip()
    )
```

In `__post_init__`, inside the `if self.polymarket_us_enabled:` block:

```python
            from polymarket.security import require_polymarket_enablement_preflight

            try:
                require_polymarket_enablement_preflight(
                    enabled=self.polymarket_us_enabled,
                    eligibility_ack_date=self.polymarket_us_eligibility_ack_date,
                )
            except ValueError as exc:
                errors.append(str(exc))
```

Update `.env.example`:

```dotenv
# Required only when POLYMARKET_US_ENABLED=true. Must be refreshed on the enablement day.
# POLYMARKET_US_ELIGIBILITY_ACK_DATE=YYYY-MM-DD
```

- [ ] **Step 5: Add artifact/log redaction rule**

Whenever Polymarket client exceptions are logged, wrap exception text with:

```python
safe_message = redact_polymarket_secret(str(exc), cfg.polymarket_us_secret)
```

Do not log request headers or auth inputs. Tests in client tasks must assert no secret appears in raised/logged messages they introduce.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_security.py tests/test_polymarket_config.py tests/test_main_startup.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add config.py .env.example polymarket/security.py tests/test_polymarket_security.py tests/test_polymarket_config.py
git commit -m "feat: add polymarket secret hygiene preflight"
```

---

### Task 5: Add normalized Polymarket binary market model

**Objective:** Convert Polymarket US market payloads into a narrow internal binary market shape.

**Files:**
- Create: `polymarket/models.py`
- Create: `polymarket/normalizer.py`
- Test: `tests/test_polymarket_normalizer.py`

**Step 1: Write failing tests**

```python
# tests/test_polymarket_normalizer.py
import pytest

from polymarket.normalizer import normalize_polymarket_market


def test_normalizes_binary_market_payload():
    payload = {
        "slug": "will-example-happen-2026",
        "title": "Will example happen in 2026?",
        "status": "open",
        "outcomes": [
            {"name": "Yes", "bestAsk": {"value": "0.42", "currency": "USD"}},
            {"name": "No", "bestAsk": {"value": "0.59", "currency": "USD"}},
        ],
        "volume": {"value": "1234.50", "currency": "USD"},
        "openInterest": {"value": "99", "currency": "USD"},
        "closeTime": "2026-12-31T23:59:59Z",
    }

    market = normalize_polymarket_market(payload)

    assert market.venue == "polymarket_us"
    assert market.market_id == "will-example-happen-2026"
    assert market.title == "Will example happen in 2026?"
    assert market.yes_ask_cents == 42
    assert market.no_ask_cents == 59
    assert market.is_binary is True
    assert market.is_tradeable()


def test_rejects_multi_outcome_payload():
    payload = {"slug": "multi", "title": "multi", "outcomes": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}

    with pytest.raises(ValueError, match="binary markets only"):
        normalize_polymarket_market(payload)
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_normalizer.py -v`

Expected: FAIL — missing normalizer.

**Step 3: Implement model and normalizer**

```python
# polymarket/models.py
from __future__ import annotations

from dataclasses import dataclass

from trading.venue import Venue


@dataclass(frozen=True)
class PolymarketMarket:
    venue: Venue
    market_id: str
    title: str
    status: str
    yes_ask_cents: int | None
    no_ask_cents: int | None
    volume_dollars: float
    open_interest_dollars: float
    close_time: str
    is_binary: bool = True

    def is_tradeable(self) -> bool:
        return (
            self.status in {"open", "active"}
            and self.is_binary
            and self.yes_ask_cents is not None
            and self.no_ask_cents is not None
            and 1 <= self.yes_ask_cents <= 99
            and 1 <= self.no_ask_cents <= 99
        )
```

```python
# polymarket/normalizer.py
from __future__ import annotations

from typing import Any

from polymarket.models import PolymarketMarket
from trading.venue import Venue


def _money_value(payload: dict[str, Any] | None, default: float = 0.0) -> float:
    if not isinstance(payload, dict):
        return default
    try:
        return float(payload.get("value", default))
    except (TypeError, ValueError):
        return default


def _price_cents(outcome: dict[str, Any]) -> int | None:
    value = _money_value(outcome.get("bestAsk"), default=-1.0)
    if value < 0:
        return None
    return max(1, min(99, int(round(value * 100))))


def normalize_polymarket_market(payload: dict[str, Any]) -> PolymarketMarket:
    outcomes = payload.get("outcomes") or []
    if len(outcomes) != 2:
        raise ValueError("Polymarket integration supports binary markets only")

    by_name = {str(o.get("name", "")).strip().lower(): o for o in outcomes if isinstance(o, dict)}
    if "yes" not in by_name or "no" not in by_name:
        raise ValueError("binary Polymarket market must contain Yes and No outcomes")

    return PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id=str(payload.get("slug") or payload.get("id") or "").strip(),
        title=str(payload.get("title") or payload.get("question") or "").strip(),
        status=str(payload.get("status") or "").strip().lower(),
        yes_ask_cents=_price_cents(by_name["yes"]),
        no_ask_cents=_price_cents(by_name["no"]),
        volume_dollars=_money_value(payload.get("volume")),
        open_interest_dollars=_money_value(payload.get("openInterest")),
        close_time=str(payload.get("closeTime") or payload.get("close_time") or ""),
        is_binary=True,
    )
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_normalizer.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add polymarket/models.py polymarket/normalizer.py tests/test_polymarket_normalizer.py
git commit -m "feat: normalize polymarket binary markets"
```

---

### Task 6: Add Polymarket public gateway client for read-only market data

**Objective:** Fetch Polymarket US public markets without credentials, using the gateway contract captured in Task 0.

**Files:**
- Create: `polymarket/public_client.py`
- Test: `tests/test_polymarket_public_client.py`

**Step 1: Write failing tests**

```python
# tests/test_polymarket_public_client.py
from unittest.mock import MagicMock

from polymarket.public_client import PolymarketPublicClient
from trading.venue import Venue


def test_get_markets_uses_public_gateway_and_normalizes(monkeypatch):
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")
    response = MagicMock()
    response.text = '{"markets":[{"slug":"m1","title":"Will X?","status":"open","outcomes":[{"name":"Yes","bestAsk":{"value":"0.40"}},{"name":"No","bestAsk":{"value":"0.61"}}]}]}'
    response.json.return_value = {
        "markets": [
            {
                "slug": "m1",
                "title": "Will X?",
                "status": "open",
                "outcomes": [
                    {"name": "Yes", "bestAsk": {"value": "0.40"}},
                    {"name": "No", "bestAsk": {"value": "0.61"}},
                ],
            }
        ]
    }
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    markets, cursor = client.get_markets(limit=1)

    assert client.venue == Venue.POLYMARKET_US
    assert len(markets) == 1
    assert markets[0].market_id == "m1"
    assert cursor is None
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://gateway.polymarket.us/v1/markets",
    )
    headers = client._session.request.call_args.kwargs["headers"]
    assert "X-PM-Access-Key" not in headers
    assert "X-PM-Signature" not in headers
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_public_client.py -v`

Expected: FAIL — missing public gateway client.

**Step 3: Implement minimal client**

```python
# polymarket/public_client.py
from __future__ import annotations

import time
from typing import Any

import requests

from config import cfg
from polymarket.normalizer import normalize_polymarket_market
from trading.venue import Venue
from utils.logger import get_logger

log = get_logger("polymarket_public")


class PolymarketPublicClient:
    venue = Venue.POLYMARKET_US

    def __init__(self, *, base_url: str | None = None):
        self._base = (base_url or cfg.polymarket_us_public_base_url).rstrip("/")
        self._session = requests.Session()
        self._last_req_time = 0.0
        self._min_interval = 1.0 / max(1, cfg.polymarket_us_public_requests_per_second)

    def _request(self, method: str, endpoint: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_req_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = self._session.request(
            method,
            self._base + endpoint,
            headers=headers,
            params=params,
            timeout=10,
        )
        self._last_req_time = time.monotonic()
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def get_markets(self, **kwargs) -> tuple[list[Any], str | None]:
        params = {"limit": kwargs.get("limit", 100)}
        cursor = kwargs.get("cursor")
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", "/v1/markets", params=params)
        raw_markets = data.get("markets", data if isinstance(data, list) else [])
        markets = []
        for raw in raw_markets:
            try:
                markets.append(normalize_polymarket_market(raw))
            except ValueError as exc:
                log.debug("Skipping unsupported Polymarket market: %s", exc)
        return markets, data.get("cursor") if isinstance(data, dict) else None

    def get_market(self, market_id: str):
        data = self._request("GET", f"/v1/markets/{market_id}")
        return normalize_polymarket_market(data.get("market", data))
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_public_client.py tests/test_polymarket_normalizer.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add polymarket/public_client.py tests/test_polymarket_public_client.py
git commit -m "feat: add polymarket public market client"
```

---

### Task 6B: Add authenticated Polymarket account client with order hard gate

**Objective:** Add authenticated account/portfolio probes without enabling live order placement.

**Files:**
- Create: `polymarket/account_client.py`
- Test: `tests/test_polymarket_account_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_polymarket_account_client.py
from unittest.mock import MagicMock

from kalshi import OrderResult
from polymarket.account_client import PolymarketAccountClient
from tests.test_polymarket_auth import _secret_b64


def test_get_positions_uses_authenticated_api_path():
    client = PolymarketAccountClient(key_id="key", secret_b64=_secret_b64(), base_url="https://api.polymarket.us")
    response = MagicMock()
    response.text = '{"positions":{},"nextCursor":null,"eof":true}'
    response.json.return_value = {"positions": {}, "nextCursor": None, "eof": True}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    data = client.get_positions(limit=10)

    assert data["positions"] == {}
    assert client._session.request.call_args.args[:2] == (
        "GET",
        "https://api.polymarket.us/v1/portfolio/positions",
    )
    headers = client._session.request.call_args.kwargs["headers"]
    assert headers["X-PM-Access-Key"] == "key"
    assert headers["X-PM-Signature"]


def test_get_balance_reads_account_balances_buying_power():
    client = PolymarketAccountClient(key_id="key", secret_b64=_secret_b64(), base_url="https://api.polymarket.us")
    response = MagicMock()
    response.text = '{"balances":[{"currency":"USD","buyingPower":850.0}]}'
    response.json.return_value = {"balances": [{"currency": "USD", "buyingPower": 850.0}]}
    response.raise_for_status.return_value = None
    client._session.request = MagicMock(return_value=response)

    assert client.get_balance() == 850.0
    assert client._session.request.call_args.args[1] == "https://api.polymarket.us/v1/account/balances"


def test_place_limit_order_is_hard_gated_before_live_phase():
    client = PolymarketAccountClient(key_id="key", secret_b64=_secret_b64())

    result = client.place_limit_order(ticker="m1", side="yes", count=1, limit_price=40)

    assert isinstance(result, OrderResult)
    assert result.status == "error"
    assert "not enabled" in result.error.lower()
    assert client._session.request.call_count == 0
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_account_client.py -v`

Expected: FAIL because `polymarket.account_client` does not exist.

- [ ] **Step 3: Implement authenticated account client**

```python
# polymarket/account_client.py
from __future__ import annotations

import time
from typing import Any

import requests

from config import cfg
from kalshi import OrderResult
from polymarket.auth import PolymarketAuth


class PolymarketAccountClient:
    def __init__(self, *, key_id: str | None = None, secret_b64: str | None = None, base_url: str | None = None):
        self._base = (base_url or cfg.polymarket_us_api_base_url).rstrip("/")
        self._auth = PolymarketAuth(
            key_id=key_id or cfg.polymarket_us_key_id,
            secret_b64=secret_b64 or cfg.polymarket_us_secret,
        )
        self._session = requests.Session()
        self._last_req_time = 0.0
        self._min_interval = 1.0 / max(1, cfg.polymarket_us_order_requests_per_second)

    def _request(self, method: str, endpoint: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_req_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        headers = {"Accept": "application/json"}
        headers.update(self._auth.headers(method, endpoint))
        resp = self._session.request(
            method,
            self._base + endpoint,
            headers=headers,
            params=params,
            timeout=10,
        )
        self._last_req_time = time.monotonic()
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def get_positions(self, *, market: str | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if market:
            params["market"] = market
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/v1/portfolio/positions", params=params)

    def get_balance(self) -> float:
        data = self._request("GET", "/v1/account/balances")
        balances = data.get("balances") or []
        for balance in balances:
            if balance.get("currency") == "USD":
                return float(balance.get("buyingPower") or balance.get("currentBalance") or 0.0)
        return 0.0

    def place_limit_order(self, *, ticker: str, side: str, count: int, limit_price: int, expiration_ts: int | None = None) -> OrderResult:
        return OrderResult(
            order_id="",
            ticker=ticker,
            side=side,
            contracts=count,
            price_cents=limit_price,
            status="error",
            error="Polymarket live order placement is not enabled in this phase",
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_account_client.py tests/test_polymarket_auth.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add polymarket/account_client.py tests/test_polymarket_account_client.py
git commit -m "feat: add gated polymarket account client"
```

---

### Task 7: Add `venue` column to paper-trade schema with Kalshi backfill

**Objective:** Make paper trades venue-aware without changing existing Kalshi results.

**Files:**
- Modify: `trading/paper_trader.py`
- Modify: `trading/portfolio.py`
- Test: `tests/test_paper_trader_venue.py`
- Test: update `tests/test_paper_trader.py` fixtures that assert full `paper_trades` column lists or row tuples.

**Step 1: Write failing tests**

```python
# tests/test_paper_trader_venue.py
from unittest.mock import patch

from tests.test_paper_trader import _make_mock_analysis


def test_record_trade_defaults_venue_to_kalshi(trader):
    analysis = _make_mock_analysis()

    with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
        trader.record_trade(analysis)

    row = trader._conn.execute("SELECT venue FROM paper_trades LIMIT 1").fetchone()
    assert row["venue"] == "kalshi"


def test_record_trade_uses_analysis_venue_when_present(trader):
    analysis = _make_mock_analysis(ticker="will-example-happen-2026")
    analysis.venue = "polymarket_us"
    analysis.market.venue = "polymarket_us"

    with patch("dataclasses.asdict", return_value={"series_ticker": ""}):
        trader.record_trade(analysis)

    row = trader._conn.execute("SELECT venue, ticker FROM paper_trades LIMIT 1").fetchone()
    assert row["venue"] == "polymarket_us"
    assert row["ticker"] == "will-example-happen-2026"
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_paper_trader_venue.py -v`

Expected: FAIL — no `venue` column.

**Step 3: Add schema column and migration**

In `_DDL` `paper_trades` table, add:

```sql
    venue                  TEXT NOT NULL DEFAULT 'kalshi',
```

Add an idempotent migration tuple like existing `_P0_PROVENANCE_COLUMNS`:

```python
_VENUE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("venue", "TEXT NOT NULL DEFAULT 'kalshi'"),
)
```

In the schema migration method, add `ALTER TABLE paper_trades ADD COLUMN venue TEXT NOT NULL DEFAULT 'kalshi'` if missing.

**Step 4: Record venue on insert**

In `PaperTrader.record_trade`, derive:

```python
venue = str(
    getattr(analysis, "venue", None)
    or getattr(analysis.market, "venue", None)
    or "kalshi"
)
```

Add `venue` to the INSERT column list and values.

**Step 5: Hydrate portfolio venue**

Modify `trading/portfolio.py`:

```python
@dataclass
class Position:
    ...
    venue: str = "kalshi"
```

Include `venue` in `load_from_db` select if the column exists; default to `kalshi` for legacy rows.

**Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_paper_trader_venue.py tests/test_paper_trader.py tests/test_portfolio.py -v`

Expected: PASS.

**Step 7: Commit**

```bash
git add trading/paper_trader.py trading/portfolio.py tests/test_paper_trader_venue.py tests/test_paper_trader.py tests/test_portfolio.py
git commit -m "feat: track paper trade venue"
```

---

### Task 7B: Add Polymarket paper accounting fields and net PnL semantics

**Objective:** Make Polymarket paper rows auditable for venue market IDs, side/outcome IDs, fees, tick size, settlement source, and net PnL before any execution smoke path is treated as readiness evidence.

**Files:**
- Modify: `trading/paper_trader.py`
- Modify: `trading/portfolio.py`
- Test: `tests/test_polymarket_paper_accounting.py`

- [ ] **Step 1: Write failing accounting tests**

```python
# tests/test_polymarket_paper_accounting.py
from unittest.mock import patch

from tests.test_paper_trader import _make_mock_analysis


def _polymarket_analysis():
    analysis = _make_mock_analysis(ticker="will-example-happen-2026", side="yes", yes_price=40.0, edge=0.10)
    analysis.venue = "polymarket_us"
    analysis.executed_price_cents = 40
    analysis.market.venue = "polymarket_us"
    analysis.market.polymarket_market_id = "will-example-happen-2026"
    analysis.market.polymarket_side_id = "yes"
    analysis.market.polymarket_outcome_id = "outcome-yes"
    analysis.market.fee_cents = 2
    analysis.market.tick_size_cents = 1
    analysis.market.settlement_source = "polymarket_us_market_settlement"
    return analysis


def test_polymarket_paper_trade_persists_accounting_fields(trader):
    analysis = _polymarket_analysis()

    with patch("dataclasses.asdict", return_value={"series_ticker": ""}):
        trade_id = trader.record_trade(analysis)

    row = trader._conn.execute(
        "SELECT venue, venue_market_id, venue_side_id, venue_outcome_id, "
        "fee_cents, tick_size_cents, settlement_source, net_cost_dollars "
        "FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()

    assert row["venue"] == "polymarket_us"
    assert row["venue_market_id"] == "will-example-happen-2026"
    assert row["venue_side_id"] == "yes"
    assert row["venue_outcome_id"] == "outcome-yes"
    assert row["fee_cents"] == 2
    assert row["tick_size_cents"] == 1
    assert row["settlement_source"] == "polymarket_us_market_settlement"
    assert row["net_cost_dollars"] > 0


def test_kalshi_rows_default_accounting_fields_without_changing_cost(trader):
    analysis = _make_mock_analysis()

    with patch("dataclasses.asdict", return_value={"series_ticker": "KXTEST"}):
        trade_id = trader.record_trade(analysis)

    row = trader._conn.execute(
        "SELECT venue, venue_market_id, fee_cents, net_cost_dollars, cost_dollars "
        "FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()

    assert row["venue"] == "kalshi"
    assert row["venue_market_id"] == analysis.market.ticker
    assert row["fee_cents"] == 0
    assert row["net_cost_dollars"] == row["cost_dollars"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_paper_accounting.py -v`

Expected: FAIL because accounting columns are missing.

- [ ] **Step 3: Add forward-only accounting columns**

Add an idempotent column tuple next to the venue/provenance migrations:

```python
_VENUE_ACCOUNTING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("venue_market_id", "TEXT"),
    ("venue_side_id", "TEXT"),
    ("venue_outcome_id", "TEXT"),
    ("fee_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("tick_size_cents", "INTEGER NOT NULL DEFAULT 1"),
    ("settlement_source", "TEXT"),
    ("net_cost_dollars", "REAL"),
    ("net_pnl_dollars", "REAL"),
)
```

Add these fields to `_DDL` for new databases and to the migration loop for existing databases. Backfill `venue_market_id=ticker`, `fee_cents=0`, `tick_size_cents=1`, `net_cost_dollars=cost_dollars`, and `net_pnl_dollars=pnl_dollars` for legacy rows.

- [ ] **Step 4: Record accounting values on insert**

In `PaperTrader.record_trade`, derive:

```python
venue_market_id = str(getattr(_market, "polymarket_market_id", None) or getattr(_market, "ticker", ""))
venue_side_id = getattr(_market, "polymarket_side_id", None) or side
venue_outcome_id = getattr(_market, "polymarket_outcome_id", None)
fee_cents = int(getattr(_market, "fee_cents", 0) or 0)
tick_size_cents = int(getattr(_market, "tick_size_cents", 1) or 1)
settlement_source = getattr(_market, "settlement_source", None)
net_cost_dollars = cost_dollars + (contracts * fee_cents / 100.0)
```

Add these values to the `paper_trades` INSERT. Do not change Kalshi cost semantics; Kalshi defaults must keep `net_cost_dollars == cost_dollars`.

- [ ] **Step 5: Include accounting fields in portfolio hydration**

Extend `Position` only with fields that portfolio/reporting needs immediately:

```python
venue_market_id: str = ""
fee_cents: int = 0
net_cost_dollars: float | None = None
```

Load them from DB when present; default legacy rows to `ticker`, `0`, and `cost_dollars`.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_paper_accounting.py tests/test_paper_trader_venue.py tests/test_paper_trader.py tests/test_portfolio.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add trading/paper_trader.py trading/portfolio.py tests/test_polymarket_paper_accounting.py tests/test_paper_trader.py tests/test_portfolio.py
git commit -m "feat: add venue paper accounting fields"
```

---

### Task 8: Make executor venue-aware but preserve Kalshi behavior

**Objective:** Ensure executor logs/risk checks include venue and that live Polymarket orders remain blocked.

**Files:**
- Modify: `trading/executor.py`
- Modify: `utils/logger.py`
- Test: `tests/test_executor_venue.py`
- Test: update `tests/test_executor.py` only if constructor fixtures need the default `venue_clients=None`.

**Step 1: Write failing tests**

```python
# tests/test_executor_venue.py
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config as _cfg_module
from tests.test_executor import _make_analysis
from trading.executor import TradeExecutor
from trading.venue import Venue


@pytest.mark.asyncio
async def test_polymarket_live_execute_is_blocked_even_when_executor_live(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", False)
    monkeypatch.setattr(_cfg_module.cfg, "polymarket_us_live_trading_enabled", False)
    rest = MagicMock()
    paper = MagicMock()
    executor = TradeExecutor(rest, paper)

    analysis = _make_analysis(ticker="will-example-happen-2026")
    analysis.venue = "polymarket_us"
    analysis.market.venue = "polymarket_us"

    with patch("trading.executor.trade_log"):
        result = await executor._execute_live(analysis)

    assert result is None
    rest.place_limit_order.assert_not_called()


def test_validate_kalshi_analysis_still_passes(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    rest = MagicMock()
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 500.0
    paper.portfolio.open_positions.return_value = []
    paper.portfolio.open_positions_by_prefix.return_value = []
    paper.portfolio.is_concentration_ok.return_value = True
    executor = TradeExecutor(rest, paper)

    assert executor._validate(_make_analysis()) is None


@pytest.mark.asyncio
async def test_polymarket_blended_candidate_refetches_through_polymarket_client(monkeypatch):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    kalshi_rest = MagicMock()
    polymarket_client = MagicMock()
    paper = MagicMock()
    paper.get_notional_bankroll.return_value = 500.0
    executor = TradeExecutor(
        kalshi_rest,
        paper,
        venue_clients={Venue.POLYMARKET_US: polymarket_client},
    )

    analysis = _make_analysis(ticker="will-example-happen-2026")
    analysis.venue = "polymarket_us"
    candidate_market = SimpleNamespace(
        ticker="will-example-happen-2026",
        venue="polymarket_us",
        is_tradeable=lambda: True,
    )
    fresh_market = _make_analysis(ticker="will-example-happen-2026").market
    fresh_market.venue = "polymarket_us"
    polymarket_client.get_market.return_value = fresh_market
    candidate = SimpleNamespace(
        fast_lane_analysis=analysis,
        market=candidate_market,
        blended_probability=0.64,
    )

    result = await executor._analysis_from_candidate(candidate)

    assert result.market is fresh_market
    polymarket_client.get_market.assert_called_once_with("will-example-happen-2026")
    kalshi_rest.get_market.assert_not_called()
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_executor_venue.py -v`

Expected: FAIL — executor does not recognize venue/live Polymarket gate or venue-specific refetch clients.

**Step 3: Implement venue helper**

In `trading/executor.py` add imports:

```python
from trading.venue import Venue, normalize_venue
```

Change constructor signature and set the registry without altering the Kalshi default:

```python
    def __init__(self, rest_client: Any, paper_trader: Any, *, venue_clients: dict[Venue, Any] | None = None):
        self._rest = rest_client
        self._paper = paper_trader
        self._venue_clients = {Venue.KALSHI: rest_client}
        if venue_clients:
            self._venue_clients.update(venue_clients)
        ...
```

Add helpers:

```python
    @staticmethod
    def _analysis_venue(analysis: SignalAnalysis) -> Venue:
        raw = getattr(analysis, "venue", None) or getattr(analysis.market, "venue", None) or Venue.KALSHI
        return normalize_venue(raw)

    def _client_for_venue(self, venue: Venue) -> Any:
        return self._venue_clients.get(venue, self._rest)
```

Inside `_analysis_from_candidate`, derive venue from `candidate.fast_lane_analysis` or `candidate.market` before refetch and replace `self._rest.get_market` with `self._client_for_venue(venue).get_market`.

**Step 4: Hard-block Polymarket live**

At the start of `_execute_live` after paper guard:

```python
        venue = self._analysis_venue(analysis)
        if venue == Venue.POLYMARKET_US and not cfg.polymarket_us_live_trading_enabled:
            log.error(
                "[LIVE_GUARD] BLOCKED Polymarket live order for %s -- POLYMARKET_US_LIVE_TRADING_ENABLED=false",
                analysis.market.ticker,
            )
            return None
```

Do not change Kalshi live path.

**Step 5: Include venue in logs and TradeLog schema**

Update `[DECISION]`, `[PAPER]`, `[LIVE]`, and skipped log kwargs to include `venue=venue.value`.

In `utils/logger.py`, extend the relevant `TradeLog` methods so `venue` is accepted and written into JSONL payloads:

```python
def log_skipped(..., venue: str = "kalshi", ...):
    record["venue"] = venue
```

Apply the same explicit `venue: str = "kalshi"` parameter to paper/live trade logging methods touched by executor output. Add assertions to existing logger tests, or to `tests/test_executor_venue.py`, that skipped payloads include `"venue": "polymarket_us"`.

**Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_executor_venue.py tests/test_executor.py tests/test_executor_f08.py tests/test_paper_mode_lock_post_wave1.py -v`

Expected: PASS.

**Step 7: Commit**

```bash
git add trading/executor.py utils/logger.py tests/test_executor_venue.py tests/test_executor.py
git commit -m "feat: add venue-aware executor guard"
```

---

### Task 9: Add a Polymarket market-data observer task, no trading

**Objective:** Fetch/cache Polymarket binary markets independently from Kalshi and emit observer diagnostics.

**Files:**
- Create: `feeds/polymarket_market_data.py`
- Modify: `utils/logger.py`
- Test: `tests/test_polymarket_market_data.py`

**Step 1: Write failing test**

```python
# tests/test_polymarket_market_data.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from feeds.polymarket_market_data import run_polymarket_market_observer


@pytest.mark.asyncio
async def test_observer_does_nothing_when_disabled(monkeypatch):
    monkeypatch.setattr("config.cfg.polymarket_us_enabled", False)
    client = MagicMock()
    logger = MagicMock()

    await run_polymarket_market_observer(client=client, logger=logger, poll_once=True)

    client.get_markets.assert_not_called()
    logger.log_polymarket_market_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_observer_logs_binary_market_snapshot(monkeypatch):
    monkeypatch.setattr("config.cfg.polymarket_us_enabled", True)
    market = MagicMock()
    market.market_id = "m1"
    market.title = "Will X?"
    market.yes_ask_cents = 40
    market.no_ask_cents = 61
    market.is_tradeable.return_value = True
    client = MagicMock()
    client.get_markets.return_value = ([market], None)
    logger = MagicMock()

    await run_polymarket_market_observer(client=client, logger=logger, poll_once=True)

    logger.log_polymarket_market_snapshot.assert_called_once()
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_market_data.py -v`

Expected: FAIL — missing observer/logger method.

**Step 3: Implement observer**

```python
# feeds/polymarket_market_data.py
from __future__ import annotations

import asyncio

from config import cfg
from polymarket.public_client import PolymarketPublicClient
from utils.logger import get_logger, trade_log, write_trade_log_async

log = get_logger("polymarket_market_data")


async def run_polymarket_market_observer(*, client=None, logger=trade_log, poll_once: bool = False) -> None:
    if not cfg.polymarket_us_enabled:
        log.info("Polymarket US observer disabled")
        return
    client = client or PolymarketPublicClient()
    while True:
        markets, _ = await asyncio.to_thread(client.get_markets, limit=100)
        for market in markets:
            await write_trade_log_async(
                logger.log_polymarket_market_snapshot,
                venue="polymarket_us",
                market_id=market.market_id,
                title=market.title,
                yes_ask_cents=market.yes_ask_cents,
                no_ask_cents=market.no_ask_cents,
                tradeable=market.is_tradeable(),
            )
        if poll_once:
            return
        await asyncio.sleep(60)
```

**Step 4: Add logger method**

In `utils/logger.py`, add `log_polymarket_market_snapshot(...)` consistent with existing JSONL event methods. Event type should be `POLYMARKET_MARKET_SNAPSHOT`.

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_market_data.py tests/test_main_pipeline.py -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add feeds/polymarket_market_data.py utils/logger.py tests/test_polymarket_market_data.py
git commit -m "feat: add polymarket market data observer"
```

---

### Task 10: Wire observer into `TradingBot` startup when enabled

**Objective:** Start the Polymarket observer only when `POLYMARKET_US_ENABLED=true`.

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_pipeline.py` or `tests/test_main_startup.py`

**Step 1: Write failing test**

Add a startup/task creation test that patches `cfg.polymarket_us_enabled=True` and asserts `run_polymarket_market_observer` is scheduled. Keep it deterministic by patching `asyncio.create_task` or the task list builder if one exists.

Example shape:

```python
def test_polymarket_observer_task_only_when_enabled(monkeypatch):
    import main

    monkeypatch.setattr(main.cfg, "polymarket_us_enabled", True)
    bot = main.TradingBot()

    tasks = bot._build_background_tasks()  # add this helper if task creation is currently inline

    assert any("polymarket" in getattr(task, "__name__", "") for task in tasks)
```

If tasks are currently created inline, first extract a non-mutating `_background_task_factories()` helper under TDD.

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_main_startup.py::test_polymarket_observer_task_only_when_enabled -v`

Expected: FAIL.

**Step 3: Wire in smallest change**

In `main.py`, import lazily near task startup:

```python
from feeds.polymarket_market_data import run_polymarket_market_observer
```

Add to background tasks only when enabled:

```python
        if cfg.polymarket_us_enabled:
            tasks.append(asyncio.create_task(run_polymarket_market_observer(), name="polymarket-market-observer"))
```

Do not add executor routing yet.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_main_startup.py tests/test_main_pipeline.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add main.py tests/test_main_startup.py tests/test_main_pipeline.py
git commit -m "feat: wire optional polymarket observer"
```

---

### Task 11: Add venue-aware reporting slices

**Objective:** Ensure performance reports can distinguish Kalshi and Polymarket paper trades.

**Files:**
- Modify: `scripts/daily_review.py`
- Modify: `scripts/performance_analysis.py`
- Modify: `tests/test_daily_review.py`
- Modify: `tests/test_performance_analysis_p0_cohorts.py`

**Step 1: Write failing test**

Add a fixture DB with one `kalshi` and one `polymarket_us` row. Assert report includes per-venue counts and does not aggregate them invisibly.

```python
def test_daily_review_reports_venue_breakdown(tmp_path):
    # create minimal paper_trades table with venue/ticker/resolved/pnl_dollars
    # run report helper
    assert "Venue breakdown" in report
    assert "kalshi" in report
    assert "polymarket_us" in report
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_daily_review.py::test_daily_review_reports_venue_breakdown -v`

Expected: FAIL.

**Step 3: Implement venue grouping**

Use SQL `GROUP BY venue` with `COALESCE(venue, 'kalshi')` for legacy rows.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_daily_review.py tests/test_performance_analysis_p0_cohorts.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/daily_review.py scripts/performance_analysis.py tests/test_daily_review.py tests/test_performance_analysis_p0_cohorts.py
git commit -m "feat: report paper trades by venue"
```

---

### Task 12: Add Polymarket paper candidate adapter, binary-only

**Objective:** Convert a normalized Polymarket binary market plus existing `SignalAnalysis` into a venue-tagged candidate the executor can paper-record.

**Files:**
- Create: `polymarket/candidate_adapter.py`
- Test: `tests/test_polymarket_candidate_adapter.py`

**Step 1: Write failing test**

```python
# tests/test_polymarket_candidate_adapter.py
from unittest.mock import MagicMock

from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.models import PolymarketMarket
from trading.venue import Venue


def test_adapt_polymarket_analysis_sets_venue_and_executed_price():
    analysis = MagicMock()
    analysis.side = "yes"
    analysis.market = MagicMock()
    market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="m1",
        title="Will X?",
        status="open",
        yes_ask_cents=40,
        no_ask_cents=61,
        volume_dollars=1000.0,
        open_interest_dollars=100.0,
        close_time="2026-12-31T23:59:59Z",
    )

    adapted = adapt_polymarket_analysis(analysis, market)

    assert adapted.venue == "polymarket_us"
    assert adapted.market.ticker == "m1"
    assert adapted.market.venue == "polymarket_us"
    assert adapted.executed_price_cents == 40
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_candidate_adapter.py -v`

Expected: FAIL.

**Step 3: Implement adapter**

Because the rest of the pipeline expects `KalshiMarket`-like objects, create a minimal `SimpleNamespace` or a dedicated adapter object with the attributes executor and paper trader need:

```python
# polymarket/candidate_adapter.py
from __future__ import annotations

import copy
from types import SimpleNamespace

from polymarket.models import PolymarketMarket


def adapt_polymarket_analysis(analysis, market: PolymarketMarket):
    if not market.is_binary:
        raise ValueError("Polymarket adapter supports binary markets only")
    if not market.is_tradeable():
        raise ValueError("Polymarket market is not tradeable")

    adapted = copy.copy(analysis)
    side = str(adapted.side).lower()
    executed = market.yes_ask_cents if side == "yes" else market.no_ask_cents
    adapted.venue = "polymarket_us"
    adapted.executed_price_cents = executed
    adapted.market = SimpleNamespace(
        venue="polymarket_us",
        ticker=market.market_id,
        series_ticker="",
        title=market.title,
        subtitle="",
        status=market.status,
        close_time=market.close_time,
        price_available=True,
        price_source="polymarket_us_rest",
        price_method="best_ask_decimal",
        price_retrieved_at=None,
        raw_payload_hash=None,
        yes_price=float(market.yes_ask_cents or 0),
        yes_bid=float(market.yes_ask_cents or 0),
        yes_ask=float(market.yes_ask_cents or 0),
        yes_prob=float(market.yes_ask_cents or 0) / 100.0,
        is_tradeable=lambda: market.is_tradeable(),
    )
    return adapted
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_candidate_adapter.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add polymarket/candidate_adapter.py tests/test_polymarket_candidate_adapter.py
git commit -m "feat: adapt polymarket binary analyses for paper execution"
```

---

### Task 13: Add paper-only Polymarket execution smoke path

**Objective:** Prove a venue-tagged Polymarket analysis can pass executor paper path and land in SQLite with `venue='polymarket_us'`.

**Files:**
- Test: `tests/test_polymarket_paper_execution.py`
- Modify: `polymarket/candidate_adapter.py`
- Modify: `trading/executor.py`
- Modify: `trading/paper_trader.py`
- Modify: `trading/portfolio.py`

**Step 1: Write failing integration test**

```python
# tests/test_polymarket_paper_execution.py
import pytest
from unittest.mock import MagicMock, patch

import config as _cfg_module
from polymarket.candidate_adapter import adapt_polymarket_analysis
from polymarket.models import PolymarketMarket
from tests.test_paper_trader import _make_mock_analysis
from trading.executor import TradeExecutor
from trading.venue import Venue


@pytest.mark.asyncio
async def test_polymarket_paper_analysis_records_venue(monkeypatch, trader):
    monkeypatch.setattr(_cfg_module.cfg, "is_paper_trading", True)
    rest = MagicMock()
    paper = trader
    executor = TradeExecutor(rest, paper)

    base = _make_mock_analysis(ticker="placeholder", side="yes", yes_price=40.0, edge=0.10)
    pm_market = PolymarketMarket(
        venue=Venue.POLYMARKET_US,
        market_id="will-example-happen-2026",
        title="Will example happen in 2026?",
        status="open",
        yes_ask_cents=40,
        no_ask_cents=61,
        volume_dollars=1000,
        open_interest_dollars=100,
        close_time="2026-12-31T23:59:59Z",
    )
    analysis = adapt_polymarket_analysis(base, pm_market)

    with patch("trading.executor.write_trade_log_async", return_value=None):
        trade_id = await executor.execute(analysis)

    assert trade_id
    row = trader._conn.execute(
        "SELECT venue, ticker, venue_market_id, fee_cents, net_cost_dollars "
        "FROM paper_trades WHERE trade_id=?",
        (trade_id,),
    ).fetchone()
    assert row["venue"] == "polymarket_us"
    assert row["ticker"] == "will-example-happen-2026"
    assert row["venue_market_id"] == "will-example-happen-2026"
    assert row["fee_cents"] == 0
    assert row["net_cost_dollars"] > 0
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_paper_execution.py -v`

Expected: FAIL until prior venue plumbing is complete.

**Step 3: Verify the exact integration points are already present**

Before changing code in this task, inspect these facts from prior tasks:

- `PaperTrader.record_trade` serializes non-dataclass market adapters through `_market_to_jsonable`.
- `TradeExecutor._analysis_from_candidate` chooses a refetch client by venue.
- Prefix/exposure checks use venue-aware keys or explicit `venue_market_id`, not a Kalshi-only `KX...` prefix assumption.
- `TradeLog` skipped/paper/live methods accept and emit `venue`.
- Accounting fields from Task 7B are present and backfilled.

If any item is false, stop this task and repair the earlier task that owns the missing behavior; do not add duplicate workaround logic here.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_paper_execution.py tests/test_executor.py tests/test_executor_venue.py tests/test_paper_trader.py tests/test_polymarket_paper_accounting.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_polymarket_paper_execution.py trading/executor.py trading/paper_trader.py trading/portfolio.py
git commit -m "feat: support polymarket paper execution path"
```

---

### Task 14: Add cross-venue market matching as diagnostics only

**Objective:** Identify overlapping Kalshi/Polymarket events without affecting trade routing.

**Files:**
- Create: `analysis/cross_venue_matcher.py`
- Test: `tests/test_cross_venue_matcher.py`
- Modify: `utils/logger.py` for `CROSS_VENUE_MATCH_DIAGNOSTIC`.

**Step 1: Write failing test**

```python
# tests/test_cross_venue_matcher.py
from types import SimpleNamespace

from analysis.cross_venue_matcher import match_cross_venue_markets


def test_matches_similar_binary_market_titles():
    kalshi = [SimpleNamespace(ticker="KXTEST", title="Will Trump sign the bill?", close_time="2026-12-31T00:00:00Z")]
    polymarket = [SimpleNamespace(market_id="trump-sign-bill", title="Will Trump sign the bill in 2026?", close_time="2026-12-31T00:00:00Z")]

    matches = match_cross_venue_markets(kalshi, polymarket, min_score=0.5)

    assert len(matches) == 1
    assert matches[0].kalshi_ticker == "KXTEST"
    assert matches[0].polymarket_market_id == "trump-sign-bill"
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_cross_venue_matcher.py -v`

Expected: FAIL.

**Step 3: Implement simple title-similarity matcher**

Use existing tokenization style from `analysis/market_matcher.py`, but keep this module diagnostic-only. Do not route trades from this output yet.

**Step 4: Emit diagnostics from observer**

When both caches are available, log:

```json
{
  "type": "CROSS_VENUE_MATCH_DIAGNOSTIC",
  "kalshi_ticker": "...",
  "polymarket_market_id": "...",
  "score": 0.72,
  "kalshi_yes_ask_cents": 43,
  "polymarket_yes_ask_cents": 40,
  "divergence_cents": 3
}
```

**Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_cross_venue_matcher.py tests/test_polymarket_market_data.py -v`

Expected: PASS.

**Step 6: Commit**

```bash
git add analysis/cross_venue_matcher.py tests/test_cross_venue_matcher.py utils/logger.py feeds/polymarket_market_data.py
git commit -m "feat: add cross-venue match diagnostics"
```

---

### Task 15: Add replay/measurement script for divergence gate

**Objective:** Measure whether Polymarket adds enough price divergence to justify trading integration.

**Files:**
- Create: `scripts/polymarket_divergence_report.py`
- Test: `tests/test_polymarket_divergence_report.py`

**Step 1: Write failing test**

```python
# tests/test_polymarket_divergence_report.py
from scripts.polymarket_divergence_report import summarize_divergence


def test_summarize_divergence_counts_edge_relevant_hours():
    rows = [
        {"kalshi_ticker": "KX1", "polymarket_market_id": "pm1", "divergence_cents": 4, "market_hour": "2026-06-01T10"},
        {"kalshi_ticker": "KX2", "polymarket_market_id": "pm2", "divergence_cents": 1, "market_hour": "2026-06-01T10"},
    ]

    summary = summarize_divergence(rows, threshold_cents=3)

    assert summary["matched_market_hours"] == 2
    assert summary["edge_relevant_market_hours"] == 1
    assert summary["edge_relevant_pct"] == 50.0
```

**Step 2: Run test to verify failure**

Run: `.venv/bin/python -m pytest tests/test_polymarket_divergence_report.py -v`

Expected: FAIL.

**Step 3: Implement script**

The script should read `logs/trades/trades.jsonl`, filter `CROSS_VENUE_MATCH_DIAGNOSTIC`, group by `(kalshi_ticker, polymarket_market_id, hour)`, and print summary.

CLI:

```bash
.venv/bin/python scripts/polymarket_divergence_report.py --trades-jsonl logs/trades/trades.jsonl --threshold-cents 3
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_polymarket_divergence_report.py -v`

Expected: PASS.

**Step 5: Commit**

```bash
git add scripts/polymarket_divergence_report.py tests/test_polymarket_divergence_report.py
git commit -m "feat: report polymarket cross-venue divergence"
```

---

### Task 16: Update docs and release metadata for observer/paper support

**Objective:** Document safe usage and keep release files in sync.

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `CHANGELOG.md`
- Modify: `VERSION`
- Optional modify: `docs/profit_path_debt_log.md` only if this becomes active tracking; do not create parallel tracking docs.

**Step 1: Update README**

Add a small “Optional Polymarket US observer / paper venue” section:

```markdown
## Optional Polymarket US Venue

Polymarket US support is disabled by default. The first supported mode is authenticated market-data observation and paper-trade recording for binary markets only.

Required only when enabling:

```dotenv
POLYMARKET_US_ENABLED=true
POLYMARKET_US_KEY_ID=...
POLYMARKET_US_SECRET=...
POLYMARKET_US_LIVE_TRADING_ENABLED=false
```

Live Polymarket orders are not enabled by this mode. Keep `POLYMARKET_US_LIVE_TRADING_ENABLED=false` unless a separate live-enable PR has passed paper evidence gates and operator approval.
```

**Step 2: Update changelog/version**

Because this changes shipped behavior when enabled, bump patch version unless the operator wants a minor version.

Run first:

```bash
.venv/bin/python scripts/sync_readme_version.py --check
```

Then update `VERSION`, `CHANGELOG.md`, and README badge/current-through line as required by repo hooks.

**Step 3: Run validation**

Run:

```bash
.venv/bin/python scripts/sync_readme_version.py --check
make lint
.venv/bin/python -m pytest tests/test_polymarket_contract_snapshot.py tests/test_polymarket_auth.py tests/test_polymarket_normalizer.py tests/test_polymarket_public_client.py tests/test_polymarket_account_client.py tests/test_polymarket_paper_accounting.py tests/test_polymarket_paper_execution.py tests/test_executor.py tests/test_paper_trader.py -q
```

Expected: all pass.

**Step 4: Commit**

```bash
git add README.md .env.example CHANGELOG.md VERSION
# include sync_readme_version changes if generated
git commit -m "docs: document polymarket us paper integration"
```

---

## Future Live-Enable Plan (Separate Branch Only)

Do not start this until observer/paper phases have merged and run cleanly.

### Live Entry Gates

All must hold:

1. Operator explicitly approves live Polymarket trading in writing.
2. `POLYMARKET_US_LIVE_TRADING_ENABLED=true` intentionally set by operator.
3. Polymarket paper run has at least 2 weeks of evidence and positive EV net of fees.
4. Colorado/state eligibility re-verified on the day branch opens.
5. No active unresolved Kalshi-edge or bankroll bug.
6. Focused adversarial code review passes.
7. Live subset is narrow: binary, non-sports, political/geopolitical only.
8. Per-venue bankroll cap is lower than or equal to Kalshi live cap until live evidence exists.

### Live Files Likely to Change

- `polymarket/account_client.py` — implement actual `place_limit_order` payload from the then-current official order docs.
- `trading/executor.py` — allow live Polymarket only when per-venue live flag true.
- `trading/paper_trader.py` or new `trading/live_ledger.py` — persist fills/settlement events by venue.
- `tests/test_polymarket_live_guard.py` — prove hard block when disabled and payload when enabled.

### Live Validation

Use mocked API responses only in CI. Never hit live order endpoints in automated tests.

Commands:

```bash
.venv/bin/python -m pytest tests/test_polymarket_live_guard.py tests/test_executor.py tests/test_paper_mode_lock_post_wave1.py -q
make lint
```

Manual/live smoke requires operator-supervised tiny order and immediate review; document results in PR, not in a new tracking doc.

---

## Risks, Tradeoffs, and Mitigations

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Accidental live Polymarket order | Critical | Separate `POLYMARKET_US_LIVE_TRADING_ENABLED`; `PolymarketAccountClient.place_limit_order` returns error until live branch; executor guard blocks Polymarket live. |
| Breaking Kalshi signing/auth | Critical | Do not edit Kalshi signing except adding a `venue` constant; run `tests/test_kalshi_signing_failfast.py`. |
| DB migration corrupts paper trades | High | Add idempotent `venue` and accounting columns with Kalshi-safe defaults; test legacy schema hydration; backup DB before runtime deploy. |
| Venue mixing in exposure/cooldowns | High | Add venue to positions and queries; decide whether same real-world event should cap across venues only after diagnostics. Initial cap remains per venue/market ID. |
| Multi-outcome accidental admission | High | Normalizer rejects non-binary payloads; test rejection. |
| State/regulatory drift | High | Live branch entry requires same-day eligibility check; no Global CLOB. |
| Polymarket API schema mismatch | Medium | Task 0 contract snapshot required before code; normalizer fails closed; observer logs skips; implementation tests use fixtures captured from current official docs/API only. |
| Added observer consumes rate limit | Medium | Use public gateway rate limit from Task 0 (`20 req/sec per IP` as of 2026-06-07); default disabled; poll interval conservative. |
| Reporting aggregates hide per-venue losses | Medium | Add venue breakdown to daily/performance reports before paper execution. |

---

## Validation Matrix

Run focused tests after each task as listed above. Before opening the first PR, run:

```bash
make lint
.venv/bin/python -m pytest tests/test_polymarket_contract_snapshot.py \
  tests/test_venue_types.py \
  tests/test_venue_client_protocol.py \
  tests/test_polymarket_config.py \
  tests/test_polymarket_auth.py \
  tests/test_polymarket_normalizer.py \
  tests/test_polymarket_public_client.py \
  tests/test_polymarket_account_client.py \
  tests/test_paper_trader_venue.py \
  tests/test_polymarket_paper_accounting.py \
  tests/test_executor_venue.py \
  tests/test_polymarket_market_data.py \
  tests/test_polymarket_paper_execution.py \
  tests/test_cross_venue_matcher.py \
  tests/test_polymarket_divergence_report.py -q
```

Before merge, run broader regression:

```bash
make lint
.venv/bin/python -m pytest -q
.venv/bin/python scripts/sync_readme_version.py --check
```

If full pytest is too slow or blocked, report exactly which focused suites passed and why full validation did not run.

---

## Pre-Implementation Open Questions

None blocking. The prior open questions are now resolved inside implementation tasks:

1. Exact API paths, payload shape, secret format, auth string, and rate limits are captured in Task 0 and must be updated whenever official docs drift.
2. Initial exposure behavior is per venue/market ID with diagnostics before cross-venue caps; matched-event caps remain a future decision after evidence.
3. Version bump is handled in Task 16 with patch as the default because the feature is optional and disabled by default.

---

## Implementation Handoff

Implement using strict TDD:

1. Write the failing test.
2. Run it and confirm the expected failure.
3. Write the minimal code.
4. Run the focused test and adjacent regression tests.
5. Commit the logical task.
6. Stop for review on high-risk tasks touching `trading/`, DB schema, or live guards.

Recommended branch order:

```bash
git checkout main
git pull --ff-only
git checkout -b feature/polymarket-us-observer-paper
```

Do not stage current runtime churn (`logs/`, `data/matcher_token_weights.json`) unless explicitly requested.
