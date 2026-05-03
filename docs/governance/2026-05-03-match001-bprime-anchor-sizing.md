# MATCH-001 B' Regression-Anchor Sizing

Ticker-level canonical guard is stricter than exact-event guard. The archive contains many low-quality matches on canonical tickers, especially KXTRUMPIRAN; exact event preservation is not the same as ticker-level preservation.

## Summary

- MATCH_DIAGNOSTIC total: 2838
- Suppressed match keys under B': 1076
- OPPORTUNITY retained: 87/260 (33.5%)
- PAPER_TRADE retained: 3/3
- Canonical ticker guard: FAIL
- Canonical ticker suppressed matches: 399
- Exact canonical event suppressed matches: 0

## Canonical Ticker Suppression

| ticker | suppressed matches |
| --- | ---: |
| KXTRUMPIRAN-26MAY01 | 399 |

## Examples

| ticker | score | tokens | flags | headline |
| --- | ---: | --- | --- | --- |
| KXTRUMPIRAN-26MAY01 | 0.0887 | ['iran'] | ['single_named_entity_only', 'minimal_overlap'] | Iran War Live Updates: Hopes for Peace Deal Rise After Iran Says Strait Is Open |
| KXTRUMPIRAN-26MAY01 | 0.1006 | ['trump'] | ['single_named_entity_only', 'minimal_overlap'] | Trump Extends Sanctions Exemption on Some Russian Oil as High Gas Prices Persist |
| KXTRUMPIRAN-26MAY01 | 0.0784 | ['iran'] | ['single_named_entity_only', 'minimal_overlap', 'near_threshold_score'] | Iran says strait of Hormuz ‘completely open’ but sounds warning on US blockade |
| KXTRUMPIRAN-26MAY01 | 0.0646 | ['iran'] | ['single_named_entity_only', 'minimal_overlap', 'near_threshold_score'] | A Potent Threat in Strait of Hormuz: Iran’s “Mosquito Fleet” |
| KXTRUMPIRAN-26MAY01 | 0.0934 | ['iran'] | ['single_named_entity_only', 'minimal_overlap'] | Six great reads: Iran’s social media memes, an abandoned department store and a 1,200-year-old record of cherry blossoms |
| KXTRUMPIRAN-26MAY01 | 0.0656 | ['trump'] | ['single_named_entity_only', 'minimal_overlap', 'near_threshold_score'] | Two weeks that pushed Trump to the edge. Is his presidency unravelling? |
| KXTRUMPIRAN-26MAY01 | 0.1006 | ['iran'] | ['single_named_entity_only', 'minimal_overlap'] | Hopes for Peace Deal Rise After Iran Says Strait Is Open |
| KXTRUMPIRAN-26MAY01 | 0.0857 | ['iran'] | ['single_named_entity_only', 'minimal_overlap'] | Iran War Live Updates: Uncertainty Remains at Strait of Hormuz After Reopening Announcement |

## Verdict

The pre-deploy guard as phrased in the landing-order spec is too strict if it means ticker-level protection. B' suppresses many low-quality matches on canonical tickers, mostly single-token `iran` / `trump` matches against `KXTRUMPIRAN-26MAY01`. The deploy-day audit should protect exact canonical event tuples/headlines, not every future low-quality match on those tickers.
