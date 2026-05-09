# P1-02 — `analysis/signal_analyzer.py` decomposition design

**Status:** Proposed
**Date:** 2026-05-09
**Phase:** Phase-3 housekeeping, Stage 3a-1
**Decision:** Extract 5 focused helpers from `_ollama_estimate_detailed` and 2 from `estimate_probability`; no redesign of the LLM pipeline or its external contracts.
**Alternatives considered:**
- Merge `_ollama_estimate_detailed` and `_anthropic_estimate_detailed` into a single generic HTTP LLM caller — rejected; Ollama has circuit-breaker state and probe logic that is provider-specific, not shared.
- Move `log_signal_analysis_detail` calls into a dedicated telemetry wrapper — rejected; the 46-field call is already a known P1-03 target; adding a new abstraction layer here pre-empts that work without completing it.
- Extract the pre-LLM gate block from `estimate_probability` into its own function — deferred; the gate already delegates to `_should_keyword_override_pre_llm_gate` and `_pre_llm_log_fields`, which are adequate helpers. The length problem in `estimate_probability` is driven by the three duplicated `log_signal_analysis_detail` callsites, not the gate.

---

## Context

`analysis/signal_analyzer.py` is the central LLM-orchestration module. Two functions dominate its line count. `_ollama_estimate_detailed` (lines 649–872, **224 lines**) handles circuit-breaker gating, HTTP I/O, HTTP-error classification, response parsing, budget-exceeded logic, and failure-counter management in a single monolithic try/except tower. `estimate_probability` (lines 1001–1263, **263 lines**) orchestrates keyword scoring, pre-LLM gate evaluation, routing decisions, LLM invocation, and three nearly-identical 30-field `log_signal_analysis_detail` calls — one per outcome branch (LLM success, no-keyword fallback, keyword-only). The combined size makes both functions hard to unit-test in isolation and produces high cognitive load on every review. The decomposition goal is readability and testability with zero behavioral change.

---

## Current structure (annotated)

### `_ollama_estimate_detailed` (lines 649–872)

| Lines | Block | Responsibility |
|-------|-------|----------------|
| 656–674 | Circuit-breaker check + probe | Read global `_ollama_down_until`; return early or fire `_ollama_ping()` |
| 676–693 | Payload construction | Build `{"model": ..., "messages": [...], ...}` dict |
| 695–735 | HTTP call + HTTP-level error handling | `aiohttp.ClientSession.post`; handle non-200 and empty body |
| 736–750 | Envelope JSON decode | `_json.loads(raw)` — outer OpenAI response envelope |
| 752–780 | Content extraction + shape validation | `data["choices"][0]["message"]["content"]` |
| 782–818 | LLM JSON extraction + budget check | `_extract_json(text)`, `_parse_llm_response`, latency vs. budget |
| 819–839 | Success path + meta construction | Reset failure counter, return tuple |
| 841–871 | Exception handlers | `ClientConnectorError`, `TimeoutError`, generic `Exception` — all update failure counter |

Natural seams: (A) the circuit-breaker gate is already self-contained but embedded inline; (B) the HTTP call + all its failure branches is one coherent unit; (C) content extraction from the OpenAI envelope is a 5-line pattern duplicated nowhere else but easily isolated; (D) budget-exceeded logic is 15 lines that could be a named predicate.

### `estimate_probability` (lines 1001–1263)

| Lines | Block | Responsibility |
|-------|-------|----------------|
| 1022–1052 | Keyword scoring preamble | `keyword_estimate`, gate helpers, `_keyword_contributions` |
| 1054–1083 | Pre-LLM gate + routing | `pre_llm_gate_enforced`, `routing_reason`, branch selection |
| 1084–1114 | LLM invocation | `llm_estimate_detailed` + debug emission |
| 1116–1176 | LLM success branch | Compute `llm_probability_movement`, trace step, `log_signal_analysis_detail` call #1 |
| 1179–1220 | No-keyword fallback branch | Trace step, `log_signal_analysis_detail` call #2 |
| 1222–1263 | Keyword-only branch | Confidence calculation, trace step, `log_signal_analysis_detail` call #3 |

Natural seams: the three `log_signal_analysis_detail` invocations share a dozen identical keyword arguments (the LLM meta fields). They are the primary source of length and repetition, and collapsing them is the highest-value extraction.

