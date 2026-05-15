# Cycle-16D task split — price-reconstruction prerequisite

**Type:** 10-task split per agent (Codex + Claude). Mirrors prior cycle prep pattern (cycle-12/13/14/15B).
**Drafted:** 2026-05-07 cycle-15B verdict landing.
**Authority:** `docs/_archive/governance/cycle-16-conditional-charter-skeletons.md` §D; `docs/governance/edge-replay-cycle15b-report.md` Claude appendix; PROFIT-EDGE-009.
**Tracker:** PROFIT-EDGE-009.

## TL;DR

Cycle-15B C10 returned verdict `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap` — extraction is repaired (Lane B 8/8 + 2/2 ✓) but 0/272 post-fix replay rows had decision-time `market_yes_price`. IC §16 acceptance is unevaluable until prices restored. Cycle-16D is pure replay-harness work: bot extraction code untouched; POST_FIX_REBUILT cohort (`data/dossier_updates_post_fix.db`) intact; no re-ingestion needed.

10 tasks per agent, 20 total. Codex implementation-heavy. Claude governance + review + scaffolding.

## Pre-stated decision criteria (locked BEFORE D1 endpoint diagnosis)

Mirrors cycle-14/15B charter pattern. Operator does NOT change them post-hoc.

### Endpoint-diagnosis criteria (D1 + M2 lock)

A `/markets/{ticker}/trades` endpoint state is classified into one of:

| classification | criterion | path |
|---|---|---|
| `solvable_auth_or_param` | auth headers fixed OR query parameters adjusted produces 200 with usable data | D3 primary backfill |
| `solvable_alternate_endpoint` | different Kalshi endpoint (e.g., `/markets/{ticker}` snapshot or `/series/{series}/markets` historical) returns per-decision-time price data | D3 primary backfill from alternate endpoint |
| `permanently_dead` | endpoint returns 404 / 410 / Gone for ≥5 distinct ticker probes spanning current + resolved markets, AND no Kalshi API documentation revision references the change, AND no operator-known auth-tier change | D4 fallback path |

If classification is ambiguous (e.g., 200 returned but data shape useless): operator picks. Lock criteria here so picks aren't post-hoc rationalized.

### Coverage acceptance (D5 + M7 lock)

Cycle-16D passes coverage iff:
- ≥ 90% of 272 post-fix dossier-update rows have `market_yes_price` populated at decision time.
- Per-market-ticker coverage reported separately; flag any ticker below 80% as coverage-anomaly requiring per-ticker investigation.
- If approximation path (D4) used: error bars on `market_yes_price` are quantified per row; CI propagation into `ev_ci_95_lo` documented.

If coverage < 90% but ≥ 70%: cycle-16D-extension scope opens for additional backfill OR multi-source merge BEFORE C10 re-runs. < 70%: escalate to operator scope-extension or fresh-charter Cycle-17.

### IC §16 re-acceptance (D8 + M6 lock)

