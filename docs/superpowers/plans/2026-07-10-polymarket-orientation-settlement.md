# Polymarket Orientation and Settlement Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore fail-closed Polymarket marking and settlement so side-order drift cannot inflate equity or resolve the wrong paper outcome.

**Architecture:** Treat `normalize_polymarket_market()` as the sole side-orientation boundary, carry an orientation version into paper snapshots, and use the dedicated public settlement endpoint as the only production settlement authority. Existing paper accounting and G7 remain unchanged and consume corrected values.

**Tech Stack:** Python 3.14, dataclasses, requests, SQLite paper state, pytest, Ruff.

## Global Constraints

- Preserve paper mode and all readiness thresholds.
- Never infer named side prices from positional `outcomePrices`.
- Unknown or legacy-unversioned Polymarket marks contribute zero equity.
- Only authoritative settlement values `0` and `1` can mutate paper resolution state.
- Do not mutate the live database during development or verification.
- Do not touch the dirty research-admission worktree.

---

### Task 1: Canonical Long-Book Normalization

**Files:**
- Modify: `polymarket/normalizer.py`
- Modify: `polymarket/models.py`
- Test: `tests/test_polymarket_normalizer.py`

**Interfaces:**
- Consumes: Polymarket market payloads containing `marketSides`, `bestBid`, and `bestAsk`.
- Produces: `PolymarketMarket.price_source: str` and `PolymarketMarket.price_method: str`; correctly oriented `yes_ask_cents` and `no_ask_cents`.

- [ ] **Step 1: Write failing orientation and validation tests**

Add tests with this production-shaped payload:

```python
def _long_book_payload() -> dict:
    return {
        "id": "123",
        "slug": "ewc-usse-ga-2026-11-03-rep",
        "question": "Will the Republican win?",
        "active": True,
        "closed": False,
        "marketSides": [
            {"name": "No", "long": False},
            {"name": "Yes", "long": True},
        ],
        "outcomes": '["No", "Yes"]',
        "outcomePrices": '["0.1300", "0.88"]',
        "bestBid": "0.12",
        "bestAsk": "0.13",
        "endDate": "2026-11-03T00:00:00Z",
    }


def test_authoritative_long_book_ignores_reversed_positional_prices():
    market = normalize_polymarket_market(_long_book_payload())
    assert market.yes_ask_cents == 13
    assert market.no_ask_cents == 88
    assert market.price_source == "polymarket_public"
    assert market.price_method == "pm_long_book_v1"


def test_string_outcomes_without_authoritative_book_are_unpriced():
    payload = _long_book_payload()
    payload.pop("marketSides")
    payload.pop("bestBid")
    payload.pop("bestAsk")
    market = normalize_polymarket_market(payload)
    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None
    assert not market.is_tradeable()


@pytest.mark.parametrize(
    "market_sides",
    [[], [{"long": True}, {"long": True}], [{"long": False}, {"long": False}]],
)
def test_ambiguous_long_side_identity_is_unpriced(market_sides):
    payload = _long_book_payload()
    payload["marketSides"] = market_sides
    market = normalize_polymarket_market(payload)
    assert market.yes_ask_cents is None
    assert market.no_ask_cents is None
```

Also parameterize NaN, infinity, negative values, and values above one; all must be unpriced. Preserve the existing explicitly named outcome-dictionary test.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_polymarket_normalizer.py -q
```

Expected: the long-book tests fail because current code zips `outcomes` with `outcomePrices`, and provenance fields do not exist.

- [ ] **Step 3: Add provenance fields and strict probability parsing**

Add compatible defaults to `PolymarketMarket`:

```python
price_source: str = ""
price_method: str = ""
```

Replace positional string-outcome pricing with helpers shaped as follows:

```python
def _probability_cents(value: Any) -> int | None:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return None
    return int(round(probability * 100))


def _long_book_prices(payload: dict[str, Any]) -> tuple[int, int] | None:
    sides = _parse_json_list(payload.get("marketSides"))
    long_values = [side.get("long") for side in sides if isinstance(side, dict)]
    if len(sides) != 2 or long_values.count(True) != 1 or long_values.count(False) != 1:
        return None
    yes_ask = _probability_cents(payload.get("bestAsk"))
    yes_bid = _probability_cents(payload.get("bestBid"))
    if yes_ask is None or yes_bid is None or yes_bid > yes_ask:
        return None
    return yes_ask, 100 - yes_bid
