# MATCH-001 B' Tokenization Equivalence Audit

The current simulation models B' with raw substring membership against the ticker. A post-fix implementation that literally uses _tokenize(ticker) set-difference would produce a different suppression surface because hyphenated tickers remain single tokens under _tokenize.

## Summary

- MATCH_DIAGNOSTIC total: 2838
- Simulation B' substring suppressed keys: 1076
- Current pre-B' production suppressed keys: 435
- B' with production `_tokenize(ticker)` set-diff suppressed keys: 0
- Simulation vs `_tokenize(ticker)` symmetric diff: 1076
- `_tokenize` examples: {'KXTRUMPIRAN-26MAY01': ['kxtrumpiran-26may01'], 'KXMOCTRUMP25-26-MAY01': ['kxmoctrump25-26-may01']}

## Divergence Examples

| ticker | ticker tokens | matched tokens | B' substring suppresses | B' tokenized setdiff suppresses | headline |
| --- | --- | --- | --- | --- | --- |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['iran'] | True | False | Iran War Live Updates: Hopes for Peace Deal Rise After Iran Says Strait Is Open |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['trump'] | True | False | Trump Extends Sanctions Exemption on Some Russian Oil as High Gas Prices Persist |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['iran'] | True | False | Iran says strait of Hormuz ‘completely open’ but sounds warning on US blockade |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['iran'] | True | False | A Potent Threat in Strait of Hormuz: Iran’s “Mosquito Fleet” |
| KXMOCTRUMP25-26-MAY01 | ['kxmoctrump25-26-may01'] | ['trump'] | True | False | Trump Spat Gives Spain Leader Pedro Sánchez a Political Lifeline |
| KXMOCTRUMP25-26-APR24 | ['kxmoctrump25-26-apr24'] | ['trump'] | True | False | Trump Spat Gives Spain Leader Pedro Sánchez a Political Lifeline |
| KXMOCTRUMP25-26-MAY01 | ['kxmoctrump25-26-may01'] | ['trump'] | True | False | How Trump Helped Pope Leo Find His Voice |
| KXMOCTRUMP25-26-APR24 | ['kxmoctrump25-26-apr24'] | ['trump'] | True | False | How Trump Helped Pope Leo Find His Voice |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['iran'] | True | False | Six great reads: Iran’s social media memes, an abandoned department store and a 1,200-year-old record of cherry blossoms |
| KXTRUMPIRAN-26MAY01 | ['kxtrumpiran-26may01'] | ['trump'] | True | False | Two weeks that pushed Trump to the edge. Is his presidency unravelling? |

## Verdict

The 1,076-key B' estimate is valid only for the substring-membership interpretation used by the simulation. It is not valid for a literal `_tokenize(ticker)` set-difference implementation: `_tokenize('KXTRUMPIRAN-26MAY01')` returns one hyphenated token, so matched tokens like `trump` and `iran` are treated as non-ticker support and the suppression surface shrinks materially. The MATCH-001 landing spec should either codify substring membership or re-run sizing after changing the tokenizer semantics.
