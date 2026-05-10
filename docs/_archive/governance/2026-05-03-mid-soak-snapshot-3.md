# Phase 2 governance shadow-soak — snapshot 3 (liveness/freshness check)

**Generated:** 2026-05-03 ~21:06Z (system clock)
**Latest cycle in decisions.jsonl:** 2026-05-03T19:29:18Z (same as snapshot 2)
**Gap to next expected cycle:** ~23 min (next fast cycle ETA ~21:29Z, ~2 h cadence)
**Soak tracker:** `PROFIT-PHASE2-001`
**Source:** `logs/governance/decisions.jsonl`
**Reviewer:** Claude
**Baselines:** snapshot 1 (`docs/governance/2026-05-03-mid-soak-health-report.md`, latest cycle 15:28Z) → snapshot 2 (`docs/governance/2026-05-03-mid-soak-snapshot-2.md`, latest cycle 19:29Z)

## TL;DR

Soak healthy. **No data delta since snapshot 2** — the bot is currently between fast cycles, not stalled. All health indicators unchanged from snapshot 2. Continue to 2026-05-15.

## Liveness check

The lack of new data is not a soak break:

| signal | observation |
|---|---|
| `launchctl list \| grep kalshi.governance` | Both `com.kalshi.governance.fast` and `com.kalshi.governance.deep` registered; PID `-` with exit code `0` — normal between-cycle state |
| `cycle.fast.stderr.log` / `cycle.deep.stderr.log` | both 0 bytes — no errors |
| `decisions.jsonl` last-modified | mid-cycle freeze; no truncation, no corruption |
| Latest cycle | `gc_2026-05-03_192918`, `duration_sec=22.10`, `decisions_proposed=5`, `batch_aborted=False` |
| Wall-clock gap to last cycle | 1.6 h (within normal 2 h fast cadence) |

The next fast cycle should land ~21:29Z and is the next opportunity to detect drift. If `decisions.jsonl` has not advanced past `gc_2026-05-03_192918` by ~22:00Z, that is a soak-break signal worth the operator's attention.

## Counts (unchanged since snapshot 2)

| metric | snapshot 2 | snapshot 3 | delta |
|---|---:|---:|---:|
| elapsed soak hours | 48.5 | 48.5 | unchanged |
| cycle starts / ends | 29 / 29 | 29 / 29 | unchanged |
| `GOVERNANCE_DECISION` count | 118 | 118 | unchanged |
| distinct targets | 19 | 19 | unchanged |
| PARSE_ERROR | 7 (all 2026-05-01 / -02) | 7 | unchanged |
| VALIDATION_ERROR | 0 | 0 | unchanged |
| KILL_SWITCH | 0 | 0 | unchanged |
| `batch_aborted=True` | 0 | 0 | unchanged |

## Recommendation

No operator action required. Re-check at ~22:00Z to confirm the next fast cycle landed; if it didn't, escalate to a launchd-state inspection. Otherwise next checkpoint is Codex's day-4 mid-soak report once 2026-05-04 records exist.