```

Use the authoritative result first. Use explicitly named embedded outcome dictionaries only when no authoritative book is present. String arrays never create quote dictionaries.

- [ ] **Step 4: Run the normalizer tests and verify GREEN**

Run the Task 1 test command. Expected: all tests pass.

- [ ] **Step 5: Commit the normalization boundary**

```bash
git add polymarket/normalizer.py polymarket/models.py tests/test_polymarket_normalizer.py
git commit -m "fix: orient Polymarket prices from long book"
```

---

### Task 2: Fail-Closed Snapshot Provenance

**Files:**
- Modify: `scripts/mark_open_positions.py`
- Test: `tests/test_mark_open_positions.py`

**Interfaces:**
- Consumes: paper `market_snapshot` JSON containing `price_method`.
- Produces: a fallback mark only for `pm_long_book_v1` or `pm_named_outcomes_v1` snapshots.

- [ ] **Step 1: Write failing snapshot provenance tests**

Update existing snapshot fixtures to include a safe method where fallback is expected. Add:

```python
def test_unversioned_polymarket_snapshot_is_not_used(tmp_path):
    snapshot = json.dumps({"yes_ask_cents": 88, "no_ask_cents": 13})
    db = tmp_path / "paper.db"
    _make_db(db, pm_rows=[_poly_row("pm-legacy", snapshot=snapshot)])

    with patch("scripts.mark_open_positions.PolymarketPublicClient") as client_cls:
        client_cls.return_value.get_market.side_effect = ValueError("unavailable")
        result = compute_open_position_marks(db)

    assert result["snapshot_fallback_count"] == 0
    assert result["unpriced_count"] == 1
    assert result["marked_value"] == 0.0


def test_versioned_polymarket_snapshot_remains_usable(tmp_path):
    snapshot = json.dumps({
        "yes_ask_cents": 13,
        "no_ask_cents": 88,
        "price_method": "pm_long_book_v1",
    })
    db = tmp_path / "paper.db"
    _make_db(db, pm_rows=[_poly_row("pm-safe", snapshot=snapshot)])

    with patch("scripts.mark_open_positions.PolymarketPublicClient") as client_cls:
        client_cls.return_value.get_market.side_effect = ValueError("unavailable")
        result = compute_open_position_marks(db)

    assert result["snapshot_fallback_count"] == 1
    assert result["priced_count"] == 1
```

- [ ] **Step 2: Run snapshot tests and verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_mark_open_positions.py -q
```

Expected: the unversioned snapshot is currently accepted and the safe fixture has no provenance enforcement.

- [ ] **Step 3: Enforce safe methods**

At the top of `_poly_snapshot_mark_cents()` parse the JSON object and require:

```python
safe_methods = {"pm_long_book_v1", "pm_named_outcomes_v1"}
if payload.get("price_method") not in safe_methods:
    return None
```

Leave live-first behavior and zero-equity treatment for unknown marks unchanged.

- [ ] **Step 4: Run snapshot tests and verify GREEN**

Run the Task 2 test command. Expected: all tests pass.

- [ ] **Step 5: Commit snapshot safety**

```bash
git add scripts/mark_open_positions.py tests/test_mark_open_positions.py
git commit -m "fix: reject unversioned Polymarket marks"
```

---

### Task 3: Dedicated Public Settlement Endpoint

**Files:**
- Modify: `polymarket/public_client.py`
- Test: `tests/test_polymarket_public_client.py`

**Interfaces:**
- Consumes: a stored Polymarket slug or numeric market ID.
- Produces: `PolymarketPublicClient.get_market_settlement(market_id: str) -> dict[str, Any]`.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_get_market_settlement_calls_slug_endpoint():
    client = PolymarketPublicClient()
    response = _response({"slug": "will-example-happen", "settlement": "1"})
    client._session.request = MagicMock(return_value=response)

    payload = client.get_market_settlement("will-example-happen")

    assert payload == {"slug": "will-example-happen", "settlement": "1"}
    call = client._session.request.call_args
    assert call.args[1].endswith("/v1/markets/will-example-happen/settlement")


def test_get_market_settlement_resolves_numeric_id_to_slug():
    client = PolymarketPublicClient()
    client.get_market_payload = MagicMock(return_value={"id": "123", "slug": "canonical-slug"})
    client._request = MagicMock(return_value={"slug": "canonical-slug", "settlement": 0})

    payload = client.get_market_settlement("123")

    assert payload["settlement"] == 0
    client._request.assert_called_once_with("GET", "/v1/markets/canonical-slug/settlement")


