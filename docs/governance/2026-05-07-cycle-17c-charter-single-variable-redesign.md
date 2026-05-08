# Cycle-17C charter — single-variable redesign program

**Type:** redesign program (not a single cycle). One hypothesis per experiment; revert by default; IC §16 acceptance bar; experiment ledger as first-class artifact.
**Drafted:** 2026-05-07 cycle-16E verdict landing → operator pick §C(b) redesign per cycle-17 skeletons.
**Authority:** PROFIT-EDGE-011 (Cycle-17 operator decision); cycle-17 skeletons §C-redesign; locked rules below.
**Owner:** Codex (per-experiment implementation); Claude (review + ledger maintenance + verdict appendices); Operator (axis-pick + keep-vs-revert sign-off).
**Tracker:** PROFIT-EDGE-011 closed on first cycle-17C experiment landing; succeeded by per-experiment debt entries (PROFIT-EDGE-012 = E1, PROFIT-EDGE-013 = E2, etc.) OR a single rolling PROFIT-EDGE-012 with ledger reference per the schema chosen at first-experiment land.
**Status:** ACTIVE.

## TL;DR

Cycle-13 → cycle-16E established that the bot's current configuration produces no IC §16-eligible slice on the 272-row replay corpus / 12 production-proxy trades. Anti-correlation framing was withdrawn (cycle-16E `scorer_fixed_no_signal_confirmed`). Operator picked §C(b) redesign over §B source onboarding / §C(a) pause / §C(c) paper-only research. Cycle-17C is the redesign program.

**Cycle-17C answers ONE question per experiment:** does this single-variable change produce ≥1 IC §16-eligible slice (`ev_ci_95_lo > 0` AND `trades ≥ 10`) on the frozen replay corpus + audited scorer?

Either YES → keep + tag new baseline, OR NO → revert. Diagnostic-only is a third bucket: the experiment teaches us where the failure is but does not justify a baseline change.

## Pre-stated decision criteria (LOCKED)

Operator does NOT change these post-hoc. Mirrors cycle-14/15B/16D/16E lock pattern.

### Operating rule

```
One hypothesis. One code change. One replay. One written verdict.
Keep only if pre-declared, IC §16-grade bar passes; otherwise revert to frozen baseline.
```

### Hard constraints

- **Fixed corpus.** Cycle-16E replay dataset (`logs/edge_replay/cycle16d/replay_dataset.jsonl`, 272 dossier rows). Baseline production-proxy = 12 trades. Each experiment reports its own resulting n and minimum-detectable effect.
- **Fixed audited scorer.** `scripts/edge_replay/scorer_forensics_audit.py` production-proxy mode + `score_counterfactual_pnl.py`. NO scorer edits during cycle-17C unless the explicit experiment is "scorer change" (and that's a special-case experiment with its own validation path).
- **Fixed replay command.** Documented at `scripts/edge_replay/run_cycle16d_replay.sh --skip-price-backfill` + `scorer_forensics_audit.py` production-proxy variant. Each experiment uses identical commands.
- **One active experiment at a time.** No parallel changes. No "while we're here" cleanup. No bundled commits mixing experiment + unrelated work.
- **No-overlap rule.** No experiment starts until the previous experiment has an explicit ledger row with final decision (`keep` / `revert` / `diagnostic-only`). Prevents overlapping half-results.
- **No source onboarding mixed in.** Source onboarding = §B scope; cycle-17C is §C(b) redesign only.
- **No bundled changes.** No simultaneous prompt + model + threshold + market-selection changes. One axis. One file/function. One commit.
- **Revert is default.** A change is kept only if it clears a pre-declared IC §16-grade bar. "Looks directionally better" is NOT enough.
- **Acceptance criteria + hypothesis LOCKED pre-replay.** Each experiment commits its hypothesis + acceptance threshold + revert condition + replay command in a separate criteria-lock commit BEFORE replay runs. Mirrors cycle-14 pre-registration pattern. Without pre-registration, "keep" decisions drift toward post-hoc rationalization (the cycle-16D M6 failure mode).
- **Acceptance bar = IC §16.** Numeric: ≥1 (source × market_family × signal_type) slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`. Anything weaker = diagnostic-only.
- **Market-implied expected wins baseline**, not 50% coin-flip. Per memory `feedback_market_implied_baseline.md`. Each experiment reports actual wins vs `Σ p_yes_at_decision_time` for the trades it produces.
- **3 sequential reverts triggers architectural-conversation rule.** Per `superpowers:systematic-debugging`. After 3 consecutive `revert` verdicts, halt cycle-17C and rethink the redesign axis itself before E4. Prevents grinding through 10 single-variable changes that all fail because the wrong axis was picked.
- **Time-box ~1 week per experiment.** Forces decisive verdict. Soft deadline; if an experiment lingers >2 weeks without verdict, file as `diagnostic-only` and move on.

### Experiment declaration (locked pre-replay)

Each experiment commits the following BEFORE replay runs:
1. **Hypothesis** — single sentence, falsifiable.
2. **Exact file/function touched** — file:line + before/after pseudocode.
3. **Expected directional effect** — qualitative + quantitative if predictable.
4. **Replay command** — exact shell command + dataset path + scorer flags.
5. **Acceptance threshold** — IC §16 (≥1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10`) OR diagnostic-only criteria explicitly stated.
6. **Revert condition** — what triggers automatic revert.
7. **Resulting-n + MDE disclosure plan** — how the experiment will report n + minimum-detectable effect post-run.

