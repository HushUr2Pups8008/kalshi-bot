# `scripts/governance_monitor.py` — fix design (KALSHI_HOME path + type-set membership)

**Status:** design (post-soak implementation; lands alongside Step 1 OBS-005 in the post-soak landing-order sequence — both are LOW-risk warm-ups for the deploy pipeline)
**Tracker:** umbrella note; debt-log entry to be opened (`PROFIT-GOV-MONITOR-001` working ID, finalize on commit) once spec is reviewed
**Owner:** Claude
**Severity:** MEDIUM (operator-facing; soak monitoring is currently flying blind)
**Drafted:** 2026-05-03
**Source incident:** `docs/governance/2026-05-03-mid-soak-health-report.md` §7 risk #1 + commit `99882b2` flagging the path bug

## 1. Problem

`scripts/governance_monitor.py` mis-reports the live Phase 2 soak state. Two bugs combine; either alone would silently break the report.

### Bug 1 — `KALSHI_HOME` env var resolves to a sibling directory

```python
# scripts/governance_monitor.py:18-20
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG = Path(os.environ.get("KALSHI_HOME", _REPO_ROOT)) / "logs/governance/decisions.jsonl"
_DEFAULT_OVERRIDES = Path(os.environ.get("KALSHI_HOME", _REPO_ROOT)) / "data/runtime_overrides.yaml"
```

The operator's shell exports `KALSHI_HOME=/Users/jacobparenti/vscode/kalshi_bot` (underscore). The actual repo lives at `/Users/jacobparenti/vscode/kalshi-bot` (hyphen). The script therefore reads `/Users/jacobparenti/vscode/kalshi_bot/logs/governance/decisions.jsonl` — which on Mac Studio either does not exist or is the legacy MacBook-era archive, depending on which machine runs the report. On the active soak host (Mac Studio) `KALSHI_HOME` resolves to a non-existent path and `_load_records` silently returns `[]`. Every aggregate is then zero, but the §8.5 status header still renders, producing a confidently-wrong report.

Reproduced: running the script from this checkout returns `"raw_decision_count": 0` while the live JSONL at `logs/governance/decisions.jsonl` has 169 events.

### Bug 2 — type-set membership uses the wrong event names

```python
# scripts/governance_monitor.py:136
elif typ in {"PARSE_ERROR", "VALIDATION_ERROR", "BATCH_ABORTED", "KILL_SWITCH"}:
    key = typ.lower()
    days[d][key] += 1
```

The actual event types written by the governance agent are prefixed with `GOVERNANCE_DECISION_` (verified against the live JSONL: `"type": "GOVERNANCE_DECISION_PARSE_ERROR"`). The membership check never matches, so `parse_error` / `validation_error` / `batch_aborted` / `kill_switch` per-day counters stay at zero even when the events fire. The mid-soak audit found 7 `GOVERNANCE_DECISION_PARSE_ERROR` events the script silently ignored.

`BATCH_ABORTED` and `KILL_SWITCH` flow through different shapes too — `batch_aborted` is a boolean field on `GOVERNANCE_CYCLE_END` records, not a separate event type. The aggregator must also read the boolean.

## 2. The fix

### 2.1 Path resolution

Drop `KALSHI_HOME` as the implicit log-file root. The script lives inside the repo it monitors; `Path(__file__).resolve().parent.parent / "logs/governance/decisions.jsonl"` is unambiguous and survives `cd` from any working directory. Operators who want to monitor a foreign file already have `--logfile` for that.

```python
# proposed
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG = _REPO_ROOT / "logs/governance/decisions.jsonl"
_DEFAULT_OVERRIDES = _REPO_ROOT / "data/runtime_overrides.yaml"
```

`KALSHI_HOME` stays in the env (other tooling reads it); the script simply ignores it for default-path resolution. If a future use case needs a configurable home root, add a dedicated env var (`KALSHI_GOVERNANCE_HOME`) instead of overloading `KALSHI_HOME`.

### 2.2 Type-set membership

Match the actual event-type prefixes plus the boolean flag on `GOVERNANCE_CYCLE_END`:

```python
# proposed — replace the single elif
elif typ in {
    "GOVERNANCE_DECISION_PARSE_ERROR",
    "GOVERNANCE_DECISION_VALIDATION_ERROR",
    "GOVERNANCE_KILL_SWITCH",
}:
    key = typ.removeprefix("GOVERNANCE_DECISION_").removeprefix("GOVERNANCE_").lower()
    days[d][key] += 1
elif typ == "GOVERNANCE_CYCLE_END" and r.get("batch_aborted"):
    days[d]["batch_aborted"] += 1
```

