# Phase 2 governance shadow-soak — snapshot 4 (liveness check; awaiting next cycle)

**Generated:** 2026-05-03 21:27Z (system clock)
**Latest cycle in decisions.jsonl:** 2026-05-03T19:29:18Z (still unchanged from snapshot 2 / snapshot 3)
**Wall-clock to last cycle:** 2.0 h — at the fast-cadence threshold; next fast cycle should fire imminently
**Soak tracker:** `PROFIT-PHASE2-001`
**Source:** `logs/governance/decisions.jsonl`
**Reviewer:** Claude

## TL;DR

Soak healthy. **No data delta vs snapshot 3.** Bot is at the fast-cadence threshold (2.0 h since last cycle). If the next fast cycle does not land by ~22:00Z this becomes a soak-break signal. Continue to 2026-05-15.

## Day-4 status

**No `2026-05-04` records exist yet.** Current UTC 21:27Z; midnight is ~2.5 h away. Day-4 mid-soak confirmation report is still pending; this snapshot is the day-3-snapshot-4 liveness check that complements Codex's day-4-pending placeholder (`docs/governance/2026-05-03-day-4-pending-mid-soak-confirmation-2.md`, commit `8001a16`).

| metric | snapshot 3 | snapshot 4 | delta |
|---|---:|---:|---:|
| total events | 183 | 183 | unchanged |
| cycle starts / ends | 29 / 29 | 29 / 29 | unchanged |
| `GOVERNANCE_DECISION` count | 118 | 118 | unchanged |
| distinct targets | 19 | 19 | unchanged |
| PARSE_ERROR | 7 | 7 | unchanged |
| VALIDATION_ERROR | 0 | 0 | unchanged |
| KILL_SWITCH | 0 | 0 | unchanged |
| `batch_aborted=True` | 0 | 0 | unchanged |
| latest cycle | 2026-05-03T19:29Z | 2026-05-03T19:29Z | unchanged |

## Liveness signal at this checkpoint

| signal | observation |
|---|---|
| `launchctl list \| grep kalshi.governance` | Both `com.kalshi.governance.fast` and `com.kalshi.governance.deep` registered; PID `-` exit code `0` |
| `cycle.fast.stderr.log` / `cycle.deep.stderr.log` | both 0 bytes — no errors |
| Wall-clock since last fast cycle | **2.0 h — at threshold; next cycle expected now** |

## Escalation criterion

If `decisions.jsonl` has not advanced past `gc_2026-05-03_192918` by **2026-05-03T22:00Z** (~33 min from this snapshot's generation), launch a launchd-state inspection:

```bash
launchctl list | grep kalshi.governance
ls -la logs/governance/cycle.*
tail -50 logs/governance/cycle.fast.stderr.log
sudo log show --last 30m --predicate 'subsystem CONTAINS "com.apple.xpc.launchd"' --info | grep kalshi.governance
```

Until then, no operator action required. The 2.0 h gap is at the fast-cadence threshold but not over it.

## Per-day cycle counts (unchanged)

| date | starts |
|---|---:|
| 2026-05-01 | 3 |
| 2026-05-02 | 14 |
| 2026-05-03 | 12 |
| 2026-05-04 | **0 (pending — UTC midnight ~2.5 h away)** |
