# Cycle-16D post-verdict action checklist (M8)

**Type:** consolidated post-verdict execution plan. Mirrors `cycle-15b-post-verdict-action-checklist.md` pattern.
**Drafted:** 2026-05-07 cycle-15B verdict landing (filed pre-D8 per locked sequencing).
**Triggered by:** Cycle-16D D8 replay output landing (`docs/governance/edge-replay-cycle16d-report.md`).

## TL;DR

Cycle-16D produces a verdict in one of 4 categories:

| verdict | trigger | Cycle-17 path |
|---|---|---|
| `extraction_fixed_with_positive_ev_slice` | D5 coverage ≥ 90% AND D8 ≥ 1 IC §16 slice | Cycle-17 §A — Wave-2 candidate slice deploy + replay validation |
| `extraction_fixed_but_information_frontier_holds` | D5 coverage ≥ 90% AND D8 0 IC §16 slices | Cycle-17 §B (source-onboarding) OR §C (redesign) per operator decision |
| `cycle_16d_extension_needed` | D5 coverage < 90% but ≥ 70% | Cycle-16D-extension second backfill attempt OR §C per operator decision |
| `escalation_required` | D5 coverage < 70% | Operator scope-extension OR fresh-charter Cycle-17 |

Pre-staging this checklist prevents improvisation when verdict lands. Verdict is consumed via the table above; no new criteria invented.

## Pre-execution: Item M2 — endpoint-diagnosis criteria-lock verification (RUN BETWEEN D1 and D3/D4)

Verifies Codex D1 classification matches the locked charter criterion. See `docs/_archive/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` (ARCHIVED Stream G R16; already landed post-D1).

## Item M4 — independent endpoint read (POST D1, parallel to M2)

Independent read of D1 raw probe outputs without consulting Codex's classification. Cross-check D2 inventory for missed candidates. Already landed alongside M2.

## Item M3 — Codex price-fetch code review (POST D3 / D4, BEFORE D6 consumes)

