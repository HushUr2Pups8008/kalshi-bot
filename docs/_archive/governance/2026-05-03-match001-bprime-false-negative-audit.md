# MATCH-001 B' Headline-Collision False-Negative Audit

Likely false negative = B' low-quality shape, at least one matched token appears in the ticker substring, B' substring semantics do not suppress it, <=1 non-ticker support token, and match_score <=0.08. This is a weak-support heuristic, not manual relevance labeling.

## Summary

- MATCH_DIAGNOSTIC total: 2838
- B' low-quality shape total: 1691
- B' substring suppressed: 1202
- Escapes with ticker-substring overlap: 0
- Likely false negatives: 0 (0.0% of B' shape)
- Top likely false-negative tickers: `{}`
- Top ticker-overlap tokens: `{}`

## Examples

| ticker | score | ticker tokens | non-ticker tokens | source | headline |
| --- | ---: | --- | --- | --- | --- |

## Verdict

Under this weak-support proxy, substring semantics did not leave a measurable false-negative tail: every archived B' low-quality shape with ticker-substring-only support is suppressed, and no escaped low-quality row met the likely-false-negative definition. Treat this as evidence for option (a)'s adequacy on the archive, with the caveat that this is heuristic rather than manual relevance labeling.
