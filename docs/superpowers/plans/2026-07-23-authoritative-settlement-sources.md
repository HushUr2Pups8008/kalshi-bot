# Authoritative Settlement Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, exact, default-off authoritative settlement source for Kalshi and Polymarket US that can drain only the existing capital-guard shadow backlog without changing paper or live financial state.

**Architecture:** Reuse `utils.bounded_https.fetch_bounded_https_ipv4` as the only transport implementation. Add narrow async JSON methods to the existing venue clients, then compose them in `trading.authoritative_settlement_source.py` behind the collector's strict async protocol. The collector receives a rehydrated immutable source-observation snapshot for correction validation, but the shadow store remains the sole writer and still owns append-only record-level supersession.

**Tech Stack:** Python 3.14, asyncio, aiohttp transport already encapsulated by `utils.bounded_https`, `requests` only in legacy clients, SQLite shadow store, pytest, Ruff.

## Global Constraints

- Reuse `utils.bounded_https.fetch_bounded_https_ipv4`; do not add a second DNS, TLS, HTTP, retry, redirect, or pinned-IP implementation.
- Accept only documented Kalshi API hosts (`external-api.kalshi.com`, `api.elections.kalshi.com`, `external-api.demo.kalshi.co`, `demo-api.kalshi.co`) and `gateway.polymarket.us`; reject arbitrary configured hosts before DNS.
- Every external lookup has one finite deadline and byte cap. Polymarket's settlement-404 disambiguation consumes the same total deadline across both official requests.
- Every Polymarket settlement response, including `200`, is paired with an exact market-by-slug identity read under the same total deadline. A clean `404` is `SettlementNotFound` only when both the settlement and exact market reads return `404`; a market `200` after settlement `404` is nonterminal. A market `404` after settlement `200` is drift. Timeout, connection, JSON, status, identity, and schema errors must not become absence.
- Only known Kalshi nonterminal lifecycle statuses return `None`; unknown lifecycle or result shapes raise `SettlementDriftError`. Kalshi accepts only `is_safe_kalshi_identifier` tickers and requires exact case-sensitive response ticker equality.
- The source accepts no ticker-only aliases as authority. Kalshi requires `alias == venue_market_id`; Polymarket requires a canonical `venue_market_id` and an exact slug alias.
- Correction validation has two separate hashes: `SettlementObservation.supersedes_observation_sha256` points to the prior *source* observation hash, while `SettlementObservationRecord.supersedes_observation_sha256` continues to point to the prior append-only *shadow-record* hash.
- No runtime registration, scheduler wiring, collection flag activation, paper DB mutation, payout, evaluation, replay, G7, sizing, liquidity, live order, weather, or configuration transition belongs in this plan.
- No external HTTP test calls. Tests inject bounded fetchers and deterministic clocks.
- Runtime databases, `.env`, `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/` stay out of commits.
- Each bounded client validates its own finite positive timeout and strict configured base before an injected or real fetcher. It supplies a named positive byte cap on every call; it never falls back to the legacy synchronous requests clients.
- At the source boundary, malformed client payload, endpoint schema, slug, ID, and response identity validation failures become `SettlementDriftError`; they must reach the collector's `source_drift` quarantine taxonomy rather than `internal_source_error`.
- Polymarket rejects any conflicting `id`, `market_id`, or `marketId` before enriching a settlement payload. It canonicalizes the verified ID to `MarketRef.venue_market_id` before normalization so numeric/string representation variation cannot create a correction.
- For an unchanged authoritative payload, the source reuses the prior observation's `effective_at` while recording a new `observed_at`. It assigns a new effective time only after a semantic change, then links the new source observation to the prior source hash.

---

### Task 1: Add Exact Bounded Kalshi Market Read

**Files:**
- Modify: `kalshi/rest_client.py:120-530`
- Modify: `tests/test_venue_client_protocol.py`
- Create: `tests/test_authoritative_settlement_clients.py`

