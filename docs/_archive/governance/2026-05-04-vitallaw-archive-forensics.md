# VitalLaw archive forensics

Date: 2026-05-04
Question: when and why was `VitalLaw.com` removed from canonical `config.py:RSS_FEEDS`?

## Findings

### 1. No git evidence that VitalLaw was ever in canonical `config.py:RSS_FEEDS`

I searched current refs and tags with pickaxe/grep for `VitalLaw`, `vital-law`, `vitallaw`, and `vital_law`. Hits begin in May governance/spec/test work plus docs that describe the archive result. I did not find a historical `config.py` RSS entry adding or removing VitalLaw.

Relevant refs available locally:

- `main`
- `origin/main`
- `pre-filter-repo-2026-05-02`
- `v0.29.59`

### 2. Archive records prove VitalLaw was observed, but not via a logged feed URL

Mac archive VitalLaw records:

| type | count |
| --- | ---: |
| EARLY_STALE_DROP | 15 |
| EARLY_FRESH_PASS | 2 |
| MATCH_DIAGNOSTIC | 21 |
| MATCH_SUPPRESSION_CANDIDATE | 18 |
| MATCH_SUPPRESSED | 18 |
| SIGNAL_ANALYSIS_DETAIL | 3 |
| SIGNAL | 3 |
| OPPORTUNITY | 3 |
| PAPER_TRADE | 3 |

The rows carry `source = "VitalLaw.com"` and headlines ending in `- VitalLaw.com`, but no feed URL/source URL field. This is consistent with publisher attribution from an aggregator/search lane, not proof of a direct `RSS_FEEDS` entry.

### 3. Current config notes point to aggregator/search-lane coverage for missing direct feeds

Current `config.py` notes direct Reuters/AP public RSS URLs are dead and says to re-evaluate `feeds/search_news_monitor.py` / `google_news_query` for Reuters/AP coverage. That pattern matches the observed VitalLaw source-string shape: publisher attributed from a discovered headline, not necessarily a configured publisher RSS URL.

## Answer

I do not find evidence that VitalLaw was removed from canonical `config.py:RSS_FEEDS`. The stronger conclusion is: VitalLaw was probably never a canonical direct RSS feed in this repo history; it appears in the Mac archive as an attributed publisher source, likely through aggregator/search ingestion.

## Deployment context

We do not know from local evidence whether VitalLaw is paywall-locked now. The archive proves the bot saw VitalLaw headlines in late April 2026 and traded three FISA markets from them. It does not prove current direct RSS accessibility.

Day-14 probe should test VitalLaw as a fresh external accessibility question, not as a rollback of a known removed config entry.