Verified against the live JSONL prefixes. The `removeprefix` chain reduces both `GOVERNANCE_DECISION_PARSE_ERROR` and a hypothetical `GOVERNANCE_KILL_SWITCH` to the bare key (`parse_error`, `kill_switch`) the render function already keys off.

### 2.3 No-op on the diversity / target / action paths

The `actions` / `targets` / `tuples` aggregations are correct logically — once Bug 1 is fixed the underlying records load and these counters populate. No code change there. The mid-soak report's "0 distinct targets" symptom is downstream of Bug 1, not a separate bug.

## 3. Components touched

Single file: `scripts/governance_monitor.py`. Two hunks (lines 18-20 and the elif near line 136). No imports added.

Plus tests: `tests/test_governance_monitor.py` (new file or appended to an existing harness — locate during implementation).

No changes to running-bot decision paths, log emitters, or storage schemas. **The fix is observability-only and outside the soak invariant; even so, lands post-soak per the project rule that no commits to monitor scripts ship during the soak window unless the soak itself is broken** (which the mid-soak audit confirmed it isn't).

## 4. Risk

- **Operator habit risk.** Anyone reading `governance_monitor.py` output during the active soak is currently looking at a falsely-FAIL report. The fix flips that to a truthful PASS report. If the operator's mental model has come to expect the FAIL line, the corrected output may briefly read as "did something break?". Mitigation: the spec's commit message and CHANGELOG entry should explicitly note that the §8.5 status flips were silently broken pre-fix.
- **`KALSHI_HOME` consumers.** Other scripts in the repo may still read `KALSHI_HOME`; the fix only removes the *implicit default* in `governance_monitor.py`. Verify with `grep -rn 'KALSHI_HOME' scripts/ utils/ tasks/` during implementation. Out-of-scope cleanup if other scripts have the same bug — file separately.
- **Test fixture drift.** The synthetic JSONL fixtures used by the new tests pin the event-type strings. If the governance agent later renames an event type, the tests break in lockstep with the script — that's the point.

No production-trade-rate impact; the fix touches no decision path.

## 5. Acceptance criteria

- `scripts/governance_monitor.py` default log path resolves to `<repo_root>/logs/governance/decisions.jsonl` regardless of `KALSHI_HOME`.
- `scripts/governance_monitor.py` default overrides path resolves to `<repo_root>/data/runtime_overrides.yaml` regardless of `KALSHI_HOME`.
- The aggregator counts `GOVERNANCE_DECISION_PARSE_ERROR` events into `per_day[<date>]["parse_error"]`.
- The aggregator counts `GOVERNANCE_DECISION_VALIDATION_ERROR` events into `per_day[<date>]["validation_error"]`.
- The aggregator counts `GOVERNANCE_CYCLE_END` records with `batch_aborted=True` into `per_day[<date>]["batch_aborted"]`.
- `target_count` and `actions` populate correctly once `GOVERNANCE_DECISION` records load.
- 4+ new tests in `tests/test_governance_monitor.py` (or equivalent) pin §2.1 + §2.2 + §2.3 contract.
- Re-run against the live `logs/governance/decisions.jsonl`: report shows 19 distinct targets, 7 parse_errors clustered on 2026-05-01 → 2026-05-02, 0 batch_aborted, 0 kill_switch.

## 6. Rollback

The fix is two hunks in one file. Revert is trivial.

Trigger to revert: post-deploy report regresses (e.g. a future `KALSHI_HOME` consumer relies on the implicit default the fix removes — caught by repo-wide grep during implementation but possible to miss).

## 7. Soak-window contract

This spec is pre-loaded during `PROFIT-PHASE2-001` soak (drafted 2026-05-03). The fix does not touch any decision-path file (`scripts/governance_monitor.py` is read-only telemetry; no production-decision behavior). Strictly speaking the soak invariant does not block this commit. However, per the post-soak landing-order spec (`2026-05-03-post-soak-landing-order-design.md`) Step 1, lower-risk warm-ups go first; this fix is a natural pair with OBS-005 (also LOW-risk, observability-adjacent).

Recommendation: land same day as OBS-005, in a separate commit. Either before or after — order is immaterial because the two fixes don't share any file.

## 8. Out of scope

- **Other `KALSHI_HOME` consumers in the repo.** Audit during implementation; file separately if other scripts share the same bug.
- **Adding new event types** (`GOVERNANCE_DECISION_REJECTED`, etc.) — the type list pinned here matches the *currently emitted* surface. New types ride future spec entries.
- **Render improvements.** The current text render is fit-for-purpose; cosmetic changes (color, sort order, etc.) are out of scope.
- **§8.5 quality status field.** Currently hard-coded `"HALTED - see PROFIT-GOV-002"`. PROFIT-GOV-002 is closed (commit `45c3a57`). The status string should follow the closure once a separate operator decision is made about §8.5 quality criteria. Out of scope for this fix.
