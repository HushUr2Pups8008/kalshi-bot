# Cross-Series Headline Overlap Audit

Primary grouping uses normalized exact headline text: case, punctuation, URL, and whitespace differences are ignored, but semantic paraphrases are not merged.

## Summary

- OPPORTUNITY total: 260
- Normalized headline groups: 138
- Cross-series headline groups: 39
- Cross-series OPPORTUNITY count: 128 (49.2%)
- Verdict: `supports_lever_c`

## Top Series Pairs

| series pair | overlapped OPPORTUNITY records |
| --- | ---: |
| KXMOCTRUMP25 / KXTRUMPIRAN | 79 |
| KXTRUMPIRAN / KXVANCEPAKISTAN | 27 |
| KXTRUMPCHINA / KXTRUMPIRAN | 12 |
| KXTRUMPENDORSE / KXTRUMPIRAN | 9 |
| KXMOCTRUMP25 / KXPARDONSTRUMP | 9 |
| KXPARDONSTRUMP / KXTRUMPIRAN | 9 |
| KXMOCTRUMP25 / KXVANCEPAKISTAN | 6 |
| KXPARDONSTRUMP / KXTRUMPCHINA | 6 |
| KXELECTIONEMERGENCY / KXTRUMPENDORSE | 3 |
| KXELECTIONEMERGENCY / KXTRUMPIRAN | 3 |

## Top Cross-Series Groups

| OPPORTUNITY | series | tickers | headline |
| ---: | --- | --- | --- |
| 9 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-APR24, KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | Iran War Live Updates: Iranian Forces Claim Seizure of 2 Ships After Trump Extends Truce |
| 6 | KXMOCTRUMP25, KXTRUMPIRAN, KXVANCEPAKISTAN | KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01, KXVANCEPAKISTAN-26APR21-APR30 | Middle East crisis live: Donald Trump cancels US envoy trip to Pakistan for ceasefire talks with Iran |
| 6 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | Middle East crisis live: Trump says Israel-Lebanon ceasefire extended by three weeks but claims he won’t rush Iran deal |
| 6 | KXTRUMPIRAN, KXVANCEPAKISTAN | KXTRUMPIRAN-26MAY01, KXVANCEPAKISTAN-26APR21-APR25, KXVANCEPAKISTAN-26APR21-APR30 | Middle East crisis live: US envoy and Trump’s son-in-law to travel to Pakistan amid hopes for renewed Iran peace talks |
| 6 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-APR24, KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | Trust Trump? Iran’s Doubts Shadow Peace Talks |
| 3 | KXMOCTRUMP25, KXPARDONSTRUMP, KXTRUMPIRAN | KXMOCTRUMP25-26-MAY01, KXPARDONSTRUMP-26APR-0, KXTRUMPIRAN-26MAY01 | Deadline approaches for Trump to secure Congressional approval for Iran war |
| 3 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-APR24, KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | ‘Don’t rush me’: Trump declines to say how long he will wait for Iran deal – Middle East crisis live |
| 3 | KXMOCTRUMP25, KXTRUMPCHINA, KXTRUMPIRAN | KXMOCTRUMP25-26-MAY01, KXTRUMPCHINA-26-APR24, KXTRUMPIRAN-26MAY01 | Donald Trump says he speaks 'for the UK more than Prince Harry' – video |
| 3 | KXELECTIONEMERGENCY, KXTRUMPENDORSE, KXTRUMPIRAN | KXELECTIONEMERGENCY-26MAY01, KXTRUMPENDORSE-26SEP15-NMOR, KXTRUMPIRAN-26MAY01 | Fed chair nominee Kevin Warsh accused of being a ‘sock puppet’ for Donald Trump – US politics live |
| 3 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-APR24, KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | Fed chair nominee Kevin Warsh faces Congress after Trump signals he is expecting him to cut rates – US politics live |
| 3 | KXTRUMPENDORSE, KXTRUMPIRAN | KXTRUMPENDORSE-26SEP15-MCOL, KXTRUMPENDORSE-26SEP15-NMOR, KXTRUMPIRAN-26MAY01 | First Thing: Trump doubles down on rift with Germany, Italy and Spain amid war on Iran |
| 3 | KXMOCTRUMP25, KXTRUMPIRAN | KXMOCTRUMP25-26-APR24, KXMOCTRUMP25-26-MAY01, KXTRUMPIRAN-26MAY01 | Iran War Live Updates: Iranian Forces Claim to Seize 2 Ships After Trump Extends Truce |

## Read

Lever C clears the 5% sizing bar by a wide margin on the 13-day archive: 128/260 OPPORTUNITY records (49.2%) share normalized exact headlines across multiple series prefixes. The dominant overlap is `KXMOCTRUMP25` / `KXTRUMPIRAN`, with a smaller but material `KXTRUMPIRAN` / `KXVANCEPAKISTAN` lane.

This does not prove the implementation should suppress every duplicate headline. It does prove the overlap rate is high enough that EXEC-002 Approach 2 should stay in scope and receive a real spec: headline-hash dedupe across series within a bounded time window, with allowlist/override handling for genuinely multi-market news.
