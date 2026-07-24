# Live Submission No-Retry Plan

1. Add failing executor and hold-store tests asserting a durable intent is
   followed by an exclusive reservation before one legacy POST, while a failed
   reservation makes zero POSTs and concurrent claimants produce one winner.
2. Add durable TradeLogger writers for the two event types using only a
   submission ID and sanitized order summary.
3. Replace the retry loop with one submission attempt and a durable per-ticker
   reservation. Keep it through every unknown outcome, release it only after a
   verified receipt plus durable `LIVE_ORDER`, and fail closed if release fails.
4. Run focused logger/executor/report regressions, static checks, and an
   independent review before publishing.
5. Prove a fresh executor is blocked while the first POST is in flight, release
   occurs only after durable journaling, and unknown-outcome holds survive
   error, cancellation, unverified receipt, and post-response journal failure.
   Disable redirects on the legacy order POST, reject 3xx responses before
   parsing, explicitly exclude `POST` from transport retries, and surface
   active or unavailable reservation state in botcheck.