**Interfaces:**
- Consumes: `utils.bounded_https.fetch_bounded_https_ipv4(url, canonical_host, provider_name, user_agent, timeout, max_bytes)`.
- Produces: `KalshiRestClient.get_market_exact_bounded(ticker: str, *, timeout_seconds: float, fetcher: BoundedHttpsFetcher | None = None) -> Coroutine[Any, Any, KalshiMarket | None]`.
- `None` is reserved for a received HTTP 404. Every other non-2xx response and all malformed payloads propagate as typed errors. The reader validates a safe ticker, a finite positive timeout, and exact case-sensitive response ticker identity before returning a market.

- [ ] **Step 1: Write failing exact-host and 404 tests**

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("host", _KALSHI_AUTHORITATIVE_HOSTS)
async def test_kalshi_bounded_exact_market_uses_only_documented_hosts(host):
    client = KalshiRestClient()
    client._base = f"https://{host}/trade-api/v2"
    calls: list[dict[str, object]] = []

    async def fetcher(url: str, **kwargs: object) -> bytes:
        calls.append({"url": url, **kwargs})
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    assert await client.get_market_exact_bounded(
        "KXTEST-1", timeout_seconds=2.0, fetcher=fetcher
    ) is None
    assert calls[0]["url"] == f"https://{host}/trade-api/v2/markets/KXTEST-1"
    assert calls[0]["canonical_host"] == host


@pytest.mark.asyncio
async def test_kalshi_bounded_exact_market_rejects_unapproved_base_before_fetch():
    client = KalshiRestClient()
    client._base = "https://untrusted.example/trade-api/v2"

    with pytest.raises(ValueError, match="documented Kalshi HTTPS host"):
        await client.get_market_exact_bounded("KXTEST-1", timeout_seconds=2.0)
```

Also test all malformed bases before fetch (wrong scheme, suffix or trailing-dot host, userinfo, query, fragment, malformed or non-443 port, extra path, and trailing slash), all unsafe tickers, non-finite or non-positive timeouts, returned ticker mismatch including case mismatch, malformed UTF-8/JSON/object/envelope payloads, and non-404 status/transport failures. Confirm the injected fetcher is called exactly once for a valid lookup and the legacy `_request` client is never called.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_authoritative_settlement_clients.py -q
```

Expected: FAIL because `get_market_exact_bounded` does not exist.

- [ ] **Step 3: Implement the narrow async reader**

```python
_KALSHI_AUTHORITATIVE_HOSTS = frozenset({
    "external-api.kalshi.com",
    "api.elections.kalshi.com",
    "external-api.demo.kalshi.co",
    "demo-api.kalshi.co",
})

async def get_market_exact_bounded(
    self,
    ticker: str,
    *,
    timeout_seconds: float,
    fetcher: BoundedHttpsFetcher | None = None,
) -> KalshiMarket | None:
    url, host = _authoritative_kalshi_market_url(self._base, ticker)
    bounded_fetch = fetcher or fetch_bounded_https_ipv4
    try:
        raw = await bounded_fetch(
            url,
            canonical_host=host,
            provider_name="Kalshi authoritative settlement",
            user_agent="kalshi-bot/authoritative-settlement-v1",
            timeout=timeout_seconds,
            max_bytes=_AUTHORITATIVE_MARKET_MAX_BYTES,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    payload = _bounded_json_object(raw, provider_name="Kalshi authoritative settlement")
    return normalize_market_detail(payload)
```

The URL helper must require an HTTPS base with one of the documented hosts, exact `/trade-api/v2` path (trailing slash rejected), no query, fragment, userinfo, or non-443 port. Validate `is_safe_kalshi_identifier(ticker)` before fetch and quote the ticker as one path segment. `_bounded_json_object` decodes strict UTF-8 JSON and requires an object without translating decode failures into not-found. After normalization, reject any ticker that is not byte-for-byte equal to the request.

- [ ] **Step 4: Run focused client regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_authoritative_settlement_clients.py \
  tests/test_venue_client_protocol.py \
  tests/test_kalshi_rest_transient_logging.py -q
```

Expected: PASS; legacy synchronous `get_market_exact` behavior remains unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add kalshi/rest_client.py tests/test_authoritative_settlement_clients.py tests/test_venue_client_protocol.py
git commit -m "feat: add bounded Kalshi market lookup"
```

---

### Task 2: Add Exact Bounded Polymarket US Reads

