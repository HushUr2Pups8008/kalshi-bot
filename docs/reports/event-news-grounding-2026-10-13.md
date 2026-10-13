# Event-news grounding report

- n rows: 0
- n vetoed: 0
- n with outcome: 0
- veto precision: not_measured
- vetoed fee-net: not_measured
- recommend live: no

Recommend live only when n with outcome is at least 20.
A veto is precise when the skipped trade later lost money.

Paper mode sends `search_parameters.mode=on` with citations. The live veto
does not, unless `EVENT_NEWS_GROK_GROUNDING=true`. No stored grounded reply
exists yet, so this snapshot cannot support a live enable.
