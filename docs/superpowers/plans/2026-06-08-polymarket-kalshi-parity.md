# Polymarket Kalshi Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make enabled Polymarket runtime expose the same operator-facing health, balance, market, ticker, and paper-execution confirmations that Kalshi exposes, while keeping Polymarket live order placement disabled.

**Architecture:** Keep venue internals separate but expose a shared operational contract: startup account probe, market universe probe, per-venue runtime heartbeat, candidate routing logs, and venue-tagged paper rows. Kalshi remains the existing baseline; Polymarket fills missing parity surfaces without weakening Kalshi gates or enabling live Polymarket orders.

**Tech Stack:** Python 3.14 runtime, pytest, ruff, existing `PolymarketAccountClient`, `PolymarketPublicClient`, `PolymarketPaperRuntime`, `TradingBot`, `TradeExecutor`, SQLite paper ledger.

---

## Current Gap Summary

Kalshi currently does all of these at runtime:
- Constructs one exchange client in `main.TradingBot.__init__`.
- Logs startup mode and exchange status.
- Calls `self.rest.get_balance()` and logs `Kalshi account balance: $...`.
- Refreshes a market cache and logs non-empty readiness.
- Subscribes Kalshi tickers to websocket price updates.
- Routes candidate analyses through blend/readiness/executor.
- Writes paper/live records through shared logs.

Polymarket currently does only part of that:
- Logs `Polymarket US: enabled=true paper_execution=blend live_trading=false`.
- Public startup probe samples one market.
- Paper runtime fetches public markets and logs `[POLYMARKET_PAPER] market_cache_refreshed markets=100`.
- Routes matched public markets through blend/readiness with `watch=False`.
- Does not instantiate `PolymarketAccountClient` at startup.
- Does not log `Polymarket account balance: $...`.
- Does not log positions count or authenticated account probe status.
- Does not expose a venue-level status heartbeat comparable to Kalshi.
- Does not maintain a first-class Polymarket ticker watch/price stream.
- Does not yet produce Polymarket paper rows unless the simplified matcher finds a matching public market.

## File Structure

- Modify `main.py`: wire authenticated Polymarket account probe and unified startup/status logs.
- Modify `polymarket/account_client.py`: keep hard-gated order placement, add safe account probe helpers if needed.
- Modify `polymarket/paper_runtime.py`: expose cache stats, last refresh, match/routing counters, and market universe summary.
- Modify `polymarket/startup_probe.py`: include balance/account readiness in status line, without logging secrets.
- Create `polymarket/status.py`: one narrow status DTO/function for operator parity logs.
- Modify tests:
  - `tests/test_polymarket_account_client.py`
  - `tests/polymarket/test_startup_probe.py`
  - `tests/polymarket/test_paper_runtime.py`
  - `tests/polymarket/test_main_runtime_wiring.py`
  - new `tests/polymarket/test_status.py`

---

### Task 1: Add Kalshi-Style Polymarket Account Balance Startup Log

**Files:**
- Modify: `main.py`
- Modify: `polymarket/account_client.py`
- Test: `tests/polymarket/test_main_runtime_wiring.py`
- Test: `tests/test_polymarket_account_client.py`

- [ ] **Step 1: Write the failing startup wiring test**

Add a test that constructs a lightweight `TradingBot`-like object or patches `PolymarketAccountClient` and verifies startup logs include:

```text
Polymarket account balance: $850.00
```

Also verify failures log:

```text
Could not fetch Polymarket account balance: <error>
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/polymarket/test_main_runtime_wiring.py -v
```

Expected: FAIL because `main.py` never constructs or calls `PolymarketAccountClient.get_balance()`.

- [ ] **Step 3: Implement minimal startup probe**

In `main.py`, after the Kalshi balance block, add:

```python
if cfg.polymarket_us_enabled:
    try:
        from polymarket.account_client import PolymarketAccountClient

        polymarket_balance = await asyncio.to_thread(
            PolymarketAccountClient().get_balance
        )
        log.info("Polymarket account balance: $%.2f", polymarket_balance)
    except Exception as exc:
        log.warning("Could not fetch Polymarket account balance: %s", exc)
```

