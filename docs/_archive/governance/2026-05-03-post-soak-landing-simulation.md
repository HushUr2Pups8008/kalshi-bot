# Post-Soak Landing Simulation

Counterfactual replay over the 13-day MacBook archive.

| step | OPPORTUNITY | SKIPPED | PAPER_TRADE | visible_skip_share | trade_rate | note |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 260 | 17 | 3 | 85.0% | 1.2% | Observed archive. |
| OBS-005 | 260 | 17 | 3 | 85.0% | 1.2% | Cooldown sentinel fix has no archive-visible counterfactual effect. |
| MATCH-001_B_prime | 87 | 9 | 3 | 75.0% | 3.4% | Estimated by removing downstream records whose MATCH_DIAGNOSTIC would meet B' suppression (1076 keys). |
| OBS-003 | 87 | 87 | 3 | 96.7% | 3.4% | Adds 78 BlendTask blocked-reason SKIPPED records; top reasons {'G1_blended_confidence': 59, 'G6_recency_score': 14, 'G2_evidence_source_class_diversity': 5}. |
| EXEC-002 | 87 | 89 | 1 | 98.9% | 1.1% | Series-correlation guard suppresses 2 paper trades inside 1h series windows. |

## Limitations

- MATCH-001 B' estimate uses archived MATCH_DIAGNOSTIC keys; it cannot model below-threshold matches that were never logged.
- OBS-003 estimate assumes every retained silent OPPORTUNITY with a nearby blocked BLEND_DECISION would emit exactly one SKIPPED.
- EXEC-002 estimate models only paper trades already present in the archive, not untraded candidates that would become newly visible after OBS-003.
