# OBS-005 — Cooldown gate sentinel-default fix

**Status:** design (post-soak implementation; do not land before PROFIT-PHASE2-001 closes ≥ 2026-05-09)
**Tracker:** `PROFIT-OBS-005` in `docs/profit_path_debt_log.md`
**Owner:** Claude
**Severity:** LOW–MEDIUM
**Dependencies:** none

## 1. Problem

`trading/executor.py:208`:

```python
last    = self._last_traded.get(analysis.market.ticker, 0.0)
elapsed = time.monotonic() - last
if elapsed < cfg.paper_ticker_cooldown:
    return f"paper cooldown: last trade {elapsed/3600:.1f}h ago ..."
```

`_last_traded.get(ticker, 0.0)` returns `0.0` for any never-traded ticker. On a freshly-booted machine, `time.monotonic()` is seconds-since-boot (low). `elapsed = monotonic - 0.0 ≈ low`, which is `< paper_ticker_cooldown = 14_400`, so the gate trips — even on tickers the bot has never touched. The bug is silent on long-uptime hosts because `time.monotonic()` quickly exceeds 14 400.

**Empirical impact (per the existing entry):** 14 CI tests fail on a freshly-booted GitLab runner; production cycles past the first ~4 h of uptime are unaffected (mitigated currently by a `CI=1` env-var stub at `tests/conftest.py`).

## 2. The fix

Single-line change: replace the sentinel default with `float("-inf")`. Never-traded tickers then report infinite elapsed time, so the cooldown gate is bypassed.

```python
# trading/executor.py:208 — current
- last    = self._last_traded.get(analysis.market.ticker, 0.0)
+ last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
```

`time.monotonic() - float("-inf")` evaluates to `+inf`, which trivially clears `< 14_400`, so the gate falls through. All other paths (a real `last` value from a real trade) are unchanged.

## 3. Components touched

Single file: `trading/executor.py`. One line.

The same sentinel pattern applies to the live-mode cooldown gate at line ~276:

```python
# trading/executor.py:276 — current
- last    = self._last_traded.get(analysis.market.ticker, 0.0)
+ last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
```

So the fix is one line at line 208 + one line at line 276. Two lines total in one file.

## 4. Data flow

No flow change. Cooldown gate evaluation:

- Before fix: never-traded ticker on fresh boot → false-positive `paper cooldown` SKIPPED.
- After fix: never-traded ticker on fresh boot → falls through cooldown gate; subsequent gates (price sanity, opposing-position guard, concentration, etc.) evaluate normally.

## 5. Risk

**Production behaviour change is bounded to one decision branch.** The fix activates the path that was previously broken (never-traded-ticker on fresh boot). Tickers that *have* been traded retain the existing behaviour. A real cooldown trip on a real previously-traded ticker still fires correctly because `_last_traded[ticker]` will be a finite `time.monotonic()` value, not the sentinel.

**Soak invariant:** the fix changes the executor's decision branch on a boundary case (never-traded ticker, fresh-process). During the active Phase 2 soak the bot has been running continuously since 2026-05-02 04:12 UTC, so `time.monotonic()` is well past 14 400 — i.e. the bug is currently silent in production. Landing the fix mid-soak would have zero observable effect on the running cycle but still violates the `decision consistency = high-risk during soak` rule out of caution. Post-soak landing is the right cadence.

**Schema / migration:** none. No persisted state touched.

**Backward compatibility:** trivial. The fix unblocks the false-positive case and changes nothing else.

## 6. Implementation plan

Single-PR-equivalent change:

1. **Source change** (`trading/executor.py`):
   - Line 208 (`paper_ticker_cooldown` gate): default sentinel `0.0` → `float("-inf")`.
   - Line 276 (`live_ticker_cooldown` gate): same change.
2. **Test additions** (`tests/test_executor.py`):
   - `test_paper_cooldown_does_not_trip_on_never_traded_ticker_after_fresh_boot` — construct a `TradeExecutor` against a paper-trader stub with empty `_last_traded` (simulate fresh boot); call `execute()` against a candidate; assert the result is *not* a `paper cooldown` skip. The stub should keep `time.monotonic()` low — the test passes on the fix but fails on the current sentinel.
   - `test_paper_cooldown_still_trips_on_recently_traded_ticker` — pre-populate `_last_traded[ticker]` with a recent monotonic timestamp; assert the gate fires correctly. (Regression guard against an over-eager fix that broke the gate entirely.)
   - Mirror both for the live path: `test_live_cooldown_does_not_trip_on_never_traded_ticker_after_fresh_boot` + `test_live_cooldown_still_trips_on_recently_traded_ticker`.
3. **CI stub revert** (`tests/conftest.py:_ci_stub_env`):
   - Remove `PAPER_TICKER_COOLDOWN=0` and `LIVE_TICKER_COOLDOWN=0` env stubs once the executor fix lands. The stubs were CI-only mitigation; they're no longer needed once the executor handles fresh-boot correctly. Document the revert in the same commit's body.
4. **Closure**:
   - Update PROFIT-OBS-005 entry: status OPEN → COMPLETE, citing the executor fix + 4 new tests + CI stub revert.
   - Top-of-file counters: Open LOW 2 → 1; Items COMPLETE += 1.

## 7. Acceptance criteria

- `trading/executor.py:208` (paper) and `trading/executor.py:276` (live) both default to `float("-inf")` for the never-traded sentinel.
- 4 new tests in `tests/test_executor.py` cover the fresh-boot-no-cooldown + cooldown-still-trips matrix for both paper and live paths.
- `tests/conftest.py:_ci_stub_env` no longer sets `PAPER_TICKER_COOLDOWN=0` / `LIVE_TICKER_COOLDOWN=0` — the GitLab CI runner's freshly-booted state passes the suite without the stub.
- Full pytest suite green locally and in CI.

## 8. Rollback

The change is two single-line edits in one file. Revert is trivial:

```python
- last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
+ last    = self._last_traded.get(analysis.market.ticker, 0.0)
```

Trigger to revert: any production cycle log shows a `paper cooldown` or live `cooldown` SKIPPED record on a ticker that has *no* row in `paper_trades.db`. (Indicates the gate broke in the wrong direction.)

## 9. Soak-window contract

This spec is pre-loaded during PROFIT-PHASE2-001 soak (drafted 2026-05-03; do not implement before 2026-05-09 organic close or 2026-05-16 hard ceiling). The fix is a decision-path edit per the `domain_constraints.md` rule. Even though the bug is currently silent in production (continuous uptime), the rule applies regardless. Post-soak landing is the right cadence.

## 10. Out of scope

- Other executor gates (price sanity, opposing-position, concentration, balance) — out of scope; only the cooldown sentinel fixes here.
- Persisted-state changes — none required; the bug is purely in-memory sentinel handling.
- CI stub (`tests/conftest.py:_ci_stub_env`) keeps its other Kalshi/Ollama env stubs. Only the cooldown stubs are removed.
