# Brave Search Shadow Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure a third, commercially licensed research transport through an explicit operator-only probe without changing any bot runtime behavior.

**Architecture:** The probe is a standalone CLI. It reads a pre-registered JSONL query file, uses the shared pinned-IPv4 HTTPS transport with a private authentication header, and writes only allowlisted scalar telemetry to an operator-selected artifact. It must not import or call runtime research, prewarm, admission, execution, dossier, or trade-log modules.

**Tech Stack:** Python 3.14, aiohttp, existing `utils.bounded_https`, pytest, JSONL.

## Global Constraints

- `ENABLE_BRAVE_SEARCH_SHADOW=true` and non-empty `BRAVE_SEARCH_API_KEY` are both required before any call; missing either produces no artifact and no network call.
- The key remains only in ignored environment configuration. It is never committed, printed, included in the URL, included in exceptions, or persisted.
- Use only canonical HTTPS host `api.search.brave.com`, pinned global IPv4 resolution, redirects disabled, serial calls, a 2.0-second timeout, 256,000-byte response cap, and at most 30 inputs.
- Do not modify `main.py`, `analysis/research_gate.py`, `tasks/research_prewarm_task.py`, `analysis/generic_search_circuit.py`, any dossier store, paper admission, execution, sizing, or `trades.jsonl`.
- Output may contain only probe run ID, input index, provider, outcome, duration milliseconds, HTTP status, body byte count, JSON schema validity, result count, and safe exception class. It must never contain query text, URL, headers, API key, response body, title, snippet, publisher URL, exception message, or repr.
- A successful probe is transport evidence only. It cannot create `ResearchEvidence`, alter a verdict, enqueue paper review, or establish relevance, edge, or profitability.
- Before execution, the operator must record account-specific commercial-use, retention, caching, redistribution, and attribution terms. Do not retain result content before that review.

---

## File Structure

- Modify: `utils/bounded_https.py` to accept validated private request headers without weakening canonical-host or redirect protections.
- Modify: `tests/test_bounded_https.py` to prove the extra-header contract.
- Create: `scripts/brave_search_shadow_probe.py` for the standalone fail-closed probe.
- Create: `tests/test_brave_search_shadow_probe.py` for activation, cap, sanitization, and import-boundary coverage.
- Modify: `docs/governance/research-shadow.env.example` to document inert, operator-only configuration.

### Task 1: Protect The Transport Header Boundary

**Files:**
- Modify: `utils/bounded_https.py:262-440`
- Test: `tests/test_bounded_https.py`

**Interfaces:**
- Produces: `fetch_bounded_https_ipv4(..., request_headers: Mapping[str, str] | None = None) -> bytes`.
- Preserves: the current URL scheme/host/port validation, global-IPv4 pinning, one deadline, no redirects, response cap, and error taxonomy.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_private_headers_are_merged_without_overriding_user_agent() -> None:
    sessions: list[_Session] = []
    raw = await fetch_bounded_https_ipv4(
        "https://api.search.brave.com/res/v1/web/search?q=contract",
        canonical_host="api.search.brave.com",
        provider_name="Brave Search API Shadow",
        user_agent="kalshi-bot-brave-shadow/1.0",
        timeout=1.0,
        max_bytes=100,
        request_headers={
            "Accept": "application/json",
            "X-Subscription-Token": "test-secret",
        },
        resolver_factory=_resolver_factory,
        connector_factory=_connector_factory,
        session_factory=_session_factory(sessions),
    )
    assert raw == b"{}"
    assert sessions[0].headers == {
        "User-Agent": "kalshi-bot-brave-shadow/1.0",
        "Accept": "application/json",
        "X-Subscription-Token": "test-secret",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    (
        {"Host": "other.example"},
        {"User-Agent": "other-agent"},
        {"X-Test": "bad\r\nheader"},
    ),
)
async def test_private_headers_cannot_override_routing_or_inject_lines(
    headers: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="request headers"):
        await fetch_bounded_https_ipv4(
            "https://api.search.brave.com/res/v1/web/search?q=contract",
            canonical_host="api.search.brave.com",
            provider_name="Brave Search API Shadow",
            user_agent="kalshi-bot-brave-shadow/1.0",
            timeout=1.0,
            max_bytes=100,
            request_headers=headers,
        )
```

- [ ] **Step 2: Verify the tests fail**

Run:
```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_bounded_https.py -q
```

Expected: failure because `request_headers` is not yet accepted.

- [ ] **Step 3: Implement the narrow extension**

Add `Mapping` import and this validator:

```python
def _validated_request_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    validated: dict[str, str] = {}
    for name, value in headers.items():
        normalized = name.strip().lower() if isinstance(name, str) else ""
        if (
            not normalized
            or normalized in {"host", "user-agent"}
            or not isinstance(value, str)
            or any(char in name or char in value for char in ("\r", "\n", "\x00"))
        ):
            raise ValueError("request headers are invalid")
        validated[name] = value
    return validated
