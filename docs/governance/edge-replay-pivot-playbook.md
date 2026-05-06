# Strategic-pivot diagnostic playbook (IC §16 Rule 5 trigger)

**Type:** pre-staged operator decision aid for the case where Cycle-13 (or later) replay returns "no positive-EV slice at any n."
**Drafted:** 2026-05-06 (cycle 13).
**Authority:** IC §16 Rule 5 — "negative replayed-EV evidence is also evidence" → triggers strategic-pivot conversation, NOT "ship anyway."
**Companion:** `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` (the redirect that authorized this pivot path).

## TL;DR

If replay finds NO positive-EV slice (current state at Cycle-12), three diagnoses are candidate root causes. Each has a different fix. **Don't pick one based on intuition.** Run the discriminating tests below, then pivot based on what the data says.

The 3 candidate causes:

1. **Calibration problem** — bot's belief model regresses to market prior; produces market-equivalent probabilities.
2. **Sample-size problem** — replay window too short to detect edge that exists.
3. **Information-frontier problem** — no edge available at this trader's data access (markets too efficient given current sources).

Cycle-13 evidence inventory check (before invoking this playbook): per cycle-13 dossier integrity audit, **21 of 24 resolved-market dossiers stuck at `current_estimate=0.5000`** despite ingesting 266 evidence rows. KXTRUMPIRAN has 107 evidence rows, still at 0.5000. This data point heavily indicts cause #1 (calibration). Run discriminators below to confirm.

## Cause 1 — Calibration problem (model regresses to prior)

**Hypothesis:** the dossier update logic only moves the estimate for specific evidence shapes (high-conf official / explicit anchor signals). Mainstream news (Guardian / NYT / Al Jazeera = 81% of evidence) is too noisy to move the prior. So the model produces `model_prob ≈ 0.50` (the prior) for most markets, regardless of underlying fact pattern.

**Discriminating tests:**

1. **Distribution of `dossiers.current_estimate` post-soak.** If concentrated at exactly 0.5000 (the prior), confirms regression-to-prior. Cycle-13 audit: 21/24 = 87.5% at 0.5000. STRONG calibration indictment.
2. **Per-source belief movement.** Group `dossier_updates` by `source`; compute average `|new_estimate - prior_estimate|`. Sources with average movement < 0.01 are calibration-inert. If most sources are inert, calibration not data is the bottleneck.
3. **LLM-vs-no-LLM split.** Filter `dossier_updates.llm_called=true` vs `llm_called=false`. If non-LLM updates produce average |delta| ≈ 0 while LLM updates produce |delta| > 0.05, the issue is keyword-routing / LLM-gating logic — non-LLM ingests don't move beliefs at all.
4. **Probe with a synthetic high-information evidence row.** Inject a single `evidence` row with crystal-clear directional content (e.g., "FISA Section 702 reauthorization signed into law on April 30, 2026" against `KXFISAEXTEND-MAY01`). Verify the model exits the prior. If still at 0.50, calibration deeply broken.

**Fix candidates if confirmed:**
- Increase weight of mainstream news in the blender (raise default `evidence_quality_score` floor for `news` class).
- Lower the LLM-call threshold so more evidence triggers LLM analysis (currently most ingestion is keyword-only).
- Add explicit anchor-signal extraction for major sources (Guardian / NYT) to bypass blender averaging.

**Spec authoring required:** any change to blender weights or LLM-call thresholds is behavioral; per IC §16, deploys only after pre/post replay shows positive-EV-slice emergence on synthetic data first.

## Cause 2 — Sample-size problem (window too short)

**Hypothesis:** edge exists but replay window (16 days, 24 markets, 552 decisions) too small to surface it at 95% CI. Real edge requires 100+ resolved markets to detect.

**Discriminating tests:**

1. **Compute statistical power of current sample.** For an effect size of 5% expected EV per trade, what sample size is needed to reject null at 95% CI? Formula: `n ≈ (z² × σ²) / effect²`. With σ ≈ 0.50 (per-trade P&L stdev for binary $1 markets) and effect=0.05, n ≈ 384. We have 3-trade sample. Power crisis confirmed. But: this only matters if there's actually edge in the population.
2. **Trend over time.** If late-window decisions show better admission rates / smaller absolute edges than early-window, model is improving with experience. If flat, no trend toward edge emergence.
3. **Extrapolation budget.** At current ingestion rate (~10 evidence/day, ~1 trade per 100 evidence), reaching n=100 trades takes ~10,000 days at current pipeline. The bot CANNOT generate enough data to power a sample-size-only fix within any reasonable horizon.

