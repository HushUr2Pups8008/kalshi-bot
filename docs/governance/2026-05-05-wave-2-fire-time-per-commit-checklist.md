# Wave-2 fire-time per-commit compact checklist

**Type:** operator-runnable single-page playbook (per Implementation Contract §9 — operator decision input).
**Audience:** operator at Wave-2 fire-time. Triggers ≥ 2026-05-18 (Branch A start) or ≥ 2026-06-02 (Branch C deploy).
**Drafted:** 2026-05-05.
**Companion:** `2026-05-05-wave-2-deploy-day-timing.md`; `wave-2-deploy-commit-order-decision.md`; `2026-05-05-wave-2-a1plus-branch-decision-table.md`; `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md`.
**Wall-clock target:** 5 min (Branch A start) or 30 min (Branch C deploy).

## Wave-2 commits

| step | type | trigger | wall-clock |
|---|---|---|---|
| 1 | Branch A start (tag only; NO code) | Wave-1 + 48h burn-in (≥ 2026-05-18) | 5 min |
| 2 | Branch C deploy (single behavioural commit) | Branch A 14d window returns 0 PAPER_TRADE (≥ 2026-06-02) | 30 min |
| 3 | option-A deploy (parallel-discretion or fallback) | operator-discretion; not recommended unless Branch C also stalls | 30 min |

## Step 1: Branch A start (5 min)

**No code change. Just tag + document.**

```bash
cd ~/vscode/kalshi-bot
git pull origin main
date -u +%Y-%m-%dT%H:%M:%SZ                   # confirm Wave-1 + 48h elapsed

# Tag the start
git tag -a a1plus-branch-a-start-$(date -u +%Y-%m-%d) \
    -m "A.1+ Branch A passive observe window starts: 14-day legal-niche PAPER_TRADE watch"
git push origin --tags

# Document in profit_path_debt_log.md PROFIT-EDGE-004 entry: append
# "Branch A observation window 2026-05-18 → 2026-06-01"
```

**Acceptance:** ≥ 1 legal-niche PAPER_TRADE in 14 d. Track via:

```bash
.venv/bin/python -c "
import json
n = 0
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'PAPER_TRADE': continue
    src = (r.get('source') or '').lower()
    if any(t in src for t in ('vitallaw','justsecurity','lawfare','scotus','politico','reuters legal')):
        n += 1; print(r.get('ts'), src)
print(f'legal-niche PAPER_TRADE in window: {n}')
"
```

If `n ≥ 1` at 2026-06-01 = EDGE-004 closes via Branch A. Stop here.
If `n = 0` at 2026-06-01 = proceed to Step 2 (Branch C deploy).

## Step 2: Branch C deploy (30 min)

### 2.1 Pre-flight (5 min)

```bash
cd ~/vscode/kalshi-bot
git pull origin main
date -u +%Y-%m-%dT%H:%M:%SZ                   # within UTC Mon-Thu 18:00-22:00 window
launchctl list | grep com.jake.kalshi-bot     # PID > 0; exit 0
```

**Abort if:** outside UTC Mon-Thu window OR within governance-fast-cycle pre-cycle 30-min window.

### 2.2 RSS-probe candidate feeds (10 min)

Per `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` §"Pre-deploy scoring":

```bash
# D1 RSS feasibility probe
for url in \
    "https://www.justsecurity.org/feed/" \
    "https://www.lawfaremedia.org/feed/" \
    "https://www.scotusblog.com/feed/" \
    "https://rss.politico.com/legal.xml"; do
    echo "=== $url ==="
    curl -I -s -o /dev/null -w "HTTP %{http_code} | %{content_type}\n" "$url"
done
```

**Pick:** Just Security primary + Lawfare secondary (recommended per rubric). Fall back to SCOTUSblog / Politico if D1 fails.

### 2.3 Land the commit (10 min)

```bash
# Edit config.py — add 1-2 selected URLs to RSS_FEEDS
# Edit main.py:_source_class_for_evidence — extend token list per A.1+ spec §3.2
# Edit tests/test_lever_a1plus_feed_config.py — remove pytest.mark.xfail decorator
# Edit tests/test_branch_c_feed_selection_rubric.py — remove pytest.mark.xfail (Codex cycle-3 harness)

# Bump VERSION
echo "0.31.0" > VERSION

# Run pre-commit hook (auto-syncs README badges)
git add VERSION
git commit -m "Wave-2 Branch C deploy: legal-analyst onboard (Just Security + Lawfare)"

# Verify tests
.venv/bin/python -m pytest -q tests/test_lever_a1plus_feed_config.py tests/test_branch_c_feed_selection_rubric.py
.venv/bin/ruff check .
```

