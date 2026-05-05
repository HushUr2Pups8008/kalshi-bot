# Next-soak cadence-tune — design spec

**Status:** design (applies to the NEXT post-Wave-1 governance shadow soak; NOT applied mid-PROFIT-PHASE2-001)
**Tracker:** PROFIT-PHASE2-002 (proposed; opens when Wave-1 closes and the next soak begins)
**Owner:** Operator + Claude
**Drafted:** 2026-05-05 (during PROFIT-PHASE2-001 wind-down)
**Resolves:** the soak-acceleration discussion 2026-05-05 (user + Codex aligned). Cadence-knob halving applies to the next soak from cycle 1, not retroactively.

## 1. Why this spec exists

PROFIT-PHASE2-001 is closing under §8.5.1 early-close (target 2026-05-08 or 2026-05-10 depending on gate-7 resolution). The 14-day soak ran on 2 h fast / 24 h deep cadence per the Phase 2 spec. Going forward, the operator wants tighter observation cadence to compress the next soak's calendar window without diluting decision-policy fidelity.

Two distinct dimensions to tune:

1. **Cadence (cycle frequency).** Fast 2 h → 90 min. Deep 24 h → 12 h. This is a load-test (60 % more LLM calls per day; 2× deep-review frequency); does NOT change decision policy.
2. **Decision policy (LLM input shape).** Evidence window 168 h, predicted-effect horizon +7 d / +1 d. Halving these would change disable/keep recommendations. Codex flagged this asymmetry; **NOT in scope for this spec.**

Spec covers (1) only. Decision-policy tuning is reserved for a separate, audited change against a dedicated soak (likely PROFIT-PHASE2-003 if needed).

## 2. The change

### 2.1 Fast cadence: 7200 s → 5400 s

`ops/launchd/com.kalshi.governance.fast.plist.template`:

```xml
<key>StartInterval</key>
- <integer>7200</integer>
+ <integer>5400</integer>
```

Effect: cycle frequency 30 / day → 40 / day. LLM call rate +33 %.

### 2.2 Deep cadence: 24 h → 12 h

`ops/launchd/com.kalshi.governance.deep.plist.template`:

The current template uses `StartCalendarInterval` with `Hour` + `Minute`. Switching to 12 h cadence requires either:

**Option A:** twice-daily `StartCalendarInterval` (preserves wall-clock-aligned scheduling):

```xml
- <key>StartCalendarInterval</key>
- <dict>
-   <key>Hour</key>
-   <integer>3</integer>
-   <key>Minute</key>
-   <integer>0</integer>
- </dict>
+ <key>StartCalendarInterval</key>
+ <array>
+   <dict>
+     <key>Hour</key>
+     <integer>3</integer>
+     <key>Minute</key>
+     <integer>0</integer>
+   </dict>
+   <dict>
+     <key>Hour</key>
+     <integer>15</integer>
+     <key>Minute</key>
+     <integer>0</integer>
+   </dict>
+ </array>
```

**Option B:** switch to `StartInterval=43200` (12 h):

```xml
- <key>StartCalendarInterval</key>
- <dict>...</dict>
+ <key>StartInterval</key>
+ <integer>43200</integer>
```

**Recommendation: Option A.** Wall-clock alignment matches operator habits (two specific times each day). Option B drifts based on launch time.

### 2.3 Evidence window — DO NOT CHANGE

`governance/evidence.py` `window_hours=168` stays at 168. Per Codex's flag (2026-05-05): halving 168 h → 84 h is a decision-policy change, not a cadence change. A source with weekly cadence may flip disable/keep recommendation under 84 h that wouldn't under 168 h. Test as a separate audited change against a fresh soak.

### 2.4 Predicted-effect horizon — DO NOT CHANGE

`governance/decision.py` `evaluate_at = decided_at + (1 d | 7 d)` stays as-is. Same reasoning as 2.3 — changes Phase 3 auto-revert baseline.

## 3. Pre-deploy verification

Before applying the cadence-tune to the next soak's launchd plists:

1. **Throughput check.** Run a 6-cycle dry-run on the Mac Studio with the 5400 s interval. Verify each cycle completes in well under 5400 s (typical observed: 22-30 s; 1.5 % CPU utilisation). If cycle duration approaches the interval, queue depth grows; abort.
2. **OllamaLocalQwenLLM throughput.** Each cycle issues N LLM calls (N = number of candidate sources flagged). At 90 min cadence × N=10-20 calls / cycle, sustained rate is 7-13 calls / hour. Compare with single-call latency (typically 10-30 s). If sustained rate × latency approaches 60 minutes, there's queue pressure; abort.
3. **Memory pressure.** qwen3:14b VRAM ~10 GB. The Mac Studio has fixed VRAM; concurrent + back-to-back calls don't multiply VRAM use, but they prevent the model from being unloaded between calls (which the operator may rely on for other workloads). Monitor `nvidia-smi`-equivalent / `system.log` for thermal-throttle events during the dry-run.
4. **launchd validation.** `plutil ops/launchd/com.kalshi.governance.{fast,deep}.plist` must return `OK` after the template substitution. Run before `launchctl load`.

## 4. Rollback

If post-cadence-tune the next soak shows:
- Cycle duration approaching the 5400 s interval (queue overflow), OR
- LLM call latency degraded > 2× the pre-tune baseline, OR
- Mac Studio thermal-throttle events > 5 / day

Rollback by reverting the plist edits and `launchctl unload && launchctl load` on both. Cadence reverts to 2 h / 24 h.

## 5. Acceptance criteria for the cadence-tune itself

These are gates on the cadence-tune CHANGE, not on the next soak's overall §8.5 acceptance:

- [ ] Fast cadence verified: 30 cycles in 24 h (was 12)
- [ ] Deep cadence verified: 2 cycles in 24 h (was 1)
- [ ] Cycle duration p90 < 60 s under the new cadence
- [ ] Zero `batch_aborted=True` events in the first 24 h
- [ ] Zero new `KILL_SWITCH` / `VALIDATION_ERROR` events
- [ ] Operator notes any thermal / latency degradation in the next-soak open-attestation

## 6. What this spec is NOT

- NOT a mid-PROFIT-PHASE2-001 cadence change. The current soak runs on 2 h / 24 h until close per the soak invariant.
- NOT an evidence-window halving. That requires a separate spec.
- NOT a decision-policy change. The LLM model, prompt, gating thresholds all stay constant across the cadence-tune boundary.
- NOT a §8.5 acceptance-criteria revision. §8.5.1 already specifies the early-close + cadence-tune-deferral policy.

## 7. Cross-links

- `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` §3 — locked recommendation that this spec implements
- `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5 + §8.5.1 — Phase 2 acceptance criteria + early-close addendum
- `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/feedback_soak_acceleration_split.md` — the (a) calendar-floor cut OK / (b) policy-knob cut defer-to-next-soak feedback memory
- `ops/launchd/install.sh` — installer that templates the plists; needs no changes (it preserves whatever values the templates contain)
