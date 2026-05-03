# Adversarial Review: Claude Commits 4a7cc38 through 48ab655

**Reviewed:** 2026-05-03
**Scope:** `4a7cc38`, `7ff5a8b`, `6e75755`, `fee5003`, `cdbf6ef`, `189c768`, `48ab655`

## Findings

### F1 — Lever E spec understates the blast radius of `min_n = 2`

**Severity:** HIGH
**Commit:** `48ab655`
**File:** `docs/superpowers/specs/2026-05-03-edge-004-lever-e-multi-source-corroboration-design.md:38`, `:48`, `:56`, `:85`, `:97`

The spec frames `min_n = 2` as a conservative/moderate gate and says single-source rate >60% makes Lever E necessary. The archive sizing in `docs/governance/2026-05-03-lever-e-source-corroboration-sizing.md` shows a much sharper result for the spec's actual source-instance mechanism: source-instance distribution is `{0: 9, 1: 251}`, so `N>=2` retains 0/260 OPPORTUNITY records and 0/3 PAPER_TRADE records.

That means the default proposed gate recreates a no-OPPORTUNITY/no-trade state on the full archive, not a moderate corroboration filter. The source-class surface is less destructive (`N>=2` retains 109/260), but the spec explicitly counts distinct `source` values, not source classes.

**Fix:** update the Lever E spec to treat source-instance `min_n = 2` as currently non-deployable unless Lever A materially changes the distribution, or change the mechanism to the source-class surface and size that explicitly.

### F2 — Bothealth harness does not pin `scripts/bothealth.sh`

**Severity:** MEDIUM
**Commit:** `189c768`
**Files:** `scripts/simulations/obs003_skipped_stream_synthesis.py:4`, `:19`; `tests/test_obs003_skipped_stream_synthesis.py:17`

The task was to pre-stage a synthetic JSONL fixture plus a script that invokes `bothealth.sh` against it, with a strict-xfail test asserting the histogram output shape. The landed harness only tests the self-contained Python reference aggregator; the test docstring explicitly says all tests pass today and no xfail markers are present.

That means the post-OBS-003 landing still has no failing contract against the real operator surface. `bothealth.sh` could ignore `SKIPPED_LOG_OVERRIDE`, parse the wrong file, or format the histogram under a different heading and this harness would stay green.

**Fix:** add a strict-xfail test that writes the synthetic fixture under `tmp_path`, invokes `scripts/bothealth.sh` with the override env, and asserts the real output contains the expected "Skipped trades" histogram rows.

### F3 — Lever C spec still contradicts itself on hash version

**Severity:** MEDIUM
**Commit:** `6e75755`
**Files:** `docs/superpowers/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md:73`, `:100`, `:108`, `:125`, `:158`

The spec's empirical recommendation says v1 should ship the §3.2 normalized-string hash because Codex's 49.2% overlap audit used that surface. The implementation checklist still says `_headline_hash` is "per §3.1 v1"; the risk section still reasons about "v1 §3.1 exact-string"; and out-of-scope still says "§3.2 / §3.3 hash functions. Land §3.1 first."

This is not a cosmetic inconsistency. If the implementer follows §5 / §11 instead of §3 / §4, the shipped Lever C will use a stricter raw-exact hash than the audited surface, and the 49.2% sizing estimate no longer applies.

**Fix:** make §3.2 normalized-string the single v1 contract everywhere, or explicitly downgrade the sizing claim to "upper bound; v1 §3.1 needs separate sizing."

### F4 — Snapshot-2 report has a timestamp/delta mismatch

**Severity:** LOW
**Commit:** `4a7cc38`
**File:** `docs/governance/2026-05-03-mid-soak-snapshot-2.md:1`, `:7`, `:15`, `:17`

The report labels itself as a "~6 h delta" from a ~13:30Z baseline to ~19:30Z snapshot, but the elapsed-soak-hours row moves from 44.5 to 48.5, a +4.0 h delta. The health conclusion is probably unaffected because cycle counts and parse-error counts are explicit, but the timebase inconsistency weakens the report as an operator artifact.

**Fix:** either correct the snapshot/baseline timestamps or correct the elapsed-hours row so the delta math is internally consistent.

## Non-Findings / Checks

- `7ff5a8b` Lever A Stage A.1 classifier harness: focused pytest result `1 passed, 6 xfailed`; strict-xfail shape is working.
- `fee5003` follow-up fixes: `ruff check tests/test_executor.py tests/test_governance_monitor.py tests/test_blend_task.py tests/test_main_pipeline.py` passes. `tests/test_governance_monitor.py` focused run returns `4 passed, 5 xfailed`; the reload-isolation fixture now restores env before reloading.
- `cdbf6ef` MATCH-001 B′ addendum now documents the substring-vs-`_tokenize` gotcha directly. Codex's independent tokenization-equivalence audit confirms the warning: substring simulation suppresses 1,076 keys, literal `_tokenize(ticker)` set-diff suppresses 0.
- `189c768` focused quality checks pass (`ruff`, `pytest tests/test_obs003_skipped_stream_synthesis.py -q`), but F2 is a contract-coverage issue rather than a unit-test failure.