### 2.4 Push + restart bot (5 min)

```bash
git push origin main
git tag -a v0.31.0 -m "Wave-2 Branch C deploy"
git push origin v0.31.0

launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
launchctl list | grep com.jake.kalshi-bot     # PID > 0
sleep 30
tail -50 logs/app/bot.log                     # no exceptions
```

### 2.5 Post-deploy 24h smoke check

Per `wave-1-post-deploy-observation-plan.md` Wave-2-specific monitoring rows (per `2026-05-05-wave-2-deploy-day-timing.md` §"Post-deploy regression watch"):

| signal | source | trigger |
|---|---|---|
| new feed RSS-poll succeeds | `feeds/rss_monitor.py` log | 0 successful polls of new URL in 24 h → rollback |
| classifier buckets new source | `EVIDENCE_INGESTION.source_class` | new feed evidence buckets to `other` → classifier patch failed |
| no signal-rate explosion | `OPPORTUNITY` count from new feed | new feed OPPORTUNITY > 5/day → under-suppression |
| no calibration drift | `CALIBRATION_CHECK` | per-lane error > 1.5× pre-Branch-C baseline → rollback |

```bash
# Run if Codex authored scripts/wave2_fire_time_smoke.sh (cycle-5 task)
bash scripts/wave2_fire_time_smoke.sh   # if available
```

### 2.6 14-day acceptance window (passive)

Track over 14 d:

```bash
# Daily check
.venv/bin/python -c "
import json
from collections import Counter
trades = []
for line in open('logs/trades/live/trades.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'PAPER_TRADE': continue
    trades.append(r)
n = sum(1 for t in trades if any(s in (t.get('source','') or '').lower() for s in ('justsecurity','lawfare','scotus','politico')))
pnl = sum(t.get('realized_pnl', 0) for t in trades if any(s in (t.get('source','') or '').lower() for s in ('justsecurity','lawfare','scotus','politico')))
print(f'Branch-C PAPER_TRADE: {n}; aggregate realized P&L: {pnl}')
"
```

**Acceptance (per Wave-2 branch decision table):**
- `n ≥ 1` AND aggregate P&L ≥ 0 → EDGE-004 closes via Branch C
- `n = 0` OR P&L < 0 → Branch D fires per `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2

## Step 3: option-A deploy (parallel-discretion or fallback)

Same shape as Step 2 but with geopolitics specialist URLs (war on the rocks / CSIS / ISW / CFR / Atlantic Council per A.1+ parent spec §3.1). Recommended deferred.

If parallel: deploy ≥ 2026-05-18+ alongside Branch A; both run concurrently; attribution muddled but compresses 42d walk to 28d.
If fallback: deploy ≥ 2026-06-16+ if Branch C also stalls; before Branch D fires.

## Rollback decision tree

```
Branch C smoke fires regression?
    │
    ▼
No env-var revert (Branch C is feed onboarding; revert via code)
    │
    ▼
git revert <branch-c-commit>
git push origin main
launchctl bootout/bootstrap (per dead-bot-runbook §3 procedure)
```

If 14d window concludes with regression but post-24h smoke was clean: investigate per `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` §2.2 (negative realized P&L on Branch C admitted candidates → Branch D fires).

## Cross-links

- `2026-05-05-wave-2-a1plus-branch-decision-table.md` — Branch A → C → option-A sequence + acceptance
- `2026-05-05-a1plus1-5-branch-c-feed-selection-rubric.md` — Branch C feed selection
- `2026-05-05-wave-2-deploy-day-timing.md` — Wave-2 timing rationale
- `wave-2-deploy-commit-order-decision.md` — locked commit order
- `wave-2-wave-3-changelog-entries-prestaged.md` — pre-staged CHANGELOG entries
- `docs/_archive/specs/2026-05-05-edge-004-lever-d-escalation-criteria-design.md` — Branch D triggers if Wave-2 stalls
