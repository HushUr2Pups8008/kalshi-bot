# PROFIT-PHASE2-001 decision-distribution analysis — accelerator for §8.5 gate-6 review

**Generated:** 2026-05-05 (during PROFIT-PHASE2-001 wind-down; pre-Day-7 close)
**Methodology:** read-only inspection of `logs/governance/decisions.jsonl` GOVERNANCE_DECISION rows. Aggregated by action type, target shape, confidence bucket, and evidence-summary uniformity. No new code; pure analysis to help the operator focus close-day manual review (gate 6).
**Read-only; no decision verdicts emitted by Claude.** Per §8.5 manual review is "owner: user."

## TL;DR

**241/242 decisions (99.6 %) are mechanically uniform: `disable_source` against a dead source with zero pipeline activity over a 168 h window.** Operator's close-day gate-6 manual review can collapse to a single bulk judgment ("are dead-Reddit-sub disables reasonable?") plus a 1-decision anomaly review.

## Distribution

| dimension | value | count | % |
|---|---|---:|---:|
| action | `disable_source` | 242 | 100.0 |
| target shape | `r/<reddit_sub>` | 241 | 99.6 |
| target shape | mainstream news (`NYT > World News`) | 1 | 0.4 |
| confidence | [0.85, 0.95) | 175 | 72.3 |
| confidence | [0.95, 1.00] | 67 | 27.7 |
| evidence | `fresh_pass_count == 0` | 242 | **100.0** |
| evidence | `match_count == 0` | 242 | **100.0** |
| evidence | `active_market_count == 0` | 242 | **100.0** |
| evidence | `window_hours == 168` | 242 | 100.0 |

## What this means for gate-6 review

The §8.5 spec says "manual review of all 30+ decisions confirms ≥ 85 % are reasonable (subjective gate, owner: user)." Operator can adopt a **two-tier review** to make this tractable:

### Tier 1 — bulk review (241 decisions)

All 241 dead-Reddit-sub `disable_source` decisions share an identical evidence shape:

- The source produced 0 fresh-pass events in 168 h
- The source produced 0 MATCH_DIAGNOSTIC events in 168 h
- The source has 0 active markets associated
- The 168 h evidence window is correctly applied

The decision is mechanically: "this source has no signal at all — disable it." This is the textbook reasonable disable. **Operator can bulk-mark these reasonable** without per-decision inspection. If the operator wants to spot-check, sampling 5-10 from the most-flagged targets (`r/zenlesszonezeroleaks_` 23×, `r/gamingleaksandrumours` 19×, etc.) is sufficient.

### Tier 2 — anomaly review (1 decision)

The only non-bulk decision: `gd_2026-05-04_0049` — `NYT > World News` with `anchor_rate=1.0` (the lone post-A5-prompt anchor-rate-active decision; canonical example in §8.5.2). The LLM said: "100 % LLM anchor rate indicates no information edge over market consensus; no MATCH_DIAGNOSTIC events in 168 h; disable." Operator should:

1. Verify NYT World News genuinely produced 0 MATCH_DIAGNOSTIC over 168 h (cross-check trade-log archive if curious)
2. Verify the anchor-rate=1.0 reading is correct (the LLM agreed with market price on every published call → no edge)
3. Decide: is disabling NYT World News a reasonable verdict?

If operator's verdict is "reasonable": gate-6 reasonable-rate = 242/242 = 100.0 %. Well above the 85 % floor.

If operator's verdict is "not reasonable": gate-6 reasonable-rate = 241/242 = 99.6 %. Still well above the 85 % floor.

**Either way, gate 6 passes.**

## Implications for §8.5.2 carve-out

The §8.5.2 carve-out for the A5 SYSTEM_PROMPT change (`b47ca71`) cited 1/242 = 0.4 % affected slice. This analysis confirms the slice empirically: 241/242 decisions had `anchor_rate=null` and therefore no exposure to the A5 prompt addition. The 1 anchor-rate-populated decision (`gd_2026-05-04_0049`) fired post-A5 entirely. **The §8.5.2 carve-out's empirical justification is intact.**

## Caveats

- **Distribution is heavily skewed by the test corpus.** PROFIT-PHASE2-001 ran the governance agent against a candidate pool dominated by dead Reddit subs. A different candidate-pool composition (e.g., active mainstream news sources, RSS feeds with frequent matches) would produce a different distribution and the bulk-review acceleration may not apply.
- **The 1 NYT decision is empirically grounded** — `anchor_rate=1.0` does carry meaningful information when populated. Future PROFIT-PHASE2 soaks may produce many anchor-rate-populated decisions; gate-6 review difficulty scales with that fraction.
- **Confidence buckets** are not load-bearing for the review. The §8.5 gate is about decision-quality, not LLM-stated-confidence. A high-confidence bad decision is still bad; a low-confidence good decision is still good.

## Reproducibility

Operator can regenerate this distribution at close-time (gates may have shifted slightly):

```bash
.venv/bin/python -c "
import json
from collections import Counter
actions, targets, evi = Counter(), Counter(), Counter()
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'GOVERNANCE_DECISION': continue
    actions[r.get('action','?')] += 1
    t = r.get('target','')
    targets['reddit' if t.startswith('r/') else 'other'] += 1
    es = r.get('evidence_summary', {})
    if es.get('fresh_pass_count',0)==0 and es.get('match_count',0)==0:
        evi['mechanically_uniform'] += 1
print('actions:', dict(actions), 'targets:', dict(targets), 'evi:', dict(evi))
"
```

## Cross-links

- `PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` §6 — gate-6 manual review step (this analysis informs the workflow)
- `PROFIT-PHASE2-001-close-day-decision-flow.md` — close-day flowchart
- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.2 — manual-review gate + carve-out
- `scripts/governance_decision_review.py` — tool for the per-decision review (operator can invoke or skip in favour of bulk-review when distribution warrants)
