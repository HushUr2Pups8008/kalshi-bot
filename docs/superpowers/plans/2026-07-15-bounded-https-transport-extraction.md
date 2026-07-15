# Shared Bounded HTTPS Transport Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the proven generic-research IPv4 HTTPS transport into one shared utility without changing research-provider behavior.

**Architecture:** Move the PR #203 DNS validation, pinned resolver, single-deadline request, redirect refusal, response cap, and exception mapping from `analysis/research_gate.py` into `utils/bounded_https.py`. Keep the existing private research function as a compatibility wrapper that injects the current shared work limiter, so provider callers and their tests remain unchanged.

**Tech Stack:** Python 3.14, asyncio, aiohttp, dnspython, pytest.

## Global Constraints

- This slice is behavior-preserving extraction only; no settlement client or source implementation.
- Do not change query text, provider URLs, timeouts, byte caps, circuit behavior, research gates, G7, sizing, allocation, or orders.
- Keep one absolute monotonic deadline covering admission, DNS, connect, TLS, write, read, and body-cap enforcement.
- Preserve canonical TLS SNI and `Host` by connecting through aiohttp's resolver interface, not by replacing the URL host with an IP address.
- Accept every 2xx response exactly as the current transport does; reject redirects and over-cap bodies.
- Propagate `CancelledError`, `TimeoutError`, certificate errors, and TLS errors exactly; map other aiohttp connection errors to `ConnectionError` exactly.
- Do not create worker threads, implicit retries, background tasks, module-global loop registries, or runtime configuration.
- Runtime artifacts under `data/`, `logs/backups/`, and `logs/state/` remain unstaged.

---

### Task 1: Specify The Shared Utility Contract

**Files:**
- Create: `tests/test_bounded_https.py`
- Create: `utils/bounded_https.py`

**Interfaces:**
- Consumes: an HTTPS URL, exact canonical host, provider label, user agent, finite timeout, positive byte cap, optional admission factory, and injectable DNS/aiohttp factories.
- Produces:

```python
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol


class AsyncAdmissionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[None]: ...


async def fetch_bounded_https_ipv4(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    timeout: float,
    max_bytes: int,
    admission_factory: AsyncAdmissionFactory | None = None,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
) -> bytes: ...
```

- [x] **Step 1: Write failing direct-utility tests**

Add tests that call `fetch_bounded_https_ipv4` directly and prove:

```python
async def test_no_admission_factory_uses_noop_context() -> None: ...
async def test_admission_wait_consumes_total_deadline() -> None: ...
async def test_dns_connect_and_read_share_one_deadline() -> None: ...
async def test_non_2xx_preserves_urllib_http_error() -> None: ...
async def test_every_2xx_is_accepted() -> None: ...
async def test_redirect_is_not_followed() -> None: ...
async def test_response_larger_than_cap_is_rejected() -> None: ...
async def test_cancellation_closes_resources_and_propagates() -> None: ...
async def test_dns_answers_must_be_global_ipv4() -> None: ...
async def test_pinned_resolver_requires_canonical_host_port_and_family() -> None: ...
async def test_certificate_and_tls_errors_are_not_reclassified() -> None: ...
async def test_other_client_connection_errors_map_to_connection_error() -> None: ...
```

- [x] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_bounded_https.py -q
```

Expected: collection fails because `utils.bounded_https` does not exist.

- [x] **Step 3: Move the existing transport implementation**

Create `utils/bounded_https.py` by moving, with names generalized but behavior unchanged:

```python
def _validated_global_ipv4_addresses(
    values: Iterable[object], *, provider_name: str
) -> tuple[str, ...]: ...


def _remaining_https_budget(deadline: float, *, provider_name: str) -> float: ...


async def _resolve_provider_ipv4(
    *,
    canonical_host: str,
    provider_name: str,
    deadline: float,
    resolver_factory: Callable[[], Any] | None = None,
) -> tuple[str, ...]: ...


class _PinnedProviderIPv4Resolver(aiohttp.abc.AbstractResolver): ...
```

Use an async no-op context when `admission_factory is None`. Compute `deadline = loop.time() + timeout` before entering admission, then run the existing DNS and aiohttp work inside `asyncio.timeout_at(deadline)`. Preserve the current connector arguments and `ClientTimeout` fields exactly.

- [x] **Step 4: Run direct utility tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_bounded_https.py -q
```

Expected: all tests pass with no leaked tasks or active fake sessions.

- [x] **Step 5: Commit the standalone utility contract**

```bash
git add utils/bounded_https.py tests/test_bounded_https.py
git commit -m "refactor: add shared bounded HTTPS transport"
```

---

### Task 2: Preserve The Research Transport Contract

**Files:**
- Modify: `analysis/research_gate.py:3425-3689`
- Test: `tests/test_research_provider_async_transport.py`
- Test: `tests/test_research_provider_transport.py`
- Test: `tests/test_bounded_https.py`

**Interfaces:**
- Consumes: `utils.bounded_https.fetch_bounded_https_ipv4`.
- Produces: the existing private `_fetch_bounded_https_ipv4` signature and all existing Google News/DuckDuckGo wrapper signatures unchanged.

- [x] **Step 1: Add a RED compatibility assertion**

