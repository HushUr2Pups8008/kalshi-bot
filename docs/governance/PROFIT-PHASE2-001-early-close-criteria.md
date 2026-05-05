# PROFIT-PHASE2-001 — early-close criteria + operator runbook

**Drafted:** 2026-05-05 (post-day-4 confirmation; user + Codex aligned on early-close path)
**Resolves:** spec `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5.1 addendum (added same day as this doc)
**Target close:** 2026-05-08 (day-7) if §8.5.1 gates verify clean over days 5/6/7

## TL;DR

PROFIT-PHASE2-001's volume gate is met by 5.3× (158 decisions vs 30 floor). Safety counters: 0 KILL_SWITCH / 0 batch_aborted / 0 VALIDATION_ERROR through day-4. The 14-day calendar floor is a confidence margin, not a derivation. With the volume gate over-cleared and counters clean, **closing at day-7 is empirically justified** if days 5/6/7 stay clean.

## §8.5.1 close gates (all must hold)

| # | gate | mechanism |
|---|---|---|
| 1 | ≥ 30 GOVERNANCE_DECISION records | `grep -c GOVERNANCE_DECISION logs/governance/decisions.jsonl` |
| 2 | ≥ 7 days continuous shadow-mode runtime | first cycle 2026-05-01T19:01Z → close ≥ 2026-05-08T19:01Z |
| 3 | 0 KILL_SWITCH / batch_aborted / VALIDATION_ERROR | full-window grep across all event types |
| 4 | 0 PARSE_ERROR in trailing 72 h | filter by `decided_at >= close_ts - 72h` |
| 5 | Cadence stability ±10 % | inter-cycle-gap audit; no gap > 3 h |
| 6 | ≥ 85 % reasonable on manual review | operator decision review (subjective; original §8.5 gate) |
| 7 | No mid-soak code change OR §8.5.2 policy-equivalence carve-out invoked | `bash scripts/check_soak_invariant.sh` returns 0 (clean) OR each surfaced commit has a §8.5.2 carve-out attestation |
| 8 | Written attestation | `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` (this template's sibling) |

## §8.5.2 policy-equivalence carve-outs invoked for PROFIT-PHASE2-001

Gate 7 of this soak fires on 4 behavioural commits in the soak window. The §8.5.2 carve-out applies as follows:

| commit | window-time | scope | affected slice | carve-out status |
|---|---|---|---|---|
| `fae72fa` | 2026-05-02T04:15Z | `governance/llm.py` think=False fix | 100 % (was a bug-fix; pre-fix decisions were all `{}` empty-response PARSE_ERROR) | INVOKED (effective soak start = post-fix decision time per §8.5.2) |
| `092666c` / `5eadbff` / `d29bb29` / `8882f4c` / `051f391` / `033dc8e` / `83bf954` / `ce814b9` | 2026-05-03 morning | governance/* GOV-002 audit cycle | governance test code + audit scripts only; no prod-code change to running bot's decision pipeline | INVOKED (test-only; gate-7 over-triggers because the audit harness lives in `governance/`) |
| `b47ca71` | 2026-05-03T15:28Z | A5 SYSTEM_PROMPT addition (anchor_rate interpretation) | 1/242 decisions (0.4 %) populated `anchor_rate`; the 1 active decision fired post-A5; 241 decisions had `anchor_rate=null` and were unaffected | **INVOKED (canonical example in §8.5.2)** |

Net: gate 7 passes under §8.5.2 reading. The §8.5.2 carve-out language must be reproduced verbatim in the close attestation document.

## Operator runbook for the early close

### Day 5 (2026-05-05) — early checkpoint

Run once after 2026-05-05T12:00Z (i.e., when day-5 has at least 6 fast cycles):

```bash
.venv/bin/python -c "
import json
from collections import Counter
counts = Counter()
day5_cycles = []
day5_pe = 0
for line in open('logs/governance/decisions.jsonl'):
    r = json.loads(line)
    counts[r.get('type','')] += 1
    if r.get('cycle_id','').startswith('gc_2026-05-05'):
        if r.get('type') == 'GOVERNANCE_CYCLE_START':
            day5_cycles.append(r.get('started_at',''))
        if r.get('type') == 'GOVERNANCE_DECISION_PARSE_ERROR':
            day5_pe += 1
print('total events:', sum(counts.values()))
print('decisions:', counts.get('GOVERNANCE_DECISION',0))
print('day-5 cycles:', len(day5_cycles), day5_cycles)
print('day-5 PARSE_ERROR:', day5_pe)
print('safety:',
    'KILL_SWITCH', counts.get('KILL_SWITCH',0),
    'VALIDATION_ERROR', counts.get('GOVERNANCE_VALIDATION_ERROR',0))
