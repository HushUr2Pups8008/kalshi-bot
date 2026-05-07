# Edge Replay Cycle 14 Diagnosis

Date: 2026-05-06

## Scope

Cycle-14 is diagnosis-only. No trading behavior, calibration math, extraction logic, source onboarding, thresholds, or sizing code changed.

Inputs:

- `logs/edge_replay/cycle13_live/replay_dataset.jsonl`
- `logs/edge_replay/cycle13_live/counterfactual_scores.json`
- `data/evidence_store.db`
- `data/paper_trades.db`
- locked criteria in `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md`

## Locked Criteria

The verdict below uses the pre-stated Cycle-14 criteria:

- `movement_rate < 10%` confirms calibration inertness.
- direction-correctness excludes rows in `[0.49, 0.51]`.
- direction-correctness `< 50%` when moved confirms sign-error suspicion.
- Lane A pass + Lane B fail means extraction layer corrupts or loses signal.
- Brier/log-loss at `n=24` are supporting only.
- Bot calibration/extraction fixes are out of scope until Cycle-15+ replay-gated work.

## Findings

| Metric | Value |
|---|---:|
| Replay rows | 255 |
| Movement rate | 1.57% |
| Moved rows | 4 |
| Direction-correctness denominator | 6 |
| Direction-correct | 0 |
| Direction-incorrect | 6 |
| Excluded no-direction rows | 249 |
| Direction-correctness when directional | 0.00% |

Moved vs unmoved:

| Slice | Candidates | Executable trades | Wins | P&L |
|---|---:|---:|---:|---:|
| Moved | 4 | 3 | 0 | -$7.50 |
| Unmoved | 251 | 0 | 0 | $0.00 |

Sized-bet subset:

| Metric | Value |
|---|---:|
| Sized trades | 3 |
| Direction-correct | 0 |
| Direction-incorrect | 3 |
| Direction-correctness | 0.00% |
| P&L | -$7.50 |

Calibration scores:

| Metric | Value |
|---|---:|
| Brier score | 0.2599 |
| Log-loss | 0.7131 |
| n | 24 |

Brier/log-loss are supporting evidence only at this sample size.

## LLM Split

| Branch | Rows | Moved | Movement rate | Direction-correctness |
|---|---:|---:|---:|---:|
| LLM-called | 0 | 0 | 0.00% | n/a |
| non-LLM-called | 238 | 0 | 0.00% | 0/3 |

The replayed dossier-update rows do not show LLM-called production movement. Production dossier inertness is in the non-LLM accumulation path and/or upstream extraction feeding that path.

## Synthetic Lanes

| Lane | Meaning | Result |
|---|---|---|
| Lane A | direct downstream evidence injection | PASS |
| Lane B | real extraction-in-loop | FAIL |

Lane A result:

- clear YES fixture moved off prior in the correct direction.
- clear NO fixture moved off prior in the correct direction.
- magnitude condition `|delta| > 0.05` passed.

Lane B result:

| Fixture | Extracted probability | Delta | Pass |
|---|---:|---:|---|
| clear_yes | 0.500 | 0.000 | false |
| clear_no | 0.500 | 0.000 | false |

At runtime, the real extraction path did not convert crystal-clear synthetic evidence into directional probability movement.

## Verdict

`extraction_broken`

Cycle-14 evidence says the downstream dossier update logic can move belief correctly when handed well-shaped directional evidence, but the real extraction-in-loop path fails to produce that directional signal. Production replay also shows severe inertness and wrong-direction movement when the model does leave neutral.

## Cycle-15 Scope

Recommended Cycle-15 scope: rebuild extraction.

Acceptance criteria for Cycle-15 should be replay-gated under IC §16:

- synthetic Lane B passes with the real extraction path.
- movement_rate improves without loosening capital posture.
- direction-correctness on moved rows is at least 50%, preferably materially above.
- no live capital; paper-only remains locked.
- replayed positive-EV evidence is required before any Wave-2/Wave-3 behavioral deploy resumes.

## Reproducibility

```bash
bash scripts/edge_replay/run_calibration_diagnostic.sh --dataset logs/edge_replay/cycle13_live/replay_dataset.jsonl --out-dir logs/edge_replay/cycle14
```

Artifacts:

- `logs/edge_replay/cycle14/calibration_audit.json`
- `logs/edge_replay/cycle14/per_source_movement.json`
- `logs/edge_replay/cycle14/llm_split.json`
- `logs/edge_replay/cycle14/synthetic_lanes.json`
- `logs/edge_replay/cycle14/structured_findings.json`

---

## Claude appendix (verdict consumption + Cycle-15 routing)

