# Gate-6 Path 1 fire-time template

**Type:** operator-fill template for close-day gate-6 attestation under Path 1 (raise daily review budget).
**Drafted:** 2026-05-06 cycle 14 prep.
**Authority:** `docs/_archive/governance/2026-05-06-gate-6-capacity-resolution-plan.md` Path 1 fallback after Path 3 failed.
**Companion:** `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` (gate-6 row this template fills).

## TL;DR

Cycle-13 capacity audit at 80/day budget: 0.663 reviewable. Path 3 (re-eval at close) failed — trend went up. Operator commits to Path 1: budget set to ≥ peak day decision count to clear gate 6.

This template lets operator pre-stage the gate-6 attestation block with concrete numbers + commands. Fill in at fire-time.

## Step 1 — Determine close-day decision counts

```bash
.venv/bin/python -c "
import json
from collections import Counter
counts = Counter()
for line in open('logs/governance/decisions.jsonl').read().splitlines():
    if not line.strip(): continue
    r = json.loads(line)
    if r.get('type') == 'GOVERNANCE_DECISION':
        d = r.get('decided_at', '')
        if d:
            counts[d[:10]] += 1
print('Per-day decisions:')
for d in sorted(counts):
    print(f'  {d}: {counts[d]}')
peak = max(counts.values()) if counts else 0
total = sum(counts.values())
print(f'\nTotal: {total}')
print(f'Peak day: {peak}')
print(f'Path 1 budget recommendation: {max(peak, 80)}')
"
```

Expected output at close-day (~2026-05-08): per-day counts 46/82/109/146/169/<TBD>/<TBD>; peak ≥ 169.

## Step 2 — Set daily review budget

```bash
DAILY_BUDGET=<peak_count_from_step_1>   # e.g. 169 OR higher if late-soak peak grew
```

If peak grew above 200 by close-day, consider whether Path 1 cost (200+ decisions reviewed by hand) is acceptable. Alternative at that point: continue to default 14-day floor (2026-05-15) and reassess; may surface a different gate-failure dynamic.

## Step 3 — Run capacity audit at chosen budget

```bash
.venv/bin/python scripts/manual_review_capacity_audit.py \
  --daily-budget $DAILY_BUDGET \
  --json
```

Expected output: `{"status": "pass", "reviewable_fraction": ≥ 0.85, ...}`

If `status: fail`, the chosen budget is insufficient. Either raise budget further OR accept gate 6 failure → continue to default 14-day floor.

## Step 4 — Run bulk review against decisions.jsonl

```bash
.venv/bin/python scripts/governance_decision_review.py \
  --since "2026-05-01T19:01Z" \
  --bulk-mode \
  --output logs/governance/review_2026-05-08.jsonl
```

`--bulk-mode` prompts once for the uniform dead-source disable class; remaining decisions reviewed individually.

Expected operator-time: at peak 169 decisions × ~30 sec avg (with bulk-mode collapsing dead-source class) → 30-90 min focused review on close-day. Cycle-9's 67-decision review took similar time per commit `9f8deef`; this is ~2× that effort.

## Step 5 — Aggregate verdict

```bash
.venv/bin/python scripts/governance_decision_review.py \
  --aggregate logs/governance/review_2026-05-08.jsonl
```

Expected output: total reviewed / reasonable count / reasonable rate. Gate 6 passes if reasonable rate ≥ 85%.

## Step 6 — Fill attestation gate-6 row

Replace `PROFIT-PHASE2-001-early-close-attestation.md` gate-6 row (currently AT RISK with cycle-13 numbers) with:

```
- [x] **Gate 6: Manual review.** Decisions reviewed: <N> / <total>; reasonable count: <R>; reasonable rate: <pct> % (≥ 85 % required). Path 1 budget = <DAILY_BUDGET>. Bulk-review ran <Y/N>. Time elapsed: <minutes>.
```

If reasonable rate < 85%: gate 6 FAILS. §8.5.1 close criteria not met → soak continues to default 14-day floor (2026-05-15). Operator updates attestation status accordingly.

## Cycle-9 manual review reference

Per commit `9f8deef`, cycle-9 manual review of 67 day-1-to-day-3 decisions returned 100 % reasonable. That's the prior baseline; same operator + same review methodology at 2.5× volume should produce similar reasonable rate barring late-soak quality drift.

## Edge cases

- **Peak grew unexpectedly mid-soak.** If close-day peak > 250: consider whether the bot's decision distribution itself has changed (degraded LLM, runaway disable_source loop). If yes, separate diagnostic before proceeding with Path 1.
- **Reasonable rate borderline (80-84%).** Re-review the borderline 5-10 decisions. If still < 85%, gate 6 fails honestly; do NOT artificially round up.
- **Operator time-constrained.** If time is the actual bottleneck, ackowledge it and choose: review subset honestly + report sub-population reasonable rate (gate 6 still fails by spec letter, but honest); OR delay close to 2026-05-09. NEVER ship gate 6 attestation without actual review.

## Cross-links

- `docs/_archive/governance/2026-05-06-gate-6-capacity-resolution-plan.md` — Path 1/2/3 decision plan (cycle 11)
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` — gate-6 row this template fills
- `scripts/manual_review_capacity_audit.py` — capacity audit
- `scripts/governance_decision_review.py` — bulk review tool
- `docs/profit_path_debt_log.md` `PROFIT-GOV-004` — Phase-3 sample-based redesign (defer-not-now)
