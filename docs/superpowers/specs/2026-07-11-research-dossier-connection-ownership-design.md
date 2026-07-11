# Research Dossier Connection Ownership Design

## Problem

The research prewarm path can exhaust the launchd process file-descriptor limit.
A 25-market temporary-database probe peaked at 98 numeric descriptors, including
79 descriptors for the dossier SQLite database. The production restart then
failed while appending a trade-log record with `OSError: [Errno 24] Too many
open files` and hung while executor threads shut down.

`ResearchDossierStore` currently uses `with self._connect() as conn`. A SQLite
connection context manager controls transaction commit or rollback, but it does
not close the connection. Nine read/write paths therefore leave connection
ownership to garbage collection during a high-concurrency research burst.

## Decision

Add one private `ResearchDossierStore` context manager that owns the full
connection lifecycle:

1. Create the connection with `_connect()`.
2. Enter the connection transaction context.
3. Yield it to the caller.
4. Commit or roll back through the SQLite context protocol.
5. Close the connection unconditionally after transaction exit.

Every dossier schema, read, and write operation will use this context. The
existing global write lock and per-market lock remain unchanged. Research
prewarm concurrency remains three.

## Alternatives Rejected

### Reduce research concurrency

Lower concurrency would reduce the descriptor burst but leave connection
ownership undefined. Repeated sequential cycles could still accumulate open
database handles.

### Raise launchd descriptor limits

A larger limit would delay failure and allow the leak to consume more process
resources. It would not make connection lifetime deterministic.

### Close connections at each call site

Manual `try/finally` blocks at nine sites duplicate transaction and close
semantics. A single context prevents future dossier methods from repeating the
bug.

## Compatibility And Safety

- Database schema and queries remain unchanged.
- Transaction commit and rollback behavior remains explicit.
- Connections close on success, query failure, and transaction failure.
- No live database mutation is required for verification; tests and FD probes
  use temporary databases.
- Paper mode, research shadow mode, and all trade gates remain unchanged.

## Verification

1. Unit-test that a successful operation commits and closes its connection.
2. Unit-test that a failing operation rolls back and still closes.
3. Exercise representative schema, read, and write methods with a tracking
   connection wrapper and assert every created connection is closed.
4. Run the focused research dossier and prewarm suites.
5. Repeat the 25-market temporary-database probe and require dossier database
   descriptors to remain bounded near active concurrency rather than dozens.
6. Restart with `restartbot`, confirm no descriptor exhaustion, and observe at
   least one complete research prewarm cycle.

## Out Of Scope

This repair does not address research admission idempotency, expired-market
eligibility, source diversity, lifecycle telemetry, or retry-log enrichment.
Those remain separate reviewable slices after runtime stability is restored.
