# PROFIT-OBS-003 Kill Attribution

Read-only attribution over the full trade-log archive.

## Summary

- OPPORTUNITY total: 264
- Accounted exits: 23
- Silent exits: 240
- Unattributed exits: 1
- Join validation balanced: True

## Silent-Exit Reasons

| trade_blocked_reason | count | share_of_silent |
| --- | ---: | ---: |
| G1_blended_confidence | 197 | 82.1% |
| G6_recency_score | 31 | 12.9% |
| G2_evidence_source_class_diversity | 12 | 5.0% |

## Accounted Exits

| exit_type | count |
| --- | ---: |
| SKIPPED | 20 |
| PAPER_TRADE | 3 |

## Top Ticker Silent-Exit Drivers

| ticker | drivers |
| --- | --- |
| KXTRUMPIRAN-26MAY01 | {"G1_blended_confidence": 77, "G6_recency_score": 31} |
| KXMOCTRUMP25-26-MAY01 | {"G1_blended_confidence": 52} |
| KXMOCTRUMP25-26-APR24 | {"G1_blended_confidence": 20} |
| KXVANCEPAKISTAN-26APR21-APR30 | {"G1_blended_confidence": 15} |
| KXVANCEPAKISTAN-26APR21-APR25 | {"G1_blended_confidence": 8} |
| KXVANCEPAKISTAN-26APR21-APR27 | {"G1_blended_confidence": 6} |
| KXVANCEPAKISTAN-26APR21-MAY04 | {"G1_blended_confidence": 2, "G2_evidence_source_class_diversity": 2} |
| KXPARDONSTRUMP-26APR-1 | {"G1_blended_confidence": 1, "G2_evidence_source_class_diversity": 2} |
| KXTRUMPENDORSE-26SEP15-NMOR | {"G1_blended_confidence": 1, "G2_evidence_source_class_diversity": 2} |
| KXELECTIONEMERGENCY-26MAY01 | {"G1_blended_confidence": 1, "G2_evidence_source_class_diversity": 1} |

## Unattributed Examples

- KXTRUMPIRAN-26MAY01 2026-04-21T01:52:46.710790+00:00: no_matching_exit_or_blocked_blend_decision
