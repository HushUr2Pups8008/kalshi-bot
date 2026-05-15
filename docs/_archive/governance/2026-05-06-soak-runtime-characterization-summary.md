# PROFIT-PHASE2-001 soak runtime characterization summary

**Type:** synthesis (Claude task per Implementation Contract §9 — review/planning).
**Source:** Codex's cycle-3-6 baseline reports + cycle-7 dry-run rehearsal data.
**Drafted:** 2026-05-06.
**Audience:** operator before Day-7 close + Wave-1 deploy.

## TL;DR

PHASE2-001 soak runtime profile is **clean across all dimensions measured.** Governance LLM throughput has >30× headroom against PHASE2-002 cadence-tune envelope; matcher-pipeline produces zero OPPORTUNITY post-cutover (consistent with PROFIT-EDGE-004 thesis); decision distribution is mechanically uniform (241/242 dead-Reddit-sub disables); cadence stability is well within tolerance. **No characterization signal argues against Day-7 close.**

## Dimensions characterized

### 1. Governance cycle health

| metric | value | source |
|---|---|---|
| Total decisions | 383 | gate-1 dry-run |
| Total cycles | 57 | gate-5 dry-run |
| Decisions per cycle (mean) | 6.7 | derived |
| Soak duration | 4d 5h 9m at 2026-05-06T00:10Z | gate-2 |
| KILL_SWITCH events | 0 | gate-3 |
| VALIDATION_ERROR events | 0 | gate-3 |
| `batch_aborted=True` events | 0 | gate-3 |
| PARSE_ERROR events (cumulative) | 7 (all day-1/-2) | cycle-3 retroactive audit |
| PARSE_ERROR events (trailing 72h) | 0 | gate-4 |
| Max inter-cycle gap | 2.01h | gate-5 |
| Cadence-deviation > ±10% events | 7 (deep-cycle wall-clock-aligned) | gate-5 |

**Verdict:** ✅ all governance gates clean. Single 2.01h max gap is well within 3h floor; cadence-deviation events are wall-clock-aligned (deep cycle adjacency to fast cycles), not failures.

### 2. LLM throughput

| metric | value | source | implication |
|---|---|---|---|
| Median call duration | 1063 ms | Codex cycle 3 `llm_throughput_audit.py` | well under PHASE2-002 60s p90 envelope |
| p90 call duration | 1986 ms | same | >30× headroom |
| Max call duration | unknown | needs re-run | informational |
| Projected calls/day at 90-min cadence | 352 | Codex cycle 6 projection script | 4× current 2h cadence rate |

**Verdict:** ✅ throughput supports PHASE2-002 90-min cadence-tune without strain. Operator can apply cadence change at PHASE2-002 cycle 1 without further audit per `PROFIT-PHASE2-002-onboarding.md` §3.

### 3. Decision distribution

Per `2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md`:

| dimension | value |
|---|---|
| `disable_source` action | 242/242 = 100% |
| `r/<reddit_sub>` target | 241/242 = 99.6% |
| `fresh_pass_count == 0` evidence | 242/242 = 100% |
| `match_count == 0` evidence | 242/242 = 100% |
| `active_market_count == 0` evidence | 242/242 = 100% |
| `window_hours == 168` | 242/242 = 100% |
| Confidence in [0.85, 0.95) | 175/242 = 72.3% |
| Confidence in [0.95, 1.0] | 67/242 = 27.7% |
| Anchor-rate-populated decisions | 1/242 = 0.4% (NYT World News post-A5) |

**Verdict:** ✅ mechanically uniform. Codex's gate-6 review verdicted 67/67 back-half decisions at 100% reasonable; the 1 anchor-rate-populated decision is the lone Tier-2 manual-review case.

### 4. Matcher / OPPORTUNITY pipeline

Per `2026-05-05-pre-wave1-baseline-interpretation.md`:

| event type | count | %  | source |
|---|---:|---:|---|
| `EARLY_STALE_DROP` | 26,711 | 96.0% | trade-log inspection |
| `MATCH_DIAGNOSTIC` | 450 | 1.6% | same |
| `EARLY_FRESH_PASS` | 265 | 0.95% | same |
| `MATCH_SUPPRESSED` | 175 | 0.63% | same |
| `NEW_MARKET` | 110 | 0.40% | same |
| `ANALYSIS_REJECTED` | 76 | 0.27% | same |
| `SIGNAL_ANALYSIS_DETAIL` | 22 | 0.08% | same |
| **`OPPORTUNITY`** | **0** | **0.0%** | same |
| **`SKIPPED`** | **0** | **0.0%** | same |
| **`PAPER_TRADE`** | **0** | **0.0%** | same |
| **`BLEND_DECISION`** | **0** | **0.0%** | same |