---

## Proposed decomposition

### Extracted helpers

**1. `_ollama_check_circuit()`**
- Signature: `async def _ollama_check_circuit() -> tuple[bool, dict[str, Any] | None]`
- Responsibility: Check `_ollama_down_until`; run `_ollama_ping()` if in probe window; return `(may_proceed, early_meta_or_None)`.
- Current location: lines 656–674 inside `_ollama_estimate_detailed`.
- Purity: **impure** — reads/writes module globals `_ollama_down_until`, `_ollama_consecutive_failures`; calls `_ollama_ping()` (async I/O); emits `log.warning/info`.

**2. `_ollama_build_payload(news, market) -> dict[str, Any]`**
- Signature: `def _ollama_build_payload(news: NewsItem, market: KalshiMarket) -> dict[str, Any]`
- Responsibility: Construct the `/v1/chat/completions` request body from news and market inputs.
- Current location: lines 679–693 inside `_ollama_estimate_detailed`.
- Purity: **pure** — no I/O, no side effects.

**3. `_ollama_post(payload, prompt_text) -> tuple[str | None, dict | None, int, int]`**
- Signature: `async def _ollama_post(payload: dict[str, Any], prompt_text: str) -> tuple[str | None, dict[str, Any] | None, int, int]`
  Returns `(content_text_or_None, early_meta_or_None, http_round_trip_ms, http_status)`.
- Responsibility: Fire the HTTP POST, handle all HTTP-level failure branches (non-200, empty body, envelope JSON decode failure, shape validation failure), and return either extracted content text or an early-exit meta dict.
- Current location: lines 695–780 inside `_ollama_estimate_detailed`.
- Purity: **impure** — async HTTP I/O; emits `log.warning`; reads `cfg.*`.
- Note: does NOT call `_extract_json` — that parses the LLM's inner response text, not the OpenAI envelope.

**4. `_ollama_record_failure(exc_type, latency_ms) -> dict`**
- Signature: `def _ollama_record_failure(exc_type: str, latency_ms: int) -> dict[str, Any]`
  `exc_type` is one of `"unavailable"`, `"timeout"`, `"error"`.
- Responsibility: Increment `_ollama_consecutive_failures`, conditionally set `_ollama_down_until`, emit the appropriate `[LLM_HEALTH]` log, and return a failure `_llm_meta` dict.
- Current location: lines 841–871, the three except-clause bodies.
- Purity: **impure** — writes module globals; emits log.
- Rationale: all three except blocks share identical counter-update + circuit-open logic differing only in the status string and log message. Collapsing them removes ~30 lines and makes the counter logic testable in isolation.

**5. `_ollama_extract_and_validate(text, market, t0, http_round_trip_ms, parse_start, prompt_text) -> tuple[tuple | None, dict]`**
- Signature as above; returns `((prob, confidence, reasoning, direction, magnitude), success_meta)` or `(None, failure_meta)`.
- Responsibility: Call `_extract_json(text.strip())`, call `_parse_llm_response`, check the stage-budget predicate, build the success or failure `_llm_meta`.
- Current location: lines 782–838 inside `_ollama_estimate_detailed`.
- Purity: **impure** — emits `log.warning/debug`; reads `cfg.ollama_stage_budget_seconds`.
- Important: `_extract_json` must NOT be inlined differently here. The call signature stays `_extract_json(text.strip())`.

**6. `_build_llm_meta_kwargs(llm_meta) -> dict`**
- Signature: `def _build_llm_meta_kwargs(llm_meta: dict[str, Any]) -> dict[str, Any]`
- Responsibility: Produce the shared subset of LLM-related keyword arguments that all three `log_signal_analysis_detail` callsites pass identically — `llm_attempted`, `llm_result_used`, `llm_result_status`, `llm_provider`, `llm_latency_ms`, `llm_total_stage_ms`, `llm_queue_wait_ms`, `llm_http_round_trip_ms`, `llm_parse_ms`, `llm_http_status`, `llm_contention_observed`, `llm_in_flight_at_entry`.
- Current location: replicated at lines 1158–1176, 1203–1218, 1248–1261.
- Purity: **pure** — dict projection, no side effects.

