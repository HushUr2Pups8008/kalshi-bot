# Kalshi FIX Settlement Ingress Plan

1. Add a test-first immutable ingress ledger for typed verified UMS envelopes.
2. Preserve raw-wire and parsed/provenance hashes; quarantine ambiguity.
3. Add passive config, status, inspector, and task protocol surfaces only.
4. Prove default-off/no-runtime/no-consumer behavior with focused tests.
5. Obtain KalshiPT or KalshiRT settlement-report access before implementing a
   real authenticated session handoff.
6. Keep fee-net accounting and active-cohort promotion blocked until complete
   authenticated receipt correlation is independently verified.
