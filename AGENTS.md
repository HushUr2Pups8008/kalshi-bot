# AGENTS.md

## Purpose

This system ingests external data, generates probability estimates, and executes trades on Kalshi when positive expected value (EV) opportunities are identified.

---

## Architecture

* `/feeds` → data ingestion (no side effects)
* `/analysis` → transforms input into probabilities (PURE)
* `/tasks` → orchestrates workflows and scheduling
* `/trading` → decision-making and execution logic
* `/kalshi` → API interaction layer

---

## Core Rules (CRITICAL)

* `/analysis` must be PURE (no API calls, no trade execution)
* `/trading` is the ONLY place where trades can be executed
* `/tasks` may orchestrate but must not contain trading logic
* `/feeds` must not modify or interpret data beyond ingestion

---

## Strategy Constraints

* Probabilities must always be between 0 and 1
* Market price is treated as implied probability
* Trades must only occur when EV is positive
* Trade frequency must not increase without justification
* Do not degrade selectivity of trades

---

## Execution Safety

* System must support paper trading mode
* No trades should be executed in testing or simulation
* Live trading must always be gated behind explicit configuration
* Never bypass mode checks

---

## Async Safety

* Do not modify async patterns unless necessary
* Avoid introducing blocking calls into async workflows
* Preserve event ordering and timing behavior
* Be cautious of duplicate event handling

---

## Safe Changes

* Refactoring without behavior changes
* Logging improvements
* Performance improvements in isolated modules
* Fixing failing tests

---

## Risky Changes (Require Human Review)

* Changes to `/analysis`
* Changes to `/trading`
* Changes affecting async flow or timing
* Changes to execution conditions or thresholds

---

## Validation Expectations

Changes must:

* Pass all tests
* Not increase number of trades without justification
* Not reduce signal quality
* Maintain or improve decision consistency

---

## Notes

This system may execute real-money trades.
Incorrect modifications can result in financial loss.