**7. `_emit_signal_analysis_log(*, ticker, source, headline, method, keywords, keyword_contributions, base_probability, final_probability, market_price, llm_meta, routing_reason, probe_fields, pre_llm_fields, llm_result_fields)`**
- Signature: `async def _emit_signal_analysis_log(*, ticker: str, source: str, headline: str, method: str, keywords: list[str], keyword_contributions: list, base_probability: float, final_probability: float, market_price: float, llm_meta: dict, routing_reason: str | None = None, probe_fields: dict = {}, pre_llm_fields: dict = {}, llm_result_fields: dict = {}) -> None`
- Responsibility: Call `write_trade_log_async(trade_log.log_signal_analysis_detail, ...)` with all fields assembled, using `_build_llm_meta_kwargs(llm_meta)` for the shared subset and accepting the outcome-specific fields via `llm_result_fields`.
- Current location: three separate callsites at lines 1144–1176, 1192–1219, 1235–1262.
- Purity: **impure** — async I/O (trade log write).
- Important: the 46 field names passed to `log_signal_analysis_detail` must be preserved exactly; this helper is a pass-through assembler, not a schema change.

> **Cross-reference to P1-03:** if Stage 3c.2 (P1-03 dataclass refactor) lands BEFORE Stage 3c.1 (this design), helper #7 collapses to a one-line `await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)` because `detail` is already a fully-constructed `SignalAnalysisDetail`. In that case, the extraction is barely worth doing — the three callsites become 3 dataclass-construction blocks of ~30 lines each, where the shared field projection from `_build_llm_meta_kwargs` is still useful but can be inlined into a `SignalAnalysisDetail.from_llm_meta()` classmethod or a free helper. The implementer should re-evaluate this section after P1-03 lands.

---

### Refactored skeletons

**`_ollama_estimate_detailed` after extraction (~40 lines)**

```python
async def _ollama_estimate_detailed(news, market):
    may_proceed, early_meta = await _ollama_check_circuit()
    if not may_proceed:
        return None, early_meta

    payload = _ollama_build_payload(news, market)
    prompt_text = _build_prompt_text(news, market)
    t0 = time.monotonic()

    try:
        text, early_meta, http_round_trip_ms, http_status = await _ollama_post(
            payload, prompt_text
        )
        if early_meta is not None:
            return None, early_meta

        parse_start = time.monotonic()
        result, meta = _ollama_extract_and_validate(
            text, market, t0, http_round_trip_ms, parse_start, prompt_text
        )
        if result is None:
            return None, meta

        if _ollama_consecutive_failures > 0:
            log.info(
                "[LLM_HEALTH] provider=ollama recovered=true failures=%d",
                _ollama_consecutive_failures,
            )
            _ollama_consecutive_failures = 0
            _ollama_down_until = 0.0

        log.debug("Ollama: dir=%s mag=%s conf=%.2f -> prob=%.3f for %s",
                  result[3], result[4], result[1], result[0], market.ticker)
        return result, meta

    except aiohttp.ClientConnectorError:
        return None, _ollama_record_failure("unavailable", int((time.monotonic() - t0) * 1000))
    except asyncio.TimeoutError:
        return None, _ollama_record_failure("timeout", int((time.monotonic() - t0) * 1000))
    except Exception as exc:
        log.warning("Ollama estimation failed: %s (%s)", exc, type(exc).__name__)
        return None, _ollama_record_failure("error", int((time.monotonic() - t0) * 1000))
```

**`estimate_probability` after extraction (~60 lines)**

Three outcome branches (LLM success, no-keyword fallback, keyword-only) each terminate in `await _emit_signal_analysis_log(...)` with their distinct field sets passed via `llm_result_fields=`. Pre-LLM gate and routing branches preserved unchanged. Return statements unchanged (preserves trading-side contract).

---

## Data flow

