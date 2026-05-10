# PROFIT-PHASE2-002 onboarding spec

**Status:** planning (next-soak setup; no code changes pre-Wave-1).
**Drafted:** 2026-05-05 (during PROFIT-PHASE2-001 wind-down).
**Audience:** operator preparing the next governance shadow-soak after Wave-1 base-stack lands.
**Companion:** `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` §3 (the cadence-tune deferral that this spec resolves).

## TL;DR

PROFIT-PHASE2-002 is the next governance shadow-soak. Triggers ≥ 48 h after Wave-1 base-stack closes (so Wave-1's behavioural changes have a burn-in window). Tightens cadence (90 min fast / 12 h deep) but **does not** change decision policy (evidence window, predicted-effect horizon, gate thresholds). Acceptance criteria mirror §8.5 ≥ 14 d / ≥ 30 decisions / ≥ 85 % reasonable, with §8.5.1 early-close path open at day-7 if gates verify clean.

## 1. Why this soak

PROFIT-PHASE2-001 closed early under §8.5.1 (day-7) on a 14-day floor that proved over-conservative for the actual decision-quality stability profile. PROFIT-PHASE2-002 has two purposes:

1. **Re-validate** the governance agent under post-Wave-1 environmental change. Wave-1 lands six behavioural commits (OBS-005 / MATCH-001 (B') / OBS-003 / EXEC-002 / GOV-003 / EDGE-004 A.1). None directly touches `governance/`, but signal volume and source-class distribution shift downstream of MATCH-001 (B') + Lever A.1, which feed back into governance audit decisions over time.
2. **Test the cadence-tune hypothesis** flagged in PROFIT-PHASE2-001 §3. Halving the fast/deep cadence is a load-test (not a decision-policy test). If the tightened cadence holds without LLM throughput degradation, future soaks can adopt it as the default.

## 2. Entry criteria

All must hold before PROFIT-PHASE2-002 starts:

1. PROFIT-PHASE2-001 closed (tag `phase2-soak-closed` on origin/main).
2. Wave-1 deploy complete (all 6 commits landed; tag `v0.30.0` on origin/main).
3. ≥ 48 h burn-in since the last Wave-1 commit landed (covers the §wave-1-post-deploy-observation-plan 24 h regression watch + a 24 h stability margin).
4. Wave-1 post-deploy regression watch (per `docs/_archive/governance/wave-1-post-deploy-observation-plan.md`) returned 0 regressions across all 14 monitoring rows.
5. Operator has applied the cadence-tune plist edits per §4 below.
6. `git pull origin main` on the running Mac Studio confirms HEAD is at the soak-start commit.

## 3. Cadence-tune knobs (the only change vs PHASE2-001)

| knob | PHASE2-001 | PHASE2-002 | code change |
|---|---|---|---|
| Fast cadence | 2 h | **90 min** | launchd plist `~/Library/LaunchAgents/com.kalshi.governance.fast.plist` `StartInterval` 7200 → 5400 |
| Deep cadence | 24 h | **12 h** | launchd plist `~/Library/LaunchAgents/com.kalshi.governance.deep.plist` `StartInterval` 86400 → 43200 |
| Evidence window | 168 h (7 d) | **NO CHANGE** | n/a (changing this would shift decision policy per Codex 2026-05-05) |
| Predicted-effect horizon | +7 d / +1 d | **NO CHANGE** | n/a (decision-policy load-bearing) |
| G1 / G2-G6 gate thresholds | per `tasks/trade_readiness_gate.py` | **NO CHANGE** | n/a (Lever B / future soak territory) |
| LLM model | qwen3:14b (or current) | **NO CHANGE** | n/a (model swap = §8.5.2 decision-policy boundary) |

**Why only fast/deep cadence:** these are LLM-throughput knobs. Halving them doubles call rate without altering the decision function. Memory `feedback_soak_acceleration_split.md` records the user/Codex alignment from 2026-05-05 — never co-apply calendar-floor cuts and decision-policy cuts in the same soak.

## 4. Plist edit procedure

Pre-soak, edit both plists on the Mac Studio:

```bash
# Stop both governance jobs first
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.deep.plist

# Edit StartInterval values
# fast: 7200 → 5400
# deep: 86400 → 43200
# (Use plutil or hand-edit; tab-aligned XML)

plutil -replace StartInterval -integer 5400 ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
plutil -replace StartInterval -integer 43200 ~/Library/LaunchAgents/com.kalshi.governance.deep.plist

# Verify
plutil -p ~/Library/LaunchAgents/com.kalshi.governance.fast.plist | grep StartInterval
plutil -p ~/Library/LaunchAgents/com.kalshi.governance.deep.plist | grep StartInterval

# Reload
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.deep.plist

# Confirm jobs alive
launchctl list | grep com.kalshi.governance
```

PROFIT-PHASE2-002 cycle 1 is the first GOVERNANCE_CYCLE_START emitted after the reload.

## 5. Acceptance criteria

Mirrors §8.5 from `2026-04-24-llm-governance-agent-design.md`, with §8.5.1 early-close path open:

| # | gate | floor |
|---|---|---|
| 1 | GOVERNANCE_DECISION volume | ≥ 30 |
| 2 | calendar floor | ≥ 14 d (or ≥ 7 d under §8.5.1 early-close) |
| 3 | safety counters | KILL_SWITCH = 0 AND batch_aborted = 0 AND VALIDATION_ERROR = 0 |
| 4 | PARSE_ERROR trailing 72 h | 0 |
| 5 | cadence stability | max gap ≤ 3 h (note: with 90 min fast cadence, the 3 h gap floor leaves only one missed cycle of headroom — this is intentional) |
| 6 | manual review reasonable rate | ≥ 85 % |
| 7 | soak invariant | 0 behavioural commits to soak surfaces (per `scripts/check_soak_invariant.sh`) OR §8.5.2 carve-out invoked |
| 8 | written attestation | `docs/governance/PROFIT-PHASE2-002-attestation.md` (template per PHASE2-001) |

**Cadence stability nuance:** at 90 min fast cadence, the per-cycle headroom against the 3 h gap floor is tighter (1 missed cycle = 3 h gap; 2 missed = 4.5 h breach). If gate 5 fires during PHASE2-002, it may be a load-test signal (LLM throughput backed up) rather than a launchd / OS bug. Operator should distinguish before falling through.

## 6. Cadence-tune-specific health checks

In addition to the §5 gates, PHASE2-002 monitors LLM throughput health:

```bash
# Per-cycle LLM-call duration distribution
.venv/bin/python -c "
import json
from statistics import median, quantiles
durs = []
for line in open('logs/governance/decisions.jsonl'):
    try: r = json.loads(line)
    except: continue
    if r.get('type') != 'GOVERNANCE_DECISION': continue
    d = r.get('llm_call_duration_seconds')
    if d is not None: durs.append(d)
if durs:
    print(f'n={len(durs)} median={median(durs):.1f}s p90={quantiles(durs, n=10)[8]:.1f}s max={max(durs):.1f}s')
"
```

**Health bound:** p90 LLM call duration < 60 s. At 90 min cadence, sustained p90 > 60 s indicates Ollama queue depth growing — fast cadence may be too tight for current model / hardware combo. **If this fires:** revert plist to 120 min fast cadence and re-evaluate with a fresh soak.

## 7. Exit criteria delta vs PHASE2-001

Same gate floors. Differences:

- **Per-day decision count is roughly 33 % higher** (90 min fast cadence vs 120 min). Volume gate is satisfied earlier in calendar terms.
- **Day-3 distribution analysis comes earlier** (~ 60 decisions by 24 h vs 36 in PHASE2-001). Operator can produce the `2026-05-05-PROFIT-PHASE2-001-decision-distribution-analysis.md` analog by day-3.
- **§8.5.1 early-close gates may fire even faster.** Day-5 close is conceivable under PHASE2-002 if gates 1-7 all hold; but the calendar-floor gate (≥ 7 d under §8.5.1, ≥ 14 d under default) is independent of cadence — it's a confidence margin, not a derivation.

## 8. Failure modes specific to PHASE2-002

- **Ollama queue overrun.** 90 min cadence + slow model = call backlog. Mitigation: §6 health check; revert plist on p90 breach.
- **launchd drift on the tighter cadence.** macOS launchd has minimum-resolution behaviour around minute boundaries. 5400 s / 90 min may alias against system load events. Mitigation: monitor `GOVERNANCE_CYCLE_START` arrival distribution; expect ±10 % normal jitter, escalate at ±20 %.
- **Decision-shape drift not attributable to cadence.** If decision distribution shifts vs PHASE2-001 (e.g., disable_source share changes), it's almost certainly the post-Wave-1 environmental change (MATCH-001 (B') + Lever A.1 changing source labels), NOT the cadence. **Document and continue** — PHASE2-002's purpose includes capturing this drift.

