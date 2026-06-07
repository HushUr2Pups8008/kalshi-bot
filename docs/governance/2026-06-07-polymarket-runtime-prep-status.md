# Polymarket Runtime Integration Prep Status

Prepared: 2026-06-07

Inputs:

- `docs/governance/2026-06-06_111138-polymarket-trading-integration.md`
- `docs/governance/2026-06-07-polymarket-integration-timing-assessment.md`
- `docs/api_contracts/polymarket_us_retail_contract.md`
- `tests/fixtures/polymarket_us/contract_snapshot.json`

## Status

Prepared for Task 0 contract-capture review.

Not prepared for broad runtime integration yet.

## Key Reason Closure Map

| Timing-assessment reason | Current action | Remaining stop condition |
| --- | --- | --- |
| Bothealth RED / `POST_FIX_NEW` NOT_READY | Captured as a non-Polymarket runtime stop condition. Polymarket work remains disabled-by-default and paper-only. | Do not use Polymarket integration to justify live readiness. High-risk runtime work still needs operator approval and separate review. |
| Since-restart bottleneck is fresh-pass to signal/edge conversion | Kept out of Polymarket scope. Contract capture does not alter matcher, LLM, executor, or runtime gates. | Continue treating Kalshi edge scarcity as separate pipeline work. |
| Mixed worktree with runtime artifacts | Added ignore coverage for `logs/backups/` and `logs/state/`. Runtime `data/matcher_token_weights.json` remains intentionally unstaged. | Before any PR, stage only plan/contract/test docs and confirm no runtime JSON/log artifacts are included. |
| High-risk surfaces: DB, executor, paper trader, credentials, observer | Executed only Task 0 artifacts. No `trading/`, DB schema, executor, credential, observer, launchd, or live-order code changed. | Tasks 3-13 require explicit operator approval, focused PRs, and review checkpoints. |
| External docs support Task 0 | Captured official Polymarket US API assumptions in markdown and JSON fixture with a focused test. | Re-check docs on the day any API-path/auth/order behavior changes. |

## Safe Next Step

Open a docs/test-only PR containing:

- `docs/governance/2026-06-06_111138-polymarket-trading-integration.md`
- `docs/governance/2026-06-07-polymarket-plan-readiness-review.md`
- `docs/governance/2026-06-07-polymarket-integration-timing-assessment.md`
- `docs/governance/2026-06-07-polymarket-runtime-prep-status.md`
- `docs/api_contracts/polymarket_us_retail_contract.md`
- `tests/fixtures/polymarket_us/contract_snapshot.json`
- `tests/test_polymarket_contract_snapshot.py`
- `.gitignore`

Do not include:

- `data/matcher_token_weights.json`
- `logs/backups/`
- `logs/state/`
- any `trading/`, `main.py`, runtime, DB, credential, launchd, or service-manager change.

## Verification Required Before PR

Run:

```bash
.venv/bin/python -m pytest tests/test_polymarket_contract_snapshot.py -v
.venv/bin/ruff check tests/test_polymarket_contract_snapshot.py
git status --short --branch
```

Expected:

- Contract snapshot test passes.
- Ruff passes.
- Git status shows only intended docs/test prep files plus known unstaged runtime `data/matcher_token_weights.json`.
