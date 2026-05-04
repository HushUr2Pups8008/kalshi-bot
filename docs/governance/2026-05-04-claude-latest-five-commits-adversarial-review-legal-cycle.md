# Adversarial review: latest Claude legal-cycle commits

Date: 2026-05-04
Reviewed range: `3267fe5`, `2892101`, `82d01ac`, `92a9fd1`, `7aa051b`
Scope: legal-class harness, A.1+1.5 legal spec, rehearsal checklist branch, VitalLaw callout, feed-config harness.

## Findings

### F1 - Evidence-scorer weight harness is referenced but not present

Severity: medium

`tests/test_main_pipeline.py:1739-1742` says the sibling `test_evidence_scorer_legal_class_weight` lands in the same hunk. It does not. The reviewed commit set contains classifier strict-xfails and spec text for `legal=0.65`, but no strict-xfail asserting `analysis.evidence_scorer._SOURCE_CLASS_QUALITY["legal"] == 0.65`.

Impact: A.1+1.5 can deploy classifier support while forgetting the scorer tier. The spec promise would be unpinned by tests.

Expected close: add the separate evidence-scorer strict-xfail in `tests/test_evidence_scorer.py`.

### F2 - TLDR v2 is not in the reviewed commit set

Severity: low

The requested EDGE-004 TLDR v2 refresh is not present in the latest five non-Codex commits I reviewed. `docs/governance/edge-004-closure-path-tldr.md` remains outside this commit set.

Impact: operator-facing guidance can lag the A.1+ option-A/B split even though the specs now carry that split.

Expected close: land the TLDR v2 doc update, then re-review that commit specifically.

### F3 - Legal harness covers source labels, not URL-like feed strings

Severity: low

The classifier harness covers `VitalLaw.com`, `vital-law analysis`, `Lawfare`, `Just Security`, `SCOTUSblog`, `Politico Legal`, `Reuters Legal`, and generic `Reuters`. The A.1+1.5 spec also discusses URL/feed identities such as `politico.com/news/legal`, `lawfaremedia.org`, and `justsecurity.org`.

Impact: deploy code could classify pretty source labels correctly while missing raw feed/domain labels if those are what `NewsItem.source` emits.

Expected close: once probe-time exact source strings are known, add one test per exact emitted source string.

## Positive checks

- Focused harness result: `1 passed, 7 xfailed` for `tests/test_main_pipeline.py::TestSourceClassClassifierLeverA1Plus15LegalBranch`.
- Generic Reuters positive control is useful and should remain non-xfail.
- The reviewed spec and feed-config harness are soak-safe documentation/test preloads; no runtime behavior changes found.
