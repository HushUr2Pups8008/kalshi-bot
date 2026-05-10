# VitalLaw direct-RSS endpoint probe

Date: 2026-05-05
Purpose: determine whether the direct VitalLaw RSS branch is feasible today.

## Result

No probed VitalLaw/Wolters Kluwer endpoint returned feed-like RSS/Atom/XML.

| URL | result |
| --- | --- |
| `https://www.vitallaw.com/feed` | 200 HTML, redirects to Wolters Kluwer SSO; not feed-like |
| `https://www.vitallaw.com/rss` | 200 HTML, title `RSS Error`; not feed-like |
| `https://www.vitallaw.com/rss.xml` | 403 HTML; not feed-like |
| `https://www.vitallaw.com/atom.xml` | 403 HTML; not feed-like |
| `https://www.vitallaw.com/news/feed` | 200 HTML, redirects to Wolters Kluwer SSO; not feed-like |
| `https://www.vitallaw.com/news/rss` | 200 HTML, redirects to Wolters Kluwer SSO; not feed-like |
| `https://vitallaw.com/feed` | 200 HTML, redirects to Wolters Kluwer SSO; not feed-like |
| `https://vitallaw.com/rss` | 200 HTML, title `RSS Error`; not feed-like |
| `https://wkproductions.cch.com/news/feed` | connection failed |
| `https://wkproductions.cch.com/news/rss` | connection failed |
| `https://www.wolterskluwer.com/en/news/rss` | 404 HTML; not feed-like |
| `https://www.wolterskluwer.com/en/expert-insights/rss` | 404 HTML; not feed-like |

## Operator read

Direct VitalLaw RSS is not currently supported by the obvious endpoint set. Treat the direct-RSS branch as fail-fast at deploy time. The realistic option-B path is either:

- re-enable or add the upstream aggregator/search path that surfaced VitalLaw in the Mac archive, or
- fall through to open-RSS legal analogues.

## Caveat

This was an unauthenticated probe. A private Wolters Kluwer/VitalLaw account feed could exist, but it is not discoverable from these public endpoints.
