# GDP Counter-Evidence Enrichment Implementation Plan

## Scope And Invariants

This plan adds one shared GDPNow structured countercheck path to the existing
research gate. It must retain `missing_counter_evidence` as the default
fail-closed result. It does not add a data provider, change market selection,
modify settlement/accounting state, alter order sizing, or restart a service.

Until a separate reviewed, operator-approved rollout lands, `live_mode=True`
must hard-exclude raw and derived GDPNow countercheck evidence from satisfying
the counter-evidence gate or promoting a live decision. This is enforced in
`run_research_gate`, not left to deployment procedure.

The only eventual code ownership is the existing research surface:

- `analysis/research_gate.py`
- `tests/test_research_gate.py`
- existing persistence/replay tests only if the current generic evidence round
  trip needs a regression assertion

Do not create a venue-specific gate, a new database table, or an alternate
research pipeline.

## Task 1: Lock The Current Failure Boundary With RED Tests

**Modify:** `tests/test_research_gate.py`

1. Keep `test_gdpnow_more_than_threshold_moves_past_official_pending_to_countercase`
   as the fail-closed baseline: a 1.1889% GDPNow value that supports the
   provisional NO side must remain `NEEDS_COUNTER_EVIDENCE`.
2. Add fixtures for a strict real-GDP SAAR `> 2.0%` contract and a verified
   FRED GDPNow observation with current timestamps and matching contract
   fingerprint/query context.
3. Add failing YES-versus-below-threshold and NO-versus-above-threshold tests.
   Each must prove the raw evidence alone is not relabeled and that a derived
   `contradiction_check` is the only new record.
4. Add failing rejection tests for equality, matching direction, unavailable
   side, no independent support, missing price/edge, `at least`, less-than,
   range/bucket, ticker-only, missing target quarter/year, conflicting text,
   wrong unit, nonfinite value, stale/future time, missing fingerprint/context,
   and spoofed source provenance.
5. Add RED idempotence/replay tests: repeated enrichment and persisted/replayed
   evidence produce one stable derived record and never derive from a raw cache
   without current-run context.
6. Add a caller-level `run_research_gate(..., live_mode=True)` RED test using a
   scenario that would qualify in paper/prewarm mode. Assert no derived record,
   no GDPNow counter qualification, and the same fail-closed live verdict as
   before enrichment.
7. Add provider/adjudicator spy RED tests. A qualifying paper/prewarm
   countercheck must make zero extra side-aware counter-provider calls and no
   second adjudicator call; a nonqualifying case must retain the one existing
   fallback/re-adjudication sequence.

Run the focused tests and confirm that only the new contract expectations fail.

## Task 2: Add Pure Contract And Provenance Parsers

**Modify:** `analysis/research_gate.py`

1. Add a private immutable `GDPThresholdContract` parser separate from
   `_gdp_threshold_from_text()`. It recognizes only strict real-GDP SAAR percent
   thresholds with one target quarter/year and returns `None` for every
   unsupported form.
2. Add a private current-run GDP observation context, constructed when the
   existing FRED GDPNow provider result is accepted for a parsed GDP contract.
   Bind it to the exact query, contract fingerprint, source URL, source date,
   retrieval timestamp, metric value/unit, and extraction confidence.
3. Add pure validators for canonical FRED provenance, finite metric values,
   normal evidence freshness, and strict mismatch direction. Do not mutate
   `ResearchEvidence` in these helpers.
4. Require a provisional YES/NO side that still has independent fresh
   directional support and valid price/edge after GDPNow structured evidence is
   excluded. Do not infer the side from a ticker, market price, or GDPNow.

Unit tests added in Task 1 must pass for parser and provenance edge cases before
integration work begins.

## Task 3: Build And Integrate The One-Shot Countercheck

**Modify:** `analysis/research_gate.py`

1. Implement a pure builder that accepts valid raw GDPNow evidence,
   `GDPThresholdContract`, current-run context, and the independently justified
   provisional side.
