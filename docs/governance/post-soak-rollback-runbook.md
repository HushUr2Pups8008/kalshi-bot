# Post-soak rollback runbook

**Status:** procedural (operator-facing). Pre-staged during the active `PROFIT-PHASE2-001` soak so incident response after the post-soak close (**2026-05-08 under §8.5.1 early-close**, OR 2026-05-15 under the default 14-day floor) is a single-document lookup, not a multi-spec scavenger hunt.
**Audience:** the operator on call when a Wave-1 / Wave-2 deploy goes wrong.
**Drafted:** 2026-05-03
**Companion:** `docs/superpowers/specs/2026-05-03-post-soak-landing-order-design.md` (the deploy-side spec)

## 0. When to use this runbook

You're in incident response if any of these triggers fires during a post-soak deploy validation window:

- The Wave-1 base-stack item just deployed shows its restart-trigger condition (per its spec).
- Realized P&L on post-deploy paper trades drops below the matched-window pre-deploy baseline.
- A safety event (PARSE_ERROR / VALIDATION_ERROR / KILL_SWITCH / `batch_aborted=True`) fires during the validation window.
- `bothealth.sh` reports a regression in any health metric.
- The launchctl jobs (`com.jake.kalshi-bot`, `com.kalshi.governance.fast`, `com.kalshi.governance.deep`) crash or stall.

If none of the above fires, **do not roll back** — the spec's validation window must run to completion.

## 1. Decision tree (60-second triage)

```
Is the soak observably broken (KILL_SWITCH / batch_aborted / unhandled exception)?
├── YES → §2 emergency revert (last deploy)
└── NO  → continue
   │
   Is the metric drift inside the spec's restart-trigger band?
   ├── NO  → not a rollback case; document + monitor
   └── YES → continue
      │
      Is there an env-var-driven fast revert?
      ├── YES → §3 env revert (no code change)
      └── NO  → §4 code revert
```

## 2. Emergency revert (last deploy)

Reserved for KILL_SWITCH / batch_aborted / unhandled exception. **Stop the bot first, then revert.**

```bash
# 1. Stop the bot to freeze damage
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# 2. git revert the last deploy commit (do not amend; create a new revert commit)
cd ~/vscode/kalshi-bot
git log -1 --oneline                             # confirm what's about to revert
git revert HEAD                                  # creates a new revert commit
git push origin main

# 3. Sync, restart bot
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist

# 4. Confirm bot is alive
launchctl list | grep com.jake.kalshi-bot       # PID > 0, exit 0
tail -50 logs/app/bot.log                       # no exceptions
```

**Always reverts the *most recent* deploy.** Cascading reverts (e.g., reverting Wave-1 Step 3 when Step 4 is already deployed) require deploying Steps 4 and 3 to be reverted in the *opposite* order from how they landed. See §5.

## 3. Env-driven fast reverts (no code change)

Five Wave-1 / Wave-2 items expose an env-var-driven kill switch. Use these when the bot can stay running with the gate disabled:

| spec | env var | revert value | what it disables |
|---|---|---|---|
| `EXEC-002` series-correlation guard | `SERIES_CORRELATION_WINDOW_SECONDS` | `0` | the guard fires never |
| `EXEC-002 Approach 2` (Lever C) cross-series headline | `CROSS_SERIES_CORRELATION_WINDOW_SECONDS` | `0` | the gate fires never |
| Lever B G1 calibration | `G1_CONFIDENCE_THRESHOLD` (if env-driven post-landing) | original `0.05` | restores the floor |
| Lever E (CLOSED — should not have landed; if it did somehow) | `MULTI_SOURCE_CORROBORATION_MIN_N` | `1` | gate fires never |
| Governance shadow→real flip (GOV.P3, future) | governance plist `mode=shadow` | n/a | reverts to shadow |

```bash
# Example: disable EXEC-002 series-correlation guard mid-soak
launchctl setenv SERIES_CORRELATION_WINDOW_SECONDS 0
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
```

**Note:** OBS-005, OBS-003, MATCH-001, governance_monitor fix, and Lever A.1 classifier-fix have **no env-driven revert** — only code revert (§4) works. They were not designed with a kill switch because their failure modes are bug-shape, not policy-shape.

## 4. Code revert (per-Wave-1 / Wave-2 item)

Each spec documents the revert hunk. Copies + commands below; refer to the spec for full diff context.

### 4.1 OBS-005 (cooldown sentinel)

Spec: `2026-05-03-obs-005-cooldown-sentinel-fix-design.md` §8.

```diff
# trading/executor.py:208 + 276 — revert
- last    = self._last_traded.get(analysis.market.ticker, float("-inf"))
+ last    = self._last_traded.get(analysis.market.ticker, 0.0)
```

```bash
git revert <commit-sha-of-OBS-005-deploy>
git push origin main
# operator restart per §2 step 3
```

### 4.2 MATCH-001 (B') token-guard predicate

Spec: `2026-05-03-match-001-token-guard-refinement-design.md` §8 (also see §5.1 tokenization gotcha addendum).

```diff
# analysis/market_matcher.py:_meets_suppression_criteria — revert from option (a) substring back to pre-fix
- _has_supporting_non_ticker_token = any(token not in ticker_lower for token in overlap)
+ _token_not_in_ticker = not any(token in ticker_lower for token in overlap)
- and not _has_supporting_non_ticker_token
+ and _token_not_in_ticker
```

