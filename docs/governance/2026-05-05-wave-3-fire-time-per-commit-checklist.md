# Wave-3 fire-time per-commit compact checklist

**Type:** operator-runnable single-page playbook (per Implementation Contract §9 — operator decision input).
**Audience:** operator at Wave-3 fire-time. Triggers ≥ 2026-06-17 (Lever B) or ≥ 2026-07-01 (Lever C). FIRES ONLY IF Wave-2 stalls AND Branch D not yet fired.
**Drafted:** 2026-05-05.
**Companion:** `2026-05-05-wave-3-deploy-day-timing.md`; `docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` (ARCHIVED Stream G R34); `docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` (ARCHIVED Stream G R33).
**Wall-clock target:** 30 min per commit; 14 d inter-commit cadence.

## Wave-3 commits

| # | spec | locked values | trigger |
|---|---|---|---|
| 1 | Lever B G1=0.04 | `G1_CONFIDENCE_THRESHOLD=0.04`; `G1_FAILSAFE=0.08`; 2× ratio invariant | Wave-2 stalls; Branch D not fired |
| 2 | Lever C v1 | §3.2 normalized hash; `cross_series_correlation_window_seconds=3600`; record-after-gate-pass | 14d after Lever B + clean validation |

Both lock addenda from cycle 2 are load-bearing. Implementation Contract §5+§11 territory.

## Lever B fire-time (commit 1)

### B.1 Pre-flight (5 min)

```bash
cd ~/vscode/kalshi-bot
git pull origin main
date -u +%Y-%m-%dT%H:%M:%SZ                   # within UTC Mon-Thu 18:00-22:00 window
launchctl list | grep com.jake.kalshi-bot     # PID > 0; exit 0

# Confirm Wave-2 14d window concluded with stall (0 PAPER_TRADE OR negative P&L)
# AND Branch D not yet fired
ls .git/refs/tags | grep edge-004-branch-d-fired   # should be empty (no Branch D fire yet)
```

**Abort if:** Branch D already fired (Wave-3 should not deploy). Re-route per Lever-D escalation runbook.

### B.2 Land the constants edit (10 min)

```bash
# Edit tasks/trade_readiness_gate.py:
#   G1_CONFIDENCE_THRESHOLD = 0.04            # was 0.05
#   G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.08   # was 0.10

# Edit tests/test_lever_b_g1_floor_lock.py — remove pytest.mark.xfail decorators (3 tests)

# Refresh existing tests/test_decision_blender.py cases that hardcoded 0.05 / 0.10
# (per parent spec §5)

# Bump VERSION
echo "0.32.0" > VERSION   # OR 0.33.0 if option-A landed in Wave-2

git add tasks/trade_readiness_gate.py tests/test_lever_b_g1_floor_lock.py tests/test_decision_blender.py VERSION
git commit -m "Wave-3 Lever B: G1 floor 0.05→0.04 + failsafe 0.10→0.08"

.venv/bin/python -m pytest -q tests/test_lever_b_g1_floor_lock.py tests/test_decision_blender.py tests/test_trade_readiness_gate.py
.venv/bin/ruff check .
```

### B.3 Push + restart (5 min)

```bash
git push origin main
git tag -a v0.32.0 -m "Wave-3 Lever B: G1=0.04"
git push origin v0.32.0

launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl list | grep com.jake.kalshi-bot
sleep 30
tail -50 logs/app/bot.log
```

### B.4 24h smoke check (passive)

Monitor the next 24 h for:

| signal | source | trigger |
|---|---|---|
| G1 SKIPPED count drops | `SKIPPED.reason == "G1_blended_confidence"` count over 24h | drops ≥ 30% per spec §6 acceptance criterion |
| Newly-admitted candidates | new OPPORTUNITY at the 0.04-floor band (blended_confidence in [0.04, 0.05)) | predicted 1-2 per 14d per Codex's counterfactual |
| No trade-rate explosion | OPPORTUNITY → PAPER_TRADE rate | < 5× pre-Lever-B baseline per spec §3 risk LOW |
| No calibration drift | per-lane CALIBRATION_CHECK | error worsening > 1.5× → rollback trigger |

### B.5 14d acceptance window

Per Lever B parent spec §6:
- G1 SKIPPED count drops ≥ 30 % at 0.04 floor
- Newly-admitted candidates: ≥ 1 PAPER_TRADE produced; aggregate realized P&L non-negative
- Calibration drift detection clean

If `realized P&L < 0` at 14d: trigger Lever-D escalation §2.2.
If clean: proceed to Lever C deploy after 14d cadence (Step C).

## Lever C fire-time (commit 2)

### C.1 Pre-flight (5 min)