```
estimate_probability(news, market, ...)
  │
  ├─ keyword_estimate() ──────────────────────────► kw_prob, kw_side, keywords, kw_reasoning
  ├─ _should_keyword_override_pre_llm_gate() ──────► keyword_override, keyword_override_mode
  ├─ _pre_llm_log_fields() ───────────────────────► pre_llm_fields  (dict, purely observability)
  ├─ _keyword_contributions() ────────────────────► keyword_contribs
  ├─ _llm_routing_reason() ───────────────────────► routing_reason
  │
  ├─ [gate/routing branches] ─────────────────────► llm_result = None | (prob, conf, reasoning, dir, mag)
  │     │                                            llm_meta   = dict
  │     └─ llm_estimate_detailed(news, market)
  │           └─ _ollama_estimate_detailed(news, market)
  │                 ├─ _ollama_check_circuit() ───► (may_proceed, early_meta)
  │                 ├─ _ollama_build_payload() ──► payload dict  [pure]
  │                 ├─ _ollama_post() ────────────► (text | None, early_meta | None, ms, status)
  │                 ├─ _ollama_extract_and_validate()
  │                 │     ├─ _extract_json(text)  [pure, load-bearing raw_decode]
  │                 │     └─ _parse_llm_response() [pure]
  │                 └─ _ollama_record_failure() ──► failure meta dict
  │
  └─ outcome branch (llm / no-keyword / keyword-only)
        ├─ _emit_extraction_trace_step()  [debug only, no state]
        └─ _emit_signal_analysis_log()   [async telemetry]
              └─ _build_llm_meta_kwargs(llm_meta)  [pure projection]
```

State shared across helpers via parameters, never via additional globals: `t0`, `http_round_trip_ms`, `parse_start`, `prompt_text`. The only true shared state is the two module-level circuit-breaker globals (`_ollama_consecutive_failures`, `_ollama_down_until`), accessed only by `_ollama_check_circuit` and `_ollama_record_failure`.

---

## Test strategy

**Existing coverage** (`tests/test_signal_analyzer.py`, 57.5 KB):
- `_extract_json`: lines 28–71 — full unit coverage of the `raw_decode` scanning path, preamble handling, last-wins behavior, and error cases. **This is the load-bearing test gate** for extraction 3 (`_ollama_post`) and 5 (`_ollama_extract_and_validate`).
- `_parse_llm_response`: lines 75–212 — direction, magnitude mapping, neutral/no-new-info guards. Gates extraction 5.
- `estimate_probability`: lines 339+ — integration-level with mocked LLM; gates the `_emit_signal_analysis_log` path indirectly via `patch("analysis.signal_analyzer.trade_log.log_signal_analysis_detail")`.
- `_ollama_estimate_detailed`: covered via `test_ollama_error_audit.py` (13.8 KB) and `test_llm_latency_observability.py` (13.3 KB).

**New unit tests required per extracted helper:**

| Helper | Test type | What to assert |
|--------|-----------|----------------|
| `_ollama_check_circuit` | Unit (mock `_ollama_ping`, manipulate globals) | Returns `(False, meta)` when in probe window; calls ping; resets counters on recovery |
| `_ollama_build_payload` | Pure unit | Correct keys present; no `repetition_penalty` key; model comes from `cfg.ollama_model`; **no `think` key** (preserves CLAUDE.md gotcha) |
| `_ollama_post` | Integration (mock `aiohttp.ClientSession`) | Returns `(None, meta, ...)` on 422; on empty body; on envelope JSONDecodeError; returns `(text, None, ...)` on 200 with valid body |
| `_ollama_record_failure` | Unit (manipulate globals) | Increments counter; sets `_ollama_down_until` when threshold crossed; correct status string per `exc_type` |
| `_ollama_extract_and_validate` | Unit (mock `_extract_json`, `_parse_llm_response`, `cfg`) | Returns failure meta on parse error; returns failure meta when budget exceeded; returns success tuple on happy path |
| `_build_llm_meta_kwargs` | Pure unit | All 12 expected keys present; values sourced correctly from input dict |
| `_emit_signal_analysis_log` | Integration (mock `write_trade_log_async`) | All 46 fields forwarded; `llm_result_fields` keys land at top level; **no schema drift** |

**Coverage target:** maintain or increase existing line coverage for `analysis/signal_analyzer.py`. The new pure helpers (`_ollama_build_payload`, `_build_llm_meta_kwargs`) should reach 100% coverage trivially.

---

## Migration plan (sequence of commits)

Each commit leaves CI green. Tests for the extracted helpers can be added before or in the same commit as the extraction.

**Commit 1 — Extract pure helpers + tests**
Extract `_ollama_build_payload` and `_build_llm_meta_kwargs`. Both are pure, zero behavioral risk. Add unit tests. No change to callers yet; helpers called inline from their original locations.

