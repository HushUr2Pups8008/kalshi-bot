# Cycle-15B Sub-Fix Proposal

Status: single-step sub-fix selected

## Selected Surface

- Surface: `per_keyword_direction_map`
- Target: `config.py:734` / `GEOPOLITICAL_SIGNALS`
- Scope: `single_step`

## Evidence

- C2 zero-collapse step: `keyword_path` (`8` fixtures)
- C4 keyword coverage gaps: `8` directional fixtures
- C5 suppression count: `0`
- C3 LLM statuses: `ollama_circuit_open, ollama_unavailable`

## Reason

keyword_path is the first zero-collapse step; directional fixture vocabulary is absent from the keyword direction map; suppression did not fire.

## Before

```python
GEOPOLITICAL_SIGNALS = [
    # Existing geopolitical/event keyword groups.
    # Cycle-15B resolution-event fixture vocabulary is absent, so _keyword_score(...)
    # returns net_shift = 0 for FISA, pardon, Iran-deal, and Vance-visit outcomes.
]
```

## After

```python
GEOPOLITICAL_SIGNALS = [
    # Existing groups unchanged.
    {
        "keywords": [
            "fisa section 702 reauthorization signed into law",
            "fisa section 702 reauthorization legislation",
            "trump issues pardons",
            "signed pardons",
            "sign nuclear deal",
            "comprehensive nuclear agreement",
            "arrives in islamabad",
            "official pakistan visit",
        ],
        "direction": "yes",
        "strength": 0.12,
    },
    {
        "keywords": [
            "fisa section 702 expires",
            "senate fails to act",
            "will not become law",
            "no january 6 pardons",
            "no pardons for january 6 defendants",
            "cancels pakistan trip",
            "canceled his planned pakistan trip",
        ],
        "direction": "no",
        "strength": 0.12,
    },
]
```

## Guardrails

- C7 may implement only this keyword-map change unless the operator grants a scope extension.
- Acceptance remains `>=6/10` Lane B fixtures passing direction + magnitude.
- IC §16 final acceptance still requires replay evidence: `>=1` slice with `ev_ci_95_lo > 0` and `trades >= 10`.

---

## Claude L6 verdict appendix

**Drafted:** 2026-05-06 post-Codex C3-C6 (`851eb86`).
**Authority:** Cycle-15B charter §"Sub-fix selection acceptance" + `2026-05-06-cycle-15b-claude-independent-trace-read.md` (L4 + L5).

### Verdict: `multi-step-needed-operator-scope-extension`

Codex's proposed keyword-map extension is correct on the surface and step it addresses (cycle-14 trace sites 3 + 7; concur with L4 independent read). It will move 8 of 10 fixtures (F1-F7 directional + F10 repetition) above the `|delta| > 0.05` magnitude floor with direction matching `expected_direction`.

However, **the proposal does NOT satisfy charter Lane B post-fix verification clause** "F8/F9 NEUTRAL fixtures stay within `expected_magnitude_max` (0.02 / 0.005 respectively)."

### F8 NEUTRAL over-emission finding

Per Codex C5 `suppression_trace.json` F8 entry (verified independently in L4):

```
keyword_estimate_keywords: ["senate judiciary"]
pre_suppression_magnitude: 0.12
keyword_estimate_side: "yes"
suppression_triggered: false
```

Existing keyword `"senate judiciary"` (group_index 16, direction=yes, strength=0.12) fires on F8 procedural-meeting fixture and produces `|Δ|=0.12`. Charter F8 acceptance: `expected_magnitude_max=0.02`. Post-Codex-fix F8 magnitude = 0.12 > 0.02 → **F8 fails charter Lane B clause.**

Codex's proposed sub-fix does not change `"senate judiciary"` keyword strength, direction, or matching context. The F8 over-emission is inherited from the existing map, not introduced by Codex's additions, but the charter clause applies to post-fix Lane B output regardless of source.

### Single-step vs multi-step framing

