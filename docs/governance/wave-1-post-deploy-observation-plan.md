# Wave-1 post-deploy observation plan

**Status:** procedural (operator-facing). Pre-staged during the active `PROFIT-PHASE2-001` soak.
**Audience:** the operator on call during the 24 h post-deploy validation window for each Wave-1 commit.
**Drafted:** 2026-05-05
**Companion:** `docs/governance/post-soak-rollback-runbook.md` (rollback if any check below fails); `docs/governance/wave-1-deploy-commit-order-decision.md` (commit order); `docs/governance/post-soak-close-rehearsal-checklist.md` §1-§6 (per-commit deploy steps).

## 0. When to use this plan

After each Wave-1 per-feature commit lands on `main`. Run the smoke check immediately post-deploy, then the 24 h regression watch over the validation window. If any row triggers, escalate per the rollback runbook §0.

Wave-1 lands six behavioural commits in this order (locked):

1. OBS-005 — cooldown sentinel-default fix
2. MATCH-001 (B') — token-guard refinement
3. OBS-003 — BlendTask SKIPPED-emission
4. EXEC-002 — series-correlation guard
5. GOV-003 — governance_monitor.py fix
6. EDGE-004 Lever A.1 — source-class classifier prerequisite

## 1. Per-commit smoke check (≤ 5 min, run immediately post-deploy)

For every commit: confirm bot alive + log writeable + no immediate exceptions.

```bash
launchctl list | grep com.jake.kalshi-bot         # PID > 0; exit 0
tail -50 logs/app/bot.log                         # no exceptions / tracebacks in last 50 lines
.venv/bin/python -m pytest -q tests/test_<spec>.py  # spec-specific harness re-run on deploy commit
```

**Smoke pass criteria (all):** PID > 0, exit 0, no `Traceback` in last 50 log lines, spec-harness `0 failed` (xfail markers removed in same hunk per `wave-1-changelog-entry-prestaged.md` §"Removed `pytest.mark.xfail` markers"). Any failure → `post-soak-rollback-runbook.md` §2 emergency revert.

## 2. 24 h regression watch matrix

Each row defines: signal to monitor, where it lives, the regression trigger, and the response.

| commit | signal | source | regression trigger | response |
|---|---|---|---|---|
| **OBS-005** cooldown sentinel | `_cooldown_remaining()` returns `None` for first-time keys | `logs/trades/live/trades.jsonl` `OPPORTUNITY` records w/ `cooldown_state` field | `cooldown_state == "0.0"` for any new ticker (sentinel default leaked) | runbook §4.1 code revert |
| **OBS-005** cooldown sentinel | trade-rate per ticker | `OPPORTUNITY` count by ticker over 24 h | trade-rate > 2× pre-deploy 24 h baseline AND no realized-edge improvement | runbook §4.1 code revert |
| **MATCH-001 (B')** token-guard | suppression-rate stable | `MATCH_DIAGNOSTIC` records w/ `low_match_quality=true` | suppression-rate falls > 30 % vs pre-deploy 24 h baseline (under-suppression) OR rises > 30 % (over-suppression) | runbook §4.2 code revert |
| **MATCH-001 (B')** token-guard | KXPSL leakage allow-list | `MATCH_DIAGNOSTIC` for `KXPSL*` tickers | any `KXPSL*` ticker emits `low_match_quality=true` (allow-list bypass) | runbook §4.2 code revert |
| **OBS-003** SKIPPED stream | SKIPPED records emit | `logs/trades/live/trades.jsonl` `SKIPPED` events | post-deploy 24 h has 0 `SKIPPED` records (BlendTask never emits) | runbook §4.3 code revert |
| **OBS-003** SKIPPED stream | `reason` field populated | `SKIPPED` records | any `SKIPPED` record with `reason == null` or empty | runbook §4.3 code revert |
| **OBS-003** SKIPPED stream | gate-attribution coverage | `SKIPPED.reason` distinct values | any reason value not in {G1_blended_confidence, G2_diversity, G3_disagreement, G4_regime_confidence, G5_drift_suspect, G6_recency} | spec re-read; possible Lever B prereq missing |
| **EXEC-002** series-correlation guard | suppression count | `SKIPPED.reason == "series_correlation_in_window"` | suppression count > 5/day OR == 0/day for 48 h post-deploy | review window setting; runbook §3 env revert (`SERIES_CORRELATION_WINDOW_SECONDS=0`) if blocking trades |
| **EXEC-002** series-correlation guard | env-revert path live | `cfg.series_correlation_window_seconds` reads from env | `launchctl getenv SERIES_CORRELATION_WINDOW_SECONDS` returns expected default (`3600`) | spec §5 verify; redeploy if dropped |
| **GOV-003** governance_monitor | KILL_SWITCH count | `logs/governance/decisions.jsonl` `KILL_SWITCH` events | any KILL_SWITCH fires post-deploy | stop bot; runbook §4.5 |
| **GOV-003** governance_monitor | VALIDATION_ERROR / batch_aborted | `logs/governance/decisions.jsonl` | any non-zero count post-deploy | stop bot; runbook §4.5 |
| **GOV-003** governance_monitor | cycle cadence | `GOVERNANCE_CYCLE_START` inter-arrival | max gap > 3 h | investigate launchd; reschedule |
| **EDGE-004 A.1** classifier | classification distribution | `EVIDENCE_INGESTION.source_class` over 24 h | `analysis` class share drops post-deploy (classifier didn't relabel) | runbook §4.6 code revert |
| **EDGE-004 A.1** classifier | trade-rate neutrality | `OPPORTUNITY` count over 24 h | trade-rate > 2× pre-deploy baseline (classifier was supposed to be archive-neutral per Codex `8001a16` replay) | runbook §4.6 code revert |

## 3. Per-row monitoring commands

Run each row's command in a 24 h cron OR ad-hoc at end-of-day. All commands assume working dir `~/vscode/kalshi-bot`.

### 3.1 OBS-005 cooldown sentinel default leak

```bash
.venv/bin/python -c "
import json
from collections import Counter
c = Counter()
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('event_type') != 'OPPORTUNITY': continue
    cs = r.get('cooldown_state')
    c[str(cs)] += 1
print(dict(c))
print('SUSPECT (sentinel-leak signature): cooldown_state=\"0.0\" with no prior trade for ticker')
"
```

### 3.2 MATCH-001 (B') suppression-rate

```bash
.venv/bin/python -c "
import json
total = supp = 0
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('event_type') != 'MATCH_DIAGNOSTIC': continue
    total += 1
    if r.get('low_match_quality'): supp += 1
print(f'24h suppression rate: {supp}/{total} = {supp/max(total,1):.1%}')
"
```

### 3.3 OBS-003 SKIPPED stream coverage

```bash
.venv/bin/python -c "
import json
from collections import Counter
reasons = Counter()
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('event_type') != 'SKIPPED': continue
    reasons[r.get('reason','MISSING')] += 1
print(dict(reasons))
"
```

### 3.4 EXEC-002 series-correlation count

```bash
.venv/bin/python -c "
import json
n = 0
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('event_type') == 'SKIPPED' and r.get('reason') == 'series_correlation_in_window':
        n += 1
print(f'24h series_correlation_in_window suppressions: {n}')
"
```

### 3.5 GOV-003 governance health

```bash
.venv/bin/python -c "
import json
from collections import Counter
c = Counter()
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    c[r.get('type','')] += 1
print('KILL_SWITCH:', c.get('KILL_SWITCH',0))
print('VALIDATION_ERROR:', c.get('GOVERNANCE_VALIDATION_ERROR',0))
print('PARSE_ERROR:', c.get('GOVERNANCE_DECISION_PARSE_ERROR',0))
print('decisions:', c.get('GOVERNANCE_DECISION',0))
"
```

### 3.6 EDGE-004 A.1 classifier distribution

```bash
.venv/bin/python -c "
import json
from collections import Counter
c = Counter()
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('event_type') != 'EVIDENCE_INGESTION': continue
    c[r.get('source_class','MISSING')] += 1
print(dict(c))
"
```

## 4. Aggregate dashboards (operator-side, optional)

If Grafana stack live (per `docs/grafana_dashboards.json`):

- Panel `Trade-Rate by Ticker (24h)` — OBS-005 + EDGE-004 A.1 row 2
- Panel `MATCH_DIAGNOSTIC Suppression Rate` — MATCH-001 (B') row 1
- Panel `SKIPPED Reasons (Counter)` — OBS-003 + EXEC-002 rows
- Panel `Governance Cycle Cadence + Safety Counters` — GOV-003 rows

If no Grafana, the §3 commands cover the same surface from CLI.

## 5. Combined regression heuristic

Single-go-/-no-go after the full 6-commit Wave-1 lands:

```bash
bash scripts/wave1_post_deploy_smoke.sh   # to be authored by Codex (companion to this plan)
```

Returns 0 on all 14 monitoring rows clean across the 24 h window; non-zero on any regression. Operator can wire to launchd or run end-of-day.

## 6. Out of scope

- **Wave-2 / Wave-3 observation plans.** Separate doc per wave.
- **Calibration drift** beyond per-feature signal counters. PROFIT-CAL-001's `CALIBRATION_CHECK` event stream covers calibration; this plan stays at signal-level health.
- **Real-mode flip observation.** GOV.P3 phase-3 surface; out of Wave-1 application-layer scope.

## 7. Cross-links

- `docs/governance/post-soak-rollback-runbook.md` — incident response if any row triggers
- `docs/governance/wave-1-deploy-commit-order-decision.md` — locked commit order
- `docs/governance/post-soak-close-rehearsal-checklist.md` §1-§6 — per-commit deploy procedure
- `docs/governance/wave-1-changelog-entry-prestaged.md` — per-feature behavioural summaries (informs trigger semantics)
- `scripts/wave1_post_deploy_smoke.sh` — Codex-authored bundle wrapper (companion task)
