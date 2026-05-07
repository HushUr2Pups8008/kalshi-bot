# Cycle-16D M7 — coverage threshold acceptance review

**Type:** review of Codex D5 coverage audit against locked charter criteria.
**Drafted:** 2026-05-07 post-Codex D5 commit `9231bf0`.
**Authority:** Cycle-16D charter §"Coverage acceptance" (`2026-05-07-cycle-16d-charter-price-reconstruction.md`); task split M7.
**Gates:** Codex D6 C10 re-run does NOT proceed until this review passes.

## TL;DR

D5 coverage audit reports **99.6324%** overall (271/272 rows). **PASS** — above ≥90% locked threshold. One per-ticker anomaly (KXPARDONSTRUMP-26APR-22, 0% / 1 row missing) flagged but inconsequential for IC §16 evaluation.

D6 C10 re-run authorized to consume `historical_prices_cycle16d.json`.

## Locked criteria recap

Per charter §"Coverage acceptance":
- ≥ 90% overall → D6 proceeds.
- per-ticker < 80% = anomaly requiring per-ticker investigation; does NOT block D6.
- coverage 70-89% = `cycle_16d_extension_needed` verdict.
- coverage < 70% = `escalation_required` verdict.

## D5 audit result

`logs/edge_replay/cycle16d/coverage_audit.json`:

| metric | value | status |
|---|---|---|
| Overall coverage | 271/272 = 99.6324% | ≥90% ✓ PASS |
| Dataset rows | 272 | matches charter expectation |
| Price ticker count | 23 / 24 | 1 ticker without price series |
| Per-ticker <80% anomalies | 1 (KXPARDONSTRUMP-26APR-22) | flagged but non-blocking |
| Overall escalation triggered | no | <70% threshold not crossed |

## Anomaly investigation: KXPARDONSTRUMP-26APR-22

| field | value |
|---|---|
| Total rows | 1 |
| Covered rows | 0 |
| Missing reason | `no_price_series_for_ticker` |
| Decision_kind | `dossier_update` |
| Decision_ts | `2026-04-26T14:44:32.331129+00:00` |

Kalshi returned no trade rows from either `/markets/trades` or `/historical/trades` endpoints for this ticker. Possible causes:
- Low-volume / never-traded market — common for niche resolved markets.
- Ticker format mismatch — date suffix convention may differ from API.
- Ticker resolved-and-archived before trade history was queryable via current endpoints.

Per charter, investigation flagged but does NOT block D6: the missing row is 1/272 = 0.37% of replay corpus. Statistical impact on per-slice ev_ci_95_lo computation is negligible.

**Recommendation for Codex D6 / D8:** when scoring slices, the KXPARDONSTRUMP-26APR-22 row is automatically excluded by the absence of `market_yes_price`. Document this exclusion explicitly in D8 report so the 1-row drop doesn't appear as a downstream silent gap.

## Note on `escalations` field in coverage_audit.json

Codex's D5 script also lists KXPARDONSTRUMP-26APR-22 under the `escalations` section. Per charter, "escalation" maps to overall coverage <70% triggering fresh-charter conversation. Codex's script appears to use the term broadly to mean "per-ticker investigation queue."

**Clarification:** the per-ticker flag is an *anomaly* per charter terminology; it is NOT an `escalation_required` charter verdict trigger. Overall coverage 99.6324% is far above the 70% escalation threshold. Charter terminology takes precedence for verdict-routing decisions.

Suggest Codex consider renaming the script's `escalations` field to `per_ticker_anomalies` in a follow-up commit to align with charter language. Not blocking.

## Pre-D6 sentinel check

Per L8 cohort note + charter §"Out of scope": D6 C10 re-run must consume only POST_FIX_REBUILT cohort rows from `data/dossier_updates_post_fix.db`. D9 sentinel verification (Codex task) confirms this post-D6.

## Summary

| dimension | status |
|---|---|
| Overall coverage ≥ 90% | ✓ 99.6324% |
| Per-ticker anomaly flagged | ✓ 1 ticker (KXPARDONSTRUMP-26APR-22, 1 row) |
| Escalation triggered | no |
| D6 C10 re-run authorized | ✓ yes |

**Codex D6 cleared to proceed against `data/dossier_updates_post_fix.db` + `logs/edge_replay/cycle16d/historical_prices_cycle16d.json`.**

## Cross-links

- `docs/governance/2026-05-07-cycle-16d-charter-price-reconstruction.md` — charter (criteria source).
- `docs/governance/2026-05-07-cycle-16d-task-split.md` M7 — task definition.
- `docs/governance/2026-05-07-cycle-16d-m3-fetch-code-review.md` — M3 fetch-code review (M3.3 KXPARDONSTRUMP anomaly originally flagged here).
- `scripts/edge_replay/price_coverage_audit.py` — D5 implementation.
- `logs/edge_replay/cycle16d/coverage_audit.json` — D5 output reviewed.
- `data/dossier_updates_post_fix.db` — POST_FIX_REBUILT cohort (D6 input).
- `logs/edge_replay/cycle16d/historical_prices_cycle16d.json` — restored prices (D6 input).
