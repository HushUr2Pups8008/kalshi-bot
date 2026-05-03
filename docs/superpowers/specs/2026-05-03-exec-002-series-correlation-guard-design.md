# EXEC-002 — Series-prefix correlation guard

**Status:** design (post-soak implementation; do not land before PROFIT-PHASE2-001 closes ≥ 2026-05-09)
**Tracker:** `PROFIT-EXEC-002` in `docs/profit_path_debt_log.md`
**Owner:** Claude
**Severity:** MEDIUM (correctness-of-risk-management; not a hard safety bug)
**Dependencies:** **soft** dependency on `PROFIT-OBS-003` BlendTask SKIPPED-emission landing first (so the new `series_correlation_in_window` reason flows cleanly through the SKIPPED stream rather than only into BLEND_DECISION). Sequencing: OBS-003 lands before EXEC-002.

## 1. Problem

Existing same-signal guards in `tasks/trade_readiness_gate.py` and `trading/executor.py` operate per-ticker. They do not detect that multiple *different* tickers in the same series prefix (or with overlapping resolution conditions) are economically correlated and therefore should not all be traded on the same news event.

Empirical demonstration (the only paper trades to date):

- 2026-05-01 01:57:33Z – 01:57:40Z (7-second window)
- 3 paper trades on `KXFISAEXTEND-26APR-MAY01`, `-MAY02`, `-MAY03`
- Same headline (`"After House Reauthorizes Surveillance Law, Senate Punts (Apr 30, 2026) - VitalLaw.com"`)
- Same LLM verdict (`direction=no, magnitude=small, confidence=0.85, P(YES)=0.432`)
- Same size (5 contracts NO @ 50¢)
- All resolved YES at 03:31Z (same hour) → all lost
- Aggregate paper-bankroll impact: −$7.50 vs −$2.50 if a single canonical trade had been placed

The 3 markets are economically the same bet at adjacent resolution boundaries. The per-ticker `MAX_TICKER_EXPOSURE_PCT=0.25` cap does not constrain correlated-series exposure: with N date-based markets in one series the bot can deploy `25N%` of bankroll on one underlying conviction.

Secondary impact — **source-credibility distortion**. The 0/3 W/L outcome dropped VitalLaw.com to 0.5× multiplier. One losing prediction was counted as three losing predictions; the credibility scorer over-penalized a source on a single conviction.

## 2. The fix (Option 1 — series-prefix dedupe within window)

When BlendTask is about to enqueue a candidate, check whether another candidate from the same series prefix has been enqueued in the last `series_correlation_window` (default 1 h). If so, mark the current candidate as `trade_blocked_reason="series_correlation_in_window"` and route through the BlendTask SKIPPED-emission path (the OBS-003 fix). The first candidate in a series-prefix burst still trades; subsequent ones within the window are suppressed.

Pseudo-flow:

```
process_fast_lane_result(fast_lane_result):
    ...                                       # blender + readiness gate, unchanged
    blocked_reason = blend_result.trade_blocked_reason or readiness.trade_blocked_reason

    if blocked_reason is None:
        series_prefix = _series_prefix(ticker)            # NEW
        last_enqueued_ts = self._recent_series_enqueues.get(series_prefix)  # NEW
        now = time.monotonic()
        if last_enqueued_ts is not None and now - last_enqueued_ts < cfg.series_correlation_window_seconds:
            blocked_reason = "series_correlation_in_window"   # NEW
        else:
            self._recent_series_enqueues[series_prefix] = now    # NEW (only set on enqueue)

    emit BLEND_DECISION (always, with blocked_reason)
    if blocked_reason:
        emit SKIPPED (per OBS-003) with reason=blocked_reason
        return BlendTaskResult(enqueued=False, ...)
    else:
        enqueue candidate; return BlendTaskResult(enqueued=True, ...)
```

