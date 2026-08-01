# Executed Price Observability Design

## Objective

Add one bounded, paper-observability-only payload to the existing
`SKIPPED` record produced when `BlendTask` rejects
`invalid_executed_price`. The payload must explain the rejected handoff scalar
first, then preserve bounded source-quote context such as emptiness, stale
age, or malformed metadata without changing the decision that rejected it. It
preserves the exact rejection and admission behavior.

The implementation is successful only when an invalid price remains unable to
create a `TradeCandidate`, enter `_trading_queue`, reach the paper executor,
or create a paper-trade row, while the emitted skip record has an
unambiguous, schema-versioned provenance object.

## Evidence

Current code already fails closed, but cannot explain the upstream condition:

- `tasks/blend_task.py:347-353` rejects any value that is not a non-boolean
  integer in `1..99` before lane reads, readiness, execution-liquidity,
  pre-queue provenance, quarantine, or queue insertion.
- `tasks/blend_task.py:646-701` returns a terminal
  `BlendTaskResult` with `candidate=None` and `enqueued=False`, then emits the
  normal `SKIPPED` record.
- `tasks/blend_task.py:1262-1338` intentionally leaves `market_price`,
  `edge`, `min_edge_threshold`, `signed_diff`, and `absolute_diff` absent
  when the executed price is invalid. That behavior is correct and remains
  unchanged.
- `kalshi/__init__.py:39-60` already carries the controlled source data needed
  for diagnosis: selected-side asks, `price_available`, `price_source`,
  `price_method`, `price_retrieved_at`, and `raw_payload_hash`. The normalizer
  sets the retrieval timestamp and source/method at
  `kalshi/normalizer.py:377-396`.
- `main.py:3307-3311` sends the already-built `SignalAnalysis` through the
  shared `BlendTask`; the research route does the same. No main-pipeline
  change is required to observe every existing producer at the shared guard.
- A runtime scan of `logs/trades` through `2026-08-01T20:18:30Z` found one
  current `SKIPPED` record with `reason=invalid_executed_price`, at
  `2026-08-01T03:27:05.820518+00:00` in
  `logs/trades/live/trades.jsonl`. It retained lifecycle and existing
  `signal_meta`, and correctly had no fabricated price fields, but it had no
  executed-price provenance. This is an observability gap, not evidence that
  any trade should be admitted.

## Scope

In scope:

- One additive `executed_price_provenance` member on `SKIPPED` records whose
  `reason` is exactly `invalid_executed_price`.
- A pure, total schema constructor and explicit logger boundary.
- Focused unit and integration-style tests for the terminal blend path and
  serialized JSONL record.

Out of scope:

- Any quote retry, REST/order-book read, quote refresh, cache refresh, or
  provider call.
- Any change to `_has_valid_executed_price_cents`, side selection, market
  tradeability, readiness, G7, quarantine, sizing, queue insertion, or
  executor admission.
- Configuration, feature flags, database schema/migration, service manager,
  restart, paper/live mode transition, live order, or historical-log rewrite.
- New metrics labels containing ticker, payload fingerprint, timestamp, or
  raw values.

## Chosen Boundary

The data is generated only in the existing
`BlendTask._invalid_executed_price_result` branch and passed as an explicit
argument through `BlendTask._emit_skipped` to `TradeLogger.log_skipped`.
It is not copied into `SignalAnalysis.signal_meta`, a `TradeCandidate`, an
`OPPORTUNITY`, a `GATE_SUMMARY`, or paper persistence.

`utils/log_records.py` owns a frozen `ExecutedPriceSkipProvenance` record and
a pure `build_executed_price_skip_provenance(...)` constructor. The constructor
accepts only primitives collected from `SignalAnalysis` and its market. It
never calls `market.is_tradeable()`, a source provider, a cache, or a network
client. Any malformed attribute, timestamp, or unexpected object maps to an
enumerated `unknown` or `invalid` state; it must not throw or alter the
terminal result.

