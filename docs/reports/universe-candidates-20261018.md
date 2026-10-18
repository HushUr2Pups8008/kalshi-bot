# Universe candidates 2026-10-18

Fetched at: 2026-10-18T19:48:48Z
Request: GET /markets?status=open&mve_filter=exclude
Market pages: 130
Raw markets: 129451
Response status active: 129451
Series pages: 1

A candidate series has at least one market with response status active,
close time inside the next 14 days, 24h volume of at least 500 contracts,
and a two-sided spread of 0c through 14c. Spread is yes ask plus no ask
minus 100. Asks outside 1c through 99c are not a two-sided quote.
A negative spread is excluded. Deny-list prefixes are excluded unless
that exact series is already in config/series_universe.yaml.
This job does not add series.

## Top 20 candidate series

| rank | series | markets | volume_24h | median_close_utc | category |
|---:|---|---:|---:|---|---|
| 1 | KXNFLGAME | 47 | 43243841 | 2026-10-27T13:30:00Z | Sports |
| 2 | KXNFLSPREAD | 222 | 20874906.39 | 2026-10-20T17:00:00Z | Sports |
| 3 | KXNFLTD | 302 | 10059862.54 | 2026-10-20T17:00:00Z | Sports |
| 4 | KXNFLTOTAL | 148 | 5722761.07 | 2026-10-20T17:00:00Z | Sports |
| 5 | KXLALIGAGAME | 13 | 2881992.79 | 2026-10-25T19:00:00Z | Sports |
| 6 | KXMLBGAME | 2 | 2297194.02 | 2026-10-21T00:00:00Z | Sports |
| 7 | KXNFLRECYDS | 364 | 1793856.46 | 2026-10-20T17:00:00Z | Sports |
| 8 | KXNFLRSHYDS | 262 | 1592566.25 | 2026-10-20T17:00:00Z | Sports |
| 9 | KXATPCHALLENGERMATCH | 17 | 1483592.01 | 2026-11-01T17:20:00Z | Sports |
| 10 | KXSERIEAGAME | 10 | 1476494.93 | 2026-10-21T17:37:30Z | Sports |
| 11 | KXLIGUE1GAME | 5 | 1415948.41 | 2026-10-20T18:45:00Z | Sports |
| 12 | KXNASCARRACE | 13 | 1209116.07 | 2026-10-26T01:00:00Z | Sports |
| 13 | KXNFLPASSYDS | 111 | 1056859.49 | 2026-10-20T17:00:00Z | Sports |
| 14 | KXNFLREC | 216 | 1047114.46 | 2026-10-20T17:00:00Z | Sports |
| 15 | KXMLBTOTAL | 10 | 641529.44 | 2026-10-21T00:00:00Z | Sports |
| 16 | KXLOLGAME | 4 | 632915.2 | 2026-10-21T05:00:00Z | Sports |
| 17 | KXNFLTEAMTOTAL | 106 | 594844.62 | 2026-10-20T17:00:00Z | Sports |
| 18 | KXCS2GAME | 13 | 485864.57 | 2026-10-20T22:00:00Z | Sports |
| 19 | KXUCLGAME | 29 | 463409.09 | 2026-10-22T19:00:00Z | Sports |
| 20 | KXLALIGATOTAL | 5 | 434148.91 | 2026-10-20T19:00:00Z | Sports |

## Politics and econ-policy subset

Included when the series category is Politics, Elections, or Economics,
the series is not on the non-politics deny list, and the series does
not match MARKET_SERIES_BLOCKLIST_PREFIXES. Ranked by the same
qualifying 24h volume. Already configured series are not recommended.

| rank | series | markets | volume_24h | median_close_utc | category |
|---:|---|---:|---:|---|---|
| 1 | KXTRUMPPHOTO | 2 | 9727.93 | 2026-10-19T14:00:00Z | Politics |
| 2 | KXHEGSETHOUT | 1 | 4471.66 | 2026-11-01T04:59:00Z | Politics |
| 3 | KXPRESSSECANNOUNCE | 2 | 4297.05 | 2026-10-26T15:59:00Z | Politics |
| 4 | KXEOWEEK | 3 | 3658.72 | 2026-10-31T14:00:00Z | Politics |
| 5 | KXTSAW | 1 | 3657.28 | 2026-10-19T03:59:00Z | Economics |
| 6 | KXVHGCB | 2 | 3124.23 | 2026-10-18T21:04:00Z | Politics |
| 7 | KXDIESELW | 2 | 2648.36 | 2026-10-19T05:59:00Z | Economics |
| 8 | KXLULAFLAVIODEBATE | 1 | 2557 | 2026-10-25T03:59:00Z | Politics |
| 9 | KXEIACRUDEW | 2 | 2000 | 2026-10-21T14:29:00Z | Economics |
| 10 | KXNJGOLFVISITCOUNT | 2 | 1943.54 | 2026-11-01T15:00:00Z | Politics |

## Configured paper series

Mode paper rows from config/series_universe.yaml. Stats are this
fetch's qualifying markets. A blank volume means the series had no
qualifying market. This job does not write the yaml and does not POST.

