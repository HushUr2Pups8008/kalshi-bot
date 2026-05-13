# Kalshi DriftCounter Halt — Operator Runbook

**Date:** 2026-05-13
**Status:** ACTIVE — operator-facing recovery procedure
**Authority:** v0.30.0 vertical-integration audit recommendation #10; supports PROFIT-API-001 (P0 closure).
**Related:** [PHASE2_RUNBOOK.md](PHASE2_RUNBOOK.md), [kill-switch-fire-procedure-runbook.md](2026-05-05-kill-switch-fire-procedure-runbook.md), [network-api-outage-runbook.md](2026-05-05-network-api-outage-runbook.md).

## What `data/runtime/kalshi_drift_halt.json` means

When the bot's Kalshi REST parser encounters a payload shape it does
not recognize, [`kalshi/normalizer.py`](../../kalshi/normalizer.py)
raises `UnsupportedPayloadContractError` at the parse boundary. The
DriftCounter accumulates these events; when the absolute count crosses
the threshold (LD-6: `>= 1` strict in v0.30.x), the counter writes
[`data/runtime/kalshi_drift_halt.json`](../../data/runtime/) and the
runtime fails closed for the remainder of the cycle.

**Sentinel-file presence alone equals halted state** (LD-6b). The bot
will not act on any cycle while the file exists. Clearance is **manual
operator-only** — the bot does NOT auto-clear on next cycle. This is
intentional: a recurring schema drift that the bot keeps auto-clearing
would mask the upstream API change rather than surface it.

## How to inspect the sentinel

The sentinel is a single JSON object with diagnostic fields:

```bash
cat data/runtime/kalshi_drift_halt.json | jq
```

Expected shape:

```json
{
  "halt_ts": "2026-05-13T01:23:45.123456+00:00",
  "cycle_drift_count": 1,
  "threshold_abs": 1,
  "sample_payload_hashes": ["<sha256>", "..."],
  "sample_tickers": ["KX...", "..."]
}
```

Key fields:

- `halt_ts` — when the halt fired (UTC).
- `cycle_drift_count` — how many `UnsupportedPayloadContractError`
  events the counter recorded in the halting cycle.
- `sample_payload_hashes` — sha256 of the unrecognised payloads (per
  LD-15: hash only in v0.30.x, raw archive deferred to P1). Use these
  to correlate with `logs/app/bot.log` `SKIPPED reason="unsupported_payload_contract"` entries.
- `sample_tickers` — a few of the affected market tickers, capped to
  keep the sentinel file small.

## How to verify whether the halt is still valid

Before clearing, determine whether the upstream contract change is
*real* or whether the halt was transient (single bad payload from
Kalshi). Cross-check:

1. **Refetch a known-clean ticker manually.** Pick one of the
   `sample_tickers` from the sentinel, then refetch it through Kalshi
   public REST:
   ```bash
   curl -s "https://api.elections.kalshi.com/trade-api/v2/markets/<TICKER>" | jq
   ```
   If the response now carries `*_dollars` fixed-point fields (per the
   v0.30.0 captured-fixture contract at
   [`tests/fixtures/kalshi_payloads/`](../../tests/fixtures/kalshi_payloads/)
   `CAPTURE_METADATA_2026-05-11.json`), the contract has not shifted —
   the halt was likely a transient single-payload anomaly.

2. **Spot-check `logs/app/bot.log` around the halt.** Grep for
   `SKIPPED` events with `reason="unsupported_payload_contract"`. If
   the count matches `cycle_drift_count`, the halt is correctly scoped
   to the halting cycle.

3. **Run the captured-fixture test suite.**
   ```bash
   .venv/bin/python -m pytest tests/test_kalshi_normalizer_p0.py -q
   ```
   If `test_p0_fixture_pinning_sha256` passes, the captured contract
   evidence is intact. If it fails, the captured fixtures themselves
   have drifted — operator must re-capture before clearing.

4. **Check botcheck heartbeat** (`p0_contract` line landed in MR `!20`):
   ```bash
   .venv/bin/python scripts/botcheck.py | grep "p0_contract"
   ```
   If `p0_contract: status=DRIFT versions=[1, N]` appears, a real
   contract bump is in flight and the halt is correctly armed — clearance
   without parser update would re-arm immediately.

## Clear procedure (operator-only)

**Do this only after the verification steps above confirm the halt is
either transient OR after parser updates have landed to handle the new
contract.**

```bash
# 1. Snapshot the sentinel before deletion so the incident is auditable.
cp data/runtime/kalshi_drift_halt.json \
   data/runtime/kalshi_drift_halt.cleared_$(date -u +%Y%m%dT%H%M%SZ).json

# 2. Delete the sentinel.
rm data/runtime/kalshi_drift_halt.json

# 3. Verify deletion.
ls -la data/runtime/
# Expected: no kalshi_drift_halt.json; *.cleared_*.json snapshot present.
```

**Do NOT do the following:**
- Edit the JSON in place to "reset" the count (the parser only checks
  file *presence*, not contents — LD-6b).
- Skip the snapshot — once deleted, the diagnostic context is gone.
- Clear without performing at least the manual REST refetch + the
  fixture-pinning test.

## Post-clear restart / checklist

After clearing the sentinel:

1. **No bot restart is required.** The DriftCounter rechecks the
   sentinel file each cycle; the next cycle (within ~60s) will resume
   normal operation.

2. **Tail the log for the first cycle.**
   ```bash
   tail -f logs/app/bot.log | grep -E "kalshi_drift|UnsupportedPayloadContractError|SKIPPED"
   ```
   Expected: cycle proceeds; no new `UnsupportedPayloadContractError`
   if the upstream contract is in fact stable.

3. **Re-arm risk.** If a new `kalshi_drift_halt.json` appears within
   the same cycle, the contract drift is *not* transient — the bot is
   correctly halting. Stop, investigate the upstream change, and do
   not clear again until parser updates handle the new contract.

4. **Verify operator-visible heartbeats:**

   ```bash
   .venv/bin/python scripts/botcheck.py
   ```

   Expected lines (post-clearance):
   - `kalshi_drift: cycle_count=0 halt=False last_halt_at=null threshold_abs=1`
   - `p0_cohort   : deployed_ts=<ts> (post-P0 replay rows: ...)`
   - `p0_contract : status=ok version=1 row_count=N`

   The daily-review report (`scripts/daily_review.py`) surfaces the
   same heartbeat at "0. SYSTEM HEALTH" (landed in MR `!15`); confirm
   that line shows `halt=False` after clearance.

## Why this halt is fail-closed and manual-clear-only

Recorded for posterity (LD-6 / LD-6b operator decisions, locked
2026-05-11 in the canonical P0 roadmap):

- **Strict `>= 1` threshold (LD-6):** during API-drift repair, a
  single unknown payload is a hard signal that the contract may have
  shifted. Relaxing to `>= 5` absolute or `>= 10%` ratio is a P1
  decision contingent on operational evidence that noise dominates
  signal.
- **Manual clearance only (LD-6b):** auto-retry can mask recurring
  schema drift. If the bot self-clears every cycle and the upstream
  contract has actually changed, the bot will repeatedly trade against
  a broken parser before any operator notices. Manual clearance forces
  the operator into the loop on every drift event.

## When in doubt

A halted bot is *non-trading*, not *misbehaving*. The fail-closed
posture means staying halted is the safe default. If the verification
steps above are ambiguous, leave the sentinel in place and consult
[PROFIT-API-001](../profit_path_debt_log.md) for context.
