# Generic Search Circuit Breaker Design

## Problem

`default_search_provider()` tries Google News RSS and then DuckDuckGo Lite.
When both generic backends are unavailable, sibling contracts repeat the same
10-12 second failure chain, exhaust the research deadline, and delay unrelated
tasks. Post-restart evidence showed family-wide provider errors and timeouts with
no admission or trade, so the gate is safe but unnecessarily expensive.

## Decision

Add a process-local, fail-closed circuit around only the RSS/DuckDuckGo fallback
pair.

- `GENERIC_SEARCH_CIRCUIT_MODE` accepts `off|shadow|enforce` and defaults to
  `shadow` for the first deployment.
- The circuit starts closed. RSS remains primary and DuckDuckGo remains fallback.
- RSS failure plus DuckDuckGo success leaves the circuit closed.
- A double availability failure opens the circuit for 120 seconds and raises a
  dedicated `GenericSearchUnavailable` exception. This preserves
  `research_provider_error`; outages are never converted to `no_research_hits`.
- Calls during an enforced open interval fail immediately without network work.
- After expiry, exactly one coroutine owns the generation-scoped half-open probe.
  Followers fail fast until the probe completes. Success closes the circuit;
  another double failure reopens it.
- `shadow` mode records would-open and would-block telemetry but preserves current
  provider calls. `off` is the independent rollback.

Only provider-availability failures open the circuit: timeout, DNS/connection
errors, HTTP 429, and HTTP 5xx. Parser errors, assertions, and programming defects
remain provider errors but do not suppress unrelated queries. Structured and
official providers run before the generic circuit and are never blocked by it.

## State And Concurrency

Circuit state contains:

- state: `closed|open|half_open`;
- monotonic `open_until`;
- generation token;
- half-open async lock/owner;
- last failure classes;
- open-transition, blocked-call, and probe-result counters; and
- one-warning-per-generation marker.

State mutation occurs on the event-loop thread. Provider functions continue in
worker threads, but their results transition the circuit only after returning to
the event loop. The initial already-concurrent query batch may finish; later
queries, siblings, and cycles observe the open circuit.

Every admitted attempt captures the current generation. An open transition
increments the generation. A result admitted under an older generation may
return evidence to its own caller but cannot mutate the newer circuit state.
Only the generation-owned half-open probe may close an open circuit; an older
in-flight success can never close a circuit opened after that attempt began.

Tests receive an injected monotonic clock and explicit reset fixture so process
state cannot leak across test cases.

## Logging And Telemetry

One warning is emitted per open transition. It contains provider names,
exception classes, mode, generation, cooldown, and circuit state. It excludes
query text, response bodies, credentials, and full URLs.

Telemetry exposes:

- would-open/open transitions;
- would-block/blocked calls;
- half-open probe successes and failures;
- remaining cooldown; and
- current per-process mode/state.

No circuit event writes evidence, changes a dossier direction, or creates an
admission. Research task state may persist the same provider-error verdict it
would have received without the circuit.

## Verification

- Both providers failing produces `research_provider_error` and a would-open/open
  transition according to mode.
- Calls during enforced cooldown perform no generic provider call.
- Expiry permits one half-open probe; concurrent followers fail fast.
- Ordered completion is safe: one concurrent call opens the circuit, an older
  admitted call then succeeds, and the newer generation remains open.
- RSS success or fallback success keeps/restores the closed state.
- Availability failure classification covers timeout, DNS/connection, 429, and
  5xx cases.
- Parser/programming failures do not open the circuit.
- Structured/official providers still execute while the generic circuit is open.
- Warning emission is once per generation and sanitized.
- `off`, `shadow`, and `enforce` modes are independently tested.
- Existing research gate, dossier, decision-grade, and activation suites remain
  green.
- Independent review confirms no gate, confidence, edge, admission, sizing, or
  execution threshold changes.

## Deployment And Rollback

1. Land in its own protected PR with `shadow` default.
2. Restart and observe at least one real would-open interval or run a bounded
   operator-approved outage probe.
3. Confirm failures span unrelated queries/backends rather than a query-specific
   response, verdicts remain fail-closed, and would-block telemetry predicts a
   material reduction in sibling latency.
4. Obtain operator approval to set `GENERIC_SEARCH_CIRCUIT_MODE=enforce` in the
   environment used by `~/Library/LaunchAgents/com.jake.kalshi-bot.plist` (or its
   referenced runtime profile), then perform a protected restart.
5. Verify reduced repeated provider latency with unchanged provider-error
   semantics, zero new decision-grade admissions attributable to the circuit,
   and no paper/live order regression.
6. Roll back independently with `GENERIC_SEARCH_CIRCUIT_MODE=off` and restart.

## Alternatives Rejected

### Convert Double Failure To No Hits

Rejected because backend failure is not evidence that no relevant source exists.

### Increase The Research Timeout

Rejected because it amplifies repeated sibling latency without improving source
availability.

### Cache Query Results Across Siblings

Rejected for this slice because sibling contracts can require different
contract-relevance and counter-evidence evaluation. The circuit addresses outage
cost without reusing evidence across contracts.

## Ownership And Execution

| work | primary_agent | second_agent_review_required | operator_gate_required | recommended_workflow | why_this_assignment | safe_while_bot_running | recommended_execution_mode |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Circuit implementation | Codex | yes | merge and restart | TDD, adversarial review, protected PR | Shared external-ingestion behavior affects research throughput | yes before restart | isolated branch, shadow default |
| Enforce transition | Operator | yes | explicit config and restart | telemetry review, protected restart, runtime verification | Changes shared provider retry behavior | no | operator-controlled cutover |

## Out Of Scope

- Evidence sharing across sibling contracts.
- Changes to search query construction.
- Changes to decision-grade, edge, admission, sizing, or execution thresholds.
- Weather capture or database schema changes.
