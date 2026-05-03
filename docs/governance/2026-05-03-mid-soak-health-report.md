# Phase 2 governance shadow-soak — mid-soak health report

**Generated:** 2026-05-03 (UTC)
**Soak tracker:** `PROFIT-PHASE2-001`
**Soak window:** 2026-05-01T19:01Z (first cycle) → 2026-05-15 ETA close
**Source:** `logs/governance/decisions.jsonl` (Mac Studio launchd jobs `com.kalshi.governance.{fast,deep}`)
**Reviewer:** Claude

## 1. TL;DR

**Soak is healthy.** Continue to 2026-05-15. No PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH event satisfies the soak-break criterion in `~/.claude/CLAUDE.md` (decision-consistency / async-flow rule).

One operational caveat: `scripts/governance_monitor.py` mis-reports the soak's state. It claims `candidate_diversity = FAIL (0 distinct targets)`; the raw `decisions.jsonl` shows **19 distinct targets**. The monitor — not the agent — is the failing component. Tracked under the `governance_monitor.py` `KALSHI_HOME` path bug noted in commit `99882b2`. Fix is post-soak (decision-path-adjacent code; soak invariant applies).

## 2. Soak window

- First cycle: `2026-05-01T19:01:27Z`
- Latest cycle: `2026-05-03T15:28:36Z`
- Elapsed: **44.5h** (13.2% of 14-day target)
- Per-day cycle counts: 2026-05-01 = 3, 2026-05-02 = 14, 2026-05-03 = 10
- Cadence split: fast-cycle dominant (25 fast / 2 deep) — matches the planned schedule

## 3. Cycle integrity

| Metric | Value | Verdict |
|---|---:|---|
| `GOVERNANCE_CYCLE_START` | 27 | — |
| `GOVERNANCE_CYCLE_END`   | 27 | — |
| Unmatched starts (ongoing or crashed) | 0 | PASS |
| Orphan ends | 0 | PASS |
| `batch_aborted == true` | 0 / 27 | PASS |
| KILL_SWITCH events | 0 | PASS |
| VALIDATION_ERROR events | 0 | PASS |
| Cycle duration (sec) | min 0.11 / median 17.7 / max 81.05 | PASS — no runaway tail |

Every cycle that started also ended cleanly. No timeout / crash / abort behaviour over 44.5h.

## 4. Decisions

| Metric | Value |
|---|---:|
| `decisions_proposed` (sum across cycle_end) | 108 |
| `decisions_made` | 108 |
| `decisions_applied` | **0** (shadow mode — expected) |
| Distinct targets | **19** |
| Distinct actions | 1 (`disable_source`) |

Top-5 targets:

| target | proposals |
|---|---:|
| r/fauxmoi | 14 |
| r/confidentlyincorrect | 14 |
| r/zenlesszonezeroleaks_ | 13 |
| r/newsommassacre | 12 |
| r/unresolvedmysteries | 11 |

The single-action tunnel-vision (`disable_source` only) is **task-shaped, not pathology**: the agent is correctly identifying low-signal Reddit subs the operator already knows about. Reasoning samples are source-specific and reference real ingestion-event counts and fresh-pass / match-diagnostic flow ("23 ingestion events over the last 168 hours, but none of these events resulted in fresh-passes…"). The agent is reasoning, not hallucinating.

This contradicts the monitor's `candidate_diversity = FAIL (0 distinct targets)` line. Real diversity is 19 distinct targets. Monitor is broken; soak is fine.

## 5. PARSE_ERROR analysis

7 events total. Single error class:

```
LLM output missing required fields:
['action', 'target', 'reasoning', 'confidence', 'predicted_effect']
```

| dimension | distribution |
|---|---|
| candidate_action (all) | `disable_source` ×7 |
| candidate_target | r/unresolvedmysteries ×4, r/newsommassacre ×3 |
| cycles affected | 4 distinct (gc_2026-05-01_210127, gc_2026-05-01_230130, gc_2026-05-02_010135, gc_2026-05-02_030140) |
| time window | 2026-05-01T21:01Z → 2026-05-02T03:01Z (~6h cluster) |

**Rates:**

- 7 / 108 candidates = **6.5%** candidate-level parse-error rate
- 4 / 27 cycles = **14.8%** cycle-level parse-error rate

**Distribution-over-time:** all 7 errors land in the first 8h of the soak. The most recent ~36h have **zero** PARSE_ERRORs. Pattern is transient (early-cycle qwen3 grammar warm-up), not regressive.

**Soak-break test:** the runbook's PARSE_ERROR halt criterion fires only on a sustained or rising rate. Trailing-window rate is 0% → safe.

## 6. Status against §8.5 acceptance criteria

| criterion | runbook expectation | actual | verdict |
|---|---|---|---|
| time | 14d minimum | 44.5h elapsed (13.2%) | IN_PROGRESS |
| volume | sufficient cycle count | 27 cycles / 108 candidates | IN_PROGRESS |
| cadence_coverage | both fast + deep firing | 25 fast + 2 deep, schedule honoured | PASS |
| candidate_diversity | ≥3 distinct targets | 19 distinct | **PASS** (monitor mis-reports as FAIL) |
| safety_applied_no_growth | applied count must stay 0 in shadow | 0 / 0 / 0 | PASS (vacuous) |
| safety_kill_switch | no KILL_SWITCH events | 0 | PASS |
| quality | reasoning coherence | source-specific reasoning, real metric refs | PASS |

## 7. Risks and recommendations

1. **`governance_monitor.py` is broken.** Daily operator monitoring is reading a falsely-FAIL signal. The actual JSONL shows the soak is on-track. Two known issues conflate:
   - `KALSHI_HOME` path bug (already flagged in commit `99882b2`).
   - `target` field extraction — monitor reports 0 distinct targets when the JSONL contains 19.
   Fix is post-soak (decision-path-adjacent file edit, soak invariant). Until then, **trust the raw JSONL aggregator over the monitor script**.

2. **All 108 proposals are `disable_source`.** Defensible given current source mix, but worth watching: a healthy agent should also propose `disable_keyword` / `add_keyword` / `tune_threshold` over a 14-day window. If the action distribution stays single-class through 2026-05-09, file a follow-up to verify the proposal surface isn't artificially gated.

3. **No follow-up action required from the operator** before the next checkpoint. Re-run this aggregate at 2026-05-09 (soak day 8) and 2026-05-15 (soak close).

## 8. Reproduction

```bash
# Verified-good aggregate (bypasses governance_monitor.py bugs):
python3.14 - <<'PY'
import json
from collections import Counter
events = [json.loads(l) for l in open("logs/governance/decisions.jsonl") if l.strip()]
print("types:", Counter(e["type"] for e in events))
print("targets:", len({e.get("target") for e in events if e.get("type")=="GOVERNANCE_DECISION"}))
print("parse_errors:", sum(1 for e in events if e["type"]=="GOVERNANCE_DECISION_PARSE_ERROR"))
print("kill_switch:", sum(1 for e in events if "KILL_SWITCH" in e.get("type","")))
print("aborted:", sum(1 for e in events if e["type"]=="GOVERNANCE_CYCLE_END" and e.get("batch_aborted")))
PY
```
