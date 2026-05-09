# P1-03 — `log_signal_analysis_detail` typed-param refactor design

**Status:** Proposed
**Date:** 2026-05-09
**Phase:** Phase-3 housekeeping, Stage 3a-2
**Decision:** Replace the 46-kwarg flat signature with a single `@dataclass(frozen=True)` named `SignalAnalysisDetail`, matching the repo-wide convention in `analysis/`, `governance/`, and `kalshi/`.
**Alternatives considered:**
- TypedDict — rejected: dict at runtime, no attribute-access type narrowing, no enforcement that required fields are set.
- Pydantic BaseModel — rejected: adds runtime validation overhead and a heavier dependency for a write-once log record; no evidence of Pydantic use anywhere in the repo.
- NamedTuple — rejected: positional construction is fragile for 46+ fields; no optional-field support without sentinel values.

---

## Context

The current function header (lines 789–837 of `utils/logger.py`) takes 46 keyword-only parameters: 9 required, 37 optional (all defaulting to `None`). The audit flagged this as the largest flat API surface in the repo. The risk is caller drift: new callers or callers modified over time can omit fields, rename fields, or reorder fields that have defaults — all silently. The emitted JSON is the data contract for downstream governance audit, edge replay, and dashboard pipelines. Any structural drift produces silent data-shape regressions; there is no schema enforcement today.

This is a solo project with no external consumers, so backwards compatibility is irrelevant.

---

## Parameter clustering analysis

| Cluster | Params | Conceptual meaning |
|---|---|---|
| `MarketContext` | `ticker`, `market_price` | Which market the signal targets |
| `EvidenceContext` | `source`, `headline`, `method`, `keywords`, `keyword_contributions`, `base_probability`, `final_probability` | The news item under analysis and keyword-derived signal |
| `LLMResult` | `llm_direction`, `llm_magnitude`, `llm_confidence`, `llm_attempted`, `llm_result_used`, `llm_result_status`, `llm_provider`, `llm_routing_passed`, `llm_routing_reason`, `llm_probability_movement`, `llm_useful` | Raw LLM output and routing outcome |
| `LLMTiming` | `llm_latency_ms`, `llm_total_stage_ms`, `llm_queue_wait_ms`, `llm_http_round_trip_ms`, `llm_parse_ms`, `llm_http_status`, `llm_contention_observed`, `llm_in_flight_at_entry` | LLM call latency and concurrency telemetry |
| `PreLLMGate` | `pre_llm_quality_pass`, `pre_llm_semantic_overlap_count`, `pre_llm_semantic_overlap_ratio`, `pre_llm_would_block`, `pre_llm_keyword_override`, `pre_llm_keyword_override_mode`, `pre_llm_keyword_signal_strength`, `pre_llm_gate_reason`, `pre_llm_gate_enforced` | Pre-LLM match quality gate decisions |
| `PreLLMTokenMetrics` | `pre_llm_headline_token_count`, `pre_llm_market_token_count`, `pre_llm_filtered_stopword_count`, `pre_llm_filtered_generic_count`, `pre_llm_semantic_token_types` | Token-level diagnostics from the pre-LLM filter |
| `ProbeFlags` | `is_startup_probe`, `is_synthetic_probe`, `pre_llm_would_block_and_useful` | Execution context flags |

Cluster count: 7, covering all 46 parameters.

---

## Type-system recommendation

### Comparison

**TypedDict**
- Pro: zero runtime overhead; IDE completion; JSON serialization is trivial (it IS a dict).
- Con: does not prevent constructing a partially-complete record; no frozen enforcement; dict at runtime means a caller can add arbitrary keys post-construction with no type error.

**`@dataclass(frozen=True)`**
- Pro: attribute access; `frozen=True` makes it immutable (fits a write-once log record); default values supported naturally; `dataclasses.asdict()` produces a dict for JSON in one call; zero external dependencies; exact match to the existing convention in `analysis/evidence_types.py`, `governance/decision.py`, `governance/evidence.py`, `governance/agent.py`.
- Con: `dataclasses.asdict()` deep-copies nested dicts/lists (minor overhead; acceptable for a logger).

**Pydantic BaseModel**
- Pro: runtime field validation; good error messages.
- Con: adds heavyweight dependency for a write-once logging record; no Pydantic imports anywhere in `analysis/`, `governance/`, or `kalshi/`; overkill for a solo project.

**NamedTuple**
- Pro: lightweight.
- Con: positional-index construction is unreadable at 46 fields; optional fields require `Optional` with explicit `None` defaults and are still positional under the hood.

### Chosen design

`@dataclass(frozen=True)` in a new file `utils/log_records.py`.

Rationale: (1) exact convention match with the rest of the codebase, (2) immutability is semantically correct — the logger is a read-only consumer of the record after construction, (3) `dataclasses.asdict()` gives a plain dict with one line, (4) no new dependencies, (5) type-checker friendliness without needing `TypedDict` workarounds.