def test_get_market_settlement_rejects_slug_mismatch():
    client = PolymarketPublicClient()
    client._request = MagicMock(return_value={"slug": "different", "settlement": 1})
    with pytest.raises(ValueError, match="slug mismatch"):
        client.get_market_settlement("expected")
```

- [ ] **Step 2: Run endpoint tests and verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_polymarket_public_client.py -q
```

Expected: `get_market_settlement` is absent.

- [ ] **Step 3: Implement canonical slug resolution and response validation**

Implement:

```python
def get_market_settlement(self, market_id: str) -> dict[str, Any]:
    requested = str(market_id).strip()
    slug = requested
    if requested.isdigit():
        market = self.get_market_payload(requested)
        slug = str(market.get("slug") or "").strip()
    if not slug:
        raise ValueError(f"Polymarket market {market_id!r} has no canonical slug")
    data = self._request("GET", f"/v1/markets/{slug}/settlement")
    if not isinstance(data, dict):
        raise ValueError("Polymarket settlement response must be an object")
    returned_slug = str(data.get("slug") or "").strip()
    if returned_slug != slug:
        raise ValueError(
            f"Polymarket settlement slug mismatch: expected {slug!r}, got {returned_slug!r}"
        )
    return data
```

Translate an endpoint HTTP 404 into the same narrow not-found `ValueError` contract used by `get_market_payload`; do not mask other HTTP or schema errors.

- [ ] **Step 4: Run public-client tests and verify GREEN**

Run the Task 3 command. Expected: all tests pass.

- [ ] **Step 5: Commit the endpoint client**

```bash
git add polymarket/public_client.py tests/test_polymarket_public_client.py
git commit -m "feat: query Polymarket settlement endpoint"
```

---

### Task 4: Authoritative Settlement Parsing

**Files:**
- Modify: `polymarket/settlement_reconciler.py`
- Test: `tests/polymarket/test_settlement_reconciler.py`

**Interfaces:**
- Consumes: the dedicated settlement payload or an injected explicit `resolvedOutcome` payload.
- Produces: boolean `resolved_yes` only for authoritative terminal outcomes.

- [ ] **Step 1: Write failing settlement tests**

Add tests proving the public source calls `get_market_settlement`, `1` resolves YES,
`0` resolves NO, and these values raise `SettlementDriftError`: `0.5`, NaN,
infinity, missing `settlement`, and outcomePrices-only payloads. Preserve the
explicit `resolvedOutcome` compatibility test.

Representative parser test:

```python
@pytest.mark.parametrize("value, expected", [(1, True), ("1", True), (0, False), ("0", False)])
def test_authoritative_settlement_values(value, expected):
    assert _resolved_yes_from_payload("market", {"settlement": value}) is expected


@pytest.mark.parametrize("value", [0.5, "0.5", float("nan"), float("inf"), -1, 2])
def test_nonbinary_settlement_values_fail_closed(value):
    with pytest.raises(SettlementDriftError):
        _resolved_yes_from_payload("market", {"settlement": value})
```

- [ ] **Step 2: Run settlement tests and verify RED**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/polymarket/test_settlement_reconciler.py -q
```

Expected: current source calls generic metadata and current parser infers from positional prices.

- [ ] **Step 3: Replace generic metadata settlement authority**

Change `PolymarketPublicSettlementSource.get_settlement()` to call
`self._client.get_market_settlement(market_id)`. Preserve narrow translation of
known not-found errors to `SettlementNotFound`.

Implement numeric parsing as:

```python
def _resolved_yes_from_settlement_value(market_id: str, raw: Any) -> bool:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise SettlementDriftError(
            f"settlement payload for {market_id} has nonnumeric settlement"
        ) from exc
    if not math.isfinite(value) or value not in {0.0, 1.0}:
        raise SettlementDriftError(
            f"settlement payload for {market_id} has nonbinary settlement"
        )
    return value == 1.0