```bash
cd ~/vscode/kalshi-bot
git pull origin main
date -u +%Y-%m-%dT%H:%M:%SZ                   # 14d after Lever B; UTC Mon-Thu 18:00-22:00
# Confirm Lever B 14d window concluded clean
```

### C.2 Land the implementation (10 min)

Per Lever C parent spec §2 + cycle-2 LOCK addendum §2:

```bash
# Edit config.py:
#   add cross_series_correlation_window_seconds field (env-var-driven; default 3600)

# Edit tasks/blend_task.py:
#   - Add _headline_hash() per parent spec §3.2 (normalized regex)
#   - Add _recent_headline_enqueues dict
#   - Add cross-series check AFTER same-series EXEC-002 check, BEFORE _emit_blend_decision
#   - Record headline hash AFTER readiness gate pass (not at entry — per LOCK addendum §2)

# Edit tests/test_lever_c_cross_series_correlation.py — remove pytest.mark.xfail (6 tests; 5 NEW from LOCK §3)

# Bump VERSION
echo "0.33.0" > VERSION

git add config.py tasks/blend_task.py tests/test_lever_c_cross_series_correlation.py VERSION
git commit -m "Wave-3 Lever C v1: cross-series headline correlation guard"

.venv/bin/python -m pytest -q tests/test_lever_c_cross_series_correlation.py
.venv/bin/ruff check .
```

### C.3 Push + restart (5 min)

```bash
git push origin main
git tag -a v0.33.0 -m "Wave-3 Lever C v1: cross-series guard"
git push origin v0.33.0

launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
sleep 30
tail -50 logs/app/bot.log
```

### C.4 24h smoke check

| signal | source | trigger |
|---|---|---|
| Cross-series suppression fires | `SKIPPED.reason == "cross_series_headline_in_window"` | > 0 within 24h (gate works) |
| No legitimate trade suppression | compare suppressed candidates' headlines vs PAPER_TRADE candidates | false-positive rate < 5% |
| Window-zero env disables | `cfg.cross_series_correlation_window_seconds=0` smoke test | gate fires never |

### C.5 14d acceptance window

Per Lever C parent spec §"Acceptance":
- Cross-series suppression count > 0 (gate is firing)
- No legitimate-trade suppression false positives (per per-trade audit)
- Calibration unchanged (suppression doesn't affect calibration of admitted trades)

If clean: EDGE-004 likely closes via combined Wave-2 + Wave-3 attribution evidence. If still no PAPER_TRADE lift: Branch D fires.

## Rollback decision tree

```
Lever B post-deploy fires regression?
    │
    ▼
No env-var revert (Lever B is hardcoded constant; revert via code)
    │
    ▼
git revert <lever-b-commit>; push; restart
(per post-soak-rollback-runbook.md §4 — code revert)

Lever C post-deploy fires regression?
    │
    ▼
Env-revert available: launchctl setenv CROSS_SERIES_CORRELATION_WINDOW_SECONDS 0
                      → restart bot; gate fires never
    │
    ▼
If env-revert insufficient: code revert per post-soak-rollback-runbook.md §4
```

## Special note: Lever B-2 (0.03 floor) follow-up

Per `docs/_archive/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` (ARCHIVED Stream G R26): if Lever B 0.04 lands cleanly with attribution lift AND operator wants further loosening, Lever B-2 (0.03 floor) is pre-staged. Activation criteria are gate-touching → Implementation Contract §11 territory; needs fresh authorization.

**Out of Wave-3 scope.** Defer to a Wave-3.5 / Wave-4 deploy.

## Cross-links

- `2026-05-05-wave-3-deploy-day-timing.md` — Wave-3 timing rationale
- `docs/_archive/specs/2026-05-03-edge-004-lever-b-g1-calibration-design.md` — Lever B parent spec (ARCHIVED Stream G R30)
- `docs/_archive/specs/2026-05-05-edge-004-lever-b-g1-0.04-floor-lock-addendum.md` — Lever B 0.04 LOCK (ARCHIVED Stream G R34)
- `docs/_archive/specs/2026-05-05-edge-004-lever-b-2-0.03-floor-followup-stub.md` — Lever B-2 stub (post-Lever-B success) (ARCHIVED Stream G R26)
- `docs/_archive/specs/2026-05-03-edge-004-lever-c-cross-series-headline-correlation-design.md` — Lever C parent spec (ARCHIVED Stream G R29)
- `docs/_archive/specs/2026-05-05-edge-004-lever-c-cross-series-v1-lock-addendum.md` — Lever C v1 LOCK (ARCHIVED Stream G R33)
- `wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG (refreshed cycle 5)
- `2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers if Wave-3 stalls
