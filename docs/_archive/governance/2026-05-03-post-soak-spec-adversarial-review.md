# Post-Soak Spec Adversarial Review

Scope: read-only review of the four pre-loaded post-soak implementation specs:

- `docs/superpowers/specs/2026-05-03-exec-002-series-correlation-guard-design.md`
- `docs/superpowers/specs/2026-05-03-obs-003-blendtask-skipped-emission-design.md`
- `docs/superpowers/specs/2026-05-03-obs-005-cooldown-sentinel-fix-design.md`
- `docs/superpowers/specs/2026-05-03-match-001-token-guard-refinement-design.md`

## Executive Read

The specs are directionally sound, but two need tightening before code lands:

1. `OBS-003` should not invent a new BlendTask SKIPPED payload by hand without first proving the logger API can accept the executor-shaped payload. The design currently says "compose the same payload shape" but does not pin the exact `utils.logger.TradeLog.log_skipped` signature or truncation/normalization behavior.
2. `EXEC-002` has a larger architecture gap: the proposed seed-from-DB step names dependencies BlendTask does not have. Current `BlendTask.__init__` has `EvidenceStoreLike`, `BlendDecisionLogger`, blender, readiness evaluator, resolver, and calibration; no paper-trader or paper-trades DB handle is available. Landing the seed step as written would either widen BlendTask ownership or skip restart protection.
3. `MATCH-001 B'` is the highest false-negative risk among the four. It assumes canonical event tokens like `witkoff`, `kushner`, and `talks` survive the existing tokenizer and overlap logic; this must be proven by archive replay and canonical harness output in the same commit that changes the predicate.

## Findings

### F1: EXEC-002 seed-from-DB is under-specified and crosses BlendTask ownership

Severity: high for implementation readiness.

The spec proposes `_seed_series_enqueues_from_db()` inside `BlendTask`, analogous to executor cooldown seeding. Current `BlendTask.__init__` has no paper-trader, portfolio, or DB dependency. Its existing persisted read side is `EvidenceStoreLike`, which exposes dossiers, structural priors, and recent evidence only. Adding paper-trade DB reads there would create a new dependency direction from blend orchestration into execution persistence.

Required pre-landing decision:

- Either drop restart seeding from the first EXEC-002 patch and document restart reset as accepted risk.
- Or add an explicit injected `SeriesTradeHistoryLike` protocol with one narrow async method such as `recent_series_enqueues(window_seconds) -> dict[str, datetime]`.
- Or move series-correlation enforcement into the executor, where `_last_traded` and paper-trade seeding patterns already exist.

Recommendation: do not let the implementation improvise this. Amend the spec before coding.

### F2: EXEC-002 `_series_prefix = ticker.split("-")[0]` is a brittle grouping rule

Severity: medium.

Splitting on the first hyphen works for the cited examples (`KXFISAEXTEND`, `KXTRUMPIRAN`, `KXMOCTRUMP25`), but it treats every market under a broad prefix as one correlated bucket. That is appropriate for date-ladder variants but may over-suppress prefixes with semantically different contracts under one series stem.

Required proof:

- Replay full archive tickers and list every prefix with more than one distinct market title.
- Add a test where same prefix but clearly different market titles are not grouped unless their close-date ladder shape matches.
- If the helper remains prefix-only, rollback criteria should include prefix false positives discovered by the replay, not only post-deploy zero-trade symptoms.

### F3: OBS-003 SKIPPED payload shape needs a logger-contract test before BlendTask tests

Severity: medium.

The spec says `_emit_skipped` should match executor SKIPPED shape, but `BlendDecisionLogger` currently only defines `log_blend_decision`. The implementation will need either a widened logger protocol or direct use of module-level `trade_log.log_skipped`. That is a testability and dependency-injection choice, not a detail.

Required tests before source edit:

- A logger double that implements both `log_blend_decision` and `log_skipped`.
- Assertion that BlendTask calls `write_trade_log_async(logger.log_skipped, ...)`, not `trade_log.log_skipped` directly, so tests and production use the same injection point.
- One payload-contract test that compares BlendTask SKIPPED keys against executor SKIPPED keys, allowing only documented differences in `reason`, `model_probability`, and `edge` source.