Add one focused test proving the research wrapper injects the existing limiter as admission and that waiting for the limiter consumes the same timeout:

```python
async def test_research_wrapper_keeps_shared_limiter_inside_total_deadline() -> None: ...
```

Do not rewrite or weaken any existing provider-transport test.

- [x] **Step 2: Run compatibility tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_bounded_https.py \
  tests/test_research_provider_async_transport.py -q
```

Expected: the new wrapper-delegation assertion fails before the migration.

- [x] **Step 3: Replace the private implementation with a compatibility wrapper**

Keep the current signature in `analysis/research_gate.py` and delegate:

```python
async def _fetch_bounded_https_ipv4(
    url: str,
    *,
    canonical_host: str,
    provider_name: str,
    user_agent: str,
    timeout: float,
    max_bytes: int,
    resolver_factory: Callable[[], Any] | None = None,
    connector_factory: Callable[..., Any] = aiohttp.TCPConnector,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
) -> bytes:
    return await fetch_bounded_https_ipv4(
        url,
        canonical_host=canonical_host,
        provider_name=provider_name,
        user_agent=user_agent,
        timeout=timeout,
        max_bytes=max_bytes,
        admission_factory=lambda: _get_generic_web_search_work_limiter().slot(),
        resolver_factory=resolver_factory,
        connector_factory=connector_factory,
        session_factory=session_factory,
    )
```

Remove only imports and helpers that moved to `utils.bounded_https`. Keep `_resolve_google_news_ipv4` only if a current caller or test still requires it; otherwise remove it with a source-scan assertion proving no reference remains.

- [x] **Step 4: Run research compatibility tests and verify GREEN**

Run:

```bash
RESEARCH_PREWARM_INTERVAL_SECONDS=900 .venv/bin/python -m pytest \
  tests/test_bounded_https.py \
  tests/test_research_provider_async_transport.py \
  tests/test_generic_search_circuit.py \
  tests/test_research_prewarm_task.py -q
```

Expected: all tests pass; timing, exception taxonomy, active-resource counts, and 2xx handling match the pre-extraction behavior.

- [x] **Step 5: Prove there is one bounded transport implementation**

Run a source scan and assert:

```text
Pinned provider resolver implementation: utils/bounded_https.py only
Global IPv4 validation implementation: utils/bounded_https.py only
Bounded HTTPS request implementation: utils/bounded_https.py only
Research compatibility wrapper: analysis/research_gate.py only
```

- [x] **Step 6: Commit the research migration**

```bash
git add analysis/research_gate.py tests/test_bounded_https.py
git commit -m "refactor: share research HTTPS transport"
```

---

### Task 3: Independent Review And Protected Merge

**Files:**
- Review: `utils/bounded_https.py`
- Review: `analysis/research_gate.py`
- Review: `tests/test_bounded_https.py`
- Review: `tests/test_research_provider_transport.py`

**Interfaces:**
- Consumes: Tasks 1-2 exact branch head.
- Produces: shared transport merged on `main`; no runtime configuration transition.

- [x] **Step 1: Run focused static verification**

```bash
.venv/bin/python -m ruff check \
  utils/bounded_https.py analysis/research_gate.py \
  tests/test_bounded_https.py tests/test_research_provider_async_transport.py \
  tests/test_research_provider_transport.py
.venv/bin/python -m py_compile \
  utils/bounded_https.py analysis/research_gate.py tests/test_bounded_https.py
git diff --check origin/main...HEAD
```

Expected: all commands pass.

- [x] **Step 2: Run the CI-equivalent full suite**

Create temporary `.venv` and `.env` symlinks in the isolated worktree only if required. Set `RESEARCH_PREWARM_INTERVAL_SECONDS=900`, run the repository's full pytest command, and remove both symlinks in `finally` cleanup. Deselect only the already reproduced installed-LaunchAgent plist drift test; do not hide any new failure.

- [x] **Step 3: Obtain independent high-risk review**

Reviewer must check canonical-host validation, global-IP rejection, deadline placement before admission, pinned TLS hostname behavior, cancellation/resource cleanup, unchanged research exception taxonomy, unchanged provider 2xx handling, and absence of duplicate implementations. Fix any Critical or Important finding with a RED regression first.

- [ ] **Step 4: Publish through protected CI**

Push a dedicated branch and open a draft PR. Record exact ordinary CI and replay-gate run IDs. Use the user's recorded override only for the known empty-corpus condition; never change replay thresholds, corpora, gates, or G7.

- [ ] **Step 5: Merge and verify no runtime boundary changed**

Merge only the independently approved exact head. Sync root `main`, preserve dirty runtime artifacts, and do not restart solely for this extraction. Verify the active PID and botcheck remain healthy and `search_cb` remains the already approved runtime value.

## Self-Review

- Spec coverage: shared extraction, research compatibility, single deadline, TLS pinning, response cap, cancellation, taxonomy, review, and protected merge are represented.
- Placeholder scan: no placeholder token or unspecified error-handling step remains.
- Type consistency: the public utility signature and compatibility wrapper arguments match across Tasks 1-2.
- Scope: settlement clients, source normalization, collector wiring, payout, evaluation, replay, G7, sizing, and orders are explicitly excluded.