### Experiment result reporting

Each experiment reports the following AFTER replay runs:
- **trades** (count)
- **wins** (count)
- **market-implied expected wins** (`Σ p_yes_at_decision_time` over trades)
- **P&L** (cents or dollars, consistent with scorer output)
- **IC §16 slices** (count of slices passing both gates)
- **delta vs frozen baseline** — trade-count delta + slice-count delta + wins delta
- **decision** — `keep` / `revert` / `diagnostic-only`
- **commit hashes** — pre-replay (criteria-lock) + post-replay (verdict + revert if applicable)

### Frozen baseline

Initial frozen baseline = cycle-16E production-proxy verdict:
- 12 trades / 0 wins / market-implied 1.005 expected wins / -$1.005 P&L / 0 IC §16 slices.
- Commit `c913ffd` (cycle-16E codex: scorer forensics audit) is the commit-hash anchor for "frozen baseline."

When an experiment is `keep`-verdict, the new baseline = post-experiment commit hash. Tag in ledger.

## Out of scope for Cycle-17C

- Source onboarding (Cycle-17B if operator picks; cycle-17C does NOT include).
- Strategic redesign as a unitary "ship a new bot" plan. Cycle-17C is incremental redesign by single-variable experimentation.
- Re-ingestion of `data/dossier_updates_post_fix.db`. POST_FIX_REBUILT cohort intact unless an experiment explicitly tests re-ingestion (and that experiment's revert condition includes restoring POST_FIX_REBUILT).
- Live-trading flag flip. PAPER-ONLY remains locked. IC §16 pass authorizes only **Cycle-17 §A deploy-candidate review** (slice-specific risk review + capital allocation per IC §16 Rule 4 + kill-switch plan + operator commit citing replay report). Live-trading flip = separate operator action.
- Wave-1 deploy interference. Wave-1 ships 2026-05-08T19:01Z on independent track.
- Multi-cycle changes per experiment. If a hypothesis requires touching 2+ files/functions to test, it's NOT a single-variable experiment; redesign the experiment OR escalate to operator scope-extension.

## Sole exception to "single variable per experiment"

Trivial measurement bug in the scorer's diagnostic output (e.g., off-by-one in a breakdown bucket count, JSON field mistyped) is in scope as a tooling fix. Bot-side or production-runtime changes are NOT.

## First-axis pick

See `2026-05-07-cycle-17c-first-axis-pick-rationale.md` (companion doc, same commit as this charter). First-axis pick = **probability update rule** (`analysis/dossier_builder.py:update_dossier`) per information-gain heuristic.

## Sequencing

Strict:
1. Charter + ledger schema + first-axis pick rationale land BEFORE E1 starts.
2. E1 hypothesis + acceptance threshold + replay command commit lands BEFORE E1 replay runs.
3. E1 replay output + verdict commit lands BEFORE E2 starts (no-overlap rule).
4. After E1 verdict, ledger row updated with `keep` / `revert` / `diagnostic-only` decision.
5. E2 (or repeat of E1 with refined hypothesis if `diagnostic-only`) starts.
6. After 3 sequential reverts → halt + architectural conversation BEFORE E4.

## Capital posture

PAPER-ONLY. NO LIVE CAPITAL throughout Cycle-17C. IC §16 pass on any single experiment authorizes only Cycle-17 §A deploy-candidate review per skeleton. Live-trading flip remains separate operator commit with replay-report citation.

## Cycle-17C success criterion

EITHER:
- **A single experiment passes IC §16 acceptance + survives Cycle-17 §A deploy-candidate review** → cycle-17C delivers an actionable Wave-2 slice candidate. PROFIT-EDGE-011 closes; Wave-2 §A path opens.
- **OR** 3+ experiments fail to clear acceptance → architectural-conversation rule fires → operator escalates to Cycle-18 (different redesign axis) OR §C(a) pause OR §C(c) paper-only research.

Either outcome is acceptable cycle exit. Indefinite drift is NOT.

## Cycle-17C failure modes (anti-patterns to watch)

- **"Looks directionally better" keep-creep.** Fix: pre-registered IC §16 bar enforced.
- **Bundled changes hidden in a single commit.** Fix: code review on each E-commit; one file/function only.
- **Post-hoc rationalization of a borderline result.** Fix: criteria-lock commit before replay; if result borderline, default = revert.
- **Sample-size cargo-culting.** Fix: each experiment discloses its own n + MDE; results below MDE = diagnostic-only.
- **Indefinite axis-grinding.** Fix: 3-revert architectural rule.
- **Scope creep into source onboarding / live-trading flip.** Fix: charter §"Out of scope" enumerates each.

## Amendments and structural findings (chronological)

This section records observations surfaced DURING cycle-17C execution that affect future experiment design but do NOT modify the locked rules above. Hard rules in §"Pre-stated decision criteria (LOCKED)" remain immutable per charter intent. Structural findings here are advisory.

### 2026-05-08 — E2 G1 admission sweep: 12-trade ceiling is a corpus property

Source: `docs/governance/2026-05-08-cycle-17c-e2-g1-admission-sweep.md` (`8629681`); E2 ledger row in `2026-05-07-cycle-17c-experiment-ledger-schema.md` (`89c6f4e`).

Finding: outcome-blind G1 admission sweep over `{0.05, 0.04, 0.03, 0.02, 0.01, 0.00}` produced identical counts at every threshold. Production-proxy admission ceiling stays at 12 regardless of any G1 threshold change. Skip-reason analysis showed `paper_price_sanity=119` is the dominant filter (45.8% of all skips), followed by readiness non-G1 (G2-G6)=55, `paper_duplicate_position=43`, `paper_ticker_cooldown=8`, `baseline_not_trade=35`.

Implications for future experiment design:
- The 12-trade ceiling is NOT a gate calibration property. It is a corpus-mix property: the cycle-16D market sample has many extreme-priced longshots that fail `paper_price_sanity` (price ≤ 2¢ or ≥ 98¢).
- Lifting the 12-trade ceiling under cycle-17C requires either: (a) loosening `paper_price_sanity` (risk-mode change, not signal experiment), (b) different corpus (violates fixed-corpus rule), or (c) signal axis whose effect is NOT mediated by trade count (e.g., per-trade EV improvement, side flip).
- Sub-readiness sweeps (G2/G3/G4/G5/G6) face the same ceiling. They may shift WHICH 12 trades admit, but they will not increase the count.

This finding does NOT trigger architectural-rethink. E2 was an axis-abandonment, not a revert.

### 2026-05-08 — E3 hypothesis sketch: replay pipeline does not re-run signal_analyzer

Source: `docs/governance/2026-05-08-cycle-17c-e3-side-inference-hypothesis-sketch.md` (`6081475`).

Finding: the cycle-17C replay infrastructure path is reingest-dossiers → build-dataset → score. `signal_analyzer.py` runs only at original ingestion time. Evidence rows in `evidence_store.db` carry pre-extracted `side`, `estimated_probability`, and keyword/LLM outputs. Replay applies updated dossier_builder logic to those frozen evidence rows but does NOT re-extract signals.

Implications for future experiment design:
- Production code changes inside `signal_analyzer.py` (extraction prompt, keyword map, side inference, magnitude mapping, LLM blend logic) cannot be tested by replay alone unless the corpus is regenerated.
- Three viable testing paths for signal_analyzer-class hypotheses:
  - **Re-extract corpus** through modified `signal_analyzer` (LLM-availability dependent, non-deterministic, time-cost).
  - **Counterfactual scorer mode** (modifies fixed audited scorer; possible charter-rule tension).
  - **Post-processing diagnostic script** (clean charter compliance; not a behavioral implementation).
- Each future signal_analyzer-class experiment must pick path explicitly at criteria-lock; the charter does not pre-prescribe.

This finding affects rank-3 (side inference) and rank-5 (extraction prompt) axes especially. May affect rank-6 (keyword map) less since keyword extraction is more deterministic.

## Cross-links

- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` — frozen baseline source.
- `docs/governance/2026-05-07-cycle-16e-claude-rescope-and-review.md` — production-proxy verification.
- `docs/governance/cycle-17-conditional-charter-skeletons.md` §C — strategic-pivot skeleton (cycle-17C-redesign instantiates).
- `docs/governance/2026-05-07-cycle-17c-experiment-ledger-schema.md` — ledger schema (companion doc).
- `docs/governance/2026-05-07-cycle-17c-first-axis-pick-rationale.md` — first-axis pick + info-gain table (companion doc).
- `data/dossier_updates_post_fix.db` — frozen corpus (POST_FIX_REBUILT cohort).
- `logs/edge_replay/cycle16d/replay_dataset.jsonl` — frozen replay dataset.
- `scripts/edge_replay/scorer_forensics_audit.py` — frozen audited scorer (production-proxy mode).
- `trading/executor.py:200-244` — production gate single source of truth (referenced by audited scorer).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs each experiment's keep-bar + Cycle-17 §A deploy-candidate review).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-011` — predecessor (Cycle-17 operator decision); succeeded by per-experiment debt entries.
- Memory: `feedback_market_implied_baseline.md` — replay win-rate baseline calculation.
- Memory: `feedback_audit_scorer_before_verdict.md` — scorer-bug hypothesis precedes signal hypothesis.