```

Add the optional keyword argument to `fetch_bounded_https_ipv4`, validate it before creating the session, and pass exactly:

```python
headers={"User-Agent": user_agent, **extra_headers}
```

Do not add headers, URLs, or header values to `BoundedHTTPSAttemptTelemetry`.

- [ ] **Step 4: Verify helper behavior**

Run:
```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_bounded_https.py tests/test_research_provider_async_transport.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add utils/bounded_https.py tests/test_bounded_https.py
git commit -m "feat: support private bounded HTTPS headers"
```

### Task 2: Add The Operator-Only Brave Probe

**Files:**
- Create: `scripts/brave_search_shadow_probe.py`
- Test: `tests/test_brave_search_shadow_probe.py`

**Interfaces:**
- Consumes: a JSONL file where every line has exactly `probe_window_id`, `ticker`, `research_run_id`, and `query`; output path; `ENABLE_BRAVE_SEARCH_SHADOW`; and `BRAVE_SEARCH_API_KEY`.
- Produces: `BRAVE_SEARCH_SHADOW_ATTEMPT` and `BRAVE_SEARCH_SHADOW_SUMMARY` records containing only allowlisted fields. Exit code is `2` for disabled/missing-key/malformed-input configuration; completed provider failures are records and exit `0`.

- [ ] **Step 1: Write failing tests**

```python
def test_disabled_or_missing_key_makes_zero_transport_calls(tmp_path: Path) -> None:
    calls: list[object] = []

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        return b'{"web":{"results":[]}}'

    result = asyncio.run(
        run_probe(
            _input_file(tmp_path),
            tmp_path / "probe.jsonl",
            enabled=False,
            api_key="",
            fetcher=fetcher,
        )
    )

    assert result.exit_code == 2
    assert calls == []
    assert not (tmp_path / "probe.jsonl").exists()


