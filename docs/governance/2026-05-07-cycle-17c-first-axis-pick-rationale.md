# Cycle-17C first-axis pick — information-gain rationale

**Type:** rationale doc for E1 axis selection.
**Drafted:** 2026-05-07 cycle-17C charter landing.
**Authority:** Cycle-17C charter §"First-axis pick" (`2026-05-07-cycle-17c-charter-single-variable-redesign.md`).
**Decision-makers:** Operator (sign-off); Codex (bias contribution); Claude (info-gain table + reasoning).

## TL;DR

Six candidate axes per cycle-17C charter. Rank by **information gain** = `likelihood-of-changing-IC-§16-outcome × magnitude-if-it-changes`. Codex bias and Claude analysis converge on **probability update rule** (`analysis/dossier_builder.py:update_dossier`) as E1.

Operator sign-off required before E1 criteria-lock commit.

## Information-gain ranking

| rank | axis | likelihood × magnitude | reasoning |
|---|---|---|---|
| **1** | **probability update rule** (`analysis/dossier_builder.py:update_dossier`) | **HIGH × HIGH** | Math layer: every dossier_update touches it. Current rule = additive linear weighted delta + capped per evidence row. Bayesian update on log-odds OR Kalman filter OR exponential-decay weighting are structurally different rules. Output is a different probability distribution → different trade admission → different IC §16 surface. Single function (`update_dossier`); compact change. Likely to change trade count materially (current 12 production-proxy → could be 5-50). High likelihood of moving IC §16. High magnitude if it moves. |
| **2** | **market-family selection** (`feeds/kalshi.py` series filter; `KALSHI_GEOPOLITICAL_SERIES` allowlist replacement) | HIGH × MEDIUM-HIGH | Universe-of-trades layer. Currently bot scans ~9k series + keyword-matches + sports-prefix blocklist (per CLAUDE.md). Restricting to specific market families (e.g., political-resolution-only, or low-volume-novel-event-only) changes the corpus structurally. Different markets → different price distributions → different signal-noise ratio. High likelihood of changing IC §16; magnitude depends on which family the bot has more signal for (unknown a priori — diagnostic risk). |
| **3** | **side inference** (`analysis/signal_analyzer.py:431` `side = "yes" if edge > 0 else "no"`) | LOW-MEDIUM × MEDIUM-HIGH | Sign convention. If wrong, flipping it is a single-line fix. Cycle-14 ruled this out at synthetic Lane B level (sites 1, 4, 5 ruled out). But cycle-16E showed production-proxy bot trades 12/12 YES — possibly side inference is fine but bot disproportionately produces YES-edge signals. Worth a single-line ablation experiment ("force NO-side trades only") as diagnostic; not expected to be a `keep`. |
| **4** | **readiness admission** (`tasks/trade_readiness_gate.py` G1-G6) | MEDIUM × LOW-MEDIUM | Gate layer. Loosening admits more trades; tightening admits fewer. With current 12-trade baseline, loosening could 2-5x trade count but risks Lever-B-counterindication pattern (more trades on a model whose IC §16 isn't proven). Tightening to "only highest-conviction trades" might surface a slice but at very low n. Either direction is more useful as diagnostic than as a kept-baseline candidate. |
| **5** | **extraction prompt** (`_LLM_SYSTEM_PROMPT` in `analysis/signal_analyzer.py`) | LOW × MEDIUM | Per cycle-15B + cycle-16E trail: extraction layer is repaired at the synthetic-fixture level (Lane B 8/8 + 2/2 ✓). Production behavior tested via C9 keyword-only re-ingestion (LLM path bypassed; L7.2 deferral). Re-running with LLM path + new prompt convention is high-effort + LLM-availability-dependent (Ollama circuit-open during cycle-16E C3) + unlikely to flip 0 IC §16 slices on the fixed corpus. Better deferred until a stronger-signal axis is exhausted. |
| **6** | **keyword map** (`config.GEOPOLITICAL_SIGNALS` + siblings) | LOW × LOW | Already heavily explored in cycle-15B (C7 keyword-map extension). Lane B 8/8 ✓ at synthetic; cycle-16E confirms keyword path is repaired. Further keyword tuning is surface-level on a layer that's already producing extraction signal. Near-zero likelihood of producing IC §16-eligible slice via keyword-map alone. |

## Recommended E1 = probability update rule

### Why update rule

1. **Single function, compact change.** `update_dossier` in `analysis/dossier_builder.py` is one function (~30 LoC). Replaceable with a different rule cleanly.
2. **High likelihood of changing trade count.** Current production-proxy 12 trades comes from current update rule's probability outputs. A meaningfully different rule (Bayesian on log-odds; Kalman; exponential-decay weighting) produces materially different probabilities → different `abs(edge)` → different admission count.
3. **High magnitude of change if it changes.** Update rule sits between extraction (now repaired) and downstream gates (audited scorer). It's the math layer that determines whether extraction-emitted signal converts to differentiated probabilities. If extraction is producing signal but update rule is dampening it, a different rule unlocks differentiation. If update rule is fine, the experiment cleanly rules it out.
4. **Diagnostic value even on `revert`.** A revert outcome here teaches "the update rule isn't the issue" — narrowing the redesign axis. Revert is information.

### Hypothesis sketch (NOT yet locked)

Operator + Codex finalize the specific update-rule replacement before E1 criteria-lock. Candidates:

- **Bayesian on log-odds.** Each evidence row contributes `log(p / (1-p))` weighted by quality_score; sum and convert back. Bounded; well-known semantics; respects the "evidence-as-likelihood-ratio" framing.
- **Kalman-filter-style weighted update.** Probability is the latent state; each evidence row updates state with a noise term proportional to source_class quality. Allows time-varying confidence.
- **Exponential decay on time-since-evidence.** Older evidence weights down exponentially; recent weighted up. Addresses possible production-evidence staleness in the 16-day replay window.

Operator picks one; criteria-lock commit names it explicitly.

### Why NOT market-family selection as E1

Strong contender (rank 2). Reason for deferral: market-family selection changes the CORPUS, which violates the "fixed corpus" constraint of cycle-17C. To test market-family selection, the experiment would need a different replay corpus (different markets). That's a meta-experiment outside the cycle-17C single-variable framework. Defer to Cycle-18 OR a special-case cycle-17C experiment with explicit corpus-change exception.

(Alternatively: if operator decides market-family selection is the highest-priority axis, cycle-17C charter's "fixed corpus" constraint must be amended for that single experiment. Operator decides.)

### Why NOT side inference as E1

Strong falsifiability but not expected to be a `keep` (cycle-14 already ruled out at synthetic Lane B). Better as a diagnostic ablation in a later experiment.

## Operator sign-off

Operator confirms E1 axis = **probability update rule** before Codex commits the criteria-lock for E1.

If operator picks differently, this doc updates with the new pick + rationale. The information-gain table above is decision support, not binding.

## Cross-links

- `docs/governance/2026-05-07-cycle-17c-charter-single-variable-redesign.md` — charter (rules source).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — ledger schema.
- `analysis/dossier_builder.py:update_dossier` — first-axis target function.
- `analysis/signal_analyzer.py:431` — side inference (rank 3 axis).
- `tasks/trade_readiness_gate.py` — readiness admission (rank 4 axis).
- `feeds/kalshi.py` series filter — market-family selection (rank 2 axis).
- `config.py` `GEOPOLITICAL_SIGNALS` — keyword map (rank 6 axis).
- `_LLM_SYSTEM_PROMPT` in `analysis/signal_analyzer.py` — extraction prompt (rank 5 axis).