The function signature becomes:

```python
def log_signal_analysis_detail(self, detail: SignalAnalysisDetail) -> None:
    record = {"type": "SIGNAL_ANALYSIS_DETAIL", **dataclasses.asdict(detail)}
    # apply existing rounding inline or in a helper
    self._write(record)
```

---

## Caller-side migration

**Call sites:**

| File:line | Context | All 46 kwargs passed? |
|---|---|---|
| `analysis/signal_analyzer.py:1144` | LLM path — passes full LLM result + routing + pre-LLM gate + probe fields | ~35 explicit + `**probe_fields` + `**pre_llm_fields` |
| `analysis/signal_analyzer.py:1193` | No-keyword gate path — passes LLM meta + routing + probe + pre-LLM, omits LLM direction/confidence | ~28 explicit + `**probe_fields` + `**pre_llm_fields` |
| `analysis/signal_analyzer.py:1236` | Keyword-only path — same shape as no-keyword path | ~28 explicit + `**probe_fields` + `**pre_llm_fields` |
| `tests/test_llm_latency_observability.py:143` | Direct logger test — passes a representative LLM timing subset | ~17 explicit (subset) |
| `tests/test_llm_latency_observability.py:182` | Direct logger test — minimal required fields only | 9 required only |
| `tests/test_llm_latency_observability.py:204` | Direct logger test — pre-LLM fields subset | ~20 explicit |
| `tests/test_main_startup.py:292` | Monkey-patches the method; does not call it | n/a |
| `tests/test_signal_analyzer.py:338+` | 14 separate tests — all patch the method; none call it with real kwargs | n/a |

The three live call sites in `signal_analyzer.py` currently spread their kwargs across three dicts: explicit kwargs, `**probe_fields` (a plain dict), and `**pre_llm_fields` (a plain dict from `_pre_llm_log_fields()`). This `**`-splat pattern is the primary source of drift risk — a key added to either helper dict bypasses the function signature silently.

**Worked example — LLM path (line 1144) before/after:**

Before:
```python
await write_trade_log_async(
    trade_log.log_signal_analysis_detail,
    ticker=market.ticker,
    source=news.source,
    # ... 17 more explicit kwargs ...
    **probe_fields,
    **pre_llm_fields,
)
```

After:
```python
detail = SignalAnalysisDetail(
    ticker=market.ticker,
    source=news.source,
    # ... explicit fields including pre_llm_* and probe flags ...
    pre_llm_quality_pass=pre_llm_fields.get("pre_llm_quality_pass"),
    pre_llm_semantic_overlap_count=pre_llm_fields.get("pre_llm_semantic_overlap_count"),
    # ... remaining pre_llm_* fields ...
)
await write_trade_log_async(trade_log.log_signal_analysis_detail, detail)
```

The critical change: `**pre_llm_fields` and `**probe_fields` splatting is eliminated. Every field is explicit in the constructor. A new key added to `_pre_llm_log_fields()` without a corresponding field in `SignalAnalysisDetail` becomes a type error at the construction site, not a silent schema addition.

---

## Default-value handling

The 9 required fields (`ticker`, `source`, `headline`, `method`, `keywords`, `keyword_contributions`, `base_probability`, `final_probability`, `market_price`) have no current default. The 37 optional fields all default to `None`.

The dataclass mirrors this directly:

```python
@dataclass(frozen=True)
class SignalAnalysisDetail:
    # Required
    ticker: str
    source: str
    headline: str
    method: str
    keywords: list[str]
    keyword_contributions: list[dict[str, Any]] | None
    base_probability: float
    final_probability: float
    market_price: float

    # Optional — all default None
    llm_direction: str | None = None
    llm_magnitude: str | None = None
    # ... 35 more ...
```

No two-struct split needed. The `frozen=True` + `None` defaults pattern is already used in `analysis/evidence_types.py` for similar optional enrichment fields. Do not use `field(default=None)` unless a mutable default (list/dict) is needed — plain `= None` is sufficient for all optional fields here.

The rounding currently applied inside the function body (`round(base_probability, 4)` etc.) should remain in `log_signal_analysis_detail()` at serialization time rather than moved into the dataclass. The dataclass stores the raw values; the logger applies presentation rounding when building the JSON `record` dict. This matches how `governance/decision.py` works.

---

## Test strategy

**Existing tests:**
- `tests/test_llm_latency_observability.py` — 3 direct calls to `log_signal_analysis_detail` with real kwargs; all must be migrated to construct `SignalAnalysisDetail` first.
- `tests/test_signal_analyzer.py` — 14+ tests; all patch the method with `unittest.mock.patch`. These need no changes to pass, but after migration they can be strengthened by asserting `call_args[0][0]` is a `SignalAnalysisDetail` instance.
- `tests/test_main_startup.py:292` — monkey-patches only; no migration needed.