**Files:**
- Modify: `polymarket/public_client.py:27-210`
- Modify: `tests/test_polymarket_public_client.py`
- Modify: `tests/test_authoritative_settlement_clients.py`

**Interfaces:**
- Consumes: the same bounded HTTPS utility and a caller-supplied remaining deadline.
- Produces:

```python
async def get_market_settlement_exact_bounded(
    self, slug: str, *, timeout_seconds: float, fetcher: BoundedHttpsFetcher | None = None
) -> dict[str, Any] | None: ...

async def get_market_by_slug_exact_bounded(
    self, slug: str, *, timeout_seconds: float, fetcher: BoundedHttpsFetcher | None = None
) -> dict[str, Any] | None: ...
```

- `None` is only a received HTTP 404. Both methods require the configured base to be exactly `https://gateway.polymarket.us` with no path, query, fragment, userinfo, or port, a non-empty exact slug, finite positive timeout, and a named positive byte cap. Returned slug must be a string byte-for-byte equal to the requested slug.

- [ ] **Step 1: Write failing settlement-404 disambiguation tests**

```python
@pytest.mark.asyncio
async def test_polymarket_settlement_404_then_exact_market_200_is_nonterminal():
    client = PolymarketPublicClient()
    responses = [http_404(), json_bytes({"slug": "exact-slug", "id": "42"})]

    async def fetcher(_url: str, **_kwargs: object) -> bytes:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    assert await client.get_market_settlement_exact_bounded(
        "exact-slug", timeout_seconds=2.0, fetcher=fetcher
    ) is None
    assert await client.get_market_by_slug_exact_bounded(
        "exact-slug", timeout_seconds=2.0, fetcher=fetcher
    ) == {"slug": "exact-slug", "id": "42"}


@pytest.mark.asyncio
async def test_polymarket_bounded_reader_rejects_wrong_slug_before_returning_data():
    client = PolymarketPublicClient(base_url="https://gateway.polymarket.us")

    async def bad_slug(_url: str, **_kwargs: object) -> bytes:
        return json_bytes({"slug": "other", "settlement": 1})

    with pytest.raises(ValueError, match="slug mismatch"):
        await client.get_market_settlement_exact_bounded(
            "exact", timeout_seconds=2.0, fetcher=bad_slug
        )
```

Add no-fetcher-call parametrized tests for non-gateway host, any path, query, fragment, userinfo, malformed/non-443 port, empty or invalid slug, and non-finite/non-positive timeout. Assert the injected fetcher sees the documented exact URL, canonical host, fixed user agent, timeout, and `_AUTHORITATIVE_POLYMARKET_MAX_BYTES`. Exercise malformed UTF-8, invalid JSON, scalar/list/null, missing/wrong-type/whitespace slug, and unexpected exact-market envelope; none may become `None`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_authoritative_settlement_clients.py -q
```

Expected: FAIL because the bounded Polymarket methods do not exist.

- [ ] **Step 3: Implement bounded JSON helpers and exact methods**

```python
_POLYMARKET_AUTHORITATIVE_HOST = "gateway.polymarket.us"

async def get_market_settlement_exact_bounded(...):
    return await self._get_exact_bounded_object(
        f"/v1/markets/{quote(slug, safe='')}/settlement",
        expected_slug=slug,
        timeout_seconds=timeout_seconds,
        fetcher=fetcher,
        provider_name="Polymarket US authoritative settlement",
    )

async def get_market_by_slug_exact_bounded(...):
    return await self._get_exact_bounded_object(
        f"/v1/market/slug/{quote(slug, safe='')}",
        expected_slug=slug,
        timeout_seconds=timeout_seconds,
        fetcher=fetcher,
        provider_name="Polymarket US authoritative market",
    )
```

`_get_exact_bounded_object` must validate the base before calling the fetcher, use `fetch_bounded_https_ipv4`, translate only `urllib.error.HTTPError.code == 404` to `None`, require UTF-8 JSON object output, and require the exact `slug`. It must not reuse the synchronous rate limiter, `requests.Session`, fallback listing lookup, or cursor traversal.

Define `_AUTHORITATIVE_POLYMARKET_MAX_BYTES` as a conservative positive constant and pass it on every call. Keep response identity checks strict: do not coerce, trim, or stringify a returned slug.

- [ ] **Step 4: Run focused Polymarket regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_authoritative_settlement_clients.py \
  tests/test_polymarket_public_client.py \
  tests/polymarket/test_settlement_reconciler.py -q
```