`utils/logger.py` accepts only this record type as the optional
`executed_price_provenance` argument to `log_skipped` and serializes its
`as_log_record()` result. The logger must not accept an arbitrary provenance
dictionary on this new field. This keeps the JSONL schema bounded at its write
boundary.

`main.py` remains unchanged. It is deliberately an inspected-but-untouched
call path: its ordinary, research-backed, and fade producers all converge on
the same `BlendTask` guard, so a single boundary avoids source-specific
admission behavior.

## Schema

For the exact `invalid_executed_price` reason, add this top-level member to
the existing `SKIPPED` record:

```json
{
  "executed_price_provenance": {
    "schema_version": 1,
    "origin": "research_decision_grade",
    "requested_side": "yes",
    "primary_fault": "executed_price_missing",
    "fault_codes": ["executed_price_missing", "source_quote_empty", "source_timestamp_missing"],
    "executed_price_state": "missing",
    "observed_executed_price_cents": null,
    "source_quote_state": "empty",
    "observed_source_quote_cents": null,
    "market_price_available": false,
    "source_kind": "rest_list",
    "source_method": "dollars_fixed_point",
    "source_timestamp_state": "missing",
    "source_price_retrieved_at": null,
    "source_price_age_seconds": null,
    "source_price_age_bucket": "unknown",
    "source_payload_sha256_prefix": null
  }
}
```

`null` in the example means that the key is emitted with a JSON null for a
fixed, known schema field. The constructor must always emit exactly the keys
listed below, in the listed shape; it must never add dynamic keys.

| Field | Type and allowed values | Rule |
| --- | --- | --- |
| `schema_version` | integer, exactly `1` | Allows an intentional later migration without overloading semantics. |
| `origin` | `news`, `research_decision_grade`, `fade_tweet`, `price_fade`, `other`, `unknown` | Map known `signal_type` values; never emit arbitrary signal text. |
| `requested_side` | `yes`, `no`, `unknown` | Map only the raw side value; do not infer a side. |
| `primary_fault` | `executed_price_missing`, `executed_price_zero`, `executed_price_invalid`, `source_quote_empty`, `source_quote_zero`, `source_quote_invalid`, `side_unknown`, `unknown` | Deterministic diagnosis of the terminal handoff first, not a new gate reason. `source_quote_stale` is observational context only and must never be the primary cause while the handoff scalar itself fails the existing terminal guard. |
| `fault_codes` | ordered list of 1 to 4 values from the `primary_fault` vocabulary plus `source_timestamp_missing`, `source_timestamp_invalid`, `source_timestamp_future` | Low-cardinality secondary facts; no exception text. |
| `executed_price_state` | `missing`, `zero`, `out_of_range_integer`, `boolean`, `non_integer`, `unknown` | Describes the raw `SignalAnalysis.executed_price_cents` value. A valid `1..99` integer cannot reach this terminal branch. |
| `observed_executed_price_cents` | integer in `[-100, 200]` or `null` | Emit only a non-boolean integer in this bounded diagnostic range. Do not stringify or serialize arbitrary objects. |
| `source_quote_state` | `valid`, `empty`, `zero`, `out_of_range_integer`, `boolean`, `non_integer`, `not_applicable`, `unknown` | Describes the selected-side ask only. |
| `observed_source_quote_cents` | integer in `[-100, 200]` or `null` | Same bounded rule as the executed value. |
| `market_price_available` | boolean or `null` | Copy only a real boolean from the market. |
| `source_kind` | `rest_list`, `rest_detail`, `polymarket_public`, `polymarket_us_rest`, `unavailable`, `other`, `unknown` | Allowlist market `price_source`; unrecognized text becomes `other`. |
| `source_method` | `dollars_fixed_point`, `legacy_cents`, `none`, `other`, `unknown` | Allowlist market `price_method`; unrecognized text becomes `other`. |
| `source_timestamp_state` | `present`, `missing`, `invalid`, `future`, `unknown` | Never treat a naive, unparseable, or future timestamp as fresh. |
| `source_price_retrieved_at` | normalized UTC ISO-8601 string of at most 32 characters, or `null` | Emit only when the source timestamp is valid, aware, and not after the local decision observation. |
| `source_price_age_seconds` | integer in `[0, 86400]` or `null` | Floor elapsed seconds, cap at 24 hours, and use the bucket to preserve an older-than-cap condition. |
| `source_price_age_bucket` | `fresh`, `stale`, `stale_capped`, `unknown` | Observational freshness classification only. |
| `source_payload_sha256_prefix` | 16 lowercase hex characters or `null` | Take only a validated SHA-256 prefix. Never emit a raw payload, URL, or exception. |

