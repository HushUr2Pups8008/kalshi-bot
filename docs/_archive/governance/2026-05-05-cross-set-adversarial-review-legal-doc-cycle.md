# Cross-set adversarial review: legal doc cycle

Date: 2026-05-05
Reviewed commits:

- Claude: `3267fe5`, `0e1d5cd`, `05e14f2`, `3f42595`, `0b4d38e`, `75a3e39`
- Codex: `46ec00b`, `df92f79`, `2575c16`, `25478e4`, `cd4c4bc`

## Findings

### F1 - A.1+1.5 docs still assert a removed-RSS history that later forensics rejects

Severity: medium

`docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md:18` says VitalLaw was "silently removed from canonical config". The same file repeats that as a paywall-removal theory at line 153. `docs/governance/edge-004-closure-path-tldr.md:21` says the bot "lost" the load-bearing source.

That conflicts with `docs/governance/2026-05-04-vitallaw-archive-forensics.md:43`, which says there is no evidence VitalLaw was ever canonical direct RSS and that archive records point to attributed aggregator/search ingestion.

Impact: the Day-14 operator decision can frame option-B as a rollback/re-onboard when the evidence supports a fresh 3-way probe: direct VitalLaw RSS, Google/aggregator path, or open-RSS analogues.

Expected close: Claude task #1 should rewrite the spec, TLDR, and rehearsal checklist around the 3-way branch.

### F2 - Codex interim review is stale after the same commit cycle

Severity: low

`docs/governance/2026-05-04-claude-latest-five-commits-adversarial-review-legal-cycle.md:13` flags the missing legal scorer harness. `0e1d5cd` later adds `tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py`, so the finding is audit-history only. The commit message notes this, but the doc body does not.

Impact: a reader scanning docs rather than commit messages can treat a closed issue as still open.

Expected close: append "closed by 0e1d5cd / 3f42595" status notes or supersede the interim review with this cross-set review.

### F3 - Direct-RSS feasibility is now lower than the option-B docs imply

Severity: low

The direct probe in `docs/governance/2026-05-05-vitallaw-direct-rss-probe.md` found no feed-like VitalLaw endpoint. Candidate VitalLaw paths redirect to Wolters Kluwer SSO, return RSS Error HTML, 403, or 404.

Impact: option-B should treat direct VitalLaw RSS as a quick fail-fast probe, not the expected path.

Expected close: prefer the aggregator path or open-RSS legal analogues unless a real feed URL is found.

## Positive checks

- `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1Plus15LegalBranch`: `1 passed, 7 xfailed`.
- `tests/test_lever_a1plus1_5_evidence_scorer_legal_weight.py`: `1 passed, 1 skipped, 1 xfailed`.
- `05e14f2` fixed the `official=0.75` spec error to the actual `news=0.70` interval framing.
- Day-7 and Wave-1 changelog docs are documentation-only and do not bump `VERSION` or edit `CHANGELOG.md`.
