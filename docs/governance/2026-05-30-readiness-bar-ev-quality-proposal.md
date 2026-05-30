# Governance Proposal — Re-anchor Live-Readiness to EV-Quality (not raw trade count)

| Field | Value |
|-------|-------|
| **Date** | 2026-05-30 |
| **Author** | Claude (implementation agent) |
| **Authority** | PROFIT-THRUPUT-001 (§C3) |
| **Status** | **DRAFT — PENDING OPERATOR DECISION.** No code changed. This is a decision artifact for operator + governance review. |
| **Type** | Pre-Go-Live Gate redefinition (R-5 contract/risk-review-level change) |
| **Reviewers** | Operator (decision authority) + a second agent (adversarial review) before any implementation |
| **Supersedes if adopted** | The `DEFAULT_MIN_TRADES = 200` POST_FIX_NEW resume bar as the *live-flip* criterion |

---

## 1. Problem statement

Two **count-based** volume gates currently stand between the bot and a live-trading decision, and they measure the **same underlying thing** — enough clean resolved paper-trade evidence to judge edge — at two different thresholds:

1. **POST_FIX_NEW readiness gate** — ≥200 production-proxy-complete resolved rows + ≥1 four-axis bin with ≥10 admissions + ≥95% completeness, post-clean-start `2026-05-13T00:02:37Z` (`scripts/edge_replay/post_fix_new_readiness_status.py:57-64`, docstring lines 6-11).
2. **OOS-corpus gate (IC §16.7)** — positive replayed EV with a 95% CI lower bound **strictly > 0**, on ≥30 resolved markets spanning ≥2 distinct market families under a pre-registered regime (`replay_gate.py:82,160-161,874-877`; `IMPLEMENTATION_CONTRACT.md` §16 Rule 1 + §16.7).

**Both are structurally unreachable at the bot's measured throughput.** Realized paper-trade rate is ~0.07/day (10 all-time, **8 resolved**; `data/paper_trades.db`, 2026-05-30). 200 resolved is *years* away; even 30 is years away. The operator named the trap directly (PROFIT-THRUPUT-001): *"the EV-validation framework needs trades that won't come."*

**C1 diagnostics (2026-05-30, PROFIT-THRUPUT-001 Evidence Log) proved there is no free volume lever to escape this:**
- The readiness gate is **correctly rejecting**, not over-blocking (`gate_correct_reject`) — the historical G1 wall was regime-dilution, already remediated by PROFIT-PRIORS-002; post-fix G1 blocks went to zero.
- Match suppression is **correctly filtering junk** (`not_worth_it`) — the false-negative recovery is ~0%.
- Every volume-raising ingestion lever manufactures negative-EV trades on no-edge markets (71% of the universe), which INV-6/7 + IC §16 Rule 3 forbid.

**And the 200 figure was never a statistical floor.** Per the Cycle-17D close (`docs/profit_path_debt_log.md:2333`), 200 was chosen as a POST_FIX_NEW cohort *size* "comparable-to-or-larger-than the ~237-272 frozen corpora that had each failed to yield a single IC §16 slice" — a **structural-sufficiency proxy** for "enough rows that a ≥10-admission four-axis bin could plausibly form," not a first-principles confidence threshold.

## 2. Proposal

**Replace the raw-count POST_FIX_NEW resume bar with the IC §16.7 EV-quality criterion as the single live-flip readiness gate.**

> **"Ready" = a passing IC §16.7 T3 replay-gate verdict on a genuine OOS corpus:** positive replayed EV with `ev_ci_95_lo > 0` (strict), on ≥30 resolved markets spanning ≥2 distinct market families, under a regime pre-registered in `docs/governance/corpus-regimes.md` and built from actual-settlement `pnl_dollars` only.