2. Emit the deterministic `ResearchEvidence` contract from the design:
   `claim_type="contradiction_check"`, opposite direction, bounded source
   confidence, copied metric/provenance/timestamps/fingerprint, and stable
   `gdpnow-countercheck-v1` title/snippet.
3. Add a private provenance dedupe key including source observation, metric
   value, contract fingerprint, strict threshold/period, and proposed side.
   Preserve the original FRED `base_rate` record unchanged.
4. Integrate the builder once after ordinary structured normalization and
   provisional selection, before the existing `_side_aware_counter_query()`
   fallback and counter re-adjudication path. Re-run the ordinary verdict after
   enrichment. If it now qualifies, return through the normal finalization path
   without a counter-provider call or a second adjudication; if it does not,
   invoke the unchanged fallback exactly once. Do not add a recursive enrichment
   pass.
5. Exclude derived counterchecks from structured-signal selection, probability
   calculation, and primary directional support. Let the existing
   `_has_counter_evidence()` relevance/confidence checks decide whether it is a
   counter result.
6. Enforce the `live_mode=True` exclusion at this integration point: do not
   derive or qualify raw/derived GDPNow countercheck evidence for a live
   decision. Existing live fail-closed behavior must remain unchanged until a
   separate rollout change is approved.

Run the GDPNow-focused tests. Assert the legacy non-GDP counter path is
unchanged.

## Task 4: Preserve Persistence And Replay Semantics

**Modify if required by test evidence:** `tasks/research_dossier.py`,
`analysis/research_timeout_replay.py`, and their focused tests.

1. Verify that the generic `ResearchEvidence` serializer/deserializer preserves
   the derived record's existing fields without schema migration.
2. Add a dossier round-trip test and a timeout snapshot/replay test containing
   both raw FRED and derived countercheck evidence.
3. Assert exact stable fields, distinct identities, one-record dedupe, freshness
   enforcement, and contract-fingerprint isolation.
4. Do not add a new persistence table or allow replay to synthesize a missing
   countercheck from cached raw GDPNow evidence.

If current generic serialization already passes these tests without source-file
changes, leave the persistence implementation untouched.

## Task 5: Telemetry, Verification, And Paper-First Rollout

**Modify:** the existing research-gate telemetry/report surface only after the
countercheck contract tests pass.

1. Record fixed reason codes for eligibility, emission, qualification, and
   rejection without storing unbounded titles/snippets in metric labels.
2. Add report coverage for GDP-only and non-GDP `missing_counter_evidence`
   rates, source age, emitted/qualified counts, duplicate suppression, and
   decision-grade transitions attributable to this path.
3. Run:
   ```bash
   CI=1 .venv/bin/python -m pytest -q tests/test_research_gate.py
   ```
   plus focused dossier/replay tests if Task 4 touched those surfaces.
4. Run a deterministic replay fixture twice and assert identical evidence order,
   identities, verdict, and telemetry reason code.
5. Deploy only to paper/prewarm observation first. The explicit live-mode guard
   remains active; review the metrics and a sampled evidence audit before any
   separately approved live-mode change. No restart, config edit, size change,
   or order action belongs to this plan.

## Completion Criteria

- `missing_counter_evidence` is unchanged for no-data, ambiguity, agreement,
  equality, stale/untrusted, or self-support cases.
- A fresh, verified GDPNow mismatch can qualify only as a counter-result to an
  independently supported side for an exact strict GDP contract.
- The raw observation is never relabeled or double counted.
- Dossier/replay behavior is deterministic and cross-contract safe.
- Qualifying enrichment bypasses no normal final gate and incurs neither an
  extra counter query nor a second adjudication; nonqualifying cases retain the
  existing fallback behavior.
- `live_mode=True` cannot use raw or derived GDPNow countercheck evidence to
  clear `missing_counter_evidence` until a separate reviewed rollout changes
  that explicit guard.
- Focused tests, full research-gate tests, and replay checks pass before any
  runtime rollout decision.
