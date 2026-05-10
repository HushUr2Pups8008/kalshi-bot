# Lever A.1+1.5 legal-analyst feed sizing

**Author:** Codex
**Date:** 2026-05-04
**Scope:** pre-load sizing for the legal/regulatory option-B path referenced by `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`

## TL;DR

The legal-niche path is still the highest-upside A.1+ route, but operational friction is real.

Recommended probe order on 2026-05-04:

1. `VitalLaw.com`
2. `Just Security`
3. `Lawfare` (surveillance / courts / rule-of-law topics)
4. `SCOTUSblog`
5. `Politico` legal
6. `Reuters Legal`

Reason:

- `VitalLaw` is the only archive-proven PAPER_TRADE source.
- `Just Security` and `Lawfare` are the best accessible analogues for surveillance / national-security-law / executive-legal coverage.
- `SCOTUSblog` has strong volume but weaker fit to the currently proven Kalshi cluster.
- `Politico` and `Reuters` have higher access friction than the open-law sources.

## 1. Historical proof point from the archive

The entire legal-niche case is anchored by one fact from the Mac archive:

- `VitalLaw.com` produced `3/3` specialist-class `PAPER_TRADE`
- all three landed on `KXFISAEXTEND-26APR-MAY0{1,2,3}`

That means the only archive-proven legal cluster is:

- **surveillance / legislative authority / congressional reauthorization**

Secondary archive-visible but non-converting surfaces from the same source:

- Senate vote / legislative process
- nominations / confirmations
- endorsement-adjacent false positives

So the deploy goal is not generic "legal content". It is **legal/regulatory feeds that still touch headline classes similar to FISA / surveillance / congressional process markets**.

## 2. Live accessibility check on 2026-05-04

| candidate | live surface checked | result | notes |
| --- | --- | --- | --- |
| `VitalLaw.com` | `https://www.vitallaw.com/news/feed` | `200`, but opaque body | returned a Wolters Kluwer landing body, not a usable RSS payload in this check |
| `Just Security` | `https://www.justsecurity.org/feed/` | `200` | open RSS, `10` items visible in current feed |
| `Lawfare` | topic pages + feed index | `200` | topic pages live; category RSS endpoints exposed in subscribe page |
| `SCOTUSblog` | `https://www.scotusblog.com/feed/` | `200` | open RSS, `25` items visible in current feed |
| `Politico` legal RSS | `https://www.politico.com/rss/legal.xml` | `404` | assumed RSS endpoint in spec is not live |
| `Politico` legal page | `https://www.politico.com/news/legal` | `200` | page is live; feed path needs separate discovery |
| `Reuters Legal` | `https://www.reuters.com/legal/` | `401` | direct access friction |

## 3. Current headline-surface read

### `Just Security`

Observed current feed shape:

- `10` visible RSS items
- heavy on national-security law, international law, war / rights framing
- closest open analogue to the `VitalLaw` FISA / surveillance lane

Top visible headlines included:

- `International Crimes and Human Rights Violations Against Muslims in BJP-Ruled Indian States Require Urgent Action`
- `The U.S. Shouldn’t Lose Sight of the Real Terrorist Threats`
- `How the Law of War Can Reckon with Longer-Term Harms of Attacks on Health`

Likely Kalshi-consumable clusters:

- surveillance / privacy / civil-liberties law
- war powers / national-security law
- executive legal authority

### `Lawfare`

Accessible topic pages were active for:

- `Courts & Litigation`
- `Surveillance & Privacy`
- `Criminal Justice & Rule of Law`

This makes `Lawfare` the best non-paywalled multi-cluster candidate. It spans:

- surveillance / privacy
- courts / litigation
- executive-branch legal process
- national-security law

That is broader than `VitalLaw`, but the surveillance topic page makes it a plausible fallback for the proven FISA-style cluster.

### `SCOTUSblog`

Observed current feed shape:

- `25` visible RSS items
- strongest current volume among the open candidates
- concentrated on Supreme Court and judiciary coverage

Top visible headlines included:

- `An abortion pill battle and new redistricting-related lawsuits`
- `Abortion pill dispute returns to Supreme Court`
- `State and federal courts jockey for power in the Roundup case and other mass public harms`

Likely Kalshi-consumable clusters:

- Supreme Court rulings
- judiciary / litigation deadlines
- constitutional disputes

Risk: this is a strong legal feed, but a weaker match to the only archive-proven cluster (`FISA` / legislative surveillance authority).

### `Politico` legal

The legal page is live, but the assumed feed endpoint is not. That makes it a weaker immediate probe despite brand strength.

Likely clusters:

- Supreme Court and judiciary
- federal legal politics
- legal/political crossover stories

### `Reuters Legal`

Direct legal surface returned `401` in this check. Treat as last-resort unless the operator already has a working authenticated path.

## 4. Ranking by deploy usefulness

| rank | candidate | why |
| --- | --- | --- |
| 1 | `VitalLaw.com` | only archive-proven PAPER_TRADE source; exact niche match |
| 2 | `Just Security` | open RSS; strongest accessible overlap with surveillance / national-security-law cluster |
| 3 | `Lawfare` | open topic surfaces; broad legal-policy coverage; surveillance lane present |
| 4 | `SCOTUSblog` | best open volume, but cluster drift toward judiciary-only markets |
| 5 | `Politico` legal | page live, feed path broken, partial paywall friction |
| 6 | `Reuters Legal` | direct surface inaccessible (`401`) |

## 5. Operator decision rule

- If `VitalLaw` can be turned back into a real polling surface, choose it first.
- If `VitalLaw` stays opaque, prefer `Just Security` before `SCOTUSblog`.
- Treat `SCOTUSblog` as the best high-volume fallback when the market mix is court-heavy.
- Do not burn the first legal deploy on `Reuters` unless auth is already solved.

## 6. Honest caveat

This is a real-time accessibility and topical-fit sizing pass, not a 14-day ingestion replay. The legal-niche candidates are current-web surfaces; they cannot be replayed the way the historical `VitalLaw` archive can. So:

- upside estimate is real
- exact lift remains uncertain
- operational friction is the main reason option-B is still only a modal-path candidate, not a guaranteed first deploy

## Cross-links

- `docs/governance/2026-05-04-lever-a1-plus-specialist-analyst-domain-normalized-audit.md`
- `docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md`