@pytest.mark.asyncio
async def test_successful_probe_writes_only_allowlisted_scalars(tmp_path: Path) -> None:
    secret = "brave-test-secret"

    async def fetcher(*args: object, **kwargs: object) -> bytes:
        return b'{"web":{"results":[{},{}]}}'

    result = await run_probe(
        _input_file(tmp_path, query="market-specific query"),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key=secret,
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert '"type":"BRAVE_SEARCH_SHADOW_ATTEMPT"' in rendered
    assert '"provider":"brave_search"' in rendered
    assert '"result_count":2' in rendered
    assert secret not in rendered
    assert "market-specific query" not in rendered
    assert "X-Subscription-Token" not in rendered


@pytest.mark.asyncio
async def test_timeout_is_sanitized_and_next_input_runs(tmp_path: Path) -> None:
    async def fetcher(*args: object, **kwargs: object) -> bytes:
        raise TimeoutError("brave-test-secret must not be stored")

    result = await run_probe(
        _input_file(tmp_path, count=2),
        tmp_path / "probe.jsonl",
        enabled=True,
        api_key="brave-test-secret",
        fetcher=fetcher,
    )

    rendered = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert rendered.count('"outcome":"timeout"') == 2
    assert '"error_class":"TimeoutError"' in rendered
    assert "brave-test-secret" not in rendered
```

- [ ] **Step 2: Verify collection fails before implementation**

Run:
```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_brave_search_shadow_probe.py -q
```

Expected: collection failure because the module does not exist.

- [ ] **Step 3: Implement the runner and CLI**

Implement exactly these core types and constants:

```python
BRAVE_HOST = "api.search.brave.com"
BRAVE_ENDPOINT = f"https://{BRAVE_HOST}/res/v1/web/search"
MAX_QUERIES = 30
TIMEOUT_SECONDS = 2.0
MAX_BYTES = 256_000

@dataclass(frozen=True)
class ProbeInput:
    probe_window_id: str
    ticker: str
    research_run_id: str
    query: str

@dataclass(frozen=True)
class ProbeRecord:
    input_index: int
    probe_window_id: str
    ticker: str
    research_run_id: str
    provider: str
    outcome: str
    duration_ms: int
    http_status: int | None
    body_bytes: int
    schema_valid: bool
    result_count: int
    error_class: str | None
```

`run_probe(...)` must parse and validate all input lines before its first call. Reject blank required fields, duplicate `research_run_id`, and more than 30 rows. For every validated row it builds a URL with `urllib.parse.urlencode({"q": row.query, "count": 3, "country": "US", "search_lang": "en"})` and calls:

```python
await fetcher(
    url,
    canonical_host=BRAVE_HOST,
    provider_name="Brave Search API Shadow",
    user_agent="kalshi-bot-brave-shadow/1.0",
    timeout=TIMEOUT_SECONDS,
    max_bytes=MAX_BYTES,
    request_headers={
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    },
)
```

Measure duration synchronously around the await. For success, parse only `web.results` as a list and retain its length. For `TimeoutError`, use `outcome="timeout"`; for `urllib.error.HTTPError`, use `outcome="http_error"` and `http_status=exc.code`; for malformed JSON use `outcome="malformed_response"`; all other exceptions use `outcome="provider_exception"`. `error_class` may only be the matching type name or `"ProviderError"`; never call `str(exc)` or `repr(exc)`.

Serialize a `ProbeRecord` field-by-field, prepend `type="BRAVE_SEARCH_SHADOW_ATTEMPT"`, `shadow_only=true`, `admission_path="none"`, `evidence_persisted=false`, and `paper_review_enqueued=false`. Append one summary containing attempts, successes, distinct `probe_window_id` values, p95 duration, and p95 successful duration. Do not serialize `ProbeInput.query`.

The CLI must accept `--input` and `--output` absolute paths only and require `--execute`. It reads both environment variables itself, emits no artifact on configuration failure, and prints only a safe run summary plus the output path.

- [ ] **Step 4: Add import-boundary and cap tests**

```python
def test_probe_has_no_runtime_admission_imports() -> None:
    source = Path("scripts/brave_search_shadow_probe.py").read_text(encoding="utf-8")
    forbidden = (
        "import main",
        "analysis.research_gate",
        "ResearchPrewarmTask",
        "run_research_gate",
        "dossier",
        "paper_admission",
        "TradeLogger",
    )
    assert not any(token in source for token in forbidden)


@pytest.mark.asyncio
async def test_probe_rejects_more_than_thirty_rows_before_transport(tmp_path: Path) -> None:
    with pytest.raises(ProbeInputError, match="30"):
        await run_probe(
            _input_file(tmp_path, count=31),
            tmp_path / "probe.jsonl",
            enabled=True,
            api_key="brave-test-secret",
        )
```

- [ ] **Step 5: Verify and commit**

Run:
```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest tests/test_brave_search_shadow_probe.py tests/test_bounded_https.py -q
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check scripts/brave_search_shadow_probe.py tests/test_brave_search_shadow_probe.py utils/bounded_https.py tests/test_bounded_https.py
```

Expected: passing tests and no Ruff findings.

Commit:
```bash
git add scripts/brave_search_shadow_probe.py tests/test_brave_search_shadow_probe.py
git commit -m "feat: add Brave search shadow probe"
```

### Task 3: Document Inert Operator Configuration

**Files:**
- Modify: `docs/governance/research-shadow.env.example`
- Test: `tests/test_brave_search_shadow_probe.py`

**Interfaces:**
- Documents: the two required environment variables while proving they do not enable runtime research or admission.

- [ ] **Step 1: Write the failing documentation test**

```python
def test_research_shadow_env_example_marks_brave_probe_as_operator_only() -> None:
    rendered = Path("docs/governance/research-shadow.env.example").read_text(
        encoding="utf-8"
    )
    assert "ENABLE_BRAVE_SEARCH_SHADOW=false" in rendered
    assert "BRAVE_SEARCH_API_KEY=" in rendered
    assert "operator-only" in rendered
    assert "does not enable runtime research or admission" in rendered
```

- [ ] **Step 2: Add the disabled documentation block**

```dotenv
# Operator-only standalone source probe. This does not enable runtime research or admission.
ENABLE_BRAVE_SEARCH_SHADOW=false
# Populate only after recording the subscribed Brave API account terms.
BRAVE_SEARCH_API_KEY=
```

- [ ] **Step 3: Run complete focused verification**

Run:
```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv("/Users/jacobparenti/vscode/kalshi-bot/.env"); import pytest; raise SystemExit(pytest.main(["tests/test_bounded_https.py", "tests/test_research_provider_async_transport.py", "tests/test_brave_search_shadow_probe.py", "tests/test_research_gate.py", "tests/test_research_prewarm_task.py", "tests/test_main_pipeline.py", "-q"]))'
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check utils/bounded_https.py scripts/brave_search_shadow_probe.py tests/test_bounded_https.py tests/test_brave_search_shadow_probe.py
git diff --check
```

Expected: all selected tests pass, Ruff is clean, and `git diff --check` emits no output.

- [ ] **Step 4: Commit**

```bash
git add docs/governance/research-shadow.env.example tests/test_brave_search_shadow_probe.py
git commit -m "docs: document Brave shadow probe configuration"
```

## Acceptance Gate Before Any Runtime Discussion

1. Run at most 30 pre-registered actual research queries across at least three observed generic-circuit-open windows.
2. Require at least 27 successful bounded calls and p95 total duration under 2 seconds before treating Brave as transport-viable.
3. Independently review a blinded 25-item contract-relevance sample using only data retained under the subscribed account terms. Probe output itself contains no result content.
4. Design a separate future runtime boundary and regression suite before any provider result is allowed near verdict or admission code.
5. Preserve all matcher, research, price-edge, G6/G7, sizing, paper-only, and live-transition controls regardless of probe outcome.