**Commit 2 — Extract `_ollama_record_failure` + circuit helpers**
Extract `_ollama_check_circuit` and `_ollama_record_failure`. These touch module globals but their behavior is deterministic given controlled global state. Replace the three except-clause bodies and the circuit-check preamble in `_ollama_estimate_detailed`. Add unit tests with global-state fixtures.

**Commit 3 — Extract `_ollama_post` and `_ollama_extract_and_validate`**
The remaining HTTP-I/O block and the parse/budget block. Add integration tests with `aiohttp` mocks. At this point `_ollama_estimate_detailed` is fully decomposed.

**Commit 4 — Extract `_emit_signal_analysis_log` + collapse `estimate_probability`**
Replace three duplicated `log_signal_analysis_detail` callsites in `estimate_probability` with `_emit_signal_analysis_log`. Verify telemetry field names with a dedicated assertion in `test_signal_analyzer.py` that checks the kwargs forwarded to `log_signal_analysis_detail` are unchanged.

The Phase-3 prompt instructs Stage 3c.1 lands as a single commit; if the user wants the 4-commit micro-sequence above, they should signal that at the gate.

---

## Risk assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| `_extract_json` behavioral regression | High | Existing tests at lines 28–71 cover all scanning cases; extraction 5 (`_ollama_extract_and_validate`) calls it unchanged as `_extract_json(text.strip())`; never rewrap or duplicate it |
| LLM probability blending re-introduced | High | `_emit_signal_analysis_log` is a telemetry assembler only; it does not touch `final_probability`; the gate-vs-input distinction lives in `estimate_probability`'s return statements, which are not touched |
| `think: False` accidentally added to Chat Completions path | Medium | `_ollama_build_payload` constructs only the `/v1/chat/completions` payload; the `think` field must not appear; add an assertion in the payload unit test that `"think" not in payload` |
| Telemetry schema drift on `log_signal_analysis_detail` | High | Add a test that captures kwargs forwarded to `log_signal_analysis_detail` across all three branches and asserts the exact key set equals the pre-refactor key set. `_build_llm_meta_kwargs` must be a strict projection, never adding or removing keys |
| `estimate_probability` return semantics change | High | Return statements are not moved; only the `write_trade_log_async` calls are replaced by `_emit_signal_analysis_log`; the 7-tuple return on each branch is untouched |
| Circuit-breaker state corruption | Medium | `_ollama_check_circuit` and `_ollama_record_failure` are the only two functions that write `_ollama_consecutive_failures` and `_ollama_down_until`; `_ollama_estimate_detailed` success path resets them directly (preserved inline, not extracted) |
| Double JSON parsing (performance) | Low | `_ollama_post` decodes the OpenAI envelope (`_json.loads(raw)`); `_ollama_extract_and_validate` calls `_extract_json` on the content string. These are two different JSON objects; the refactor does not add a third parse pass |

---

## Open questions

1. `_ollama_estimate_detailed` success path resets `_ollama_consecutive_failures` and `_ollama_down_until` inline (lines 819–825) interleaved with a `log.info` call. The skeleton above keeps this inline to avoid a third global-state function. Implementer should confirm: is a `_ollama_record_recovery()` function cleaner, or does inline remain acceptable?

2. `_ollama_post`'s return signature is four values `(text, early_meta, http_round_trip_ms, http_status)`. If `early_meta` is not None, `text` is None and `http_round_trip_ms` may be 0. A named tuple or dataclass would eliminate positional confusion. Is that in scope for Stage 3c.1 or deferred to a later housekeeping cycle?

3. `_emit_signal_analysis_log` receives `llm_result_fields` as a plain dict that gets `**`-unpacked into the log call alongside `**probe_fields` and `**pre_llm_fields`. If any key appears in more than one of these dicts the call will raise `TypeError`. The current code avoids this by construction; the implementer must verify no key overlaps exist before collapsing the three callsites. **(This concern is moot if P1-03 lands first — the dataclass enforces uniqueness by construction.)**

4. `test_ollama_error_audit.py` and `test_llm_latency_observability.py` test `_ollama_estimate_detailed` at the integration level. Confirm they still pass after the extraction — especially the circuit-open and timeout paths — before landing Commit 3.

5. **Ordering with P1-03:** the implementer should ask the user whether to swap Stage 3c.1 and 3c.2. P1-03 first is cleaner — see cross-reference note on helper #7 above.
