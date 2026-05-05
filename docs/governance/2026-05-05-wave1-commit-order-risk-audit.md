# Wave-1 commit-order risk audit

Date: 2026-05-05
Question: size rollback blast radius for one bundled Wave-1 commit vs six per-feature commits.

## Wave-1 deploy units

| unit | likely implementation files | preload test files | rough xfail markers in touched test files |
| --- | --- | --- | ---: |
| OBS-005 | `trading/paper_trader.py`, `tests/conftest.py` | `tests/test_executor.py` | 5 |
| MATCH-001 B' | `analysis/market_matcher.py` | `tests/test_market_matcher.py` | 2 |
| OBS-003 | `tasks/blend_task.py`, `scripts/bothealth.sh` | `tests/test_blend_task.py` | 10 |
| EXEC-002 | `tasks/blend_task.py`, `config.py` | `tests/test_executor.py`, `tests/test_blend_task.py` | 15 |
| GOV-003 | `scripts/governance_monitor.py` | `tests/test_governance_monitor.py` | 5 |
| Lever A.1 | `main.py` | `tests/test_main_pipeline.py` | shared file; 6 target xfails plus later A.1+ xfails |

The xfail counts are file-level rough counts, not all per-feature markers; `tests/test_executor.py`, `tests/test_blend_task.py`, and `tests/test_main_pipeline.py` contain multiple harness families.

## Rollback sizing

| strategy | commits | bisect resolution | rollback unit | attribution |
| --- | ---: | --- | --- | --- |
| bundled | 1 | coarse: all six features in one bad commit | revert all six or hotfix inside bundle | weakest; post-deploy metrics are confounded |
| per-feature | 6 | worst-case `ceil(log2(6)) = 3` bisect steps to isolate bad feature | revert one feature commit | strongest; each validation window maps to one commit |

## Risk read

Bundling optimizes CI/runtime overhead but destroys rollback granularity. The riskiest overlap is `tasks/blend_task.py`: OBS-003 and EXEC-002 both touch BlendTask, so a bundled commit makes SKIPPED-emission regressions and series-correlation regressions harder to separate.

Per-feature commits match the rehearsal checklist's existing Day 0 / Day 1 / Day 4 / Day 6 validation windows. They also let strict-xfail removal counts drop monotonically, which is a useful deploy-health signal.

## Recommendation

Use per-feature commits for Wave-1, with `VERSION` and `CHANGELOG.md` updated only on the final "Wave-1 base stack closed" commit. If the operator insists on a single `0.30.0` bump, keep the six source/test commits at `0.29.60-dev` semantics and make the final metadata commit the release marker.