Expected: PASS; synchronous public client behavior remains unchanged.

- [ ] **Step 5: Commit Task 2**

```bash
git add polymarket/public_client.py tests/test_authoritative_settlement_clients.py tests/test_polymarket_public_client.py
git commit -m "feat: add bounded Polymarket settlement reads"
```

---

### Task 3: Carry a Validated Prior Source Observation Through the Shadow Backlog

**Files:**
- Modify: `trading/capital_guard_shadow.py:794-858,1414-1636,1748-1941`
- Modify: `tasks/capital_guard_shadow_settlement.py:33-198`
- Modify: `tests/test_capital_guard_shadow_settlement.py`

**Interfaces:**
- Extends `CandidateSettlementBacklog` with `prior_authoritative_observation: SettlementObservation | None`.
- Extends the collector source protocol:

```python
async def get_settlement_exact(
    self,
    market_ref: MarketRef,
    *,
    prior_observation: SettlementObservation | None,
) -> SettlementObservation | None: ...
```

- Produces an exact rehydration of the current head's source observation, using `CurrentAuthoritativeHead.authoritative_observation_sha256`, not the shadow-record `observation_sha256`.

- [ ] **Step 1: Write failing snapshot and correction-link tests**

```python
class CapturingSource:
    def __init__(self, observation: SettlementObservation) -> None:
        self.observation = observation
        self.prior_observations: list[SettlementObservation | None] = []

    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation:
        assert market_ref == self.observation.market_ref
        self.prior_observations.append(prior_observation)
        return self.observation


@pytest.mark.asyncio
async def test_collector_passes_rehydrated_source_head_to_the_exact_source(tmp_path):
    store = _initialized_store(tmp_path)
    record = _append_candidate(store, candidate())
    market_key = store.settlement_market_backlog(limit=1)[0]
    market_ref = store.candidate_settlement_backlog(market_key, limit=1).market_ref
    first = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(
        store=store, source=SequenceSource({market_ref: [first]})
    ).run_once()
    source = CapturingSource(first)

    await CapitalGuardShadowSettlementCollector(store=store, source=source).run_once()

    assert source.prior_observations[0] is not None
    assert source.prior_observations[0].observation_sha256 == first.observation_sha256


@pytest.mark.asyncio
async def test_valid_source_correction_must_link_prior_source_hash_and_store_links_prior_record(tmp_path):
    store = _initialized_store(tmp_path)
    record = _append_candidate(store, candidate())
    market_key = store.settlement_market_backlog(limit=1)[0]
    market_ref = store.candidate_settlement_backlog(market_key, limit=1).market_ref
    prior = _authoritative(market_ref)
    await CapitalGuardShadowSettlementCollector(
        store=store, source=SequenceSource({market_ref: [prior]})
    ).run_once()
    prior_head = store.current_authoritative_head(market_ref)
    assert prior_head is not None
    correction = build_settlement_observation(
        market_ref=market_ref,
        outcome=MarketOutcome.NO,
        authoritative_outcome="no",
        authoritative_payload={"market_id": market_ref.venue_market_id, "result": "no"},
        observed_at=prior.observed_at + timedelta(seconds=1),
        effective_at=prior.effective_at + timedelta(seconds=1),
        rules_version=prior.rules_version,
        source_id=prior.source_id,
        previous_observation=prior,
        supersedes_observation_sha256=prior.observation_sha256,
    )
    result = await CapitalGuardShadowSettlementCollector(
        store=store, source=SequenceSource({market_ref: [correction]})
    ).run_once()

    assert result.inserted_observations == 1
    with sqlite3.connect(store.db_path) as conn:
        supersedes = conn.execute(
            "SELECT supersedes_observation_sha256 "
            "FROM capital_guard_shadow_observations "
            "WHERE authoritative_observation_sha256 = ?",
            (correction.observation_sha256,),
        ).fetchone()[0]
    assert supersedes == prior_head.observation_sha256
    assert supersedes != prior.observation_sha256
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_capital_guard_shadow_settlement.py -q
```