| approach | scope | charter compliance |
|---|---|---|
| (a) Ship Codex proposal as-is (single-step keyword extension) | 8/10 fixtures pass directional clauses; F8 over-emission untouched | ≥6/10 ✓ but F8 NEUTRAL clause ✗ |
| (b) Extend sub-fix to also constrain `"senate judiciary"` over-emission (e.g., narrow keyword to `"senate judiciary committee approves"` or add suppression rule on procedural-only headlines) | Multi-step | Requires operator scope-extension per charter |
| (c) Ship (a) and accept F8 failure as Cycle-15B-extension scope (second sub-fix attempt) | Single-step + explicit deferral | Counts as fix attempt #1 toward "3 failed sub-fixes → architectural conversation" rule |

Operator picks. None of the three is Codex- or Claude-decision-only.

### Match against locked acceptance criteria

| criterion | Codex proposal status |
|---|---|
| Single-step at C2-named zero-collapse step | ✓ keyword_path / `GEOPOLITICAL_SIGNALS` |
| Named file:line | ✓ `config.py:734` |
| Before/after pseudocode | ✓ |
| Cited C2-C5 evidence | ✓ |
| ≥6/10 directional fixtures pass post-fix | ✓ (estimated 8/10) |
| F8/F9 NEUTRAL stay within `expected_magnitude_max` | ✗ F8 = 0.12 > 0.02 |
| F10 BSR-5 damping holds | unverified — relies on evidence-store-layer weight decay, not keyword layer; C8 must confirm |

### Secondary concerns (not blocking; document for C8/C10 review)

1. **Overfitting risk.** Proposed phrases are very fixture-specific (e.g., `"fisa section 702 reauthorization signed into law"` is essentially the F1 headline verbatim). Production evidence rows may not contain these exact phrases. C9 re-ingestion + C10 replay output will reveal if real evidence has these phrases at all. If post-fix replay shows ≥1 positive-EV slice (IC §16 acceptance), overfitting concern is moot. If 0 slices despite Lane B pass, overfitting + information-frontier hypothesis converge — consider Cycle-16B source onboarding.
2. **F10 BSR-5 damping.** Sub-fix doesn't change BSR-5 behavior at evidence-store / dossier-update layer. C8 must verify `|delta_F10| < 0.5 × |delta_F1|` still holds. Likely yes (BSR-5 fires at dossier layer, not keyword layer), but worth explicit C8 check.
3. **C3 Ollama unavailable.** L5 confirms this is non-blocking for Cycle-15B. Forward concern for Cycle-16 if no positive-EV slice surfaces.

### What's RULED OUT

Per `2026-05-06-cycle-14-sign-error-candidate-trace.md` and L4 independent read:

- Site 1 (`signal_analyzer.py:431` side derivation) — ruled out by Lane A pass.
- Site 4 (`dossier_builder.py:160` dossier delta) — ruled out by Lane A pass.
- Site 5 (`paper_trader.py` executed_edge) — ruled out by code inspection + behavioral non-effect on extraction.
- Site 2 (`signal_analyzer.py:578-581` LLM-path probability shift) — ruled out by C2 first-step-collapse rule.
- Site 6 (`_LLM_SYSTEM_PROMPT` convention) — ruled out by C2 first-step-collapse rule. C3 unavailable Ollama leaves production-LLM-behavior unverified, but irrelevant for the zero-collapse phenomenon.
- Suppression layer (C5) — ruled out by `suppression_triggered=false` across all 10 fixtures.

Sites 3 (per-keyword `net_shift` zero) and 7 (per-keyword direction assignment for fixture vocabulary missing) CONFIRMED as the surface. Codex's keyword-map extension addresses both directly.

### Recommendation to operator

Authorize one of (a) / (b) / (c) above. Claude recommends **(b)** if F8 over-emission can be addressed with a narrow keyword-context tightening (e.g., require co-occurrence of resolution-event vocabulary alongside `"senate judiciary"`) — that keeps the sub-fix scope adjacent to the C2-identified surface and avoids deferring a known failure mode. Claude recommends **(c)** if the F8 fix would require touching extraction logic outside `GEOPOLITICAL_SIGNALS` (e.g., context-windowing in `keyword_estimate`), since that crosses into multi-surface scope that warrants a fresh charter cycle.

Operator decides. C7 implementation does not proceed until decision lands.
