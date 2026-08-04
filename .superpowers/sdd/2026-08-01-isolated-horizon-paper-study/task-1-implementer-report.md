# Task 1 Implementer Report

## Scope

- `polymarket/horizon_selection.py`
- `polymarket/paper_runtime.py`
- `tests/test_horizon_selection.py`
- `tests/polymarket/test_paper_runtime.py`

## TDD

### Red

- Added/expanded focused tests for:
  - shared pre-admission policy parity between runtime and selector,
  - malformed-record fail-closed exclusion,
  - literal `(14.0, 30.0]` coverage using `14.000001` and `30.000001` days,
  - eager bound/clock validation on empty input,
  - legacy reversed-band `_horizon_shadow_market_sets` behavior.
- Ran:

```bash
CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q tests/test_horizon_selection.py tests/polymarket/test_paper_runtime.py
```

- Result: red as expected. Confirmed failures included:
  - `AttributeError: 'NoneType' object has no attribute 'venue'` from malformed selector input,
  - no `ValueError` for naive `now`,
  - no `ValueError` for `(-1.0, 0.0)` / `(-2.0, -1.0)` empty-input bounds,
  - `ValueError: select_polymarket_horizon_band requires ordered finite bounds` from reversed shadow band.

### Green

- Canonicalized the selector pre-admission/suppression policy to runtime's prior semantics inside `polymarket/horizon_selection.py` and routed runtime helpers through that shared implementation.
- Added eager selector validation for:
  - aware `now`,
  - finite non-bool bounds,
  - `lower_exclusive_days >= 0`,
  - `upper_inclusive_days > 0`,
  - ordered bounds.
- Added per-market fail-closed exclusion for malformed market inspection and malformed horizon inputs.
- Preserved legacy `_horizon_shadow_market_sets()` behavior for reversed/equal production-to-shadow bands by returning the production set plus an empty shadow set instead of raising.
- Re-ran:

```bash
CI=1 /Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python -m pytest -q tests/test_horizon_selection.py tests/polymarket/test_paper_runtime.py
```

- Result: `72 passed in 0.33s`

## Additional Verification

- Ran:

```bash
/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/ruff check polymarket/horizon_selection.py polymarket/paper_runtime.py tests/test_horizon_selection.py tests/polymarket/test_paper_runtime.py
```

- Result: `All checks passed!`

## Files Changed

- Created `polymarket/horizon_selection.py`
- Modified `polymarket/paper_runtime.py`
- Created `tests/test_horizon_selection.py`
- Modified `tests/polymarket/test_paper_runtime.py`

## Commit

- Pending new fix commit for adjudication round 1/5.

## Caveats

- The task brief’s `.venv/bin/python` command does not resolve from this worktree because the virtualenv lives at the root checkout. I used `/Users/jacobparenti/vscode/kalshi-bot/.venv/bin/python` instead, with no code or environment changes.
