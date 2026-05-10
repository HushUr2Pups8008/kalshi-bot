# Claude Commits 56d641e..9e2fffa Adversarial Review

Reviewed commits: `56d641e`, `6489882`, `dec4636`, `9e2fffa`.

## Findings

### F1 - `test_governance_monitor.py` reload isolation still reloads under the patched env

Severity: MEDIUM

Commit: `56d641e`

The `_gm_module_reload_isolation(monkeypatch)` fixture depends on `monkeypatch`. In pytest, teardown runs in reverse fixture-finalizer order, so the custom fixture finalizer runs before `monkeypatch` restores the environment. The teardown `importlib.reload(_gm)` therefore re-derives `_DEFAULT_LOG` / `_DEFAULT_OVERRIDES` while `KALSHI_HOME=/tmp/nonexistent/kalshi_home_override` is still set. The intended cleanup does not happen.

Suggested fix: make the two default-path tests use `try/finally` inside the test after `monkeypatch.delenv("KALSHI_HOME", raising=False)` or use `monkeypatch.context()` around the patched import and reload the module after the context exits.

### F2 - Latest preloaded harnesses fail ruff

Severity: MEDIUM

Commit: `56d641e`

`ruff check tests/test_executor.py tests/test_governance_monitor.py tests/test_blend_task.py` fails with:

- `tests/test_blend_task.py`: undefined `cfg` in two EXEC-002 tests.
- `tests/test_executor.py`: undefined `_make_paper_executor_for_obs005`.
- `tests/test_executor.py`: unused `TradeExecutor` import inside the new OBS-005 runtime test.
- `tests/test_governance_monitor.py`: unused `os` import.

The commit cites pytest verification only. These xfail preload files should still be lint-clean; otherwise the 2026-05-15 landing can fail before reaching the intended strict-xfail contract.

### F3 - OBS-005 runtime test only excludes the cooldown string, not full validation success

Severity: LOW

Commit: `56d641e`

`test_paper_never_traded_runtime_behavior_under_small_monotonic` passes if `_validate()` returns a different rejection string. Because the test is strict-xfail, that would still surface as an XPASS today, but post-fix it would allow a fixture defect to masquerade as a cooldown fix. The landing-time version should assert `result is None` after constructing an analysis that clears every non-cooldown branch.

### F4 - Lever A Stage A.1 spec's broad `press releases` token needs the proposed archive false-positive audit before implementation

Severity: LOW

Commit: `dec4636`

The spec correctly flags `"press releases"` as broad. Treat that warning as a hard pre-deploy acceptance check, not a note: run the classifier over the distinct source strings in the archive and report false-positive rate before landing. Without that, industry feeds using generic "press releases" titles can be over-promoted to `official`, directly touching evidence weights.

## No Issue

`9e2fffa` correctly fixes the MATCH-001 guard shape from ticker-level to headline-level. That matches the Codex anchor-sizing data: exact canonical headlines remain protected while `KXTRUMPIRAN-26MAY01` legitimately hosts many low-quality suppressions.

`6489882` accurately frames Lever B as an attribution/calibration lever rather than a direct edge-lift lever, matching the G1 counterfactual.
