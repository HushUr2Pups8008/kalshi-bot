# Cycle-16D charter — price-reconstruction prerequisite

**Type:** focused single-deliverable cycle. Pure replay-harness scope; bot extraction code untouched.
**Drafted:** 2026-05-07 cycle-15B verdict landing.
**Authority:** Cycle-15B verdict = `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` (`docs/governance/edge-replay-cycle15b-report.md`); skeleton §D (`docs/governance/cycle-16-conditional-charter-skeletons.md`); task split (`docs/governance/2026-05-07-cycle-16d-task-split.md`).
**Owner:** Codex (implementation, D1-D10); Claude (review + governance + scaffolding, M1-M10).
**Tracker:** PROFIT-EDGE-009.
**Status:** ACTIVE.

## TL;DR

Cycle-15B C10 returned `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` — extraction repaired (Lane B 8/8 + 2/2 ✓) but 0/272 post-fix replay rows had decision-time `market_yes_price`. IC §16 acceptance unevaluable until prices restored. Cycle-16D restores per-decision-time price reconstruction so the gate can be evaluated against the post-Cycle-15B-fix dossier corpus.

**Cycle-16D answers ONE question:** with restored prices, does any (source × market_family × signal_type) slice produce `ev_ci_95_lo > 0` AND `trades ≥ 10`?

**Pure replay-harness scope.** Bot extraction code untouched; POST_FIX_REBUILT cohort (`data/dossier_updates_post_fix.db`) intact; no re-ingestion needed.

## Pre-stated decision criteria (LOCK BEFORE D1 endpoint diagnosis)

These criteria are LOCKED. Operator does NOT change them post-hoc. Mirrors task-split doc §"Pre-stated decision criteria"; replicated here as charter authority.

### Endpoint-diagnosis criteria (D1 + M2)

| classification | criterion | path |
|---|---|---|
| `solvable_auth_or_param` | auth headers fixed OR query parameters adjusted produces 200 with usable data | D3 primary backfill |
| `solvable_alternate_endpoint` | different Kalshi endpoint returns per-decision-time price data | D3 primary backfill from alternate endpoint |
| `permanently_dead` | endpoint returns 404 / 410 / Gone for ≥5 distinct ticker probes spanning current + resolved markets, AND no Kalshi API doc revision references the change, AND no operator-known auth-tier change | D4 fallback path |

If classification is ambiguous: operator picks. Lock criteria here so picks aren't post-hoc rationalized.

### Coverage acceptance (D5 + M7)

- ≥ 90% of 272 post-fix dossier-update rows have `market_yes_price` populated at decision time.
- Per-market-ticker coverage reported separately; flag any ticker below 80% as coverage-anomaly requiring per-ticker investigation.
- If approximation path (D4) used: error bars on `market_yes_price` are quantified per row; CI propagation into `ev_ci_95_lo` documented.

If coverage < 90% but ≥ 70%: cycle-16D-extension scope opens for additional backfill OR multi-source merge BEFORE D6 C10 re-runs. < 70%: escalate to operator scope-extension or fresh-charter Cycle-17.

### IC §16 re-acceptance (D8 + M6)