Cycle-16D C10 re-run passes IC §16 iff:
- ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` (unchanged from IC §16 spec).
- If approximation path used: `ev_ci_95_lo` accounts for price-imprecision error bars; conservative CI (wider than zero-error CI).
- Reproducible via documented commands.

If 0 slices despite ≥ 90% coverage: clean `extraction_fixed_but_information_frontier_holds` verdict (per cycle-16 skeleton §B / §C routing). Distinguishes "no signal" from previous "scorer-blocked" state.

If ≥ 1 slice surfaces: routes to cycle-16 skeleton §A (Wave-2 candidate slice deploy + replay validation).

## Codex 10 tasks (implementation)

| # | Task | Output | Acceptance |
|---|---|---|---|
| D1 | **Diagnose `/markets/{ticker}/trades` 404.** Full HTTP probe across 5+ tickers (mix resolved + open). Capture status code, response body, headers. Cross-reference current Kalshi API docs vs cycle-13 fetch-script docstring. | `logs/edge_replay/cycle16d/endpoint_diagnosis.json` per-ticker probe results + classification per locked criteria. | Classification matches one of `solvable_auth_or_param` / `solvable_alternate_endpoint` / `permanently_dead`. |
| D2 | **Alternative-endpoint inventory.** Catalog Kalshi REST endpoints producing time-series price data: `/markets/{ticker}`, `/markets`, `/markets/{ticker}/candles` (if exists), orderbook endpoints, settlement endpoints. For each, note: per-decision-time granularity, historical-window depth, auth tier required. | `docs/_archive/governance/2026-05-07-cycle-16d-price-source-inventory.md` (ARCHIVED Stream G R7). | ≥3 candidate endpoints inventoried; recommendation matrix sortable by granularity × depth × auth-cost. |
| D3 | **Backfill prices via primary path** (if D1 = solvable). Populate `logs/edge_replay/cycle13_live/historical_prices.json` (or sibling file) for the 24-market replay window. Per-market-ticker, per-decision-timestamp `market_yes_price`. | Refreshed `historical_prices.json` (or `historical_prices_cycle16d.json`). | ≥ 90% of 272 post-fix dossier rows have `market_yes_price` populated. |
| D4 | **Backfill prices via fallback path** (if D1 = permanently_dead). Choose ONE of: (a) Kalshi orderbook archive recovery, (b) third-party data, (c) computed approximation from settlement + volume curve. Document choice rationale + tradeoffs. | Refreshed `historical_prices.json` + `docs/governance/2026-05-07-cycle-16d-fallback-rationale.md`. | Same coverage criterion as D3. If (c): error-bar methodology documented + per-row error bars computed. |
| D5 | **Price-coverage audit.** For the 272 post-fix dossier rows in `data/dossier_updates_post_fix.db`, report: total coverage fraction; per-market-ticker coverage; missing-rows table with reasons. | `logs/edge_replay/cycle16d/coverage_audit.json`. | Coverage fraction reported. Per-ticker breakdown present. |
| D6 | **Re-run C10 against post-fix DB with restored prices.** Same `score_counterfactual_pnl.py` + `build_replay_dataset.py` paths as cycle-15B C10, but with refreshed prices fed in. | `logs/edge_replay/cycle16d/replay_dataset.jsonl` + `counterfactual_scores.json`. | Replay rows have `market_yes_price` populated; counterfactual P&L computable for ≥ 90% of rows. |
| D7 | **Quantify price-imprecision** (only if D4 = approximation path). Compute per-row error bars on `market_yes_price`. Propagate into `ev_ci_95_lo` via Monte-Carlo or analytical method. CI on EV must widen to reflect price uncertainty. | `logs/edge_replay/cycle16d/price_imprecision.json` + updated counterfactual_scores.json with widened CIs. | Error bars conservative (overestimate uncertainty rather than under). Methodology documented + reviewable. |
| D8 | **Cycle-16D replay report.** Per (source × market_family × signal_type) slice table with `ev_ci_95_lo` + `trades` + `win_rate` + Brier (n=24 caveat). IC §16 acceptance check explicit. | `docs/governance/edge-replay-cycle16d-report.md`. | Report names whether ≥1 IC §16 slice surfaces; if 0, names whether finding is "no signal" (vs the prior "scorer-blocked"); reproducibility commands present. |
| D9 | **POST_FIX_REBUILT cohort sentinel verification.** Per L8 cohort note: confirm Cycle-16D replay reads ONLY POST_FIX_REBUILT rows from `data/dossier_updates_post_fix.db`; no PRE_FIX leakage. | Sentinel record in `coverage_audit.json` confirming `cycle_15b_c7_deploy_commit` metadata stamp present + cohort flag = "post_fix_rebuilt". | Sentinel verified; no PRE_FIX cohort rows scored. |
| D10 | **Reproducibility script.** Single shell wrapper that: (a) re-runs price backfill, (b) re-runs C10 against post-fix DB, (c) emits the report. Documented in cycle-16D-report Reproducibility section. | `scripts/edge_replay/run_cycle16d_replay.sh` + report section. | Wrapper executes end-to-end; output matches D8 deterministically. |

## Claude 10 tasks (governance + review + scaffolding)

| # | Task | Output | Acceptance |
|---|---|---|---|
| M1 | **Cycle-16D charter document.** Mirror cycle-14/15B charter pattern: scope, locked endpoint-diagnosis + coverage + IC §16 criteria. Locked BEFORE D1 runs. | `docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md`. | Charter authored; criteria above replicated; cross-links to skeleton §D + cycle-15B report. |
| M2 | **Pre-execution endpoint-diagnosis criteria-lock verification.** When D1 lands, verify Codex's classification (solvable_auth_or_param / solvable_alternate_endpoint / permanently_dead) matches the locked criterion (5+ distinct ticker probes, no doc revision, no auth-tier change). If criterion drifts, flag to Codex; D3/D4 path selection does not proceed until aligned. | `docs/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` (post-D1). | Verification doc landed; criterion drift flagged or absence confirmed. |
| M3 | **Codex price-fetch code review.** Review D3 / D4 fetch implementations for: auth header correctness (RSA-PSS/SHA-256 per CLAUDE.md Kalshi-API gotcha), rate-limit compliance (no >1 req/sec without backoff), idempotence (re-run produces same JSON), no leak of API keys into logged output, no clobbering of existing `historical_prices.json`. | Code review feedback comment or PR review on Codex commit. | Findings filed before D6 C10 re-run consumes the output. |
| M4 | **Independent read of D1 endpoint diagnosis.** Without consulting D2 inventory or D3/D4 selection, read D1 raw probe outputs. Identify endpoint state independently. If Claude's classification differs from Codex's, both perspectives recorded; operator picks. Cross-check D2 inventory for missed candidates. | `docs/governance/2026-05-07-cycle-16d-claude-independent-endpoint-read.md`. | Independent classification recorded; matches or differs from Codex with both rationales; missed candidates (if any) flagged. |
| M5 | **Approximation-error review** (only if D4 + D7 fire). Verify D7 error bars are conservative (under-confidence preferred to over-confidence). Verify CI propagation is correct (e.g., Monte-Carlo iteration count adequate; analytical formula accounts for correlation across rows in same market). | Code review on D7 commit + section in M6 verdict appendix. | Error-bar methodology reviewed; conservative posture confirmed; CI widening documented. |
| M6 | **Sub-cycle verdict appendix to Cycle-16D report.** Mirror cycle-14/15B Claude-appendix pattern. Independent voice on D8 IC §16 acceptance check; verify it matches locked criteria. If verdict differs from Codex's read, flag. Verdict consumption per cycle-16-skeleton verdict-to-skeleton map (positive-EV → §A; no slice → §B/§C; coverage failure → cycle-16D-extension). | Claude appendix in `docs/governance/edge-replay-cycle16d-report.md`. | Appendix landed; matches/disagreement recorded; downstream Cycle-17 routing decision authored. |
| M7 | **Coverage threshold acceptance review.** When D5 lands, verify coverage fraction matches locked criterion (≥ 90%; flag <80% per-ticker; <70% overall = escalate). If approximation path: verify D7 error-bar methodology is in scope. | Section in M2 doc OR standalone `2026-05-07-cycle-16d-coverage-acceptance.md`. | Coverage verdict recorded; if escalation triggered, fresh-charter conversation flagged. |
| M8 | **Cycle-16D post-verdict action checklist.** Mirror `cycle-15b-post-verdict-action-checklist.md` pattern: pre-staged ROADMAP wording per Cycle-16D verdict (positive-EV slice surfaces / no-signal-confirmed / coverage-failure / extension-needed), EDGE_STATUS refresh template, debt-log close-and-file-successor template. Pre-stage BEFORE D8 lands. | `docs/governance/cycle-16d-post-verdict-action-checklist.md`. | Checklist landed pre-D8; verdict-to-wording maps cover the 4 outcome cases. |
| M9 | **Conditional Cycle-17 skeletons.** If Cycle-16D D8 produces ≥ 1 IC §16 slice → Cycle-17A = Wave-2 candidate slice deploy + replay validation (mirror cycle-16 §A). If 0 slices with prices verified → Cycle-17 routes to cycle-16 §B (source onboarding) or §C (redesign). If coverage failure → Cycle-17 = cycle-16D-extension OR fresh-charter scope. Pre-stage skeletons analogous to `cycle-16-conditional-charter-skeletons.md`. | `docs/_archive/governance/cycle-17-conditional-charter-skeletons.md`. | Skeleton set covers 3+ outcome branches; verdict-to-skeleton map present; pre-stages BEFORE D8 verdict landing. |
| M10 | **PROFIT-EDGE-009 closure + PROFIT-EDGE-010 file.** Mirror cycle-15B Item 10 pattern. Status: COMPLETE (regardless of verdict; the cycle ran). Verdict + Cycle-17 scope reference in Notes. PROFIT-EDGE-010: title matches verdict (e.g., "Cycle-17A Wave-2 candidate slice deploy" / "Cycle-17 source-onboarding scope" / "Cycle-17 strategic-pivot decision" / "Cycle-16D-extension second backfill attempt"). | Append to `docs/profit_path_debt_log.md`. | Both entries landed in same commit as ROADMAP/EDGE_STATUS refresh. |

## Sequencing

Strict: **M1 + M8 + M9 land BEFORE Codex D1.**
Strict: **M2 lands AFTER D1, BEFORE D3/D4 path selection.**
Parallel: D1-D2 trace can interleave with M3 code review prep.
Strict: **M3 lands AFTER D3/D4, BEFORE D6 consumes restored prices.**
Parallel: M4 independent endpoint read fires post-D1 alongside M2.
Strict: **M5 fires only if D7 fires** (approximation path); lands BEFORE D8 reports.
Strict: **M7 fires AFTER D5, BEFORE D6 consumes** (coverage gate).
Strict: **M6 verdict appendix fires AFTER D8 lands, BEFORE M10 closes PROFIT-EDGE-009.**

## What Cycle-16D does NOT do

- No bot extraction code changes. Cycle-15B C7 keyword-map fix stays in place.
- No re-ingestion of `data/dossier_updates_post_fix.db`. POST_FIX_REBUILT cohort intact per L8 cohort note.
- No new keyword-map sub-fixes. Cycle-15B is closed.
- No source onboarding. That is Cycle-17 §B scope, blocked until §D delivers.
- No live-trading flag flip. PAPER-ONLY remains locked.
- No LLM-path audit. L7.2 deferral; revisit only if D8 produces 0 slices despite ≥90% coverage.
- No Wave-1 deploy interference. Wave-1 ships 2026-05-08T19:01Z on independent track.

## Cross-links

- `docs/governance/edge-replay-cycle15b-report.md` — Cycle-15B verdict source.
- `docs/_archive/governance/cycle-16-conditional-charter-skeletons.md` §D — price-reconstruction skeleton.
- `docs/governance/cycle-15b-post-verdict-action-checklist.md` — analogous post-verdict pattern (M8 mirrors).
- `docs/governance/2026-05-06-cycle-15b-paper-trades-cohort-note.md` — L8 cohort note (D9 sentinel verification).
- `data/dossier_updates_post_fix.db` — POST_FIX_REBUILT cohort (D6 input).
- `logs/edge_replay/cycle13_live/historical_prices.json` — current price data (insufficient coverage; D3/D4 refresh).
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs D8 acceptance).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-009` — debt entry tracking this cycle.
- CLAUDE.md "Kalshi API: Signing algorithm is RSA-PSS/SHA-256" — load-bearing for D3/D4 auth correctness (M3 review reference).