Expected: FAIL because the backlog does not expose a prior source observation and the protocol does not accept it.

- [ ] **Step 3: Rehydrate and validate the source-level prior observation**

```python
def _source_observation_from_head(
    head: CurrentAuthoritativeHead,
) -> SettlementObservation:
    return build_settlement_observation(
        market_ref=head.market_ref,
        outcome=MarketOutcome(head.outcome),
        authoritative_outcome=json.loads(head.authoritative_outcome_json),
        authoritative_payload=json.loads(head.source_payload_json),
        observed_at=head.observed_at,
        effective_at=head.effective_at,
        rules_version=head.rules_version,
        source_id=head.source_id,
        void_refund=_void_refund_from_json(head.void_refund_json),
    )
```

Require the rebuilt source hash to equal `head.authoritative_observation_sha256`; otherwise raise `CapitalGuardShadowIdentityError` with `authoritative_head_invalid` before source I/O. `candidate_settlement_backlog` puts this object in the new field in the same read transaction that snapshots `current_head_sha256`. The collector passes it as a keyword to every source call and continues to quarantine a changed candidate set or head after fetch.

- [ ] **Step 4: Accept only a source correction linked to the exact prior source hash**

```python
if head is None and observation.supersedes_observation_sha256 is not None:
    return quarantine_source_drift(...)
if head is not None and head.semantic_sha256 != semantic_sha256:
    if observation.supersedes_observation_sha256 != head.authoritative_observation_sha256:
        return quarantine_source_drift(...)
if head is not None and head.semantic_sha256 == semantic_sha256:
    if observation.supersedes_observation_sha256 is not None:
        return quarantine_source_drift(...)

record = SettlementObservationRecord(
    ...,
    supersedes_observation_sha256=(
        head.observation_sha256 if head is not None else None
    ),
)
```

Keep record-level supersession owned by the store. A source hash must never be inserted into the record foreign key. Preserve existing void deferral, race quarantine, collision, and append-only behavior.

- [ ] **Step 5: Run focused shadow-store tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_capital_guard_shadow.py \
  tests/test_capital_guard_shadow_settlement.py \
  tests/test_capital_guard_shadow_runtime_surface.py -q
```

Expected: PASS, including forged source supersession, current-head race, equal/backward-time correction, and collision cases.

- [ ] **Step 6: Commit Task 3**

```bash
git add trading/capital_guard_shadow.py tasks/capital_guard_shadow_settlement.py tests/test_capital_guard_shadow_settlement.py
git commit -m "feat: validate shadow settlement correction lineage"
```

---

### Task 4: Compose the Strict Official Settlement Source

**Files:**
- Create: `trading/authoritative_settlement_source.py`
- Modify: `tests/test_authoritative_settlement_clients.py`
- Modify: `tests/test_capital_guard_shadow_settlement.py`

**Interfaces:**

```python
class AuthoritativeSettlementSource:
    async def get_settlement_exact(
        self,
        market_ref: MarketRef,
        *,
        prior_observation: SettlementObservation | None,
    ) -> SettlementObservation | None: ...
```

- Consumes: exact bounded venue client methods, `normalize_kalshi_settlement`, `normalize_polymarket_settlement`, and `SettlementNotFound`.
- Produces: only `SettlementObservation`, `None` for a known nonterminal lifecycle, or typed `SettlementNotFound`, `SettlementDriftError`, `TimeoutError`, or `ConnectionError` for the collector taxonomy.

- [ ] **Step 1: Write failing adapter behavior tests**

```python
@pytest.mark.asyncio
async def test_source_turns_known_kalshi_nonterminal_status_into_none():
    source = AuthoritativeSettlementSource(
        kalshi_client=FakeKalshiClient(status="closed"),
        polymarket_client=FailingPolymarketClient(),
        clock=fixed_clock,
        monotonic=fixed_monotonic,
    )
    assert await source.get_settlement_exact(
        MarketRef(Venue.KALSHI, "KXTEST-1", "KXTEST-1"),
        prior_observation=None,
    ) is None


