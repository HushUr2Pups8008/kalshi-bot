# Lever A.1 Source-Classifier Counterfactual

Archived EVIDENCE_INGESTION records do not carry raw source labels, so the exact spec replay over each evidence source string is unavailable. OPPORTUNITY and MATCH_DIAGNOSTIC source labels are reported as archive-visible surrogates.

## Summary

- EVIDENCE_INGESTION rows: 248
- EVIDENCE_INGESTION rows with raw source string: 0
- OPPORTUNITY official target: 1/260 (FAIL against >=30)

## OPPORTUNITY Source-Label Replay

- rows: 260
- rows with source string: 260
- current distribution: `{'news': 213, 'other': 46, 'official': 1}`
- post-A.1 distribution: `{'news': 215, 'other': 44, 'official': 1}`
- flips: `{'other->news': 2}`

| source | current | post-A.1 | ticker | headline |
| --- | --- | --- | --- | --- |
| Breaking Defense | other | news | KXMOCTRUMP25-26-MAY01 | President Trump should secure America’s nuclear future by taking weapons out of DoE |
| Defense News | other | news | KXTRUMPIRAN-26MAY01 | Ceasefire ‘stops’ War Powers clock on Iran, Hegseth claims |

## MATCH_DIAGNOSTIC Source-Label Replay

- rows: 2838
- rows with source string: 2838
- current distribution: `{'news': 1653, 'other': 1112, 'official': 63, 'social': 10}`
- post-A.1 distribution: `{'news': 1661, 'other': 1104, 'official': 63, 'social': 10}`
- flips: `{'other->news': 8}`

| source | current | post-A.1 | ticker | headline |
| --- | --- | --- | --- | --- |
| Breaking Defense | other | news | KXMOCTRUMP25-26-MAY01 | President Trump should secure America’s nuclear future by taking weapons out of DoE |
| Defense News | other | news | KXTRUMPIRAN-26MAY01 | US military commanders to brief Trump on military options against Iran |
| Defense News | other | news | KXMOCTRUMP25-26-MAY01 | US military commanders to brief Trump on military options against Iran |
| Defense News | other | news | KXELECTIONEMERGENCY-26MAY01 | US military commanders to brief Trump on military options against Iran |
| Breaking Defense | other | news | KXRUCRUDEX-26MAY13-T4.0 | Russia’s windfall from the Iran war is temporary. Ukraine’s isn’t. |
| Breaking Defense | other | news | KXTRUMPIRAN-26MAY01 | Russia’s windfall from the Iran war is temporary. Ukraine’s isn’t. |
| Defense News | other | news | KXTRUMPIRAN-26MAY01 | Ceasefire ‘stops’ War Powers clock on Iran, Hegseth claims |
| Defense News | other | news | KXUSAIRANAGREEMENT-27-26MAY | US Navy turns to AI firm Domino for options to counter Iranian mines |

## Verdict

The exact EVIDENCE_INGESTION replay promised by the spec is blocked by archive shape: the raw source labels are absent. On OPPORTUNITY source labels, the post-A.1 classifier does not reach the >=30/260 official target. On the broader MATCH_DIAGNOSTIC feed surface it does recover additional official labels, so the fix may improve upstream classification without proving an OPPORTUNITY-surface lift.
