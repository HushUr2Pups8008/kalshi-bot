# Cycle-14 sign-error candidate-site trace

**Type:** read-only code trace. Independent identification of candidate "1-line sign error" locations BEFORE Codex's synthetic injection test fires.
**Drafted:** 2026-05-06 cycle 14 prep.
**Authority:** Cycle-14 charter §"Sign-inversion '1-line fix' verification gate" — diagnosis must name file:line, not just "sign error suspected."

## Purpose

If Cycle-14 synthetic Lane A fails (model doesn't move correctly on synthetic high-info evidence), the diagnosis must point at a specific location. This doc enumerates candidates I checked and ruled in/out via code inspection; gives Codex's synthetic test concrete targets to verify or falsify.

If Lane A passes (model moves correctly on synthetic), the candidates here are RULED OUT, and the bug lives elsewhere (extraction, real-source quality, or information-frontier).

## Trace path

`feeds.NewsItem` → `analysis.signal_analyzer._analyze_news()` → `SignalAnalysis(estimated_probability, side, edge, ...)` → `main._signal_to_evidence()` → `Evidence(implied_probability=analysis.estimated_probability)` → `analysis.dossier_builder.update_dossier()` → `dossier.current_estimate` updated.

Bottleneck question: at which step could a sign be inverted?

## Candidate sites

### Site 1 — `analysis/signal_analyzer.py:431` — side derivation from edge sign

```python
edge = estimated_prob - market.yes_prob
side = "yes" if edge > 0 else "no"
```

**Math check:** `edge > 0` ⇔ bot's prob > market's prob ⇔ bot is bullish vs market ⇔ bet YES. ✅ Correct.

**Ruled out** unless someone refactored `edge` semantics elsewhere. (Confirmed: `analysis/__init__.py:15` documents edge as `estimated_probability - market_yes_price/100` — bot perspective, correct.)

### Site 2 — `analysis/signal_analyzer.py:578-581` — LLM-path probability shift application

```python
if direction == "yes":
    prob = min(0.95, market.yes_prob + shift)
else:
    prob = max(0.05, market.yes_prob - shift)
```

**Math check:** LLM says `direction=="yes"` (bullish for YES outcome) → push prob UP from market price. ✅ Direction-correct given LLM convention.

**Possible failure mode:** LLM convention drift. If qwen3 has been fine-tuned/prompted to output `direction="yes"` meaning "I agree with the market" (NOT "I believe the YES outcome"), the convention is inverted at the prompt layer. Code is then correctly applying the wrong-meaning input.

**Action:** check `_LLM_SYSTEM_PROMPT` definition. If "yes" semantically means "bullish for YES outcome," this site is fine. If ambiguous, flag for prompt clarification.

### Site 3 — `analysis/signal_analyzer.py:326` — keyword-path net_shift

```python
net_shift = direction_weights["yes"] - direction_weights["no"]
```

**Math check:** YES-leaning keywords increase `net_shift` positive → added to base → push prob UP. ✅ Correct.

**Possible failure mode:** per-keyword direction assignment is wrong. E.g., the keyword `"expires"` (FISA market context) might be tagged `direction="yes"` (signaling the bill DID NOT pass before May 1, which is the YES-resolving event in some market interpretations vs the NO-resolving event in others). Per-keyword rather than per-system bug; surfaces as direction-correctness anomaly clustered on specific keyword classes.

**Action:** spot-check the keyword direction map for FISA-resolution-related terms (`expires`, `lapsed`, `reauthorize`, `signed`, `passed`, `failed`, `vetoed`). If any are wrong-direction, propose a per-keyword fix in Cycle-15.

### Site 4 — `analysis/dossier_builder.py:160` — dossier delta application

```python
raw_delta = evidence_score.implied_probability - current_estimate
weighted_delta = raw_delta * evidence_score.original_weight
capped_delta = max(-cap, min(cap, weighted_delta))
new_estimate = max(0.0, min(1.0, current_estimate + capped_delta))
```

**Math check:** delta = signal - current; weighted; capped; applied additively. ✅ Direction-correct.

**Ruled out** modulo `original_weight` sign — confirm `original_weight` is always positive (it's a weight, not a signed adjustment).

### Site 5 — `trading/paper_trader.py:record_trade` — executed_edge semantics

```python
executed_edge = analysis.edge if analysis.side == "yes" else -analysis.edge
```

**Math check:** YES-side trades: executed_edge = analysis.edge (already YES-perspective). NO-side trades: executed_edge = -analysis.edge (negate to NO-perspective). For our 3 trades (side=no, analysis.edge=-0.068): executed_edge = +0.068 (correct from NO-side perspective). ✅

**Ruled out** as behavioral sign-error. This is a persistence-shape fix (PROFIT-OBS-004 closed); doesn't affect decision flow.

### Site 6 — LLM `direction` field convention (PROMPT layer)

`_LLM_SYSTEM_PROMPT` defines what "direction" means in LLM output. Sign-error here would propagate through Site 2 even though Site 2's code is fine.

**Possible failure mode:**
- LLM output convention: `direction="yes"` means "the news evidence pushes toward YES outcome."
- Bot's interpretation (Site 2): `direction=="yes"` → push prob UP toward YES outcome.
- These match. ✅ unless prompt is ambiguous.

**Action:** read `_LLM_SYSTEM_PROMPT` carefully. Check whether qwen3's actual outputs match the documented convention. (Prompt-engineering pathology — same class of failure as PROFIT-GOV-002 rubber-stamp bias on disable_source.)

If prompt is ambiguous, this is a CYCLE-15 PROMPT-ENGINEERING fix, NOT a 1-line sign inversion.

### Site 7 — Per-keyword direction assignment

Each keyword in `_KEYWORDS` (or equivalent) has a `direction` field (yes/no). If specific keywords are mis-assigned, evidence containing those keywords flows wrong-direction.

**Possible failure mode:** keyword like `"reauthorize"` mis-tagged `direction="no"` instead of `"yes"` — for FISA-style markets (resolves YES on legislation passing), seeing "reauthorize" should push prob UP, not DOWN.

**Action:** Cycle-14 should produce a per-keyword direction-correctness audit. If clustering by specific keywords, that's a Cycle-15 keyword-table fix (typically multi-keyword corrections, not 1-line).

## Ruling

**Sites 1, 4, 5: math is internally consistent. Ruled out as systematic 1-line sign errors.**

**Sites 2, 3, 6, 7: depend on data inputs.** Specifically:
- Site 2 + Site 6: depend on LLM output convention matching prompt convention. Prompt-engineering check needed.
- Site 3 + Site 7: depend on per-keyword direction assignment correctness. Per-keyword audit needed.

**If Cycle-14 synthetic Lane A passes:** all sites ruled out. Bug is in extraction, source quality, or information-frontier (not in the math).

**If Cycle-14 synthetic Lane A fails:** Codex should test sites 2/3/6/7 in order:
1. Synthetic Lane A with `direction="yes"` LLM-output → does prob go up? (Site 2)
2. Synthetic Lane A with all-YES keywords → does prob go up? (Site 3)
3. Synthetic Lane A with `_LLM_SYSTEM_PROMPT` review against actual LLM behavior → match? (Site 6)
4. Synthetic keyword-only test against per-keyword direction assignments → spot inversions? (Site 7)

## What this trace does NOT replace

This is preparatory for Cycle-14. Cycle-14 synthetic injection is the AUTHORITATIVE test. Code inspection rules out math bugs but cannot prove behavioral correctness without runtime test. Lane A + Lane B remain the truth.

## What I'm NOT proposing

- Don't fix anything in Cycle-14 based on this trace. Diagnosis-only per charter.
- Don't ship even a 1-line "fix" for a candidate site without:
  1. Cycle-14 synthetic test confirming the site is broken (not just suspected)
  2. Codex + Claude + operator agreement on file:line + before/after pseudocode
  3. Cycle-15 deploy with replayed-EV evidence per IC §16

## Cross-links

- `analysis/signal_analyzer.py` — sites 1, 2, 3, 6
- `analysis/dossier_builder.py:160` — site 4
- `trading/paper_trader.py:record_trade` — site 5
- `tests/fixtures/cycle14_synthetic_evidence.json` — Cycle-14 synthetic fixtures
- `docs/governance/2026-05-06-cycle-14-charter-calibration-diagnosis.md` — Cycle-14 charter
- `docs/governance/edge-replay-pivot-playbook.md` — IC §16 Rule 5 diagnostic playbook