Keep it read-only. Do not call `place_limit_order()`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/polymarket/test_main_runtime_wiring.py tests/test_polymarket_account_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit and PR**

```bash
git add main.py tests/polymarket/test_main_runtime_wiring.py tests/test_polymarket_account_client.py
git commit -m "Log Polymarket account balance at startup"
```

Open PR, wait for checks, apply only the approved replay override if the artifact is exactly `tier=T3` plus `InsufficientCorpusError: 0 usable corpora`, merge, sync `main`, restart, verify log line.

---

### Task 2: Add Unified Venue Startup Status Lines

**Files:**
- Create: `polymarket/status.py`
- Modify: `main.py`
- Modify: `polymarket/paper_runtime.py`
- Test: `tests/polymarket/test_status.py`

- [ ] **Step 1: Write failing status tests**

Create tests expecting a status object with:

```python
status.enabled is True
status.live_trading is False
status.paper_execution == "blend"
status.public_markets == 100
status.account_balance_dollars == 850.0
status.positions_count == 0
status.last_error is None
```

Expected formatted log:

```text
Polymarket status: enabled=true live_trading=false paper_execution=blend public_markets=100 account_balance=$850.00 positions=0
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_status.py -v
```

Expected: FAIL because `polymarket/status.py` does not exist.

- [ ] **Step 3: Implement `polymarket/status.py`**

Create a dataclass:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolymarketRuntimeStatus:
    enabled: bool
    live_trading: bool
    paper_execution: str
    public_markets: int
    account_balance_dollars: float | None
    positions_count: int | None
    last_error: str | None = None

    def log_line(self) -> str:
        balance = (
            "unavailable"
            if self.account_balance_dollars is None
            else f"${self.account_balance_dollars:.2f}"
        )
        positions = "unavailable" if self.positions_count is None else str(self.positions_count)
        return (
            "Polymarket status: "
            f"enabled={str(self.enabled).lower()} "
            f"live_trading={str(self.live_trading).lower()} "
            f"paper_execution={self.paper_execution} "
            f"public_markets={self.public_markets} "
            f"account_balance={balance} "
            f"positions={positions}"
        )
```

- [ ] **Step 4: Wire status line**

After startup balance and public market probe, log `PolymarketRuntimeStatus.log_line()`. Keep individual logs too; the status line is for operator parity.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_status.py tests/polymarket/test_main_runtime_wiring.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit and PR**

```bash
git add polymarket/status.py main.py tests/polymarket/test_status.py tests/polymarket/test_main_runtime_wiring.py
git commit -m "Add Polymarket runtime status line"
```

Open PR, checks, merge, sync, restart, verify status line.

---

### Task 3: Add Polymarket Market Universe Parity Counters

**Files:**
- Modify: `polymarket/paper_runtime.py`
- Modify: `main.py`
- Test: `tests/polymarket/test_paper_runtime.py`

- [ ] **Step 1: Write failing tests**

Assert `PolymarketPaperRuntime` exposes:

```python
runtime.market_count == 100
runtime.last_refresh_age_seconds >= 0
runtime.last_match_count == 0
runtime.total_news_processed == 1
runtime.total_routed == 0
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py -v
```

Expected: FAIL because these counters do not exist.

- [ ] **Step 3: Implement counters**

In `PolymarketPaperRuntime.__init__`, add:

```python
self.total_news_processed = 0
self.total_routed = 0
self.last_match_count = 0
self.last_error: str | None = None
```

In `process_news`, increment `total_news_processed`, set `last_match_count`, increment `total_routed` by routed count, and set `last_error` on fetch/analysis failures.

- [ ] **Step 4: Log heartbeat**

In `main.py`, after the cache refresh/probe path, log:

```text
[POLYMARKET_PAPER] heartbeat markets=100 total_news_processed=N total_routed=M last_match_count=K
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py tests/polymarket/test_main_runtime_wiring.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit and PR**

```bash
git add polymarket/paper_runtime.py main.py tests/polymarket/test_paper_runtime.py tests/polymarket/test_main_runtime_wiring.py
git commit -m "Add Polymarket paper runtime heartbeat"
```

Open PR, checks, merge, sync, restart, verify heartbeat.

---