Claude reviews D3 / D4 fetch implementations for:
- **Auth header correctness.** RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH` per CLAUDE.md Kalshi-API gotcha. HMAC and PKCS1v15 produce 401 with generic error.
- **PEM key normalization.** `_normalize_pem` in `kalshi/rest_client.py` converts `\n` → real newlines per CLAUDE.md.
- **Rate-limit compliance.** Kalshi REST rate limits respected; backoff on 429.
- **Idempotence.** Same query parameters produce same JSON output. No timestamp leak.
- **No API key leak in logs.** Sensitive-string redaction analogous to D1's `_sanitize_body`.
- **No clobber of existing `historical_prices.json`.** Either write to a new path or back up the existing file.

**If review fails:** D6 C10 re-run does NOT proceed until D3/D4 re-shipped.

## Item M5 — approximation-error review (POST D7 if approximation path; else SKIP)

Only fires if D4 = approximation path AND D7 produces error bars. Claude verifies:
- Error bars are conservative (under-confidence preferred to over-confidence).
- CI propagation correct: Monte-Carlo iteration count adequate (≥1000 typical) OR analytical formula accounts for cross-row correlation within same market.
- Documented methodology + reviewable formula.

## Item M7 — coverage threshold acceptance review (POST D5, BEFORE D6 consumes)

Claude verifies coverage fraction matches locked criterion:
- ≥ 90% overall ✓ → D6 proceeds
- 70% ≤ coverage < 90% → cycle-16D-extension verdict (defer D6 OR run D6 with caveat)
- < 70% → escalate (fresh-charter conversation BEFORE D6)

Per-ticker breakdown: any ticker < 80% flagged as anomaly requiring per-ticker investigation; does not block D6 if overall ≥ 90%.

## Items 5/8/9/10 — post-verdict refresh (POST D8)

Mirror cycle-14/15B post-verdict pattern.

### Item 5 — diagnosis-doc co-authoring (M6)

Codex authors `docs/governance/edge-replay-cycle16d-report.md` with D8 numerical findings. Claude appends:

1. **Verdict** in one of: `extraction_fixed_with_positive_ev_slice`, `extraction_fixed_but_information_frontier_holds`, `cycle_16d_extension_needed`, `escalation_required`. Verdict derives from D8 output against locked charter criteria; no new criteria invented.
2. **Cycle-17 scope recommendation** per verdict-to-skeleton map in `cycle-17-conditional-charter-skeletons.md` (M9).
3. **Independent voice** on what the numbers mean. If Claude reads the data differently from Codex, both perspectives recorded. Operator picks.
4. **What's RULED OUT.** Which Cycle-17 paths are eliminated by D5 / D8 evidence.

### Item 8 — ROADMAP refresh

`docs/ROADMAP.md` Wave-2/3/Branch-D rows: change "HALTED PENDING CYCLE-16D PRICE-RECONSTRUCTION + POST-RECONSTRUCTION REPLAY VERDICT" to one of:

| verdict | new ROADMAP wording |
|---|---|
| `extraction_fixed_with_positive_ev_slice` | "AUTHORIZED PER CYCLE-16D IC §16 ACCEPTANCE — Wave-2 candidate slice = <slice>; Wave-2 deploy gated on slice-specific replay validation" |
| `extraction_fixed_but_information_frontier_holds` | "HALTED PENDING CYCLE-17 SOURCE ONBOARDING + REPLAY VALIDATION" or "HALTED PENDING CYCLE-17 STRATEGIC REDESIGN DECISION" per operator pick |
| `cycle_16d_extension_needed` | "HALTED PENDING CYCLE-16D-EXTENSION SECOND BACKFILL ATTEMPT" |
| `escalation_required` | "HALTED PENDING OPERATOR SCOPE-EXTENSION OR FRESH-CHARTER CYCLE-17 DECISION" |

Cycle-16D row itself: "DELIVERED <DATE>; verdict = <verdict>; Cycle-17<X> active."

Cycle-15B row stays "DELIVERED 2026-05-07; verdict = `extraction_fixed_but_ic_§16_scorer_blocked_by_price_gap`."

Capital posture row: stays "PAPER-ONLY" UNLESS verdict = `extraction_fixed_with_positive_ev_slice` AND operator explicitly authorizes live-trading-enabled flip with Cycle-16D + slice-specific replay-report citation in the commit message.

### Item 9 — EDGE_STATUS dashboard refresh

`docs/EDGE_STATUS.md` updates in 3 places:

1. **TL;DR replay verdict line** updated to: `Cycle-16D verdict: <verdict>. Cycle-17<X> active per matching skeleton.`
2. **Wave deploy status table** rows updated per ROADMAP wording above.
3. **Replay verdict log** appended with cycle-16D row including coverage fraction, IC §16 slice count, ev_ci_95_lo of best slice (if any).

### Item 10 — Debt log: PROFIT-EDGE-009 closure + PROFIT-EDGE-010 file (M10)

`docs/profit_path_debt_log.md`:

**PROFIT-EDGE-009 status:**
- Status: COMPLETE (regardless of verdict; the cycle ran).
- Verdict (from Cycle-16D D8) recorded in Notes.
- Recommended Cycle-17 scope reference.

**PROFIT-EDGE-010 (NEW):**
- Title matches verdict — e.g., "Cycle-17A Wave-2 candidate slice deploy + replay validation" (positive-EV verdict) OR "Cycle-17 source onboarding scope" (frontier-holds verdict) OR "Cycle-16D-extension second backfill attempt" (coverage failure 70-90%) OR "Cycle-17 strategic redesign decision" (escalation OR operator picks redesign).
- Category: matches verdict path.
- Severity: HIGH (gates Wave-2/Wave-3 deploy decision OR strategic-direction decision).
- Status: ACTIVE.
- Priority: NOW (positive-EV verdict; deploy-blocking) OR LATER (frontier-holds; multi-week scope) OR Operator-decision-only (redesign or escalation).
- Owner: Codex (implementation) OR Operator-decision-only.
- Depends On: PROFIT-EDGE-009 (delivered).
- Blocks: All Wave-2/Wave-3 deploys per IC §16 (only positive-EV verdict can unblock).

Acceptance criteria match the matching Cycle-17<X> skeleton acceptance section.

## Cross-links

- `docs/_archive/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` — Cycle-16D charter (ARCHIVED Stream G R16)
- `docs/_archive/governance/2026-05-07-cycle-16d-task-split.md` — Codex D1-D10 + Claude M1-M10 (ARCHIVED Stream G R16)
- `docs/_archive/governance/cycle-17-conditional-charter-skeletons.md` — Cycle-17 skeletons (verdict-to-skeleton map)
- `docs/_archive/governance/2026-05-07-cycle-16d-pre-execution-criteria-verification.md` — M2 + M4 verification (already landed) (ARCHIVED Stream G R16)
- `docs/governance/edge-replay-cycle16d-report.md` — Cycle-16D D8 report (FUTURE)
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate (governs Cycle-17 deploys)
