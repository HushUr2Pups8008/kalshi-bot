# Source-Class Diversification Audit

Source-class diversity is measured from BLEND_DECISION evidence_ids_contributing joined to EVIDENCE_INGESTION.source_class; OPPORTUNITY records without a nearby blend or evidence IDs are bucketed as unknown.

## Summary

- OPPORTUNITY total: 260
- PAPER_TRADE total: 3
- EVIDENCE_INGESTION total: 248

## OPPORTUNITY Source Classes

| source_class | count |
| --- | ---: |
| news | 238 |
| other | 122 |
| unknown | 9 |
| official | 9 |

## Class Diversity Per OPPORTUNITY

| bucket | count |
| --- | ---: |
| 1_class | 142 |
| 2_classes | 100 |
| unknown | 9 |
| 3_classes | 9 |

## Edge Sign By Class

- zero: {'news': 237, 'other': 117, 'unknown': 9, 'official': 9}
- positive: {'other': 2, 'news': 1}
- negative: {'other': 3}

## Read

The OPPORTUNITY surface is still news-heavy: `news` appears on 238 OPPORTUNITY joins, `other` on 122, and `official` on only 9. Per-opportunity diversity is mixed rather than absent: 142/260 have one known class, 109/260 have two or more known classes, and 9 are unknown. The positive-edge cases are not official-source driven (`other` x2, `news` x1), so merely adding official sources is not the direct EDGE-004 unlock. The better next hypothesis is source-mix plus readiness/blending interaction: broaden non-news classes enough to improve G2/structural context, then verify whether G1/G6 still dominate after OBS-003 makes those kills visible in SKIPPED.