### F4: OBS-003 "tests/test_governance_monitor.py" is the wrong monitoring target

Severity: low-medium.

`scripts/governance_monitor.py` reads `logs/governance/decisions.jsonl`, not trade logs. The OBS-003 SKIPPED volume change affects `logs/trades/`. The spec's suggested `tests/test_governance_monitor.py` change will not catch double-counting or skip-histogram changes for trade-log consumers.

Better target:

- Add or extend tests around `scripts/trade_log_summary.py`, `scripts/botcheck.py`/`scripts/bothealth.sh` if testable, or a small trade-log summarizer fixture that includes both BLEND_DECISION and SKIPPED for the same blocked candidate.

### F5: OBS-005 rollback trigger is backwards

Severity: medium.

The spec says revert if a production cycle logs cooldown on a ticker with no paper-trade DB row. That is the existing bug, not a likely failure mode of the `float("-inf")` fix. The more plausible bad fix is disabling cooldown too broadly, causing repeated same-ticker trades inside the cooldown window.

Correct rollback trigger:

- Any ticker with two PAPER_TRADE records inside `paper_ticker_cooldown` after the patch, excluding explicit operator override.
- Any test showing a populated finite `_last_traded[ticker]` fails to trip cooldown.

### F6: OBS-005 live-mode test may require more than a sentinel setup

Severity: low.

The live path usually has additional safety gates before cooldown, including balance and live-mode halts. A direct `execute()` integration test may fail before reaching live cooldown. The spec should permit a narrower `_validate()`-level test or dependency stubbing that isolates live cooldown without requiring live execution plumbing.

### F7: MATCH-001 B' assumes tokenization support that the spec does not prove

Severity: high for false-negative risk.

The spec's safety case depends on non-ticker support tokens such as `witkoff`, `kushner`, `pakistan`, and `talks`. The current matcher has several token filters and quality flags. If any canonical event reduces to only `{trump, iran}` after the real pipeline, B' will suppress a legitimate match.

Required proof:

- Before editing `analysis/market_matcher.py`, run `scripts/simulations/match_score_audit.py --json` and persist the canonical events' matched tokens, not just top-3 tickers and scores.
- Add tests directly against the actual `find_candidates` path, not a pure predicate helper only.

### F8: MATCH-001 archive replay acceptance band is too loose without an opportunity-retention floor

Severity: medium.

The spec accepts 600-1,300 additional suppressions and zero canonical ticker flips. That does not protect the historical OPPORTUNITY surface. A threshold/predicate change could preserve the five canonical probes while dropping a high share of the 260 OPPORTUNITY-producing archive matches.

Required acceptance addition:

- Report retention of historical OPPORTUNITY-producing `(ticker, headline, source)` matches under the new predicate.
- Set an explicit floor, for example retain at least 95% of historical OPPORTUNITY-linked MATCH_DIAGNOSTIC rows unless the dropped rows are manually classified as off-topic.

### F9: Landing order should put OBS-005 before EXEC-002 if implementation is cheap and isolated

Severity: low.

OBS-005 is a two-line executor sentinel fix with direct tests and no dependency on OBS-003. EXEC-002 is a new risk gate with state and prefix semantics. If the post-soak goal is minimum-risk unblock sequencing, OBS-005 can land before EXEC-002 without waiting on OBS-003. The current order still makes sense for audit narrative, but not for risk minimization.

## Recommended Spec Edits Before Coding

1. Amend EXEC-002 to choose a concrete restart-state design. Do not leave "read from injected EvidenceStoreLike or PaperTrader" as an implementation-time choice.
2. Amend OBS-003 to define the logger injection contract and move monitoring tests from governance monitor to trade-log consumers.
3. Amend OBS-005 rollback criteria to guard against repeated same-ticker trades, not continued cooldown false positives only.
4. Amend MATCH-001 to require historical OPPORTUNITY retention and canonical matched-token evidence in the archive replay.

## Go / No-Go

- OBS-005: go after rollback criteria correction.
- OBS-003: go after logger-contract clarification.
- MATCH-001 B': no-go until archive replay includes OPPORTUNITY retention and matched-token evidence.
- EXEC-002: no-go until restart-state ownership and prefix grouping semantics are resolved.
