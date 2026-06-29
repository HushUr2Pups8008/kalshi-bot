# Research Multi-Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only multi-agent research QA workflow that proves whether the research feature is functioning and capital-safe from current runtime evidence.

**Architecture:** Add `scripts/research_multi_agent_workflow.py` as an orchestrator of independent, deterministic "agent" lanes: activation, signal flow, prewarm quality, dossier evidence, capital safety, and optional rollout readiness. Reuse `scripts.botcheck`, `scripts.research_activation_status`, and `scripts.research_rollout_gate` instead of duplicating parsing logic.

**Tech Stack:** Python 3.14, stdlib dataclasses/argparse/sqlite/json, existing pytest suite.

## Global Constraints

- Read-only: no config mutation, DB writes, service restarts, external web calls, or trades.
- Profit filter: failures must identify capital risk or expected research-budget waste.
- Runtime dirt in `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/` stays out of commits.
- Verification command: `.venv/bin/python -m pytest tests/test_research_multi_agent_workflow.py -q`.

---

### Task 1: Multi-Agent Workflow Script

**Files:**
- Create: `scripts/research_multi_agent_workflow.py`
- Test: `tests/test_research_multi_agent_workflow.py`

**Interfaces:**
- Produces: `evaluate_research_multi_agent_workflow(...) -> ResearchWorkflowAssessment`
- Produces: CLI `scripts/research_multi_agent_workflow.py --home ... --trades-log ... --now ...`

- [ ] **Step 1: Write failing tests**

Add tests that create temporary `.env`, trade log rows, and a minimal `data/evidence_store.db`. Assert the workflow passes for active shadow research with fresh diverse prewarm and fresh dossier evidence, fails for repeated prewarm spend, and fails when live orders appear.

- [ ] **Step 2: Verify tests fail**

Run: `.venv/bin/python -m pytest tests/test_research_multi_agent_workflow.py -q`

- [ ] **Step 3: Implement minimal script**

Implement dataclasses:
- `ResearchAgentResult(name, ok, priority, metrics, findings)`
- `ResearchWorkflowAssessment(ok, agents, failures)`

Implement lanes:
- `activation`: profile matches and live trading is not enabled.
- `signal_flow`: recent research rows exist and are fresh.
- `prewarm_quality`: recent prewarm rows exist, no synthetic/non-Kalshi tickers, duplicate ratio below threshold.
- `dossier_evidence`: dossier DB present with fresh evidence.
- `capital_safety`: no live orders; paper trades allowed only when `--allow-paper-trades` is set.
- `rollout_readiness`: optional fail-closed only when `--require-live-cache` is passed.

- [ ] **Step 4: Verify green**

Run: `.venv/bin/python -m pytest tests/test_research_multi_agent_workflow.py -q`

### Task 2: Operator Documentation

**Files:**
- Create: `docs/governance/research-multi-agent-workflow.md`
- Modify: none

**Interfaces:**
- Documents command, exit semantics, and deployment use after `restartbot`.

- [ ] **Step 1: Add concise governance doc**

Document lanes, expected operator command, and how PASS/FAIL maps to capital protection.

- [ ] **Step 2: Verify docs are referenced by PR body**

No runtime command required.
