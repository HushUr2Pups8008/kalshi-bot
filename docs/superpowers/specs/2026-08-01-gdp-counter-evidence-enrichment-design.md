# GDP Counter-Evidence Enrichment Design

## Problem

`ResearchStatus.NEEDS_COUNTER_EVIDENCE` is deliberately fail-closed. The
current GDPNow path can create a useful structured `base_rate` observation, but
it cannot satisfy `_has_counter_evidence()` because that function only evaluates
`disconfirming` or `contradiction_check` evidence. The current diagnostic is
reproducible in `tests/test_research_gate.py:4403-4482`: a 1.1889% GDPNow value
is normalized to NO for a strict 2.0% GDP contract, yet the verdict remains
`missing_counter_evidence`.

The gap is not permission to relabel arbitrary supporting research as a
countercase. It is a narrow enrichment for the case where a verified GDPNow
observation actually contradicts an independently justified proposed side for
the exact GDP threshold contract.

## Decision

Add one shared, pure GDPNow countercheck builder inside
`analysis/research_gate.py`. It consumes already-fetched FRED GDPNow evidence,
the current contract, and a provisional selected side. It may append exactly
one deterministic `contradiction_check` evidence record only when every
eligibility rule below holds. No new network request, market admission bypass,
or trade action is introduced.

The builder runs once after ordinary evidence normalization and provisional
direction selection, but before the final existing `decide_research_verdict()`
call. The final verdict is always recomputed through the existing gates. If the
new record is not valid, nothing is appended and `missing_counter_evidence`
remains the result.

## Supported GDP Contract Semantics

The first version supports only an unambiguous strict-threshold contract:

- the rules explicitly identify real GDP growth;
- the settlement unit is seasonally adjusted annualized percent, matching
  `gdpnow_real_gdp_growth_saar` and `percent_saar`;
- the YES rule uses exactly one strict comparator: `more than`, `above`, `over`,
  or `greater than` a single numeric percent threshold;
- the contract identifies one target quarter and year in its rules/title; and
- the current provider result is bound to the same current-run contract
  fingerprint and GDP query context.

The existing `_gdp_threshold_from_text()` is useful for a primary signal but is
not sufficient countercheck proof because it also accepts `at least` and ticker
fragments. The countercheck gets a separate strict parser returning a small
`GDPThresholdContract` value: metric, unit, strict comparator, threshold,
target quarter/year, and contract fingerprint.

Ranges, buckets, less-than contracts, `at least` wording, more than one
threshold, missing/contradictory period text, ticker-only matches, unparseable
numbers, and non-SAAR contracts are unsupported. They generate no countercheck
and retain the existing fail-closed status.

For a valid strict `YES iff GDP > threshold` contract:

- provisional YES is contradicted only when GDPNow is strictly below threshold;
- provisional NO is contradicted only when GDPNow is strictly above threshold;
- equality is ambiguous for a nowcast and never qualifies;
- a GDPNow value that agrees with the proposed side never qualifies.

The proposed side must already be justified by the normal decision path without
counting GDPNow countercheck evidence. In particular, remove GDPNow structured
evidence from the provisional support set and require independent fresh
directional support, a valid price/edge, and no unresolved contradiction. This
prevents a GDPNow observation from selecting a side and then certifying itself
as that side's countercase.

## Source, Recency, And Provenance

Only a raw observation from the existing FRED GDPNow provider is eligible:

- `source_class == "specialized_data"`;
- `source_name == "FRED GDPNow"`;
- canonical FRED GDPNow source URL;
- `claim_type == "base_rate"` before enrichment;
- `metric_name == "gdpnow_real_gdp_growth_saar"`;
- finite numeric value, `metric_unit == "percent_saar"`, and valid extraction
  confidence;
- the current contract fingerprint and a matching in-memory GDP query context;
- a parseable source observation date, a parseable UTC retrieval time, and
  `_is_fresh_decision_evidence()` success.

The implementation must carry the query-to-observation association only in the
current run. It must not infer a target period from a ticker or reuse a cached
GDPNow observation for another contract. A replay without the stored derived
countercheck does not synthesize one from cached raw evidence; it remains
fail-closed. A replay with the derived record revalidates its normal evidence
freshness and contract fingerprint.

Spoofed source names/URLs, missing timestamps, stale/future data, missing
contract fingerprint, nonfinite values, low-confidence extraction, or an
ambiguous contract are no-data cases. They append nothing, do not change model
direction or probability, and leave `missing_counter_evidence` intact.

## Deterministic Evidence Contract

The builder leaves the original FRED evidence unchanged and constructs a second
`ResearchEvidence` only for a true mismatch. It uses existing persisted fields;
there is no table migration or parallel evidence store.