### Task 4: Align Ticker Behavior and Candidate Logs

**Files:**
- Modify: `polymarket/paper_runtime.py`
- Modify: `polymarket/candidate_adapter.py`
- Test: `tests/polymarket/test_paper_runtime.py`
- Test: `tests/polymarket/test_candidate_adapter.py`

- [ ] **Step 1: Write failing tests**

Assert every Polymarket candidate emits Kalshi-like stages:

```text
[POLYMARKET_MATCH] ticker=<market_id> score=<score>
[POLYMARKET_ANALYSIS] candidate ticker=<market_id> ...
[POLYMARKET_PAPER] routed ticker=<market_id> ...
```

Also assert no-match logs include market count:

```text
[POLYMARKET_MATCH] no_match markets=100 headline=...
```

- [ ] **Step 2: Run failing tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py -v
```

Expected: FAIL because current logs only emit `no_match` and `routed`.

- [ ] **Step 3: Add structured candidate logs**

Add logs at match, analysis start, analysis skip, and route outcome. Do not write raw payloads or secrets.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/polymarket/test_paper_runtime.py tests/polymarket/test_candidate_adapter.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit and PR**

```bash
git add polymarket/paper_runtime.py polymarket/candidate_adapter.py tests/polymarket/test_paper_runtime.py tests/polymarket/test_candidate_adapter.py
git commit -m "Align Polymarket paper candidate logs"
```

Open PR, checks, merge, sync, restart, verify logs.

---

### Task 5: Define Polymarket Price Feed Parity Boundary

**Files:**
- Create: `docs/polymarket_price_feed_parity.md`
- Create or Modify: `polymarket/price_client.py` only if a supported read-only BBO endpoint is confirmed.
- Test: `tests/polymarket/test_price_client.py` only if implemented.

- [ ] **Step 1: Document current difference**

Write:

```markdown
Kalshi has `KalshiWebSocketClient` and `self.ws.watch([...])`.
Polymarket currently has no equivalent runtime websocket/BBO watcher wired.
Polymarket paper decisions use public market snapshot ask prices from `/v1/markets`.
```

- [ ] **Step 2: Verify official endpoint before implementation**

Use primary documentation or existing contract snapshot to confirm whether Polymarket US exposes a read-only BBO endpoint suitable for runtime refresh.

- [ ] **Step 3: If endpoint is confirmed, write failing tests**

Test that `PolymarketPriceClient.get_bbo(market_id)` returns executable yes/no ask cents and never signs orders.

- [ ] **Step 4: Implement read-only price refresh**

Only implement GET-based price refresh. Do not enable live order placement.

- [ ] **Step 5: Commit and PR**

```bash
git add docs/polymarket_price_feed_parity.md polymarket/price_client.py tests/polymarket/test_price_client.py
git commit -m "Define Polymarket price feed parity"
```

Open PR, checks, merge, sync, restart if runtime code changed.

---

## Acceptance Criteria

- Startup logs include both:
  - `Kalshi account balance: $...`
  - `Polymarket account balance: $...`
- Startup logs include:
  - `Polymarket status: enabled=true live_trading=false paper_execution=blend public_markets=<N> account_balance=$... positions=<N>`
- Post-boot logs include:
  - `[POLYMARKET_PAPER] market_cache_refreshed markets>0`
  - `[POLYMARKET_PAPER] heartbeat ...`
- Candidate logs show why Polymarket did or did not route, with market counts.
- Polymarket paper rows, when generated, are venue-tagged `polymarket_us`.
- `PolymarketAccountClient.place_limit_order()` remains hard-gated until a separate live-trading phase.
- No secrets in logs.
- Runtime dirty state remains out of commits.

## Verification Commands

```bash
.venv/bin/python -m pytest tests/test_polymarket_account_client.py tests/polymarket/test_status.py tests/polymarket/test_paper_runtime.py tests/polymarket/test_main_runtime_wiring.py tests/polymarket/test_candidate_adapter.py -v
.venv/bin/ruff check main.py polymarket tests/polymarket tests/test_polymarket_account_client.py
zsh -ic botcheck
```

Post-restart log scan must verify the account balance/status/heartbeat lines after the new boot timestamp.