| series | markets | volume_24h | median_close_utc | category | title |
|---|---:|---:|---|---|---|
| KXHORMUZWEEKLY | 12 | 54361.38 | 2026-10-24T01:00:00Z | Politics | Traffic through the Strait of Hormuz |
| KXSUEZWEEKLY | 14 | 41141.61 | 2026-10-27T13:00:00Z | Politics | Traffic through the Suez Canal |
| KXBABELMANDEBWEEKLY | 12 | 34714.26 | 2026-10-24T01:00:00Z | Politics | Traffic through the Bab al-Mandab Strait |
| KXTRUMPUFC | 1 | 22470.13 | 2026-10-25T14:00:00Z | Politics | Trump attend another UFC event this year |
| KXHORMUZMAX | 5 | 16262.27 | 2026-10-20T13:00:00Z | Politics | Maximum traffic through the Strait of Hormuz? |
| KXTRUMPAPPROVE | 6 | 16077.93 | 2026-10-19T16:59:00Z | Politics | Trump approval rating |
| KXTRUMPNOMNUM | 4 | 12568.1 | 2026-10-25T14:00:00Z | Politics | How many nominations will Trump send to the Senate? |
| KXHORMUZPEAK | 4 | 10572.35 | 2026-10-20T13:00:00Z | Politics | Peak traffic through the Strait of Hormuz? |
| KXPANAMAWEEKLY | 8 | 10189.39 | 2026-10-24T01:00:00Z | Politics | Traffic through the Panama Canal |
| KXUSAIRANAGREEMENT | 1 | 9800.74 | 2026-11-01T15:00:00Z | Politics | US Iran nuclear deal |

## Paper filter log

Paper rows and live rows with paper_score run through the favorite
band, the 55c YES floor, the 0-35c executed-ask band, the 8-contract
cap, and the live 0.04 edge floor. No probability was supplied, so
edge is edge_not_evaluated and the Grok veto is veto_not_called.
would_post is false.

| series | ticker | decision | reasons | veto |
|---|---|---|---|---|
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T220 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T210 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T205 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T200 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T195 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T190 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T185 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T180 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T170 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T160 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT25-T150 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T185 | skip | wide_spread,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T250 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T240 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T230 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T220 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T210 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T200 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T190 | skip | ask_outside_favorite_band,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T180 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T170 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T160 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXBABELMANDEBWEEKLY | KXBABELMANDEBWEEKLY-26OCT18-T150 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT25 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT24 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT23 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT22 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT21 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT20 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT25-OCT19 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT18 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT17 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT16 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT15 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT14 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT13 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZMAX | KXHORMUZMAX-26OCT18-OCT12 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T9 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T8 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T7 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T6 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T5 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T4 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T3 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT25-T10 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T3 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T8 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T6 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T5 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T4 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T14 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T12 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZPEAK | KXHORMUZPEAK-26OCT18-T10 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T50 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T40 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T35 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T30 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T25 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T20 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT25-T15 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T22 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T75 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T50 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T45 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T40 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T35 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T30 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T25 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T20 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T15 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T100 | skip | ask_outside_favorite_band,edge_not_evaluated | veto_not_called |
| KXHORMUZWEEKLY | KXHORMUZWEEKLY-26OCT18-T10 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T220 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T210 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T205 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T200 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T195 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T190 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T185 | skip | illiquid_open_interest,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T180 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT25-T170 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T185 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T250 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T240 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T230 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T220 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T210 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T200 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T190 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T180 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXPANAMAWEEKLY | KXPANAMAWEEKLY-26OCT18-T170 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T330 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T320 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T310 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T300 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T290 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T280 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T270 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT25-T260 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T330 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T320 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T310 | skip | ask_outside_favorite_band,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T300 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T290 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T280 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T270 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXSUEZWEEKLY | KXSUEZWEEKLY-26OCT18-T260 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-U38.5 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E39.1 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E39.0 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E38.9 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E38.8 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E38.7 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E38.6 | incomplete_no_probability | edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-E38.5 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPAPPROVE | KXTRUMPAPPROVE-26OCT19-A39.1 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPNOMNUM | KXTRUMPNOMNUM-26OCT18-T40 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPNOMNUM | KXTRUMPNOMNUM-26OCT18-T20 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPNOMNUM | KXTRUMPNOMNUM-26OCT18-T10 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPNOMNUM | KXTRUMPNOMNUM-26OCT18-T1 | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXTRUMPUFC | KXTRUMPUFC-26OCT2-DJT | skip | near_certain_ask,edge_not_evaluated | veto_not_called |
| KXUSAIRANAGREEMENT | KXUSAIRANAGREEMENT-27-26NOV | skip | near_certain_ask,edge_not_evaluated | veto_not_called |

## Settlement

n is settled paper trades with a fee-net pnl. Hit rate is wins over n.
A YES trade hits when resolved_yes is 1. A NO trade hits when
resolved_yes is 0. Fee-net EV is the mean fee_net_pnl_dollars,
printed half-up to 0.0001 dollars. A series with n of 0 has no
fee-net EV. Missing pnl is not recorded as 0.

| series | n | hits | hit_rate | fee_net_ev | note |
|---|---:|---:|---:|---:|---|
| KXTRUTHSOCIAL | 3 | 3 | 1 | 1.3157 |  |
| KXCANUSDEAL | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXMOCTRUMP25 | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXTRUMPACT | 2 | 1 | 0.5 | -3.1216 |  |
| KXTRUMPSAY | 1 | 1 | 1 | 3.832 |  |
| KXTRUMPMENTION | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXAPRPOTUS | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXPOLLPOTUS | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXEFFTARIFF | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXVOTESAVEAMERICA | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXSBUDGETRES | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXLEAVECONGRESS | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXFISAEXTEND | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXHORMUZWEEKLY | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXSUEZWEEKLY | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXBABELMANDEBWEEKLY | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXTRUMPUFC | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXHORMUZMAX | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXTRUMPAPPROVE | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXTRUMPNOMNUM | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXHORMUZPEAK | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXPANAMAWEEKLY | 0 | 0 |  |  | no_settled_fee_net_trades |
| KXUSAIRANAGREEMENT | 0 | 0 |  |  | no_settled_fee_net_trades |

## Promotion rule

A series moves to live after at least 15 settled paper trades with fee-net EV above 0. This report does not change modes.