| Field | Required value |
| --- | --- |
| `source_class`, `source_name`, `source_url` | Copied FRED provenance; source name gains a fixed `GDPNow countercheck` suffix only in the derived record. |
| `title`, `snippet` | Fixed `gdpnow-countercheck-v1` templates containing target period, strict comparator, threshold, value, proposed side, and opposite result. |
| `claim_type` | Exactly `contradiction_check`. |
| `supports_direction` | Exactly the side opposite the independently proposed side. |
| `supports_confidence` | Never higher than the normalized raw GDPNow confidence or extraction confidence; must still meet `MIN_COUNTER_EVIDENCE_CONFIDENCE`. |
| `metric_name`, `metric_value`, `metric_unit` | Copied GDPNow metric fields without rounding the stored value. |
| `published_at`, `retrieved_at`, `inserted_at`, `contract_fingerprint`, `aggregator_url` | Preserved from the source, with the current contract fingerprint required. |

Construction is deterministic over the raw observation, parsed strict contract,
and proposed side. A private provenance key includes source URL, source
observation date, metric value, contract fingerprint, comparator/threshold,
target period, and proposed side. It prevents duplicate append on retries even
where `_evidence_identity()` is intentionally coarser. The derived record's
existing identity remains distinct because it has a different claim type and
direction.

## Counter Qualification And No Laundering

The derived record may be seen by `_has_counter_evidence()` only as a directional
counter-result. It must not:

- change the raw GDPNow record's claim type, direction, or confidence;
- count as primary decision support in `_is_decision_directional_support()` or
  `_qualifying_directional_support()`;
- participate in `_structured_gdpnow_signal()` candidate selection or alter the
  estimated probability/model direction;
- be treated as a second independent FRED source, a neutral no-counter result,
  or a substitute for a disconfirming query where there is no true mismatch;
- create another countercheck when final verdict evaluation is retried.

The final existing relevance-token overlap and confidence checks still apply.
If there is no independent supporting evidence for the proposed side, the
derived record cannot pass the counter gate. This blocks support laundering and
prevents a single source from manufacturing both sides of a decision.

## Persistence, Cache, And Replay

`ResearchEvidence` already carries the data needed by research dossiers and
timeout replay: source provenance, claim type, direction/confidence, timestamps,
metric fields, and contract fingerprint. The enrichment therefore uses only the
existing serializer/deserializer paths in `tasks/research_dossier.py` and
`analysis/research_timeout_replay.py`.

Persistence requirements:

- store both the untouched raw FRED observation and, when valid, one derived
  countercheck;
- preserve exact values and deterministic text through dossier and timeout
  snapshot round trips;
- dedupe repeated runs by the private provenance key before persistence;
- never generate a new derived countercheck from stale/cross-contract cached
  raw evidence; and
- re-run normal freshness, relevance, confidence, and contract-fingerprint
  checks after replay.

## Verification

Implementation must add focused `tests/test_research_gate.py` coverage for:

1. strict `> 2.0%` contract, independently proposed YES, verified 1.1889%
   GDPNow: exactly one NO `contradiction_check`, qualifying counter result, and
   all other decision gates still required;
2. symmetric independently proposed NO with GDPNow strictly above threshold:
   exactly one YES countercheck;
3. the existing 1.1889% / proposed-NO diagnostic remains
   `NEEDS_COUNTER_EVIDENCE`; matching GDPNow cannot self-qualify;
4. exact threshold equality, no proposed side, no independent non-GDP support,
   no price/edge, and agreement with proposed side: no record and no promotion;
5. `at least`, less-than, range/bucket, ticker-only, missing quarter/year,
   conflicting threshold text, non-SAAR unit, invalid numeric metric, stale or
   future timestamps, missing fingerprint/query context, and spoofed FRED
   provenance: all fail closed;
6. duplicate invocation, dossier reload, and timeout-replay round trip: stable
   record fields, no duplicate, no cross-contract reuse; and
7. explicit checks that raw GDPNow stays `base_rate`, derived evidence is not
   directional support/probability input, and non-GDP counter behavior is
   unchanged.

## Rollout Metrics And Safe Runtime Boundary

Emit structured, low-cardinality counters for eligible, emitted, qualified, and
rejected GDP counterchecks. Rejection reasons are fixed: `unsupported_contract`,
`ambiguous_contract`, `no_proposed_side`, `no_independent_support`, `same_side`,
`threshold_equality`, `untrusted_provenance`, `stale_source`, `missing_context`,
and `duplicate`.

Review GDP-only versus non-GDP rates for `missing_counter_evidence`, emitted
counterchecks, qualifying counterchecks, decision-grade transitions attributable
to the countercheck, source age, duplicate suppression, later price-edge
survival, and settled paper-trade outcomes. A transition is observational only;
it is not evidence of profit or live readiness.

The implementation stays inside the existing decision-grade research gate and
uses an already-fetched source. It changes no API credentials, launchd service,
database schema, runtime sizing, order placement, or live-mode configuration.
Initial deployment is paper/prewarm observation with normal fail-closed verdicts
and a sampled evidence audit. Extending the same path to live decision use
requires a separate operator-approved rollout after those metrics and replay
checks are reviewed.

## Non-Goals

- No generic conversion of base-rate data into counter evidence.
- No claim that GDPNow is the BEA settlement value.
- No relaxation of `missing_counter_evidence`, price/edge, freshness, source
  relevance, official-pending, or contradiction gates.
- No new network source, polling loop, persistent table, config switch, or
  market-specific fork.