If the deploy used option (b) tokenizer instead of (a) substring, refer to `2026-05-03-match-001-tokenization-option-b-design.md` §rollback for the alternative diff.

### 4.3 OBS-003 (BlendTask SKIPPED emission)

Spec: `2026-05-03-obs-003-blendtask-skipped-emission-design.md` §9.

Revert is one method addition + one call-site insertion in `tasks/blend_task.py`. Use `git revert <sha>` rather than hand-editing — the additive nature means the revert is non-conflicting unless EXEC-002 has landed in between (in which case revert in opposite order: §5).

### 4.4 EXEC-002 (series-correlation guard)

Spec: `2026-05-03-exec-002-series-correlation-guard-design.md` §9.

**Prefer §3 env revert** (`SERIES_CORRELATION_WINDOW_SECONDS=0`) for fast operator response. Code revert is the BlendTask + config diff.

### 4.5 governance_monitor.py fix

Spec: `2026-05-03-governance-monitor-fix-design.md` §6.

```bash
git revert <commit-sha-of-monitor-fix-deploy>
```

The fix is two hunks in one file; revert is non-conflicting.

### 4.6 Lever A.1 classifier fix

Spec: `2026-05-03-edge-004-lever-a-stage-a1-source-class-classifier-fix-design.md` §6.

Two-hunk single-file revert in `main.py:_source_class_for_evidence`. Use `git revert <sha>`.

## 5. Cascading-revert sequencing

If multiple Wave-1 items have already deployed and validation regresses on item N, revert items in **opposite landing order** (most recent first):

```
Order to revert if regression on Step 1 (OBS-005) after Step 4 (EXEC-002) has deployed:
  1. EXEC-002      (env revert preferred)
  2. OBS-003
  3. MATCH-001
  4. OBS-005
```

Reason: each later step's tests / dependencies may rely on the earlier step's behaviour. EXEC-002's spec §8 explicitly assumes OBS-003 has landed (the `cross_series_correlation_in_window` reason needs the OBS-003 SKIPPED emission). Reverting OBS-003 *before* EXEC-002 produces an inconsistent state where EXEC-002 emits a SKIPPED reason that BlendTask doesn't know to log.

**Don't try to be clever.** Cascading reverts are rare; if you find yourself doing more than one in a single response, escalate to a full rollback to pre-deploy main and re-plan.

## 6. Post-revert checklist

After any rollback (env or code):

1. **Bot alive:** `launchctl list | grep com.jake.kalshi-bot` shows PID > 0 and exit code 0.
2. **No exceptions in `bot.log`:** `tail -200 logs/app/bot.log | grep -iE 'exception|traceback|error' | head`.
3. **Trade-log structure intact:** the latest `OPPORTUNITY` / `BLEND_DECISION` / `SKIPPED` events parse cleanly. `python -c "import json; [json.loads(l) for l in open('logs/trades/live/trades.jsonl').readlines()[-50:] if l.strip()]"` exits silently.
4. **Health-check report fresh:** `bash scripts/bothealth.sh` produces the daily MD without errors.
5. **Document the rollback** in `docs/profit_path_debt_log.md` with the commit SHA reverted, the trigger reason, and the rollback method (env vs code). Future audit needs this.
6. **Pause downstream Wave deploys** until the regression is root-caused. The post-soak landing-order spec's wave timeline assumes successful predecessors.

## 7. When to escalate to a full rollback

Reset the post-soak deploy effort entirely (full rollback to the `pre-wave-1-deploy-${DATE}` rollback anchor created by `scripts/pre_soak_close_branch_backup.sh`) if:

- Two or more Wave-1 items have failed validation back-to-back.
- A Wave-1 revert produces unexpected secondary failures (cascading-revert §5 fails).
- The operator is uncertain which deploy caused the regression and the trade-log evidence is ambiguous.
- Any safety event (KILL_SWITCH / batch_aborted) fires more than once in a single validation window.

```bash
# Reset main to the pre-Wave-1 SHA (recorded by the operator at deploy day 0)
PRE_WAVE1_SHA="<commit-sha-recorded-at-deploy-day-0>"
git checkout main
git reset --hard $PRE_WAVE1_SHA
git push --force-with-lease origin main         # destructive; only after full deliberation
```

Force-push reset to main is a destructive operation. Confirm explicit operator authorization before running. Document the full-rollback decision and the pre-Wave-1 SHA in `docs/profit_path_debt_log.md`.

## 8. Out of scope

- **GOV.P3 real-mode flip rollback.** Different shape (governance soak / runbook). See `docs/governance/PHASE2_RUNBOOK.md`.
- **Reverting infrastructure changes** (launchd plists, .env mutations, DB schema migrations). Each has its own runbook in `docs/governance/`. This document is for the application-layer Wave-1 / Wave-2 specs only.
- **Trade-log post-incident reconstruction.** Beyond rollback scope; see `scripts/replay_dossier.py` + manual SQL recovery if dossier corruption is suspected.
- **Cascading rollback across waves** (revert Wave-2 to recover Wave-1 stability). Out of scope; full rollback per §7 is the prescribed escalation.