**Fix candidates if confirmed:**
- Wait. Continue operating the bot for N months until corpus expands. NOT recommended — runs operationally for free with negative EV, accumulating losses to test a hypothesis that may be false anyway.
- Backfill historical evidence from public sources (Guardian / NYT archives + matching Kalshi resolved markets); replay against expanded corpus. Plausible if archive sources accessible.
- Lower the "trades ≥ 10" threshold and accept lower-confidence positive-EV claims at, say, n=5 with 90% CI. Loosens discipline; risky.

**This cause is rarely standalone.** Usually combined with calibration (the bot makes decisions but they're not informative). Verify cause 1 isn't the dominant explanation first.

## Cause 3 — Information-frontier problem (no edge available)

**Hypothesis:** Kalshi's market-makers + crowd already aggregate the public news streams the bot uses. Bot's information set ≈ market's information set ⇒ no informational advantage ⇒ no edge to capture.

**Discriminating tests:**

1. **Time-to-price-update measurement.** For a known-resolved market (e.g., FISA signed April 30), at what timestamp did Kalshi YES price first move > 5%? At what timestamp did the bot ingest the corresponding news? If price moved 30+ minutes before bot ingestion, market is faster ⇒ no edge available via mainstream feeds.
2. **Source mix analysis.** Compare bot's source distribution (81% Guardian/NYT/Al Jazeera mainstream) to "what produces the alpha" benchmarks: typically primary-source government / regulatory / specialist feeds beat mainstream news. If bot's only "specialist" source is VitalLaw with n=3, the source mix is structurally adversarial to edge.
3. **Manual market-by-market case study.** For 5 of the 24 resolved markets, manually identify: what news outlet broke the resolving fact first? Kalshi YES price reaction time post-news. If bot's pipeline ingests the news AFTER price moves, edge is unavailable.

**Fix candidates if confirmed:**
- **Onboard structurally faster sources:** government Twitter/social, primary-source regulatory feeds (SEC EDGAR, Federal Register, court filings), specialist bloggers with insider sourcing. NOT generic legal/geopolitics RSS (which is downstream of mainstream news).
- **Reframe: don't compete on speed, compete on interpretation.** Use slower sources but apply better LLM-driven inference (e.g., reading 10-K filings for forward-looking cues that markets undervalue). Slower, deeper, contrarian.
- **Reframe market selection.** Instead of trying to beat efficient markets (sports, election outcomes), target markets where the crowd is wrong systematically — niche regulatory, low-volume, novel-event markets. Per `data/paper_trades.db`, bot already ended up in legal-niche (KXFISAEXTEND); the niche choice may be right but the source (VitalLaw) is mid-tier.
- **Strategic withdrawal.** Bot is not a viable trading strategy as currently designed; redirect resources elsewhere. Honest answer if causes 1+2 also point this direction.

## Discriminator: which cause to investigate first

Run in this order:

1. **Cause 1 (calibration)** — already 87.5% of evidence per cycle-13 audit points here. Check sources 2-3 of cause 1 to confirm or rule out.
2. **If cause 1 ruled out:** Cause 3 (information frontier). Cheap to test via the time-to-price-update measurement on 1-2 resolved markets.
3. **Cause 2 (sample size) is rarely the answer alone.** Verify cause 1 + 3 first. If both ruled out and replay window is genuinely tiny (n < 30), pause to gather more data; otherwise accept the negative result.

## Output format

Each diagnostic test produces a finding. Document them in `docs/governance/edge-replay-pivot-finding-<YYYY-MM-DD>.md` (named per the date of the diagnosis). The doc closes with:

- Confirmed cause(s).
- Fix candidate selected.
- Spec doc to author (or "strategic withdrawal" if applicable).
- IC §16 implications: any new behavioral deploy requires replayed-EV evidence under the new approach.

## What this playbook PREVENTS

Without this playbook, the failure mode is: replay returns "no positive-EV slice" → operator + Claude jump to "more feeds!" or "lower G1!" → same speculation cycle that triggered IC §16 in the first place. The playbook forces the question "WHY no edge?" before "how do we get edge?"

## Cross-links

- `docs/IMPLEMENTATION_CONTRACT.md` §16 — Replayed-EV gate (Rule 5 triggers this playbook)
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — redirect authority
- `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md` — data inventory (confirms 21/24 dossiers at prior)
- `docs/governance/edge-replay-cycle12-report.md` — replay harness output
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-005` — replay harness debt entry
