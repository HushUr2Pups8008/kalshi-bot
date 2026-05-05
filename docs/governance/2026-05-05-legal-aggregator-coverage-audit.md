# Legal aggregator coverage audit

Date: 2026-05-05
Purpose: size whether the load-bearing path is VitalLaw itself or the upstream aggregator/search route.

## Method

Read-only scan over:

- `mac_archive/macbook_2026-05-01_import/logs/trades`
- `logs/trades/archive/2026/05`

Rows were filtered to legal-ish source/headline terms and event types that participate in the ingestion-to-trade path. Grouping by `_ingestion_path` was attempted using available metadata fields:

- `ingestion_path`
- `ingestion_task_id`
- `source_family`
- `family`
- `feed_family`
- `collector`
- `origin`
- `provider`
- `query`
- `url` / `link` / `source_url` / `feed_url`

## Results

| metric | value |
| --- | ---: |
| legal-ish records | 17,450 |
| VitalLaw records | 77 |
| records with explicit path metadata | 3 |
| records with unknown path metadata | 17,447 |

Legal-ish event mix:

| type | count |
| --- | ---: |
| EARLY_STALE_DROP | 17,134 |
| EARLY_FRESH_PASS | 192 |
| MATCH_DIAGNOSTIC | 96 |
| SIGNAL_ANALYSIS_DETAIL | 19 |
| SIGNAL | 3 |
| OPPORTUNITY | 3 |
| PAPER_TRADE | 3 |

Top legal-ish sources:

| source | records |
| --- | ---: |
| SCOTUSblog | 1,508 |
| Campaign Legal Center | 736 |
| Brennan Center for Justice | 707 |
| The New York Times | 701 |
| World news \| The Guardian | 504 |
| CNN | 478 |
| PBS | 454 |
| Politico | 431 |
| BBC | 408 |
| NPR | 368 |
| NBC News | 324 |
| Just Security | 307 |

VitalLaw path metadata:

| type | count |
| --- | ---: |
| EARLY_STALE_DROP | 42 |
| MATCH_DIAGNOSTIC | 21 |
| SIGNAL_ANALYSIS_DETAIL | 3 |
| SIGNAL | 3 |
| OPPORTUNITY | 3 |
| PAPER_TRADE | 3 |
| EARLY_FRESH_PASS | 2 |

Only the three `SIGNAL` rows carry URL metadata. All three point to the same Google News RSS article URL:

`https://news.google.com/rss/articles/CBMiwAFBVV95cUxOaTgweFlMNXZTckQ5ZVh6RVZnTC12OHJrcHkyWDBxMzdyWGdHa0JZSk1EMU1Qd0tWTTJIWE1pdDJaQXRyNmRBNjByY2w0WUpRaXE1UXdOMXVDWnRwaFRaWHc1OGowS3I1bG5Gekp2Z3EzSmpza3ZodmZia21lWERHd3BBSENoS29mMnF2MXFGWEtxRUFkZUdKci1KU3J3aUh1bUk5S0dmdkdmME5oemVzWjBqMVFxbjRYUWxudzc3OGY?oc=5`

## Readout

The measurable upstream path for the PAPER_TRADE-producing VitalLaw rows is Google News RSS, not direct VitalLaw RSS. Most archived rows do not retain enough ingestion metadata to group cleanly by path, so the audit cannot fully reconstruct coverage by `_ingestion_path`.

Operationally, the aggregator path is load-bearing for the observed VitalLaw trades. Recreating option-B should prioritize a controlled Google News legal query or equivalent aggregator lane if direct VitalLaw RSS remains unavailable.
