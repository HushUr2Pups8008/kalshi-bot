# Lever A.1+ specialist-analyst audit — domain-normalized replay

**Author:** Codex
**Date:** 2026-05-04
**Source surface:** `mac_archive/macbook_2026-05-01_import/logs/trades`
**Purpose:** independent re-derivation of the 2026-05-03 per-source audit using outlet/domain normalization instead of the token-bucket labels in `scripts/simulations/lever_a1_plus_specialist_analyst_per_source_sizing.py`

## TL;DR

The alternate grouping agrees with the original result. `VitalLaw.com` remains the load-bearing source:

- specialist_analyst totals: `251 MATCH_DIAGNOSTIC`, `21 OPPORTUNITY`, `3 PAPER_TRADE`
- `vitallaw.com`: `21 MATCH_DIAGNOSTIC`, `3 OPPORTUNITY`, `3 PAPER_TRADE`
- specialist-class PAPER_TRADE share from `vitallaw.com`: `100%`
- non-VitalLaw specialist outlets contribute `18 OPPORTUNITY` and `0 PAPER_TRADE`

## Method

- Read all JSONL rows under `mac_archive/macbook_2026-05-01_import/logs/trades`
- Keep only `MATCH_DIAGNOSTIC`, `OPPORTUNITY`, `PAPER_TRADE`
- Assign outlet by normalized domain/brand, not by sub-niche token order
- Specialist outlets recognized in this replay:
  - `vitallaw.com`
  - `kyivindependent.com`
  - `kyivpost.com`
  - `timesofisrael.com`
  - `iranintl.com`
  - `defensenews.com`
  - `breakingdefense.com`
  - `bellingcat.com`

## Results

| normalized outlet | MATCH_DIAGNOSTIC | OPPORTUNITY | PAPER_TRADE | specialist OPP share | specialist PAPER share |
| --- | ---: | ---: | ---: | ---: | ---: |
| `vitallaw.com` | 21 | 3 | 3 | 14.3% | 100.0% |
| `timesofisrael.com` | 54 | 6 | 0 | 28.6% | 0.0% |
| `kyivindependent.com` | 75 | 5 | 0 | 23.8% | 0.0% |
| `kyivpost.com` | 66 | 3 | 0 | 14.3% | 0.0% |
| `iranintl.com` | 26 | 2 | 0 | 9.5% | 0.0% |
| `defensenews.com` | 5 | 1 | 0 | 4.8% | 0.0% |
| `breakingdefense.com` | 3 | 1 | 0 | 4.8% | 0.0% |
| `bellingcat.com` | 1 | 0 | 0 | 0.0% | 0.0% |

## Interpretation

This replay removes the main objection to the 2026-05-03 audit: the result is not an artifact of the `vital_law` token bucket. Even under direct outlet normalization:

- `VitalLaw` is still the only specialist outlet with any archived PAPER_TRADE
- the geopolitics specialist surface is real (`18 OPPORTUNITY`) but conversion-free on this archive
- A.1+ option-A remains an OPP-surface expansion, not a historically validated PAPER_TRADE-restoration path
- A.1+ option-B remains the only path with archive-backed trade conversion evidence

## Practical implication

If the operator wants the highest-probability first-feed attempt, the decision tree is:

1. Probe `VitalLaw` itself first.
2. If blocked by paywall/auth/robots, move to legal-niche analogues.
3. Treat geopolitics-specialist feeds as a separate option with weaker empirical support.

## Cross-links

- `docs/governance/2026-05-03-lever-a1-plus-specialist-analyst-per-source-sizing.md`
- `docs/superpowers/specs/2026-05-03-edge-004-lever-a1plus-feed-onboarding-design.md`
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`
