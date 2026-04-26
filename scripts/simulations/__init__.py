"""Behavioural simulations for kalshi-bot pipeline stages.

Each simulation in this package is a runnable, parameterised harness that
exercises a specific pipeline stage end-to-end against either production
state or curated synthetic fixtures, and reports on observed behaviour
relative to a documented expectation.

Simulations differ from unit tests in three ways:

1.  They run *production code paths*, not isolated functions, and may read
    real production state (trade-log archives, evidence_store, paper_trades
    DB) — under the constraint that they NEVER mutate that state.
2.  They are designed to be re-run against future code revisions to surface
    behavioural drift before it becomes a production incident; pytest
    smoke tests in `tests/test_simulations_smoke.py` ensure the harnesses
    themselves stay green.
3.  Their primary deliverable is a structured *report* (printed table,
    JSONL artefact) the operator can audit, not a binary pass/fail that
    halts CI.

Origin: 2026-04-26 PROFIT-EDGE-001 / EDGE-002 / EDGE-003 systematic-debugging
investigation. The G1/G4 calibration analyses and the 5-LLM-positive-event
readiness/executor walkthroughs originated as `/tmp/*.py` scripts during
that investigation; this package is their permanent home.
"""
