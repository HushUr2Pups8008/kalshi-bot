# EDGE-004 TLDR doc-quality review

Date: 2026-05-05
Doc reviewed: `docs/governance/edge-004-closure-path-tldr.md`

## Findings

### F1 - Title says v2 while section says v2.1

Severity: low

The document title remains `v2 (2026-05-04 refresh)`, while the decision section is `REVISED v2.1`. Operator-facing docs should expose one version signal.

Expected close: rename title/status to v2.1 or remove version numbering from the title.

### F2 - Legacy option-A/option-B table competes with the current branch table

Severity: low

The current branch table is the right operator surface. The legacy option-A/option-B table is useful for harness naming, but it repeats older decision language and makes the page feel like two decision frameworks.

Expected close: keep only branch table in the main body; move the harness-naming compatibility note to a short footnote.

### F3 - Default recommendation should name the actual next action

Severity: low

The current recommendation still says "option-B first." After the simplification, the actual next action is Branch A: observe the already-active Google News path for 14 d after Wave-1 close, then Branch C if no legal-niche PAPER_TRADE surfaces.

Expected close: replace "option-B first" with "Branch A observe first; Branch C deploy if A produces 0 legal-niche PAPER_TRADE in 14 d."

## Positive checks

- Cross-links are comprehensive: lever menu, per-source audit, aggregator-path forensics, direct-RSS probe, Wave forecasts, rehearsal checklist, rollback runbook, and escalation paths are all represented.
- The branch table is compact and scannable.
- The "Honest read" section is useful; it gives the operator a realistic modal path rather than over-selling EDGE-004 closure.

## Operator read

The TLDR is close. One cleanup pass should remove version ambiguity and legacy decision-table friction.
