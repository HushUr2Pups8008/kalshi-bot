# AGENTS.md

## Purpose

This system ingests external data, generates probability estimates, and executes trades on Kalshi when positive expected value (EV) opportunities are identified.

## Architecture

* `/feeds` → data ingestion (no side effects)
* `/analysis` → transforms input into probabilities (PURE)
* `/tasks` → orchestrates workflows and scheduling
* `/trading` → decision-making and execution logic
* `/kalshi` → API interaction layer

## Core Rules (CRITICAL)

* `/analysis` must be PURE (no API calls, no trade execution)
* `/trading` is the ONLY place where trades can be executed
* `/tasks` may orchestrate but must not contain trading logic
* `/feeds` must not modify or interpret data beyond ingestion

## Strategy Constraints

* Probabilities must always be between 0 and 1
* Market price is treated as implied probability
* Trades must only occur when EV is positive
* Trade frequency must not increase without justification
* Do not degrade selectivity of trades

## Execution Safety

* System must support paper trading mode
* No trades should be executed in testing or simulation
* Live trading must always be gated behind explicit configuration
* Never bypass mode checks

## Time Handling (CRITICAL)

**All system time must be handled in UTC.**

Requirements:
* All timestamps in logs, events, and persisted data must be UTC
* Use ISO-8601 or explicitly marked UTC timestamps
* Do not use local system time for:
  * logging
  * event timestamps
  * comparisons
  * freshness calculations

Logging:
* Application logs (e.g. `bot.log`) must use UTC timestamps
* Structured logs (e.g. `trades.jsonl`) must use UTC timestamps
* If using Python logging, set:
  ```python
  logging.Formatter.converter = time.gmtime
  ```
Conversions:
* Only convert to local time at presentation layer (if ever needed)
* Internal logic must remain UTC-only

Enforcement:
* Mixed timezones are considered a defect
* Any component emitting non-UTC timestamps must be corrected

## Async Safety

* Do not modify async patterns unless necessary
* Avoid introducing blocking calls into async workflows
* Preserve event ordering and timing behavior
* Be cautious of duplicate event handling

## Safe Changes

* Refactoring without behavior changes
* Logging improvements
* Performance improvements in isolated modules
* Fixing failing tests

## Risky Changes (Require Human Review)

* Changes to `/analysis`
* Changes to `/trading`
* Changes affecting async flow or timing
* Changes to execution conditions or thresholds

## Validation Expectations

Changes must:

* Pass all tests
* Not increase number of trades without justification
* Not reduce signal quality
* Maintain or improve decision consistency

## Cross-Platform Script Standards

* Operational workflows must be cross-platform by default
* Prefer Python entrypoints for any script that operators may need to run manually or on a schedule
* PowerShell scripts (`.ps1`) may exist as Windows convenience wrappers, but must not be the only supported execution path
* New operational tooling must not require Windows-only shells or utilities when a cross-platform alternative is practical
* If a workflow is needed on both Windows and macOS, the Python implementation is the source of truth and shell wrappers should delegate to it

### Shell Guidance

* Bash/zsh and Python are preferred for cross-platform compatibility
* Avoid introducing new PowerShell-only dependencies for core workflows
* If a PowerShell wrapper is kept, document the equivalent Python command

---

## Codex Behavior Rules (CRITICAL)

When making changes, you MUST:

### Scope Control

* Modify the smallest number of files necessary
* Do not make unrelated edits or "cleanup" changes
* Do not refactor unless explicitly requested

### Pre-Change Verification

* Inspect relevant files before editing
* Confirm where target variables/functions are defined
* Do not assume structure without reading the code

### Diff Discipline

* Show proposed diff before applying changes when task is narrow
* Apply only the exact diff that was proposed
* Do not expand scope after diff is shown

### Safety Boundaries

* Never modify `/trading`, `/analysis`, or async flow unless explicitly instructed
* If a requested change appears to require risky areas, STOP and explain why

### Validation

* Run relevant tests if they exist
* If no tests apply, state that explicitly
* Do not claim validation that was not performed

### Determinism

* Prefer minimal, explicit changes over "smart" rewrites
* Avoid introducing ambiguity or implicit behavior

### Failure Handling

* If uncertain, ask for clarification instead of guessing
* If constraints conflict, prioritize safety rules

### Config Consistency
* Do not introduce contradictions between configuration sections
* If a source is both enabled and disabled, flag it instead of silently proceeding

---

## Notes 
* This system may execute real-money trades.
* Incorrect modifications can result in financial loss.