**Verdict:** ✅ post-cutover Mac Studio matcher-pipeline produces zero OPPORTUNITY events through 4 days. **This is the operationally-expected pre-Wave-1 state per PROFIT-EDGE-004 thesis** ("matcher signal-quality / market-mix root cause behind the persistent zero-edge pattern"). Wave-1 commit 6 (Lever A.1 classifier) is archive-replay-confirmed neutral on this; Wave-2 Branch C is the actual edge-production lever.

### 5. Source-class distribution (pre-A.1 baseline)

Per Codex cycle 3 `source_class_evolution_audit.py`:

| source class | rows | % |
|---|---:|---:|
| `news` | 603 | 63.5% |
| `other` | 347 | 36.5% |
| `analysis` | 0 | 0.0% |
| `official` | 0 | 0.0% |
| TOTAL | 950 | 100.0% |

**Verdict:** ✅ pre-A.1-deploy baseline. Wave-1 commit 6 will add tokens to `_source_class_for_evidence` to bucket existing official/specialist sources to `analysis` class. Post-Wave-1 expectation: `analysis` class share lifts from 0% to non-zero; `other` share drops correspondingly.

## What this characterization implies for the close-day decision

All 5 dimensions support **proceeding with Day-7 close on 2026-05-08T19:01Z+:**

- §8.5.1 gates 1-5 + 7 PASS or projected-PASS
- Gate 6 manual review is well-pre-staged (≥ 99% reasonable projected)
- Gate 8 attestation is operator-action only

**No characterization signal argues against Day-7 close.**

## What this characterization implies for Wave-1 deploy posture

- **Wave-1 commits 1-5 (OBS-005, MATCH-001 (B'), OBS-003, EXEC-002, GOV-003) ship plumbing fixes** that operate on a pipeline currently producing 0 OPPORTUNITY. Smoke + 24h-watch tests verify the plumbing is correct, not that trade-rate changes.
- **Wave-1 commit 6 (Lever A.1) is archive-neutral hygiene** per Codex `8001a16` archive replay. Post-deploy expectation: source-class distribution shifts (`other` → `analysis` for previously-misclassified sources); no trade-rate change.
- **Wave-2 Branch C is the actual edge-production lever.** If Wave-1 ships clean, Wave-2 fires Branch A passive observe → Branch C if Branch A stalls. EDGE-004 closes only if Branch A or Branch C produces ≥ 1 PAPER_TRADE w/ +P&L.

## What this characterization rules out

- **Operational regression risk during PHASE2-001 close.** Safety counters all 0 across 4d+ window; cadence stable; no PARSE_ERROR cluster.
- **LLM throughput bottleneck for PHASE2-002 cadence-tune.** >30× headroom; 90-min cadence is empirically safe.
- **Decision-distribution non-uniformity that would invalidate gate-6 bulk-review.** Mechanically uniform; Codex's --bulk-mode flag handles 99.6% of corpus.

## What this characterization does NOT rule out

- **Wave-1 commit-by-commit regression** post-deploy. Each commit needs its 24h watch.
- **Wave-2 / Wave-3 / Branch D outcomes.** Future evidence; out of pre-close characterization scope.
- **Network/API outage during the close window.** Per `2026-05-05-network-api-outage-runbook.md`; defer fire-time.

## Out of scope

- Pre-cutover MacBook archive characterization (already documented in `README.md` Status 2026-05-01).
- Per-source matcher behavior (matcher itself is well-characterized; OPPORTUNITY-rate is what changes post-Wave-2).
- Per-market-class regime classifier behavior (out of close-readiness scope).

## Cross-links

- `docs/governance/2026-05-06-pre-day7-dry-run-rehearsal.md` — gate-script dry-run
- `docs/_archive/governance/2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` — decision distribution source
- `docs/_archive/governance/2026-05-05-pre-wave1-baseline-interpretation.md` — matcher baseline source
- `docs/governance/2026-05-05-PROFIT-PHASE2-001-llm-throughput-baseline.md` — LLM throughput source
- `docs/governance/2026-05-05-PROFIT-PHASE2-001-source-class-evolution-baseline.md` — source-class source
- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` — close gates
- `docs/_archive/governance/2026-05-05-pre-wave1-baseline-interpretation.md` — Wave-1 deploy posture