Cycle-16D D8 re-run passes IC §16 iff:
- ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` (unchanged from IC §16 spec).
- If approximation path used: `ev_ci_95_lo` accounts for price-imprecision error bars; conservative CI (wider than zero-error CI).
- Reproducible via documented commands.

If 0 slices despite ≥ 90% coverage: clean `extraction_fixed_but_information_frontier_holds` verdict (per cycle-16 skeleton §B / §C routing). Distinguishes "no signal" from previous "scorer-blocked" state.

If ≥ 1 slice surfaces: routes to cycle-16 skeleton §A (Wave-2 candidate slice deploy + replay validation).

## Cycle-16D deliverables (Codex D1-D10)

Per `docs/governance/2026-05-07-cycle-16d-task-split.md` Codex 10 tasks. Charter does not duplicate task descriptions; refer to task split doc.

Strict sequencing (re-stated):
- M1 (this charter) + M8 + M9 land BEFORE Codex D1. (Codex raced ahead of M1; charter retroactively documents locked criteria. No verdict-consumption breakage because criteria match task-split doc.)
- M2 + M4 land BETWEEN D1 and D3/D4 path selection.
- M3 lands BEFORE D6 consumes restored prices.
- M7 lands BETWEEN D5 and D6.
- M5 fires only if D7 fires (approximation path).
- M6 verdict appendix lands AFTER D8, BEFORE M10 closes PROFIT-EDGE-009.

## Out of scope for Cycle-16D

- Bot extraction code. Cycle-15B C7 keyword-map fix stays in place.
- Re-ingestion of `data/dossier_updates_post_fix.db`. POST_FIX_REBUILT cohort intact per L8 cohort note.
- New keyword-map sub-fixes. Cycle-15B is closed.
- Source onboarding. Cycle-17 §B scope, blocked until §D delivers.
- Live-trading flag flip. PAPER-ONLY remains locked.
- LLM-path audit. L7.2 deferral; revisit only if D8 produces 0 slices despite ≥90% coverage.
- Wave-1 deploy interference. Wave-1 ships 2026-05-08T19:01Z on independent track.

## Sole exception to "no fixes in Cycle-16D"

Trivial measurement bug **in the diagnostic / fetch tooling itself** (e.g., off-by-one in time-bucketing, JSON parse bug in price fetch). NOT in bot extraction code. NOT in dossier update logic. NOT in any prod path. If found, fix and re-run; does NOT count toward fix-attempt budgets.

## Capital posture

PAPER-ONLY. NO LIVE CAPITAL. Wave-2 + Wave-3 + Branch-D HALTED until Cycle-16D + post-reconstruction replay produces ≥1 IC §16 slice. Live-trading flip requires explicit operator override per `tests/test_paper_mode_lock_post_wave1.py`.

## Sequencing relative to Wave-1 deploy

Cycle-16D runs in parallel with Wave-1 deploy commits 1-6 (2026-05-08T19:01Z → 2026-05-16T06:00Z+). Wave-1 ships cleanup/observability hygiene only — independent track. Cycle-16D D3/D4 backfill MUST land in commits separate from Wave-1 deploy commits. `tests/test_paper_mode_lock_post_wave1.py` enforces no live-trading-flag flip in either track.

## Cycle-16D success criterion

D8 report produced + signed by both Codex and Claude. Verdict matches one of:
- `extraction_fixed_with_positive_ev_slice` (Lane B post-fix already passed in Cycle-15B; D8 confirms ≥1 IC §16 slice)
- `extraction_fixed_but_information_frontier_holds` (D8 0 slices with prices verified; routes to §B / §C per operator)
- `cycle_16d_extension_needed` (D5 coverage < 90% but ≥ 70%; second backfill attempt)
- `escalation_required` (D5 coverage < 70%; fresh-charter conversation)

Cycle-17 scope derived FROM verdict via `docs/governance/cycle-17-conditional-charter-skeletons.md` (M9), not invented to fit a preferred path.

## Cross-links

- `docs/governance/edge-replay-cycle15b-report.md` — Cycle-15B verdict source.
- `docs/governance/cycle-16-conditional-charter-skeletons.md` §D — price-reconstruction skeleton.
- `docs/governance/2026-05-07-cycle-16d-task-split.md` — 20-task split (10 Codex + 10 Claude).
- `docs/governance/cycle-16d-post-verdict-action-checklist.md` — M8 post-verdict checklist (pre-staged).
- `docs/governance/cycle-17-conditional-charter-skeletons.md` — M9 conditional Cycle-17 skeletons (pre-staged).
- `docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — L8 cohort note (D9 sentinel).
- `data/dossier_updates_post_fix.db` — POST_FIX_REBUILT cohort (D6 input).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs D8 acceptance).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-009` — debt entry tracking this cycle.
- `docs/EDGE_STATUS.md` — operator-facing dashboard (refreshed at end of Cycle-16D).
- CLAUDE.md "Kalshi API: Signing algorithm is RSA-PSS/SHA-256" — load-bearing for D3/D4 (M3 review).
