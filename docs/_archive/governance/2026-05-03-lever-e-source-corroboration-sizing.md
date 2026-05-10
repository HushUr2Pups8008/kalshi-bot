# Lever E Source/Source-Class Corroboration Sizing

Distinct sources and source classes are recovered from BLEND_DECISION.evidence_ids_contributing joined to EVIDENCE_INGESTION.source/source_class. OPPORTUNITY rows without a joined blend or known evidence ids count as 0-source/0-class and would be cut by Lever E.

## Summary

- OPPORTUNITY total: 260
- PAPER_TRADE total: 3

## Source-Class Diversity Distribution

| distinct source classes | OPPORTUNITY | edge signs | paper trades |
| ---: | ---: | --- | ---: |
| 0 | 9 | {'zero': 9} | 0 |
| 1 | 142 | {'zero': 138, 'negative': 3, 'positive': 1} | 3 |
| 2 | 100 | {'zero': 99, 'positive': 1} | 0 |
| 3 | 9 | {'zero': 9} | 0 |

## Source-Instance Diversity Distribution

| distinct sources | OPPORTUNITY | edge signs | paper trades |
| ---: | ---: | --- | ---: |
| 0 | 9 | {'zero': 9} | 0 |
| 1 | 251 | {'zero': 246, 'negative': 3, 'positive': 2} | 3 |

## Source-Class Threshold Counterfactual

| threshold | retained OPPORTUNITY | cut OPPORTUNITY | retention | retained PAPER_TRADE |
| --- | ---: | ---: | ---: | ---: |
| N>=2 | 109 | 151 | 41.9% | 0 |
| N>=3 | 9 | 251 | 3.5% | 0 |

## Source-Instance Threshold Counterfactual

| threshold | retained OPPORTUNITY | cut OPPORTUNITY | retention | retained PAPER_TRADE |
| --- | ---: | ---: | ---: | ---: |
| N>=2 | 0 | 260 | 0.0% | 0 |
| N>=3 | 0 | 260 | 0.0% | 0 |

## Read

Using source classes, `N>=2` would retain 109/260 OPPORTUNITY records and cut 151; `N>=3` would retain 9 and cut 251.

Using distinct source instances, `N>=2` would retain 0/260 OPPORTUNITY records and cut 260; `N>=3` would retain 0 and cut 260.

This is a high-blast-radius filter. Source-class N>=2 removes a large single-class tail; source-instance N>=2 removes the entire historical OPPORTUNITY set because every joined candidate has at most one distinct source instance. Use the table above as the pre-deploy expectation, not as a direct EV claim.