This **collapses the two redundant gates into one**, anchors readiness to **demonstrated edge** rather than volume, and reuses the harness the project already built and verified (`build_corpus` → `replay_gate` → `ci_entry`, PR #46).

It does **not** change the *number* of resolved trades the system can produce; it changes **what we require of them**: quality (positive EV at decision-grade statistical confidence on diverse OOS data) instead of quantity (200 of anything).

## 3. Rationale

1. **The two gates are redundant; one is unreachable and the other is stricter.** Both proxy "enough clean resolved evidence." The EV-CI form measures the *end goal* (positive EV at 95% confidence) **directly** instead of proxying it through corpus size.
2. **200 is the wrong axis for a structurally-small edge surface.** The tradeable surface is intrinsically ~13 event-driven series. A raw count rewards volume; an EV-CI criterion rewards quality — which is what "ready to risk real money" actually means.
3. **The readiness script already re-implements a weaker echo of IC §16.** Its admission predicate (confidence ≥0.85, model_prob ≥0.60/≤0.40, `post_fix_new_readiness_status.py:178-194`) and four-axis-bin / ≥10-admission requirement are a degraded version of IC §16's per-slice `ev_ci_95_lo > 0 AND trades ≥ 10`. Unifying removes a duplicated, weaker reimplementation in favor of the canonical one.
4. **The bar becomes HARDER to fake, not easier to reach.** `ev_ci_95_lo > 0` at n=30 is intentionally difficult; a single lucky streak on a thin sample cannot clear it. This is a re-anchoring to quality, **not a relaxation**.
5. **It reuses shipped, verified infrastructure.** No new validation framework; the replay harness already validates behavioral changes against recorded data without new live trades.

## 4. Operator-locked invariants — PRESERVED verbatim (non-negotiable)

Adopting this proposal **does not touch** any of the following. They are the safeguards that keep the redefinition a re-anchoring rather than a loophole:

- **Selectivity (INV-6/7) unchanged.** The admission gates (G1/G4), freshness, match-suppression, and `*_MIN_MATCH_SCORE` are NOT loosened to "reach 30 faster." IC §16 Rule 3: *"'May increase trades' is NOT enough… loosening converts the existing zero-edge floor into a thinner zero-edge floor."* The PROFIT-THRUPUT-001 Forbidden Zone stands.
- **n ≥ 30 CLT floor.** Do not drop below the IC §16 Rule 1 minimum; the normal-approximation CI is unreliable below it.
- **Strict positive CI.** `ev_ci_95_lo > 0` (strictly), never `≥ 0`.
- **≥2-family OOS / registered regime.** Calendar-only diversity is not OOS (`IMPLEMENTATION_CONTRACT.md:947`); regime label registered in `corpus-regimes.md` *before* the corpus is built.
- **Actual-settlement `pnl_dollars` only.** No retrospective `model_prob × resolution` estimates (sub-path A2, rejected as self-affirming).
- **Cohort discipline + clean-start anchor.** PRE_FIX/POST_FIX cohort flags (IC §16 Rule 6) and the failed-start carve-out are preserved in whatever window feeds the unified gate.
- **Paper-only until met; operator-gated cutover; no agent-executed flip.** R-5 pre-go-live hard block holds; the live flip remains operator authority.
- **Immutable audited verdicts** at `logs/edge_replay/ci_runs/<commit>/`.

## 5. Risks & safeguards

| Risk | Severity | Safeguard |
|------|----------|-----------|
| Selectivity erosion — 200→30 becomes a backdoor to loosen admission gates | **HIGH** | Invariants §4. The Forbidden Zone is explicit; gate thresholds untouched; this proposal only changes *which evidence standard* defines ready. |
| Small-n statistical fragility at exactly 30 | MED | Keep CLT floor at 30; strict CI>0; ≥2-family/registered-regime so the 30 are genuinely OOS, not correlated bets on one event. |
| Gaming a smaller corpus | MED | Registered-regime-before-build; `corpora="all_diverse"` auto-discovery; actual-settlement pnl; immutable post-merge verdict. |
| Authority/process | **HIGH** | Dated governance artifact (this doc → memo on adoption); dual-agent high-assurance review; operator performs/approves; no agent-executed cutover. R-5 contract/risk-review change. |
| Loss of cohort discipline | MED | Preserve cohort flag + clean-start anchor in the unified window. |

## 6. Honest caveat — necessary, NOT sufficient

This proposal does **not** by itself produce trades or unblock GREEN soon:

- Even the unified bar needs ≥30 resolved (currently **8/30**). At ~0.07 trades/day that is still far off — C3 makes the bar **measurable and quality-anchored and ~6.7× closer**, but the trade *rate* still depends on **L-dossier** (raise conversion of existing opportunities; see `~/.claude/plans/l-dossier-lane-population-scope.md`) and on accepting that this bot is structurally **low-volume / selective**.
- **Bootstrap dependency:** the replay gate needs the OOS corpus, the corpus needs 30 resolved. Until then, every CI run auto-passes via the PROFIT-PHASE3-002 corpus-absent pass-through and **the operator gate remains the only behavioral-regression check** — unchanged by this proposal.

C3 is the **strategic re-anchoring** that stops chasing an unreachable, mis-axised number; it pairs with L-dossier (conversion) to make the *new* bar reachable in months rather than years.

## 7. If adopted — implementation sketch (separate, gated)

1. Land this as a dated `docs/governance/` memo with operator sign-off, preserving the §4 invariants verbatim; register the readiness regime in `docs/governance/corpus-regimes.md`.
2. Point the live-flip readiness verdict at the **IC §16.7 T3 replay-gate result on the OOS corpus** rather than the POST_FIX_NEW count. Minimal code: the readiness reporter consumes the replay-gate verdict; the 200-count path is retired (or demoted to an informational metric). The script's existing `--min-trades` CLI knob is *not* the mechanism — the change is semantic (count → EV-CI), which is why it is a governance decision, not a config tweak.
3. Dual-agent adversarial review; operator approves. PAPER-ONLY posture holds until the new gate genuinely passes.
4. Merge the cycle ledger back into `docs/profit_path_debt_log.md` under PROFIT-THRUPUT-001 / PROFIT-EDGE-012 at close (R-10).

## 8. GO / NO-GO

**Operator decision required.** Options:
- **ADOPT** — re-anchor readiness to EV-quality; proceed to §7 implementation (gated).
- **ADOPT-WITH-CHANGES** — e.g. a higher resolved floor than 30, or additional family/regime requirements.
- **DEFER** — keep the 200-count bar; revisit after L-dossier raises throughput.
- **REJECT** — retain 200 as-is.

**Operator sign-off:** _______________  Date: __________  Decision: __________

---

## 9. Cross-references

- `PROFIT-THRUPUT-001` (debt log) — parent; the reframe + C1 evidence + this fork.
- `scripts/edge_replay/post_fix_new_readiness_status.py` — the current 200-count bar.
- `replay_gate.py`, `build_corpus.py`, `ci_entry.py`, `.github/workflows/replay-ci-gate.yml` — the IC §16.7 harness reused.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 / §16.7 — the EV-quality standard (source of truth).
- `docs/_archive/governance/2026-05-10-cycle-17d-charter-amendment.md` — the charter framework the 200 derived from.
- `PROFIT-PHASE3-003` — first OOS corpus build, DEFERRED on the same volume constraint.
- `~/.claude/plans/l-dossier-lane-population-scope.md` — the complementary conversion lever.