```

In `_resolved_yes_from_payload()`, prefer `settlement` when present, preserve
explicit `resolvedOutcome`, and reject `outcomePrices` as insufficient.

- [ ] **Step 4: Run settlement tests and verify GREEN**

Run the Task 4 command. Expected: all tests pass and drift tests leave DB rows unresolved.

- [ ] **Step 5: Commit settlement parsing**

```bash
git add polymarket/settlement_reconciler.py tests/polymarket/test_settlement_reconciler.py
git commit -m "fix: settle Polymarket from authoritative result"
```

---

### Task 5: Financial-Path Integration Proof

**Files:**
- Modify: `tests/test_mark_open_positions.py`
- Modify: `tests/test_main_startup.py`
- Modify: `tests/test_blend_task.py`

**Interfaces:**
- Consumes: corrected normalized held-side marks.
- Produces: a regression proof that corrected equity exceeds the G7 drawdown threshold and readiness binds on `G7_open_exposure_drawdown`.

- [ ] **Step 1: Write the failing end-to-end mark/G7 regression**

Build a temporary paper DB with `$8.76` bankroll and held-side fixtures matching
the audited open exposure. Stub public-client responses with authoritative long
books. Assert marked value is approximately `$29.59`, equity approximately
`$38.35`, and drawdown greater than `0.20`. Pass that drawdown into the existing
BlendTask readiness fixture and assert the gate summary binds on
`G7_open_exposure_drawdown`.

- [ ] **Step 2: Run the integration tests and verify RED before production changes, or document prior GREEN from lower-level fixes**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_mark_open_positions.py \
  tests/test_main_startup.py \
  tests/test_blend_task.py \
  tests/test_trade_readiness_gate.py -q
```

Expected before Tasks 1-4: wrong-side fixtures produce an optimistic mark. If
Tasks 1-4 already make this new integration test pass, preserve the earlier RED
outputs as the TDD evidence and do not weaken the assertion.

- [ ] **Step 3: Run the complete P0 and Polymarket suites**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/test_polymarket_normalizer.py \
  tests/test_polymarket_public_client.py \
  tests/test_mark_open_positions.py \
  tests/test_main_startup.py \
  tests/test_blend_task.py \
  tests/test_trade_readiness_gate.py \
  tests/polymarket/test_settlement_reconciler.py -q

/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest \
  tests/polymarket tests/test_polymarket*.py tests/test_mark_open_positions.py \
  tests/test_main_startup.py tests/test_blend_task.py \
  tests/test_trade_readiness_gate.py -q
```

Expected: zero failures.

- [ ] **Step 4: Run Ruff and diff checks**

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check \
  polymarket/normalizer.py polymarket/models.py polymarket/public_client.py \
  polymarket/settlement_reconciler.py scripts/mark_open_positions.py \
  tests/test_polymarket_normalizer.py tests/test_polymarket_public_client.py \
  tests/test_mark_open_positions.py tests/polymarket/test_settlement_reconciler.py
git diff --check
```

Expected: zero findings.

- [ ] **Step 5: Commit the financial-path proof**

```bash
git add tests/test_mark_open_positions.py tests/test_main_startup.py tests/test_blend_task.py
git commit -m "test: prove corrected Polymarket drawdown gate"
```

---

### Task 6: Review, PR, and Runtime Verification

**Files:**
- Read: all changed files and `logs/reports/bot_7d_multi_agent_assessment_20260710.md`
- No live DB writes.

**Interfaces:**
- Consumes: the completed P0 branch.
- Produces: merged PR, synced running worktree, restarted bot, and post-boot evidence.

- [ ] **Step 1: Request independent financial-path review**

Review must check side orientation, 0/1 settlement semantics, legacy snapshot
fail-closed behavior, atomic resolution preservation, and test coverage.

- [ ] **Step 2: Push and create the PR**

```bash
git push -u origin fix/polymarket-outcome-settlement
gh pr create --base main --head fix/polymarket-outcome-settlement \
  --title "Fix Polymarket mark orientation and settlement" \
  --body-file /tmp/polymarket-p0-pr.md
```

- [ ] **Step 3: Wait for required CI and merge**

All required checks must pass. Merge only after the independent review has no
unresolved high-severity finding.

- [ ] **Step 4: Sync the protected running worktree**

Fetch `origin/main` and fast-forward the current `research-paper-admission-bridge`
branch only after confirming the P0 paths do not overlap its dirty files. Do not
stage or clean research/runtime changes.

- [ ] **Step 5: Restart with the authorized alias**

```bash
zsh -ic botrestart
```

- [ ] **Step 6: Verify post-boot safety**

Run `zsh -ic botcheck`, recompute current marks read-only, and scan post-boot logs.
Required evidence:

- LaunchAgent running on a fresh PID and boot timestamp.
- Paper mode still enabled; no live order.
- Corrected equity near `$38.35` and drawdown near `23.31%`, subject to live price movement.
- Next eligible gate decision binds `G7_open_exposure_drawdown` while drawdown exceeds 20%.
- Settlement polling no longer emits positional `ambiguous outcomePrices` failures.
- No new traceback, CRITICAL, or non-transient ERROR markers.