The enclosing record retains all existing fields and rules. In particular,
the provenance object never supplies `market_price`, `edge`,
`min_edge_threshold`, `signed_diff`, or `absolute_diff` when the executed
price is invalid.

## Fault Classification

The constructor chooses `primary_fault` in this order. It may retain up to
three additional deterministic `fault_codes`, but must not use the codes to
change a decision.

1. `side_unknown` when the raw side is neither `yes` nor `no`; no source ask
   is selected.
2. `executed_price_missing`, `executed_price_zero`, or
   `executed_price_invalid` whenever `executed_price_cents` fails the existing
   terminal predicate. This remains the primary cause even if the selected ask
   is empty, stale, unavailable, or malformed.
3. `source_quote_empty`, `source_quote_zero`, or `source_quote_invalid` only
   when the handoff scalar is unavailable for classification or when the
   constructor is invoked outside the normal invalid-handoff branch in tests.
4. `unknown` for malformed access or an unclassifiable combination.

`source_quote_stale` is not a valid `primary_fault` for the guarded terminal
path. If the selected ask is otherwise in the executable range but its source
timestamp is older than `MARKET_CACHE_TTL_SECONDS`, retain
`source_quote_stale` only as a secondary `fault_codes` member alongside the
executed-price primary fault. Freshness context must never mask the invalid
handoff scalar.

## Selected Ask Truth Table

The selected-side ask is the direct `yes_ask_cents` or `no_ask_cents`
attribute for the requested side. Its state is observational only and must
match the same executable-range boundary as the real handoff guard:
`0 < cents < 100`.

| Selected ask input | `source_quote_state` | Secondary fault code | Notes |
| --- | --- | --- | --- |
| side unknown | `not_applicable` | `side_unknown` primary only | No ask selected. |
| `None` | `empty` | `source_quote_empty` | Absence is distinct from malformed values. |
| integer `0` | `zero` | `source_quote_zero` | Exact zero boundary. |
| integer `1..99` | `valid` | none, or `source_quote_stale` if timestamp old | Exact executable range. |
| integer `100` | `out_of_range_integer` | `source_quote_invalid` | `100` is invalid because the real guard rejects it. |
| integer `< 0` or `> 100` | `out_of_range_integer` | `source_quote_invalid` | Negative and over-100 values are invalid. |
| boolean | `boolean` | `source_quote_invalid` | Booleans must never be treated as integers. |
| non-integer object/string/float | `non_integer` | `source_quote_invalid` | Malformed numeric shapes stay bounded. |

`price_available` is a separate observed fact. When it is exactly `False`,
emit `market_price_available=false`, but do not let that overwrite a populated
selected ask. A populated integer ask with `price_available=False` therefore
keeps its own `source_quote_state` from the table above and may carry an
additional secondary `source_quote_empty` code only if the selected ask itself
is absent. Absence, malformed values, and availability disagreement remain
distinct states.

The constructor must use only direct, guarded attribute reads from the market:
`yes_ask_cents` or `no_ask_cents`, `price_available`, `price_source`,
`price_method`, `price_retrieved_at`, and `raw_payload_hash`. It must not
read legacy midpoint fields or create a price fallback.

## Timestamp and Freshness Contract