**New tests to add in `tests/test_log_records.py`:**

1. **Struct construction + serialization**: build a `SignalAnalysisDetail` with all required fields, call `dataclasses.asdict()`, assert output keys match expected JSON keys. Verify optional fields absent from output when `None` (the logger's `if x is not None` guard handles omission, so the test validates that contract).

2. **Snapshot test on JSON output shape**: call `logger.log_signal_analysis_detail(detail)` with a fully-populated `SignalAnalysisDetail`, read the emitted JSONL line, and assert the exact set of top-level keys matches a stored snapshot set. This catches any future field addition that was not reflected in the struct definition. The snapshot can be a `frozenset` of key names checked in.

---

## Migration plan

Single commit. Rationale: the project is solo, all three live call sites are in one function in one file, the test surface is small, and the old 46-kwarg signature and the new struct can be introduced and callers migrated atomically. Staging the migration adds commit complexity with no safety benefit.

Commit order within the single change:

1. Add `utils/log_records.py` with `SignalAnalysisDetail` dataclass.
2. Update `utils/logger.py`: replace the 46-kwarg signature with `def log_signal_analysis_detail(self, detail: SignalAnalysisDetail) -> None`.
3. Update `analysis/signal_analyzer.py`: the three `write_trade_log_async(trade_log.log_signal_analysis_detail, **...)` call sites each become an explicit `SignalAnalysisDetail(...)` construction followed by the struct being passed. Eliminate all `**pre_llm_fields` and `**probe_fields` splatting at the call site.
4. Update `tests/test_llm_latency_observability.py`: replace bare kwarg calls with struct construction.
5. Add `tests/test_log_records.py` with construction + serialization + snapshot tests.

> **Cross-reference to P1-02:** if Stage 3c.1 (P1-02 decomposition) lands BEFORE this stage, the three callsites will already be collapsed into a single `_emit_signal_analysis_log(...)` helper. In that case this refactor's caller-side migration shrinks to one site (the helper itself), which is much cleaner. **Recommend swapping Stage 3c.1 and 3c.2 — do P1-03 first, then P1-02.** P1-02's helper extraction then becomes a one-line forwarder around the dataclass.

---

## Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Downstream JSON consumers depend on field names | High | `dataclasses.asdict()` preserves Python attribute names verbatim as JSON keys. Verify each field name in `SignalAnalysisDetail` matches the current kwarg name exactly — they are identical by construction. Add snapshot test on emitted key set. |
| `**pre_llm_fields` / `**probe_fields` splat contained fields not in the new struct | Medium | Audit `_pre_llm_log_fields()` return keys against the 46-param list. All keys emitted by that helper are already present in the current signature. Explicitly enumerate them in the constructor call site at migration time. |
| `None`-omission semantics change | Medium | The current function omits optional fields from the JSON record via `if x is not None`. The new function must preserve that pattern — `dataclasses.asdict()` will include `None`-valued fields unless the logger explicitly skips them. The existing `if x is not None: record[k] = v` loop must remain, or be replaced by a dict comprehension that filters `None`. |
| `dataclasses.asdict()` deep-copies nested dicts | Low | `keyword_contributions` is a `list[dict]` and `pre_llm_semantic_token_types` is a `dict`. The deep copy is correct behavior and negligible overhead for a logger. |
| Type checker noise | Low | The repo uses `@dataclass(frozen=True)` already in five other files. No new type discipline is introduced. mypy strictness level is unchanged. |

---

## Open questions

1. **`write_trade_log_async` signature**: it currently accepts `(fn, **kwargs)` and calls `fn(**kwargs)`. After migration it should accept `(fn, detail)` and call `fn(detail)`. Confirm no other callers of `write_trade_log_async` exist that would break with this interface change, or make the argument generic (`*args`).

2. **Rounding location**: currently `base_probability`, `final_probability`, `market_price`, `llm_confidence`, etc. are rounded in the function body at serialization time. The struct stores raw floats. Confirm the implementer does not move rounding into the dataclass `__post_init__` (that would require dropping `frozen=True` or using `object.__setattr__`, both awkward). Keep rounding in the logger.

3. **`keyword_contributions` required vs optional**: the current signature marks it as required but accepts `None`. Consider making it `list[dict[str, Any]] | None = None` (optional with default) to simplify call sites where contributions are unavailable.

4. **`method` as a literal type**: the field currently accepts any `str`. The actual values in use are `"llm"`, `"keyword"`, and `"keyword_gate"`. A `Literal["llm", "keyword", "keyword_gate"]` annotation would catch caller drift on this field at type-check time. Decide whether to add this in the same commit or defer.

5. **Stage ordering:** the implementer should ask the user whether to swap Stage 3c.1 and 3c.2. Strongly recommend this stage runs first — see cross-reference note in Migration plan.
