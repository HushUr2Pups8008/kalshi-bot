# Cycle-15B paper_trades cohort note (L8)

**Type:** schema-or-doc note enforcing pre-fix-vs-post-fix data separation.
**Drafted:** 2026-05-06 cycle-14 verdict landing (filed pre-Codex C1).
**Authority:** Cycle-15B charter §"Out of scope" (`2026-05-06-cycle-15b-charter-extraction-rebuild.md`); skeleton §A.5 transferable to §B (`cycle-15-conditional-charter-skeletons.md`).
**Tracker:** PROFIT-EDGE-008 (Cycle-15B); referenced from IC §16.

## TL;DR

Pre-Cycle-15B paper trades and `dossier_updates` rows are **not ground truth** for calibration validation post-fix. Cycle-15B sub-fix changes the function `(NewsItem) → SignalAnalysis.estimated_probability`. Trades and dossier updates produced by the pre-fix function describe a different (broken) extraction layer, not the post-fix one.

Future replay tooling MUST distinguish pre-Cycle-15B vs post-Cycle-15B cohorts. This note is the load-bearing reference.

## Cohort definitions

| cohort | data source | calibration validity post-Cycle-15B |
|---|---|---|
| **PRE_FIX** | All `paper_trades` rows + `dossier_updates` rows produced before Cycle-15B C7 sub-fix lands | NOT ground truth. Diagnostic-historical only. |
| **POST_FIX_REBUILT** | `dossier_updates` rebuilt by Cycle-15B C9 re-ingestion pipeline over the 16-day evidence window | Ground truth for Cycle-15B C10 replay. |
| **POST_FIX_NEW** | `paper_trades` rows produced after Cycle-15B C7 deploys (live runtime) | Ground truth for Cycle-16+ replay validation. |

The 3 lifetime PRE_FIX paper trades (`KXFISAEXTEND-26APR-MAY01/02/03`, all NO-side, all 0/3 wrong-direction, lifetime P&L -$7.50) stay in the database for audit + historical verdict-trace; they do NOT feed Cycle-15B+ replay calibration.

## Pre-fix preservation requirement (cross-ref Codex C9)

Cycle-15B C9 re-ingestion pipeline MUST preserve PRE_FIX `dossier_updates` in a separate table OR backup file before rebuilding POST_FIX_REBUILT rows. Rationale:

- Audit trail for Cycle-14 verdict reproducibility (Cycle-14 diagnostic ran against PRE_FIX data; future verdict-replay must recover that state).
- Rollback path if C9 re-ingestion has bugs.
- Cohort comparison (PRE_FIX vs POST_FIX_REBUILT side-by-side movement_rate / direction-correctness diff is itself useful evidence).

L7 atomicity review (Claude) verifies preservation path is executable BEFORE C10 consumes C9 output.

## Replay tooling guidance

Future replay scripts (`scripts/edge_replay/score_counterfactual_pnl.py` and siblings) MUST accept a `--cohort` flag selecting one of `pre_fix | post_fix_rebuilt | post_fix_new | all`. Default = `post_fix_rebuilt | post_fix_new` (excludes PRE_FIX from new replay output).

Cohort discriminator: `dossier_updates.created_at` (or equivalent timestamp column) compared against the Cycle-15B C7 deploy commit timestamp. Codex C9 emits a sentinel record `cycle_15b_c7_deploy_ts` in `logs/edge_replay/cycle15b/reingestion_audit.json` for downstream tooling.

## What this note does NOT do

- Does NOT delete PRE_FIX rows from `paper_trades.db` or `evidence_store.db`. Both stay for historical audit.
- Does NOT block Cycle-14 verdict-replay tooling from accessing PRE_FIX data. Cycle-14 verdict is reproducible against PRE_FIX (per cycle-14 diagnosis-doc reproducibility section).
- Does NOT modify the bot's runtime behavior. Pure replay-tooling guidance.

## Cross-links

- `docs/_archive/governance/cycle-15-conditional-charter-skeletons.md` §A.5 — origin pattern (transferable to §B).
- `docs/governance/2026-05-06-cycle-15b-charter-extraction-rebuild.md` — charter referencing this note.
- `docs/governance/2026-05-06-cycle-15b-task-split.md` C9 + L7 — re-ingestion pipeline + atomicity review.
- `docs/IMPLEMENTATION_CONTRACT.md` §16 Rule 4 — replay evidence reproducibility requirement.
- `data/paper_trades.db` — 3-trade PRE_FIX cohort.
- `data/evidence_store.db` — 266-row evidence corpus (re-ingested by C9 into POST_FIX_REBUILT dossier_updates).
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-008` — debt entry referencing this note.
