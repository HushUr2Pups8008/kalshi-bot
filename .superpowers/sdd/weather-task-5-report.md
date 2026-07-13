# Weather Shadow Task 5 Report

## Status

Implemented atomic KXHIGHNY outcome labeling, daily public-data revalidation,
correction checks, and seven-day sealing orchestration on top of the hardened
Task 2 store lifecycle. The path remains shadow-only and has no probability,
admission, paper-settlement, sizing, or trading dependency.

## TDD Evidence

- Initial RED: the new outcome suite stopped at collection with the expected
  missing `official_high_market` import.
- Validation GREEN / orchestration RED: `19 passed, 4 failed`; all failures were
  the expected missing `WeatherShadowCaptureTask.run_label_once` interface.
- Store RED: same-day target suppression returned one already-checked target
  instead of `()` after reopen.
- Review RED: a single sibling status correction changed the batch hash but left
  two sibling row identities unchanged, so a complete corrected version could
  not survive the store collision guard.
- Cancellation RED: both blocking-worker regressions returned while the
  persistence thread was still blocked; external cancellation also prevented
  the planned check and seal operations from starting.
- Focused Task 5 GREEN:
  `.venv/bin/python -m pytest tests/test_kxhighny_shadow_outcomes.py tests/test_weather_shadow_store.py tests/test_kxhighny_shadow_capture.py -q`
  -> `85 passed` after the projection and persistence-drain regressions.
- Final Task 1-5 GREEN:
  `.venv/bin/python -m pytest tests/test_kxhighny_shadow_validation.py tests/test_weather_shadow_store.py tests/test_kalshi_public_market_data.py tests/test_nws_public_client.py tests/test_kxhighny_shadow_capture.py tests/test_kxhighny_shadow_outcomes.py -q`
  -> `211 passed in 0.74s`.
- Required Ruff:
  `.venv/bin/ruff check tasks/kxhighny_shadow_validation.py tasks/kxhighny_shadow_capture.py tasks/weather_shadow_store.py tests/test_kxhighny_shadow_outcomes.py`
  -> `All checks passed!`.

## Implementation

- Reused the exact frozen `OutcomeRow`, `OutcomeBatch`, and `OutcomeCheck` DTOs
  introduced with the approved schema and verified their frozen behavior.
- Added fail-closed validation for complete finalized/settled siblings, explicit
  results, capture fingerprint continuity, exactly one official-high winner,
  KNYC/KOKX CLI identity, evidence hash, target civil day, and issue/retrieval
  ordering.
- Stable settlement hashes include event/market status, result, bounds, all
  three fingerprints, and official CLI identity/evidence. Quote, size, volume,
  and observation/retrieval drift are excluded; temporal metadata remains on
  the immutable persisted outcome rows.
- A canonical complete sibling/official projection is hashed before any row ID
  or source hash. Every row then combines its stable market source identity
  with that complete projection hash, so any sibling correction versions the
  entire ladder without circular derived-hash inputs.
- Added independent `run_label_once()` / `label_event()` orchestration using only
  dedicated label clients under an exact 20-second lane budget. Capture and
  label cycles are scheduled concurrently and contain failures independently.
- Initial complete labels persist atomically. Daily checks re-fetch both public
  settlement and CLI evidence, compare the stable batch hash to the baseline,
  append changed versions for conflict quarantine, then request store-owned
  sealing.
- Network, build, and validation finish inside the 20-second budget before any
  persistence starts. Each complete outcome/check/seal plan runs in one named,
  shielded task; external cancellation drains the task before propagating, so
  worker-thread commits cannot appear after the caller returns.
- Target selection now returns only unlabeled captures or a missing check due on
  the current UTC day inside days 1-7. Reopen retries suppress completed UTC-day
  checks while overdue missing days still create durable conflicts.

## Self-Review

- No raw market or CLI payload enters conflict details or logs.
- Labeling never imports or calls paper resolution, research admission, live
  cache, blending, sizing, executor, or authenticated Kalshi code.
- Existing Task 2 atomic/idempotent/conflict/seal tests remain green, including
  injected rollback, duplicate checks, missed days, seven exact agreements, and
  legacy incoherence quarantine.
- Unrelated `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/`
  runtime changes were not staged or modified.

## Residual Risk

After a valid seven-day seal, later external corrections remain a reported
residual risk by design; this task does not silently rewrite sealed labels.
