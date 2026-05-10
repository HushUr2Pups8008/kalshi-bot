# Legal-niche probe order: domain-keyword overlap re-derivation

Date: 2026-05-04
Task: independently re-derive the legal-niche probe order using domain-keyword overlap against market titles.

## Input

`kalshi_universe.json` is not present in this checkout. I used the closest local proxy: 77 distinct `market_title` strings observed in the 2026-04 Mac archive trade logs.

Method:

- Candidate keyword sets were hand-built from each legal source niche.
- Market titles were tokenized, stopwords removed, and de-duplicated.
- Score = overlapping candidate keywords weighted by title-token IDF and log-scaled record frequency.

## Ranking

| rank | candidate | weighted score | hit titles | raw hits |
| ---: | --- | ---: | ---: | ---: |
| 1 | Politico Legal | 194.4 | 44 | 59 |
| 2 | Just Security | 167.6 | 34 | 41 |
| 3 | Lawfare | 159.4 | 34 | 40 |
| 4 | SCOTUSblog | 104.0 | 31 | 32 |
| 5 | VitalLaw | 6.0 | 2 | 2 |
| 6 | Reuters Legal | 6.0 | 2 | 2 |

## Comparison to earlier order

Earlier empirical/operator order:

1. VitalLaw
2. Just Security
3. Lawfare
4. SCOTUSblog
5. Politico Legal
6. Reuters Legal

Alternate overlap order:

1. Politico Legal
2. Just Security
3. Lawfare
4. SCOTUSblog
5. VitalLaw
6. Reuters Legal

## Interpretation

The overlap method favors broad political/legal vocabulary (`trump`, `congress`, `election`, `federal`) and therefore boosts Politico Legal. It demotes VitalLaw because the observed title universe has only two strongly legal/FISA title surfaces, even though VitalLaw is the only source with historical PAPER_TRADE conversion.

Operator read: keep VitalLaw first for empirical load-bearing recovery if accessible. If VitalLaw probe fails, the alternate title-overlap method supports Just Security and Lawfare ahead of SCOTUSblog, with Politico Legal useful for broad title coverage but more likely to overlap generic politics rather than the specific VitalLaw-shaped FISA/legal-regulatory niche.