### Threshold-lock verification

`scripts/edge_replay/calibration_audit.py` audited against the 7 charter-locked thresholds in `docs/governance/cycle-14-post-verdict-action-checklist.md` §"Pre-execution":

| threshold | check | location |
|---|---|---|
| `movement_floor = 0.01` (|delta| > 0.01) | ✓ | `calibration_audit.py:22, 60` |
| direction-correctness exclusion zone `[0.49, 0.51]` | ✓ | `calibration_audit.py:20-21, 52-54` |
| `excluded_no_direction` reported separately | ✓ | `calibration_audit.py:103` |
| Brier/log-loss caveat present at n=24 | ✓ | `calibration_audit.py:156, 161` |
| sized-bet subset reported separately | ✓ | `calibration_audit.py:148-152` |
| moved vs unmoved EV/P&L split | ✓ | `calibration_audit.py:146-147` |
| synthetic Lane A + Lane B direction + magnitude | ✓ | `synthetic_injection_lanes.py` + Findings table above |

No threshold drift. Verdict consumable per charter.

### Verdict (Claude — concur)

`extraction_broken`. Derived strictly from the charter mapping:

- Lane A (downstream-of-extraction injection): PASS — clear-YES + clear-NO move off prior with `|delta| > 0.05`.
- Lane B (real extraction-in-loop): FAIL — both fixtures return `model_prob = 0.500`, `delta = 0.000`.

Charter §"verdict criteria": "Lane A pass + Lane B fail → `extraction_broken`." Verdict matches without operator improvisation.

### Cycle-15 scope recommendation

`docs/governance/cycle-15-conditional-charter-skeletons.md` §B — **Cycle-15B Extraction-layer rebuild scope** instantiates. No substitution, no blending with §A/C/D/E/F.

### Independent voice on what the numbers mean

Lane B is more severe than `extraction_broken` typically implies. The expected failure mode for a sign-inverted extraction is `delta < 0` on a clear-YES fixture (movement, wrong direction). What Lane B actually shows is `delta = 0.000` on BOTH fixtures — extraction produced **no signal at all**, not an inverted one. This is calibration **inertness** at the extraction layer, not sign inversion at the extraction layer.

Implication for Cycle-15B priorities (in suggested order):

1. **First investigate why extraction produces zero movement on directional input.** Likely culprits per `2026-05-06-cycle-14-sign-error-candidate-trace.md` sites 2/3/6/7:
   - LLM returning `magnitude="none"` or `direction="neutral"` on clear-YES/NO synthetic inputs (prompt-engineering pathology, same class as PROFIT-GOV-002 rubber-stamp bias).
   - Keyword-path producing zero `net_shift` because per-keyword direction map has no entries matching the synthetic fixtures' vocabulary.
   - Geo-coherence suppression or magnitude-shift mapping zeroing out the signal en route to the dossier updater.
2. **Then test whether direction is correct once magnitude is non-zero.** Before declaring sign-error fixed-or-not, the extraction must first produce non-zero magnitude. The 3/3 wrong-direction sized trades on production data could be sign error OR could be downstream noise once the rare extracted signal lands in the dossier.

This re-frame does not change the Cycle-15B verdict — extraction rebuild is correct — it sharpens the scope: Cycle-15B Codex deliverable #1 should be a per-extraction-step trace on the synthetic Lane B fixtures, identifying at which step `magnitude` collapses to zero. That trace is the discriminator between sub-fixes (LLM prompt rewrite vs keyword-table extension vs suppression-logic relaxation).

### What's RULED OUT

Per `docs/governance/2026-05-06-cycle-14-sign-error-candidate-trace.md`, Lane A pass rules out the following candidate sites as systematic 1-line sign errors:

- Site 1 (`analysis/signal_analyzer.py:431`, side derivation from edge sign) — confirmed correct by Lane A.
- Site 4 (`analysis/dossier_builder.py:160`, dossier delta application) — confirmed direction-correct by Lane A.
- Site 5 (`trading/paper_trader.py:record_trade`, executed_edge semantics) — already ruled out by code inspection; Lane A doesn't touch this path but PROFIT-OBS-004 closure stands.

Sites 2, 3, 6, 7 (LLM-path probability shift application + LLM prompt convention + keyword-path net_shift + per-keyword direction assignment) are **NOT ruled out** — they live in the extraction layer that Lane B exercised. They are the prime candidates for Cycle-15B's per-step trace.

### Capital posture

PAPER-ONLY remains locked. Cycle-14 verdict does not unblock live-trading; only a Cycle-15B post-fix replay with `ev_ci_95_lo > 0` AND `trades ≥ 10` per IC §16 does.
