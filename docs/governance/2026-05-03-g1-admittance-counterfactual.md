# G1 Admittance Counterfactual

Read-only sizing over archived `BLEND_DECISION` records. A candidate is counted as admitted if its recorded `blended_confidence * regime_confidence` clears the tested G1 floor.

## Summary

- BLEND_DECISION total: 252
- G1-killed candidates: 197
- Limitation: Archive BLEND_DECISION records store only the first readiness failure. This audit sizes G1-only admittance; candidates admitted by a lower G1 floor may still fail G2/G3/G4/G5/G6 once the full gate is re-evaluated.

## Floor Sweep

| G1 floor | G1 kills admitted | admission rate | edge obs | edge >= 0.02 | edge >= 0.05 | mean predicted edge | p50 | p90 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.04 | 32 | 16.2% | 32 | 1 | 1 | 0.0020 | 0.0 | 0.0 |
| 0.03 | 65 | 33.0% | 65 | 2 | 1 | 0.0014 | 0.0 | 0.0 |

## Top Admitted Tickers

### G1 floor 0.04
- KXMOCTRUMP25-26-APR24: 9
- KXVANCEPAKISTAN-26APR21-APR25: 8
- KXMOCTRUMP25-26-MAY01: 7
- KXVANCEPAKISTAN-26APR21-APR24: 2
- KXVANCEPAKISTAN-26APR21-APR30: 2
- KXTRUMPENDORSE-26SEP15-NMOR: 1
- KXTRUMPCHINA-26-APR24: 1
- KXVOTESAVEAMERICA-26APR-MAY23: 1
- KXTXRUNOFFENDORSE-26MAY26-DJT-BOTH: 1

### G1 floor 0.03
- KXMOCTRUMP25-26-MAY01: 24
- KXTRUMPIRAN-26MAY01: 12
- KXMOCTRUMP25-26-APR24: 9
- KXVANCEPAKISTAN-26APR21-APR25: 8
- KXVANCEPAKISTAN-26APR21-APR24: 2
- KXVANCEPAKISTAN-26APR21-MAY04: 2
- KXVANCEPAKISTAN-26APR21-APR30: 2
- KXPARDONSTRUMP-26APR-1: 1
- KXELECTIONEMERGENCY-26MAY01: 1
- KXTRUMPENDORSE-26SEP15-NMOR: 1

## Verdict

Lever B has meaningful throughput leverage but weak direct edge leverage on the archived mix. Dropping G1 from 0.05 to 0.04 admits 32/197 G1-killed candidates; 0.03 admits 65/197. Most admitted candidates still have predicted edge near zero, so the main reason to land B is to expose post-OBS-003 attribution and calibrate G1, not to expect an immediate paper-trade lift by itself.
