# Matcher Jaccard Threshold Bisection

Read-only sweep over archived `MATCH_DIAGNOSTIC` records.

## Summary

- MATCH_DIAGNOSTIC total: 2838
- OPPORTUNITY total: 260
- OPPORTUNITY with observed MATCH_DIAGNOSTIC key: 237
- SKIPPED total: 17
- PAPER_TRADE total: 3
- Score range: 0.06 to 0.2852 (p50 0.0754)
- Limitation: Archive MATCH_DIAGNOSTIC records only cover candidates above the live threshold; this sweep estimates tightening/raising thresholds, not below-floor recall.

## Read

Raising the threshold improves `OPPORTUNITY` yield only modestly while rapidly destroying historical opportunity coverage. The 0.08 threshold retains 42.6% of diagnostics and all 3 paper trades, but only 51.5% of OPPORTUNITY records. At 0.10, opportunity retention falls to 27.7%. This argues against a simple Jaccard-threshold raise as the primary EDGE-004 fix; predicate-level suppression such as MATCH-001 B' has a better risk shape because it targets low-quality single-entity noise without globally discarding the archive's few opportunity-producing matches.

## Threshold Sweep

| threshold | diagnostics_retained | diagnostic_retention | opportunities_retained | opportunity_retention | opportunity_yield | skipped_retained | paper_retained |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.06 | 2838 | 100.0% | 237 | 91.2% | 8.4% | 10 | 3 |
| 0.07 | 1863 | 65.6% | 185 | 71.2% | 9.9% | 7 | 3 |
| 0.08 | 1208 | 42.6% | 134 | 51.5% | 11.1% | 7 | 3 |
| 0.09 | 846 | 29.8% | 104 | 40.0% | 12.3% | 6 | 3 |
| 0.10 | 581 | 20.5% | 72 | 27.7% | 12.4% | 4 | 3 |
| 0.12 | 331 | 11.7% | 35 | 13.5% | 10.6% | 1 | 1 |
| 0.15 | 91 | 3.2% | 9 | 3.5% | 9.9% | 0 | 0 |
| 0.20 | 17 | 0.6% | 3 | 1.2% | 17.6% | 0 | 0 |

## Top Heuristic Flags

- single_named_entity_only: 1691
- minimal_overlap: 1691
- near_threshold_score: 1639
- low_token_overlap: 590
