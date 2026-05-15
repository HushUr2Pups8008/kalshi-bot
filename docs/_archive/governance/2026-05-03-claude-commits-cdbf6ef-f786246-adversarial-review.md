# Adversarial Review: Claude Commits cdbf6ef / 189c768 / 4279bd2 / f786246

**Reviewed:** 2026-05-03
**Scope:** `cdbf6ef`, `189c768`, `4279bd2`, `f786246`

## Findings

### F1 — Lever C spec still contains §3.1-vs-§3.2 implementation contradictions

**Severity:** MEDIUM
**Commit:** `f786246` attempted fix for `6e75755`
**File:** `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md:108`, `:125`, `:158`

The top of the spec now correctly marks §3.2 normalized-string hash as v1, but three downstream instructions still point back to §3.1:

- Components touched says `_headline_hash(headline)` is "per §3.1 v1".
- Risk section still discusses "v1 §3.1 exact-string".
- Out-of-scope still says "§3.2 / §3.3 hash functions. Land §3.1 first."

This preserves the original ambiguity at the implementation checklist, which is where the landing engineer is most likely to work from. If implemented literally, production would ship the raw-exact hash while the 49.2% archive sizing was measured against the normalized §3.2 surface.

**Fix:** replace those three remaining §3.1-v1 references with §3.2-v1, and move raw-exact §3.1 to explicit fallback-only language.

### F2 — Bothealth harness still does not invoke `scripts/bothealth.sh` or assert output shape

**Severity:** MEDIUM
**Commit:** `f786246` follow-up to `189c768`
**File:** `tests/test_obs003_skipped_stream_synthesis.py:167`

The original requested contract was a synthetic JSONL fixture plus a script/test that invokes `bothealth.sh` against it and asserts the histogram output shape. The follow-up adds a strict-xfail source-inspection test, which is useful, but it still cannot catch the real operator-path failures: ignoring `SKIPPED_LOG_OVERRIDE`, parsing the wrong file, emitting the wrong section heading, or formatting reason rows incompatibly.

Source inspection proves the future code mentions `SKIPPED`; it does not prove the aggregator works.

**Fix:** keep the source-inspection xfail if desired, but add a second strict-xfail integration-style test that writes the synthetic JSONL under `tmp_path`, invokes `scripts/bothealth.sh` with an override env, and asserts rows for `G1_blended_confidence`, `G6_recency_score`, and `G2_evidence_source_class_diversity`.

### F3 — Lever menu still references closed Lever E in sequencing/risk text

**Severity:** LOW
**Commit:** `4279bd2`
**File:** `docs/superpowers/specs/2026-05-03-edge-004-lever-menu-design.md:73`, `:133`

The Lever E closure itself is clear, but two residual lines still reason over A/B/E as if E can land:

- Lever D verdict: "Only consider D if Levers A + B + E all succeed..."
- Risk: "B + E together may produce a non-linear interaction..."

This is now stale after the same commit closes E as structurally infeasible. It is unlikely to break implementation, but it undermines the menu's role as the current state source of truth.

**Fix:** rewrite those references as A/B/C or remove the E interaction risk entirely.

## Non-Findings / Checks

- `cdbf6ef` MATCH-001 tokenization addendum correctly documents substring semantics and the `_tokenize(ticker)` gotcha. Codex's independent audit in `docs/_archive/governance/2026-05-03-match001-tokenization-equivalence-audit.md` agrees: substring suppresses 1,076 keys; literal `_tokenize(ticker)` set-diff suppresses 0.
- `4279bd2` correctly closes Lever E based on source-instance `{0: 9, 1: 251}` and source-class N>=2 retaining 0 historical paper trades.
- `f786246` fixed the snapshot-2 time-axis mismatch and added a useful bothealth source-inspection xfail, even though F2 above remains.