`price_retrieved_at` is the only source timestamp considered. A timestamp is
`present` only when it is either an aware `datetime` or an offset-bearing
ISO-8601 string that parses to UTC and is not after the local observation time.
Naive, malformed, empty, future, and arbitrary objects are not normalized;
they receive `invalid`, `missing`, or `future` with no timestamp or age value.

For a present timestamp, compute the age from one local observation supplied
by `BlendTask` and floor it to seconds. `MARKET_CACHE_TTL_SECONDS` is the
existing source-cache policy and is reused as the observational stale boundary;
there is no new configuration knob. Age at or below that threshold is `fresh`.
Age above it is `stale`; age above 24 hours is recorded as `86400` with
`stale_capped`. Neither stale classification nor a bad timestamp may trigger
a quote refresh, retry, a different price, or a different gate result.

If the local observation clock is itself unavailable or invalid, leave the
timestamp-derived values `unknown` rather than raising. This makes the new
diagnostic total and preserves the existing terminal skip even under malformed
test or runtime input.

## Sampling, Redaction, and Cardinality

- Emit no new event and perform no sampling decision. The existing one
  `SKIPPED` event receives one bounded member only for its exact reason.
- Keep `fault_codes` to four values, all drawn from fixed enums. Downstream
  counters may group only by `primary_fault`, `origin`, `requested_side`,
  `source_kind`, `source_method`, and `source_price_age_bucket`.
- Do not make a metric label from ticker, lifecycle ID, timestamp, payload
  fingerprint, raw numeric values, headline, URL, `signal_meta`, source name,
  or exception text.
- Redact all non-integer raw values. The two numeric diagnostic fields admit
  only `[-100, 200]`; every other value is represented by its enum state and a
  null. The fingerprint is a validated 16-character prefix only.
- Do not copy `signal_meta` into the payload. Existing `signal_meta` behavior
  remains unchanged, including its lifecycle context; the new object must not
  amplify it.

## Safety Invariants

1. The accepted executed-price predicate stays byte-for-byte equivalent:
   non-boolean integer in `1..99` only.
2. An `invalid_executed_price` result stays terminal: no store read, readiness,
   execution-liquidity provider, pre-queue provider, quarantine sink, queue
   insertion, executor, or paper DB call.
3. The payload is built after the same invalid predicate has already decided
   the result. Its `primary_fault` is never used by readiness or admission.
4. The implementation performs no I/O, retry, cache mutation, configuration
   lookup beyond the existing constant, or live action.
5. Invalid values never become a price field, a float fallback, an edge, or a
   threshold in the record.
6. Missing or malformed diagnostic inputs produce bounded `unknown` fields,
   never an exception that could reopen or alter the terminal path.

## Verification and Evidence Criteria

Focused tests must prove all of the following before any later rollout:

- Empty, zero, invalid, stale, populated-but-unavailable, `100`, negative, and
  over-`100` source-quote fixtures serialize the expected fixed state
  combinations with valid schemas.
- Fresh-plus-invalid and stale-plus-invalid handoff fixtures keep the same
  `executed_price_*` primary fault while differing only in bounded secondary
  source timestamp / stale facts.
- Naive, future, malformed, and missing timestamps never become `fresh` and
  never cause a provider call.
- The normal terminal result is unchanged: `ready=False`, `candidate=None`,
  `enqueued=False`, queue empty, no store/readiness/liquidity/pre-queue calls,
  and no paper admission.
- The JSONL record still omits all price-derived decision fields while adding
  only `executed_price_provenance` for the exact invalid-price reason.
- Valid-price skips and unrelated `SKIPPED` events have no new field.
- Unknown source strings, dynamic objects, raw payloads, headlines, and
  unbounded integer values cannot leak through the provenance serializer.

After a separately approved deployment, runtime evidence is one newly observed
`SKIPPED/invalid_executed_price` line that passes the schema checks and still
has no price-derived decision fields or associated paper-trade row. Zero
runtime events only means no sample occurred; it is not proof of admission
health, profitability, or trade readiness.