The `_recent_series_enqueues: dict[str, float]` tracker is an in-memory BlendTask instance attribute, populated only when a candidate successfully enqueues (mirrors the executor's existing `_last_traded` pattern at `trading/executor.py:39`).

## 3. Components touched

- `tasks/blend_task.py`:
  - New instance attribute `self._recent_series_enqueues: dict[str, float]` initialized in `__init__`.
  - New helper `_series_prefix(ticker: str) -> str` — strips the trailing date / variant suffix from a Kalshi ticker. Examples: `KXFISAEXTEND-26APR-MAY01 → KXFISAEXTEND`, `KXMOCTRUMP25-26-APR24 → KXMOCTRUMP25`, `KXTRUMPIRAN-26MAY01 → KXTRUMPIRAN`. Implementation: split on `-`, take the first component.
  - Series-prefix check inserted between the readiness-gate evaluation (line ~180) and the `_emit_blend_decision` call (line ~195). Sets `blocked_reason = "series_correlation_in_window"` if the series-prefix burst is in-window.
  - On successful enqueue (the `enqueued=True` branch at line ~221), update `self._recent_series_enqueues[series_prefix] = time.monotonic()`.
- `config.py`:
  - New `SERIES_CORRELATION_WINDOW_SECONDS` env-var-driven config knob, default 3600 (1 h). Loaded into `cfg.series_correlation_window_seconds` like other timing configs.
- `tests/test_blend_task.py`:
  - 2026-05-01 FISA replay: 3 synthetic candidates with the same `KXFISAEXTEND` series prefix arriving in <10s; assert exactly 1 enqueues, 2 emit `series_correlation_in_window` SKIPPED records.
  - Cross-series non-interference: 2 candidates with different series prefixes (e.g. `KXFISAEXTEND-…` and `KXTRUMPIRAN-…`) arriving in <10s; assert both enqueue.
  - Window-expiry: same series prefix, second candidate arriving >window seconds later; assert second candidate enqueues.
  - Window override: `cfg.series_correlation_window_seconds=0` disables the guard; assert all candidates enqueue.

No source change to `trading/executor.py` — its existing per-ticker cooldown / opposing-position / concentration guards remain unchanged. The series-correlation guard sits in BlendTask, *upstream* of the executor.

## 4. Why BlendTask, not the executor or readiness gate

Three placement options were considered:

1. **In `evaluate_readiness` as a new G7 predicate.** Cleanest from a "readiness gate is the single point of pre-trade evaluation" perspective. But `evaluate_readiness` is stateless (`tasks/trade_readiness_gate.py:139` — pure function over `blend_result + regime_confidence`), and the series-correlation check requires state (recent enqueues). Adding state to a stateless function violates the module's contract.
2. **In BlendTask, before enqueue (this design).** Stateful BlendTask instance already exists; the recent-enqueues tracker is a natural fit alongside the existing context-loading state. The series-correlation check is a "gate" but it's a stateful one tied to the BlendTask's enqueue history.
3. **In the executor's `_validate()`.** Requires the executor to read the trading queue's contents, which violates the executor's "I evaluate one candidate at a time, statelessly except for `_last_traded`" contract.

Option 2 is the right fit. BlendTask is already the boundary that decides "this candidate proceeds to executor or doesn't"; adding a stateful series-correlation check there matches the existing responsibility shape.

## 5. Why OBS-003 must land first

Without the OBS-003 BlendTask SKIPPED-emission fix, `trade_blocked_reason="series_correlation_in_window"` would only appear in BLEND_DECISION records, not in the SKIPPED stream. Audit consumers keying off SKIPPED would silently miss the new gate firing. The OBS-003 fix routes every BlendTask blocked-reason through SKIPPED with the reason string preserved — which means EXEC-002's new reason value flows automatically once OBS-003 is in place.

If the implementation order ever inverts (EXEC-002 lands before OBS-003), the EXEC-002 fix is still functionally correct but accounting is murkier — the new gate fires silently in the SKIPPED stream and only shows up in BLEND_DECISION. Acceptable but not preferred.

## 6. Risk

**Trade volume reduction on series-burst hot news.** The fix deliberately suppresses correlated trades on a single news event. Empirical: the 2026-05-01 case would have produced 1 trade instead of 3 (saving $5 on the loss; would have saved less on a win). Going forward, on any series-burst headline the bot will trade exactly once. Expected EV impact is *positive* on average (correlated trades are over-sized risk relative to underlying conviction; right-sizing improves Kelly fidelity).

**Restart resets the tracker.** `_recent_series_enqueues` is in-memory; bot restart clears it. Within the first `series_correlation_window` after restart, a series-prefix burst from a still-cached headline could re-trigger before the in-memory state has caught up. Mitigation: seed the tracker from `paper_trades.db.series_ticker` on construction, mirroring `_seed_cooldowns_from_db` at `trading/executor.py:82`. The seeding query: for each series prefix in `paper_trades` with `ts` newer than `now - window`, set `_recent_series_enqueues[series_prefix] = now_monotonic - age_in_seconds`. Same pattern as the executor's existing seeding.

**Window calibration.** Default 1 h is operator-tunable via env var. The 2026-05-01 FISA case had a 7-second placement window; any window ≥ a few minutes catches it. 1 h is conservative — suppresses any series-correlated trade within a typical news cycle. Operator can tighten if needed.

**Soak invariant:** the change adds a new gate to the decision path. Solidly under the `decision consistency = high-risk during soak` rule. Post-soak landing required.

**Backward compatibility:** the new env var `SERIES_CORRELATION_WINDOW_SECONDS` defaults to enabled (3600). Setting it to `0` disables the guard, restoring pre-fix behaviour. Operator escape hatch.

## 7. Implementation plan

1. **Source change** (`tasks/blend_task.py`):
   - `__init__`: initialize `self._recent_series_enqueues: dict[str, float] = {}`.
   - Add `_series_prefix(ticker: str) -> str` helper (private static method).
   - Add `_seed_series_enqueues_from_db()` constructor helper (analogous to executor's `_seed_cooldowns_from_db`); read from the injected `EvidenceStoreLike` or `PaperTrader` portfolio for recent series-ticker activity. Seed within constructor.
   - Insert the series-correlation check between readiness evaluation and `_emit_blend_decision`. Set `blocked_reason = "series_correlation_in_window"` when in-window.
   - On successful enqueue, update `self._recent_series_enqueues[series_prefix] = time.monotonic()`.
2. **Config change** (`config.py`):
   - New `SERIES_CORRELATION_WINDOW_SECONDS` env var; load into `cfg.series_correlation_window_seconds`. Default 3600.
   - Document in `.env.example` if that file exists in the repo (verify during implementation).
3. **Test additions** (`tests/test_blend_task.py`): the 4 cases in §3 above. Plus:
   - `test_series_prefix_extraction` — unit test for the `_series_prefix` helper across the canonical Kalshi ticker shapes (KXFISAEXTEND-…, KXMOCTRUMP25-…, KXTRUMPIRAN-…, KXSBUDGETRES-…, KXVANCEPAKISTAN-…).
   - `test_seed_series_enqueues_from_db` — pre-populate `paper_trades.db` with a recent FISA trade; construct BlendTask; assert `_recent_series_enqueues` carries the seeded series.
4. **Documentation**: update the SKIPPED schema's `reason` enum docstring (per the OBS-003 spec's §7 step 3) to include `series_correlation_in_window` as a recognized BlendTask-side reason.
5. **Closure**:
   - Update PROFIT-EXEC-002 entry: status OPEN → COMPLETE, citing the BlendTask guard + 6 new tests + 24h post-deploy verification.
   - Top-of-file counters: Open MEDIUM 1 → 0 (assuming MATCH-001 also closes); Items COMPLETE += 1.

## 8. Acceptance criteria

- The 2026-05-01 FISA replay test asserts exactly 1 of 3 same-series candidates enqueues; the other 2 emit `series_correlation_in_window` SKIPPED records (assumes OBS-003 SKIPPED-emission fix has landed).
- Cross-series, window-expiry, and window-override tests all pass.
- 24-hour post-deploy audit confirms no series-prefix burst produced more than 1 trade. The audit query: `SELECT series_ticker, COUNT(*), MIN(ts), MAX(ts) FROM paper_trades WHERE ts >= datetime('now', '-1 day') GROUP BY series_ticker HAVING COUNT(*) > 1 AND (MAX(ts) - MIN(ts)) < <window>;`. Expected: zero rows.
- `cfg.series_correlation_window_seconds` env-var override works (operator can disable via `=0`).
- Full pytest suite green.

## 9. Rollback

Revert is the BlendTask diff + config diff. Trivial.

Operator-side fast revert: set `SERIES_CORRELATION_WINDOW_SECONDS=0` in the env, restart bot. Disables the guard without code change.

Trigger to revert: post-deploy 24-hour audit shows the bot is producing zero trades AND the BLEND_DECISION stream shows `series_correlation_in_window` firing on candidates that should have been independent (i.e., the `_series_prefix` extraction is too aggressive and is grouping unrelated tickers). That's a `_series_prefix` bug; revert + fix the helper.

## 10. Soak-window contract

This spec is pre-loaded during PROFIT-PHASE2-001 soak (drafted 2026-05-03; do not implement before 2026-05-09 organic close or 2026-05-16 hard ceiling). The change adds a new gate to the decision path; clear "decision consistency = high-risk" violation if landed mid-soak. Post-soak landing required, and per §5 should land *after* OBS-003 for cleaner SKIPPED accounting.

## 11. Out of scope

- **Approach 2 (headline-hash dedupe across series)** — defer until EDGE-004's matcher-quality work quantifies the cross-series-headline overlap rate. Filed as a future follow-up if Approach 1's empirical impact is insufficient.
- **Retroactive source-credibility fairness adjustment** for VitalLaw.com (0.5× → ~0.83× to reflect 1 losing prediction with 1 degree of freedom) — separate concern, track independently if pursued.
- **`MAX_TICKER_EXPOSURE_PCT` extension to series-level cap** — this fix prevents the 3-trade burst entirely, so the per-series cap question becomes moot. If a future operator wants series-level exposure caps for other reasons (e.g., cross-source correlation rather than same-source), file as a separate entry.
- **Cross-series-single-headline correlation** (e.g., one Trump headline firing on KXTRUMPIRAN, KXMOCTRUMP25, KXPARDONSTRUMP simultaneously) — Approach 2 territory. Out of scope for this entry; revisit post-EDGE-004.