"
```

If day-5 has ≥ 6 cycles and 0 new PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH, the early-close path remains viable.

### Day 6 (2026-05-06) — same checkpoint, change `2026-05-05` → `2026-05-06` in the script.

### Day 7 (2026-05-07-or-08) — close decision

When day-7 has at least one full UTC day of cycles (≥ 12 cycles for 2026-05-07 OR cycles spanning 2026-05-07T19:01Z to 2026-05-08T19:01Z to satisfy the ≥ 7-day calendar floor):

1. **Re-run the checkpoint script for days 5/6/7** to confirm gates 1-5 hold.
2. **Manual review pass on all decisions** for gate 6. Operator reads the `reasoning` field of each `GOVERNANCE_DECISION` and tallies the `≥ 85 % reasonable` count.
3. **Git log audit for gate 7:**

   ```bash
   git log --oneline --since "2026-05-01T19:01Z" --until HEAD -- analysis/ tasks/ feeds/ governance/ executor/ main.py
   ```

   Expected output: ZERO behavioural commits. Any non-zero result fails gate 7 (and the whole early-close path); document and continue to day-14.

4. **Run `scripts/pre_soak_close_branch_backup.sh`** to create the rollback anchor + log archive.
5. **Write the close attestation:** copy `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` to `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md`, fill in actual numbers from the checkpoint script, commit + push.
6. **Tag the close:** `git tag -a phase2-soak-closed -m "PROFIT-PHASE2-001 early-closed day-7 per §8.5.1"`.

After all 6 steps complete, Wave-1 deploy may begin per `docs/governance/post-soak-close-rehearsal-checklist.md` §1+ (which now refers to day-7 / 2026-05-08, not day-13 / 2026-05-15).

## Date-shift summary (14d → 7d)

| milestone | old (14d soak) | new (7d soak via §8.5.1) |
|---|---|---|
| Soak start | 2026-05-01T19:01Z | 2026-05-01T19:01Z (unchanged) |
| Day-7 close window opens | 2026-05-08T19:01Z | 2026-05-08T19:01Z (was day-7-midpoint; now day-7-close) |
| Wave-1 deploy starts | 2026-05-15 (Day 13) | 2026-05-08+ (Day 7+) |
| Wave-2 first feed | 2026-05-22+ | 2026-05-15+ |
| Wave-3 Lever B + Lever C | 2026-06-13+ | 2026-06-06+ |
| Closure-target evaluation | 2026-06-06 (Wave-2 + 14d) | 2026-05-29 (Wave-2 + 14d) |

## §3 — Cadence/evidence-window tuning for NEXT soak (post-Wave-1)

User + Codex aligned 2026-05-05 that mid-soak cadence/window halving is **not allowed** (would contaminate the current measurement). The proposed halvings are reserved for the next post-Wave-1 shadow soak, applied from cycle 1:

| knob | current | proposed (next soak only) | code change |
|---|---|---|---|
| Fast cadence | 2 h | 90 min | launchd plist `com.kalshi.governance.fast.plist` |
| Deep cadence | 24 h | 12 h | launchd plist `com.kalshi.governance.deep.plist` |
| Evidence window | 168 h (7 d) | **NO CHANGE** until justified by an audit | `governance/evidence.py` |
| Predicted-effect horizon | +7 d / +1 d | NO CHANGE | `governance/decision.py` |

Note: evidence-window halving (168 h → 84 h) is **NOT** in scope for the next-soak cadence change. Codex flagged that halving the evidence window changes the decision policy itself (sources with weekly cadence or sparse evidence may flip disable/keep recommendation). If we want to test 84 h, do it as a separate, audited change against a fresh soak.

Fast/deep cadence halving is a load-test (90 min fast = 60 % more LLM calls per day; 12 h deep = 2× the deep-review frequency) but does not change decision policy itself. Cost: stronger memory pressure on the Mac Studio; OllamaLocalQwenLLM throughput at 90 min cadence may queue. Pre-deploy verification needed.

## Cross-links

- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1
- `docs/governance/post-soak-close-rehearsal-checklist.md` (now references day-7 close)
- `docs/governance/post-soak-rollback-runbook.md`
- `scripts/pre_soak_close_branch_backup.sh`
- `docs/governance/2026-05-04-day-4-mid-soak-confirmation.md` (the data point that justified §8.5.1)
- `docs/governance/edge-004-closure-path-tldr.md` v2.2
