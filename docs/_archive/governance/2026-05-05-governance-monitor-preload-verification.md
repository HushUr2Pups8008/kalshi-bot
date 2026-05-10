# GOV-003 governance_monitor preload verification

Date: 2026-05-05
Scope: `docs/superpowers/specs/2026-05-03-governance-monitor-fix-design.md`, `tests/test_governance_monitor.py`, `scripts/governance_monitor.py`

## Result

Spec, harness, and production code remain aligned. The production fix has not landed yet; the strict-xfail harness still describes real current drift.

Focused test:

`tests/test_governance_monitor.py -q`

Result:

`4 passed, 5 xfailed`

## Alignment checks

| item | spec expects | harness pins | production today |
| --- | --- | --- | --- |
| default path | ignore stale `KALSHI_HOME`; resolve repo-root `logs/governance/decisions.jsonl` | strict-xfail default-path tests | still uses `Path(os.environ.get("KALSHI_HOME", _REPO_ROOT))` |
| overrides path | ignore stale `KALSHI_HOME`; resolve repo-root `data/runtime_overrides.yaml` | strict-xfail default-overrides test | still uses `Path(os.environ.get("KALSHI_HOME", _REPO_ROOT))` |
| parse/validation event names | count `GOVERNANCE_DECISION_PARSE_ERROR` / `GOVERNANCE_DECISION_VALIDATION_ERROR` | strict-xfail type-set tests | still checks bare `PARSE_ERROR` / `VALIDATION_ERROR` |
| batch aborts | count `batch_aborted=True` on `GOVERNANCE_CYCLE_END` | strict-xfail batch-abort test | still checks bare `BATCH_ABORTED` event type |

## Drift assessment

No spec drift found. No production drift toward the fixed behavior found. GOV-003 remains a valid Wave-1 companion deploy: land the path-resolution and event-type changes, then remove the five strict-xfail markers in the same commit.
