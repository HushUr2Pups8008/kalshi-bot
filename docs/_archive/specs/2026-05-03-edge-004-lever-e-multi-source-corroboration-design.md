# PROFIT-EDGE-004 Lever E — multi-source corroboration

**Status:** **CLOSED — empirically infeasible** (closed 2026-05-03 same day as drafted, after Codex's sizing audit landed)
**Tracker:** `PROFIT-EDGE-004` (Lever E entry from `2026-05-03-edge-004-lever-menu-design.md`)
**Owner:** Claude (closure) + Codex (sizing audit that produced the closure)
**Closure date:** 2026-05-03
**Closure basis:** `docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md` (commit `e5b7213`)

## 0. Closure summary (added 2026-05-03 post-Codex audit)

Codex's 2026-05-03 sizing audit ran the distinct-source distribution per OPPORTUNITY across the 13-day MacBook archive:

| dimension | distribution | N≥2 retention |
|---|---|---:|
| source-CLASS | `{0: 9, 1: 142, 2: 100, 3: 9}` | 109/260 (41.9 %), kills 100 % of historical PAPER_TRADE |
| source-INSTANCE | `{0: 9, 1: 251}` (no record has ≥ 2) | **0/260** |

**Three framings of Lever E, all dead:**

| framing | empirical result | verdict |
|---|---|---|
| source-INSTANCE N≥2 | retains 0/260 — kills everything | structurally infeasible (pipeline always joins ≤ 1 source per blend) |
| source-CLASS N≥2, no fast-lane exemption | retains 109/260, kills 3/3 historical PAPER_TRADE | trade-rate killer |
| source-CLASS N≥2, with fast-lane exemption | identical to existing G2 (`tasks/trade_readiness_gate.py:192`) | redundant |

**The 3 historical paper trades all sit in the N=1-source-class bucket.** They passed `G2_evidence_source_class_diversity` only via G2's fast-lane exemption (G2 fires only when a dossier exists). Lever E proposed a stricter G7 with no fast-lane exemption — would have suppressed every historical paper trade plus the 142+9 zero/single-class tail.

**Why source-instance is structurally always 1 (most likely):** the `BLEND_DECISION.evidence_ids_contributing` join → `EVIDENCE_INGESTION.source` produces ≤ 1 distinct source per blend. Could be (a) fast-lane fires on the first matching source and downstream sources don't get joined into the blend, (b) `evidence_ids_contributing` dedupes to one source per blend by design, or (c) typical headlines come from one source instance even when present in multiple feed channels. Regardless of cause, the empirical fact is: source-instance ≥ 2 doesn't happen on the 13-day archive.

**Closure verdict:** Lever E is closed against EDGE-004's closure path. The sequence revises from A → B → E → C → D to **A → B → C → D** (D already outside the closure path per its own demotion in commit `18e0b6c`). EDGE-004 has three in-scope levers: A (intake diversification), B (G1 calibration / attribution), C (cross-series headline correlation). Codex's empirics already characterise A's path strongly; B is an attribution lever, not an edge lever; C is a cross-series risk-control lever. **Realistic EDGE-004 closure path is essentially just Lever A.** If A.1 + A.1+ feeds fail to lift conversion to ≥ 5 % over 14 d, EDGE-004's honest closure requires escalation to PROFIT-LLM-001 (signal-analyzer LLM unification, currently gated behind GOV.P4) or P4-GATE Appendix A market-mix work — both ROADMAP-tracked and out of EDGE-004 scope.

**Companion edits in this same closure commit:**
- `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md` §3-E updated to reflect Codex's empirics + closure verdict.
- §5 sequencing updated A → B → E → C → D ⇒ A → B → C → D.

**Spec body below is preserved for historical context.** It documents the original design intent that the audit invalidated. Do not re-implement against this spec; treat it as a closed entry.

---

## HISTORICAL — Original design intent (drafted 2026-05-03, invalidated same day)

The text below is the as-drafted Lever E spec. It is preserved verbatim for the audit trail. **Do not implement.**

## 1. Why this lever (HISTORICAL)

The EDGE-004 lever menu's revised sequencing puts E third after A and B. Codex's 2026-05-03 source-class diversification audit (`docs/governance/2026-05-03-source-class-diversification-audit.md`) shows 142/260 OPPORTUNITY events are *single-source-class* — meaning a candidate can clear today's readiness gates with evidence from one source class only. The G2 gate (`evidence_source_class_diversity ≥ 2`) is the existing *class*-level corroboration check; Lever E adds a stricter *source-instance* check on top.

**Distinction matters:** G2 catches "all evidence is news-class" (`Reuters` + `AP` both count as the same class). Lever E catches "all evidence is from a single source instance" (`Reuters` only, even across multiple classes). Single-source signals are vulnerable to the publisher's house style, framing bias, and rare-but-systematic editorial errors. Multi-source corroboration tightens the conviction floor.

**Why Lever E sits *after* Lever B:** if B (G1 calibration) admits more candidates, the *aggregate* OPPORTUNITY pool grows; the multi-source threshold's cut becomes easier to size against the broader pool. Sequencing E before B would size against a narrower pool and risk over-tuning. Codex's earlier G1 admittance counterfactual (`docs/_archive/governance/2026-05-03-g1-admittance-counterfactual.md`) showed B doesn't lift trade rate directly; B's value to E is the broader attribution dataset, not new admit volume.

## 2. The fix

A new gate `G7_multi_source_corroboration` in `tasks/trade_readiness_gate.py`. The gate fires post-G2 (after the existing class-diversity check passes) and requires:

```python
# pseudo-code, exact placement TBD at landing time after looking at the
# tasks/trade_readiness_gate.py:evaluate_readiness layout
distinct_sources = {ev.source for ev in evidence if ev.source}
if len(distinct_sources) < cfg.multi_source_corroboration_min_n:
    failure_reasons.append("G7_multi_source_corroboration")
```

Default `cfg.multi_source_corroboration_min_n = 2`. With this default, candidates whose evidence comes from a single source instance (regardless of class) are SKIPPED with `reason="G7_multi_source_corroboration"`.

**Important:** the gate counts distinct *source* values (e.g., `"Reuters"`, `"AP"`, `"AlJazeera"`) — NOT distinct headlines or distinct evidence records. Two evidence records from `"Reuters"` count as 1 distinct source.

## 3. Floor candidates and decision space

| `min_n` | rationale | expected cut | risk |
|---|---|---|---|
| 1 (gate disabled) | baseline | — | — |
| 2 (default) | conservative — at least one corroboration | moderate cut, similar shape to G2's cut on class-diversity | LOW |
| 3 | stricter — broad cross-source consensus | larger cut; admits only well-corroborated signals | MED |
| 4+ | strict — multi-class consensus implied | severe cut; risks no-trade pattern resurgence | HIGH |

Recommendation: **start at `min_n = 2`** as the default. If post-deploy attribution shows the gate cuts too few candidates (G7 SKIPPED count < 5 % of total), tighten to 3 in a follow-up commit. Don't skip past `min_n = 2` to `min_n = 3` without empirical justification — the Jaccard-bisection lesson applies here too.

## 4. Sizing methodology (Codex's task — pre-deploy)

The 13-day archive replay must answer:

1. **Distinct-source distribution per OPPORTUNITY:** for each OPP event, count distinct `source` values across its `evidence_ids_contributing` (joined to `EVIDENCE_INGESTION.source` via `evidence_id`).
2. **Cut at `min_n = 2`:** how many OPP events have only 1 distinct source? These are the candidates Lever E would suppress at the default floor.
3. **Cut at `min_n = 3`:** how many OPP events have ≤ 2 distinct sources? These are the candidates the stricter floor would suppress.
4. **Edge correlation:** do single-source OPPORTUNITY events have systematically lower / higher / equal predicted edge vs multi-source events? If single-source events skew positive-edge, Lever E suppresses tradeable signal — bad. If they skew negative-edge or zero-edge, Lever E suppresses noise — good.
5. **PAPER_TRADE intersection:** of the 3 historical paper trades, how many were single-source? If any were single-source, Lever E at `min_n = 2` would have prevented them (recall the 3 historical paper trades were all losses on FISA from VitalLaw.com — single-source scenario; Lever E would correctly suppress).

**Decision criteria post-audit:**

- If single-source rate < **30 %** of OPP: Lever E provides marginal value; lower-priority than C. Defer.
- If single-source rate ∈ **[30 %, 60 %]**: Lever E worth landing at `min_n = 2`.
- If single-source rate > **60 %**: Lever E necessary; default `min_n = 2`; no need for stricter floor.

**Sizing-cost:** LOW. Same archive-replay pattern as Codex's G1-admittance / source-class / cross-series audits. New logic: distinct-source counting per OPP via `evidence_ids_contributing` join.

## 5. Components touched

- `analysis/trade_readiness_gate.py` (or `tasks/trade_readiness_gate.py`):
  - Add the G7 check post-G2, pre-`trade_blocked_reason` resolution.
  - Add `G7_MIN_DISTINCT_SOURCES` constant defaulting to 2.
- `config.py`:
  - New `MULTI_SOURCE_CORROBORATION_MIN_N` env-var-driven config knob, default 2. Loaded into `cfg.multi_source_corroboration_min_n`.
- `tests/test_trade_readiness_gate.py`:
  - New tests pinning the G7 firing / not-firing under `min_n = 2` and `min_n = 3`.
- Pre-load 5 strict-xfail tests as a follow-up commit if the lever lands; defer pre-load until Lever B's verdict (~2026-06-13) per the same logic as Lever C.

**No changes to** `analysis/decision_blender.py`, `tasks/blend_task.py`, `trading/`, `feeds/`. The gate sits in the readiness layer; existing pipelines don't need rewiring.

## 6. Risk

- **Composition with G2 (class-diversity).** G2 requires ≥ 2 distinct *classes*; G7 requires ≥ N distinct *sources*. They're orthogonal: a candidate can have 2 sources of the same class (G2 fails, G7 passes at `min_n = 2`), or 1 source spanning 2 classes (impossible — source-class is per-source), or N sources from N distinct classes (both pass). The cleanest interpretation: G2 prevents single-class OPP; G7 prevents single-source-instance OPP. Both are valid corroboration constraints.
- **Single-source FISA-shape bias.** The 3 historical paper trades were all single-source losses (VitalLaw.com on KXFISAEXTEND). Codex's pre-deploy audit (§4 step 5) must confirm Lever E would have correctly suppressed these. If not, the gate is mis-calibrated.
- **Conflict with Lever A's intake-diversification.** Lever A specifically aims to broaden the source mix. Once A.1 lands (classifier fix) and A.1+ adds new feeds, the single-source rate naturally drops as more sources reach more events. Lever E sized against pre-A data over-estimates its cut. Mitigation: Codex's audit should report sizing under both pre-A and post-A archive states, even if post-A is hypothetical.
- **Restart resets.** No in-memory state to seed; the gate reads `evidence_ids_contributing` from `BLEND_DECISION` time. Restart-safe.
- **Soak invariant.** Lever E adds a new gate to the readiness layer — decision-path edit. Cannot land mid-soak. Wave 3 of post-soak.

## 7. Acceptance criteria

- Codex's distinct-source distribution audit (§4) confirms single-source rate ≥ 30 %.
- `analysis/trade_readiness_gate.py` (or `tasks/trade_readiness_gate.py`) carries the new G7 check; `cfg.multi_source_corroboration_min_n` knob present.
- `min_n = 2` default; `=0` disables the gate (operator-side fast revert).
- 14 d post-deploy: G7 SKIPPED count is non-zero and proportional to the pre-deploy single-source rate; no canonical-event ticker in the G7 SKIPPED stream (regression guard).
- OPPORTUNITY → PAPER_TRADE conversion rate stays flat or improves vs the post-MATCH-001 + post-A baseline (Lever E suppresses noise, not signal — so trades preserved should be the high-conviction ones).
- 14 d post-deploy realized P&L on candidates that *passed* G7 is **non-negative**.
- Full pytest suite green.

## 8. Rollback

Operator-side fast revert: `MULTI_SOURCE_CORROBORATION_MIN_N=1` in env + restart. Disables the gate without code change. Code revert is the readiness-gate diff + config diff. Trivial.

**Trigger to revert:** post-deploy 14 d realized P&L on G7-passing candidates is *negative* — multi-source corroboration is suppressing wins, not losses (opposite of expectation), OR no PAPER_TRADE events fire across 14 d (gate too aggressive, recreating no-trade pattern).

## 9. Soak-window contract

Documentation only pre-deploy. Lands in **Wave 3** of post-soak landing — after base stack (Wave 1: 4 items + governance_monitor fix), after Lever A + Lever B (Wave 2). Earliest implementation: 2026-06-13 (after Lever B's 14 d window runs through 2026-06-13).

## 10. xfail harness pre-load decision

**Defer.** Lever E lands only if Lever A AND Lever B both fail to close EDGE-004. Same dead-code-risk logic as Lever B and Lever C — pre-loading 5 tests against an implementation that may never exist is wasted commits. If Lever A's first-feed verdict (~2026-05-29) closes EDGE-004, Lever E never lands.

If after Lever B's verdict (~2026-06-13) Lever E becomes the chosen path, draft the harness pre-load with the chosen `min_n` value at that time.

## 11. Out of scope

- **Per-class source-instance counts.** Counting distinct sources *within* a class (e.g., "≥ 2 distinct news sources") is over-engineering; G2's class diversity already covers cross-class structure. G7 stays at the cross-source level.
- **Source-quality weighted corroboration.** Treating Reuters as "stronger" than `r/worldnews` for corroboration purposes — already handled by `evidence_scorer._SOURCE_CLASS_QUALITY`. No interaction needed.
- **Time-windowed corroboration.** Counting only sources within the last N hours — out of scope; G6 already enforces recency.
- **Cross-headline corroboration.** Multiple headlines from the same source counted together — already the case (the gate counts distinct source instances, not distinct events).
- **`PROFIT-LLM-001`** signal-analyzer LLM unification. Outside EDGE-004 entirely.