## 9. Out of scope

- **Evidence-window halving (168 h → 84 h).** Decision-policy change. Defer to a separate, audited soak per `PROFIT-PHASE2-001-early-close-criteria.md` §3.
- **Gate-threshold tuning (G1 / G2 / etc.).** Lever B / Lever C / future Wave-3 territory. Land in their own deploys with their own pre-deploy attribution.
- **LLM model swap.** §8.5.2 decision-policy boundary; would require a fresh soak.
- **Cross-platform validation (Windows).** Not in PHASE2 scope; Mac Studio is the soak surface.

## 10. Post-PHASE2-002 next steps

If gates pass: cadence-tune is validated as default for future soaks. Update PROFIT-PHASE2-003 onboarding (TBD) to start at 90 min / 12 h cadence by default.

If gate 5 (cadence stability) or §6 health bound fires: revert plist to 120 min / 24 h, document the breach, and re-run PHASE2-002 at the slower cadence with the same soak window as a fall-back.

If gate 6 (manual review reasonable rate) drops below 85 % for the first time: deeper investigation. The cadence change should not affect decision quality; if it does, it's an unexpected LLM-context-window / batch-coherence interaction.

## 11. Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` §3 — cadence-tune deferral that PHASE2-002 resolves
- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1 + §8.5.2 — acceptance criteria + early-close + carve-out
- `docs/governance/PROFIT-PHASE2-001-day-7-close-day-walkthrough.md` — operator playbook (mirrored for PHASE2-002 at close time)
- `docs/governance/PROFIT-PHASE2-001-early-close-attestation-template.md` — attestation template (mirrored for PHASE2-002)
- `docs/governance/wave-1-post-deploy-observation-plan.md` — entry criteria #4 dependency
- `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_soak_acceleration_split.md` — discipline rule that bars co-applying calendar + policy cuts