@pytest.mark.asyncio
async def test_source_disambiguates_polymarket_settlement_404_with_one_deadline():
    client = FakePolymarketClient(settlement=None, market={"slug": "exact", "id": "42"})
    source = AuthoritativeSettlementSource(
        kalshi_client=FailingKalshiClient(),
        polymarket_client=client,
        clock=fixed_clock,
        monotonic=advancing_monotonic,
        timeout_seconds=3.0,
    )
    assert await source.get_settlement_exact(
        MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
    ) is None
    assert client.timeout_arguments[1] < client.timeout_arguments[0]


@pytest.mark.asyncio
async def test_source_confirms_polymarket_canonical_identity_for_settlement_200():
    client = FakePolymarketClient(
        settlement={"slug": "exact", "settlement": 1},
        market={"slug": "exact", "id": 42},
    )
    source = AuthoritativeSettlementSource(
        kalshi_client=FailingKalshiClient(),
        polymarket_client=client,
        clock=fixed_clock,
        monotonic=advancing_monotonic,
        timeout_seconds=3.0,
    )
    observation = await source.get_settlement_exact(
        MarketRef(Venue.POLYMARKET_US, "42", "exact"), prior_observation=None
    )
    assert observation is not None
    assert observation.authoritative_payload["id"] == "42"
    assert len(client.timeout_arguments) == 2
    assert client.timeout_arguments[1] < client.timeout_arguments[0]
```

Add source-level deterministic-clock tests for settlement `200` with matching ID, settlement `404` plus matching market `200`, both `404`, exact-market `404` after settlement `200`, timeout before the second fetch, malformed market payload, mismatched ID/slug, conflicting ID in a settlement payload, and non-2xx. All failures except clean dual-404 must fail closed. Verify two identical polls with different `observed_at` create no source correction or shadow append; changed outcome must create one source correction linked to the prior source hash while the store record links to the prior record hash.

- [ ] **Step 2: Run adapter tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_authoritative_settlement_clients.py -q
```

Expected: FAIL because `AuthoritativeSettlementSource` does not exist.

- [ ] **Step 3: Implement the venue dispatcher and exact lifecycle policy**

```python
_KALSHI_NONTERMINAL = frozenset({
    "initialized", "inactive", "active", "closed", "determined",
    "disputed", "amended", "unopened", "open", "paused",
})
_KALSHI_TERMINAL = frozenset({"finalized", "settled"})

async def get_settlement_exact(...):
    if market_ref.venue is Venue.KALSHI:
        return await self._get_kalshi(market_ref, prior_observation=prior_observation)
    if market_ref.venue is Venue.POLYMARKET_US:
        return await self._get_polymarket(market_ref, prior_observation=prior_observation)
    raise SettlementDriftError("unsupported authoritative settlement venue")
```

For Kalshi: require the exact alias/ticker equality, fetch by `venue_market_id`, return `None` for only `_KALSHI_NONTERMINAL`, reject unknown statuses, and normalize terminal markets. For Polymarket: require a canonical numeric `venue_market_id` and valid exact slug alias before I/O. Fetch settlement by slug, then fetch exact market-by-slug with the remaining deadline for both settlement `200` and `404`. Market `200` requires exact slug and canonical ID equivalence. A settlement `404` plus matching market `200` returns `None`; dual `404` raises `SettlementNotFound`; a market `404` after settlement `200` is `SettlementDriftError`. Reject any conflicting settlement `id`, `market_id`, or `marketId`; remove equivalent ID aliases and inject the verified canonical `MarketRef.venue_market_id` as `id` before normalization.

At the source boundary, translate malformed client payload, schema, slug, ID, and response-identity `ValueError` failures to `SettlementDriftError`, while preserving `SettlementNotFound`, timeout, connection, and HTTP errors. To prevent correction churn, first normalize the candidate with `effective_at=prior_observation.effective_at` when a prior observation exists, otherwise use the source clock. If the provisional candidate hash equals the prior source hash, return it without supersession even when `observed_at` changed. If it differs, rebuild using a new effective time from the source clock with `previous_observation=prior_observation` and `supersedes_observation_sha256=prior_observation.observation_sha256`. Do not infer or supply a void refund contract.

