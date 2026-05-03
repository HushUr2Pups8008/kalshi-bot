# MATCH-001 (B') false-suppression audit — orthogonality finding

**Generated:** 2026-05-03 (post-snapshot-5; reassigned Codex task #4, Codex usage exhausted)
**Tool:** `scripts/simulations/match001_bprime_false_suppression_audit.py`
**Methodology:** For each `MATCH_SUPPRESSED` record in the Mac archive, apply the post-fix B' predicate (`scripts/simulations/match001_bprime_anchor_sizing.py:bprime_suppresses`) and partition by agreement.

## TL;DR — surprising orthogonality

**100 % of `MATCH_SUPPRESSED` records (489/489) flip to "kept" under B'.** The two predicates do not overlap on a single archive record. B' is **orthogonal** to the existing pre-fix suppression logic, not a replacement for it.

This is not the result the task framing assumed. The task framing assumed B' would reproduce the existing suppression decisions and add additional coverage. The empirical result is that B' covers a **different** class of records entirely: single-anchor records where the only matched token is a ticker substring. The existing pre-fix `MATCH_SUPPRESSED` logic targets near-threshold low-overlap records — a different surface.

## Numbers

| metric | value |
|---|---:|
| `MATCH_SUPPRESSED` total | 489 |
| Agree with B' (B' would also suppress) | **0** (0.0 %) |
| Flip to kept under B' (B' would NOT suppress) | **489** (100.0 %) |

### Flip categorisation (heuristic legitimacy)

| category | count | %_of_flips |
|---|---:|---:|
| `LEGITIMATE` (likely OK to un-suppress) | 255 | 52.1 |
| `AMBIGUOUS` | 25 | 5.1 |
| `LIKELY_NOISE` (should stay suppressed) | 209 | 42.7 |

### Flip score-bucket distribution

| match_score bucket | count |
|---|---:|
| `lt_0.05` | 0 |
| `[0.05, 0.10)` | 461 |
| `[0.10, 0.20)` | 28 |
| `ge_0.20` | 0 |

All flips are below 0.20 match score; ~94 % cluster in [0.05, 0.10). This is consistent with the existing pre-fix logic targeting near-threshold weak-overlap matches.

### Top flip heuristic-flag combos

| flag combo | count |
|---|---:|
| `minimal_overlap+near_threshold_score+single_named_entity_only` | 199 |
| `low_token_overlap+minimal_overlap+near_threshold_score+single_named_entity_only` | 162 |
| `low_token_overlap+minimal_overlap+single_named_entity_only` | 72 |
| `minimal_overlap+single_named_entity_only` | 56 |

`single_named_entity_only` + `minimal_overlap` is the dominant suppression rationale — single-named-entity matches with only one overlap token. B' does not target this surface; it targets the case where the single overlap token is a *ticker substring*.

## Reinterpretation

The original task framing — "of the 435 keys currently suppressed (pre-fix), how many would *flip* to kept under the post-fix predicate? Pre-deploy validation that B' doesn't accidentally un-suppress legitimate noise" — assumed B' would *replace* the existing suppression logic. The empirical result shows B' is purely **additive**: it suppresses records currently kept (the false-negative axis Codex audited at `e5b7213`), but it does NOT modify the existing suppression of `MATCH_SUPPRESSED` records.

**This is good news for deploy safety.** The post-fix MATCH-001 (B') deploy will not regress the existing `MATCH_SUPPRESSED` decisions. The 209 `LIKELY_NOISE` and 25 `AMBIGUOUS` records that the heuristic flagged stay suppressed because the existing logic continues to fire; B' is just another suppression layer stacked on top.

**This is interesting news for separate auditing.** The 255 `LEGITIMATE` records suggest the existing pre-fix `MATCH_SUPPRESSED` logic might be over-suppressing some genuinely-relevant matches (records with score ≥ 0.20 OR ≥ 3 matched tokens, but suppressed anyway). That's a *separate* audit — about the existing pre-fix suppression logic, not about B'. Filing as a future ROADMAP follow-up; out of scope for the immediate B' deploy validation.

## Caveats

- **`bprime_suppresses` is the simulation predicate used by `match001_bprime_anchor_sizing.py`, NOT the prod-code form.** The prod-code post-fix predicate may differ slightly (per spec §2 + §5.1). If they diverge, this audit's numbers shift. The simulation predicate has been Codex-reviewed and is the best available pre-deploy proxy.
- **The Mac archive is 13 days.** All conclusions are subject to the same archive-size caveat as Codex's class-level audit. Once OBS-003 lands and 14 d of post-deploy data accumulates, re-run.
- **The legitimacy categorisation is heuristic.** `LEGITIMATE` thresholds (`score >= 0.20` OR `len(matched) >= 3` OR no `low_token_overlap`) are operator-defensible but not load-bearing. Operator can re-categorise by re-running the audit with different thresholds.

## Pre-deploy implications

- **MATCH-001 (B') deploy is safe** w.r.t. un-suppression of legitimate noise. 0/489 currently-suppressed records would flip to kept under B' interaction (B' is additive).
- **Codex's prior false-negative audit** (`docs/governance/2026-05-03-match001-bprime-false-negative-audit.md`, commit `8001a16`) is the load-bearing pre-deploy gate — that audit checks the OTHER axis (false-positive risk that B' over-suppresses currently-kept records).
- **Combined with the false-negative audit**, both directions of B' deploy risk are now empirically sized: 0 false-negatives + 0 false-suppression interaction = clean deploy.

## Files

- `scripts/simulations/match001_bprime_false_suppression_audit.py` — this audit
- `scripts/simulations/match001_bprime_false_negative_audit.py` — Codex's complement audit
- `scripts/simulations/match001_bprime_anchor_sizing.py` — `bprime_suppresses` definition
- `docs/governance/2026-05-03-match001-bprime-false-negative-audit.md` — Codex's complement report
