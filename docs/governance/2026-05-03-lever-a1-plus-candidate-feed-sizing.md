# Lever A.1+ Candidate Feed-Class Sizing

Archive-visible evidence favors specialist_analyst first: it is the only non-mainstream candidate class with PAPER_TRADE coverage and has materially more OPPORTUNITY surface than government_bulletin. Government bulletins remain strategically useful but did not reach the archive OPPORTUNITY surface often enough to be the highest-ROI first feed class.

| feed class | MATCH_DIAGNOSTIC | OPPORTUNITY | PAPER_TRADE | ANALYSIS_REJECTED | median age sec | p90 age sec |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| government_bulletin | 63 | 1 | 0 | 14 | 2396.01 | 2396.01 |
| specialist_analyst | 251 | 21 | 3 | 112 | 2239.5 | 2414.51 |
| market_microstructure | 0 | 0 | 0 | 0 | None | None |
| mainstream_news | 2524 | 238 | 0 | 1123 | 2232.9049999999997 | 2968.92 |
| unknown | 0 | 0 | 0 | 0 | None | None |

## Examples

### government_bulletin

| type | source | ticker | score | headline |
| --- | --- | --- | ---: | --- |
| MATCH_DIAGNOSTIC | News – The White House | KXMOCTRUMP25-26-MAY01 | 0.1354 | President Donald J. Trump and First Lady Melania Trump to Welcome His Majesty King Charles the III of the United Kingdom of Great Britain and Northern Ireland and Her Majesty Queen Camilla for a State Visit |
| MATCH_DIAGNOSTIC | News – The White House | KXPARDONSTRUMP-26APR-6 | 0.0643 | President Donald J. Trump and First Lady Melania Trump to Welcome His Majesty King Charles the III of the United Kingdom of Great Britain and Northern Ireland and Her Majesty Queen Camilla for a State Visit |
| MATCH_DIAGNOSTIC | News – The White House | KXPARDONSTRUMP-26APR-24 | 0.0655 | President Donald J. Trump and First Lady Melania Trump to Welcome His Majesty King Charles the III of the United Kingdom of Great Britain and Northern Ireland and Her Majesty Queen Camilla for a State Visit |
| MATCH_DIAGNOSTIC | News – The White House | KXPARDONSTRUMP-26APR-22 | 0.0643 | President Donald J. Trump and First Lady Melania Trump to Welcome His Majesty King Charles the III of the United Kingdom of Great Britain and Northern Ireland and Her Majesty Queen Camilla for a State Visit |
| MATCH_DIAGNOSTIC | News – The White House | KXPARDONSTRUMP-26APR-2 | 0.0655 | President Donald J. Trump and First Lady Melania Trump to Welcome His Majesty King Charles the III of the United Kingdom of Great Britain and Northern Ireland and Her Majesty Queen Camilla for a State Visit |

### specialist_analyst

| type | source | ticker | score | headline |
| --- | --- | --- | ---: | --- |
| MATCH_DIAGNOSTIC | Kyiv Post | KXTRUMPIRAN-26MAY01 | 0.0637 | Zelensky Says Iran War Puts Ukraine’s Air Defense Supplies at Risk |
| MATCH_DIAGNOSTIC | Kyiv Post | KXRUCRUDEX-26MAY13-T4.0 | 0.0821 | 20,000 Teddy Bears on National Mall Highlight Ukrainian Children Taken by Russia |
| MATCH_DIAGNOSTIC | Kyiv Post | KXRUCRUDEX-26MAY13-T4.0 | 0.0893 | Russia Calls New EU Sanctions ‘Unlawful’ |
| MATCH_DIAGNOSTIC | Kyiv Post | KXRUCRUDEX-26MAY13-T4.0 | 0.0722 | Putin Silent as Massive Tuapse Oil Terminal Fire Sparks Toxic Smog Across Russia’s Black Sea Coast |
| MATCH_DIAGNOSTIC | Kyiv Post | KXRUCRUDEX-26MAY13-T4.0 | 0.0754 | Russia Says Foiled Bomb Plot Against Telecoms Officials Amid Online Curbs |

### market_microstructure

No archive examples.

### mainstream_news

| type | source | ticker | score | headline |
| --- | --- | --- | ---: | --- |
| MATCH_DIAGNOSTIC | NYT > World News | KXTRUMPIRAN-26MAY01 | 0.0887 | Iran War Live Updates: Hopes for Peace Deal Rise After Iran Says Strait Is Open |
| MATCH_DIAGNOSTIC | NYT > World News | KXTRUMPIRAN-26MAY01 | 0.1006 | Trump Extends Sanctions Exemption on Some Russian Oil as High Gas Prices Persist |
| MATCH_DIAGNOSTIC | Middle East and north Africa | The Guardian | KXTRUMPIRAN-26MAY01 | 0.0784 | Iran says strait of Hormuz ‘completely open’ but sounds warning on US blockade |
| MATCH_DIAGNOSTIC | NYT > World News | KXTRUMPIRAN-26MAY01 | 0.0646 | A Potent Threat in Strait of Hormuz: Iran’s “Mosquito Fleet” |
| MATCH_DIAGNOSTIC | NYT > World News | KXMOCTRUMP25-26-MAY01 | 0.083 | Trump Spat Gives Spain Leader Pedro Sánchez a Political Lifeline |

### unknown

No archive examples.

## Caveat

This is archive-surface sizing, not live internet feed probing. It measures what the existing pipeline saw from source labels that map to each candidate feed class. Live onboarding still needs a per-feed probe for freshness, auth, and request reliability.