- [ ] **Step 4: Run source and collector integration tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_authoritative_settlement_clients.py \
  tests/test_capital_guard_shadow_settlement.py \
  tests/test_settlement_observation.py -q
```

Expected: PASS; source errors map to the existing collector taxonomy and no test creates a runtime collector.

- [ ] **Step 5: Commit Task 4**

```bash
git add trading/authoritative_settlement_source.py tests/test_authoritative_settlement_clients.py tests/test_capital_guard_shadow_settlement.py
git commit -m "feat: add bounded authoritative settlement source"
```

---

### Task 5: Verify Scope, Protected Delivery, and Runtime Non-Activation

**Files:**
- Modify: `docs/superpowers/plans/2026-07-23-authoritative-settlement-sources.md`
- Review: `utils/bounded_https.py`
- Review: `kalshi/rest_client.py`
- Review: `polymarket/public_client.py`
- Review: `trading/authoritative_settlement_source.py`
- Review: `trading/capital_guard_shadow.py`
- Review: `tasks/capital_guard_shadow_settlement.py`

**Interfaces:**
- Consumes: Tasks 1-4 exact branch head.
- Produces: a merged, default-off source adapter with no process restart required.

- [ ] **Step 1: Run static and focused verification**

```bash
.venv/bin/python -m ruff check \
  kalshi/rest_client.py polymarket/public_client.py \
  trading/authoritative_settlement_source.py \
  trading/capital_guard_shadow.py tasks/capital_guard_shadow_settlement.py \
  tests/test_authoritative_settlement_clients.py \
  tests/test_capital_guard_shadow_settlement.py
.venv/bin/python -m py_compile \
  kalshi/rest_client.py polymarket/public_client.py \
  trading/authoritative_settlement_source.py \
  trading/capital_guard_shadow.py tasks/capital_guard_shadow_settlement.py
.venv/bin/python -m pytest \
  tests/test_bounded_https.py \
  tests/test_authoritative_settlement_clients.py \
  tests/test_polymarket_public_client.py \
  tests/test_venue_client_protocol.py \
  tests/test_settlement_observation.py \
  tests/test_capital_guard_shadow.py \
  tests/test_capital_guard_shadow_settlement.py \
  tests/test_capital_guard_shadow_runtime_surface.py -q
git diff --check origin/main...HEAD
```

Expected: all commands pass.

- [ ] **Step 2: Run the CI-equivalent full suite**

Create temporary `.venv` and `.env` symlinks only if absent. Set `RESEARCH_PREWARM_INTERVAL_SECONDS=900`, run the full pytest suite, and remove temporary links in `finally`. Deselect only the already reproduced installed-LaunchAgent plist drift test; do not hide any new failure.

- [ ] **Step 3: Obtain independent financial-safety review**

Reviewer must inspect official-host allowlists, URL quoting, DNS/TLS/deadline reuse, 404 disambiguation, exact ID/slug semantics, source-vs-record supersession separation, raw error taxonomy, append-only behavior, and prove absent runtime wiring, payout, evaluation, replay, paper DB mutation, G7 changes, sizing changes, order changes, or collection activation. Any Critical or Important finding requires a RED regression before a fix.

- [ ] **Step 4: Publish through protected CI**

Push a dedicated branch and open a draft PR. Record exact ordinary CI and raw replay-gate run IDs. If raw replay is again T3 with zero usable corpora, use only the user's existing authorized override; do not alter thresholds, corpora, gates, G7, or source behavior. Merge only the reviewed head after ordinary CI and the override pass.

- [ ] **Step 5: Sync root without restarting the bot**

Fast-forward `main` while preserving runtime artifacts. Run `scripts/botcheck.py` after sync and verify that `capital_guard_shadow: capture=off collection=off` and canonical settlement remains off. Do not restart because this slice adds no runtime wiring.

- [ ] **Step 6: Commit the completed verification checklist**

```bash
git add docs/superpowers/plans/2026-07-23-authoritative-settlement-sources.md
git commit -m "docs: record authoritative source verification"
```
