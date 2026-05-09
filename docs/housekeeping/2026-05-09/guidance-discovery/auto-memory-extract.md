# Auto-Memory Extract — Phase 5 Agent 3

**Generated:** 2026-05-08
**Source:** ~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/
**Total entry files:** 6 (excluding MEMORY.md)
**Index file present:** YES

## MEMORY.md index (verbatim)
```
- [Soak confirmation cadence](feedback_soak_confirmation_cadence.md) — cap mid-soak confirmation reports at 1 per UTC day per agent
- [Soak-acceleration split](feedback_soak_acceleration_split.md) — calendar-floor cut vs decision-policy cut: never co-apply mid-soak
- [Monitor probe vs TaskList](feedback_monitor_probe_vs_tasklist.md) — Monitor lives in a separate namespace; probe via TaskOutput, never TaskList
- [Edge priority over deploy safety](feedback_edge_priority_over_deploy_safety.md) — making money is the goal; behavioral deploys require replayed-EV evidence per IC §16, NOT just "deploy ready"
- [Audit scorer before verdict](feedback_audit_scorer_before_verdict.md) — charter-label match is necessary but not sufficient; load-bearing scorer assumptions must be audited before consuming operational interpretation
- [Market-implied baseline](feedback_market_implied_baseline.md) — replay win-rate baseline is `Σ p_yes_at_decision_time`, not 50% coin-flip; longshot YES selection makes naive 50% framing produce false anomaly readings
```

## Index ↔ files cross-check
| Indexed in MEMORY.md? | File on disk? | Filename | Title in index |
|---|---|---|---|
| ✓ | ✓ | feedback_soak_confirmation_cadence.md | Soak confirmation cadence |
| ✓ | ✓ | feedback_soak_acceleration_split.md | Soak-acceleration split |
| ✓ | ✓ | feedback_monitor_probe_vs_tasklist.md | Monitor probe vs TaskList |
| ✓ | ✓ | feedback_edge_priority_over_deploy_safety.md | Edge priority over deploy safety |
| ✓ | ✓ | feedback_audit_scorer_before_verdict.md | Audit scorer before verdict |
| ✓ | ✓ | feedback_market_implied_baseline.md | Market-implied baseline |

No orphans, no missing entries. Index and disk are in perfect alignment.

## Per-entry catalog

### `feedback_audit_scorer_before_verdict.md`

**name:** Audit scorer assumptions before accepting verdict
**description:** Load-bearing scorer/harness assumptions must be audited before consuming a verdict's operational interpretation; charter-label match is necessary but not sufficient
**type:** feedback
**file size:** 2316 bytes
**last modified:** 2026-05-06 UTC

**Body:**
```
When a replay/scorer/harness produces a verdict that meets a charter-locked label, the **operational interpretation** of that label (e.g., "information frontier holds", "no signal", "negative EV") still depends on load-bearing scorer assumptions being correct. Charter-label match is necessary but not sufficient.

**Why:** 2026-05-07 Cycle-16D incident. M6 Claude appendix accepted `extraction_fixed_but_information_frontier_holds` per locked criteria, hypothesized "anti-correlated signal vs overfit" rationale for the 0.84% win rate. Operator independent review caught three scorer bugs that M6 missed:
1. `would_have_traded` did not gate on G1-G6 readiness — over-admitted trades.
2. 231 YES / 6 NO bias — sign / overfit / scorer-construction issue.
3. Price-unit cents-vs-dollars consistency unaudited — possible 100x error.

Routing to §B onboarding / §C redesign on a flawed scorer would have wasted operator time. Operator override withdrew the operational reading; PROFIT-EDGE-010 amended from "Cycle-17 operator decision" → "Cycle-16E scorer forensics."

**How to apply:** Before any future M6/L6-equivalent verdict-acceptance step in a replay/scorer cycle:

1. **List the scorer's load-bearing assumptions explicitly** before consuming the verdict — e.g., "trade gating matches production G1-G6", "price units consistent end-to-end", "sample is not biased by selection in N dimensions", "deduplication matches production cooldown".
2. **Sanity-check at least one anomalous data point** against scorer assumptions. A 0.84% win rate vs random ~50% is anomalous; should trigger "is the scorer doing what we think?" before "is the bot signal anti-correlated?". The simpler hypothesis (scorer bug) precedes the harder hypothesis (genuine anti-correlation).
3. **Charter-label match ≠ verdict acceptance.** Both must hold. Charter-label is the necessary condition; clean scorer is the sufficient condition. Document this distinction in the M6/L6 appendix to surface the assumption set.
```

**Cross-reference flags:**
- Mentions specific function/symbol names: `would_have_traded`, `G1-G6`, `extraction_fixed_but_information_frontier_holds`
- Mentions tracking IDs: `PROFIT-EDGE-010`
- Contains a specific date: `2026-05-07` (absolute, not relative — OK)
- References cycle labels: `Cycle-16D`, `Cycle-16E`, `Cycle-17`, `M6`, `L6`
- Likely overlaps another memory entry: `feedback_market_implied_baseline.md` (same Cycle-16D incident; baseline is one of the scorer assumptions this entry warns about)

---

### `feedback_edge_priority_over_deploy_safety.md`

**name:** Edge creation is the goal — not deploy safety
**description:** Project's purpose is making money on Kalshi; behavioral deploys require replayed-EV evidence per IC §16, not "deploy readiness theater"
**type:** feedback
**file size:** 2642 bytes
**last modified:** 2026-05-05 UTC

**Body:**
```
The kalshi-bot project's entire purpose is making money. As of 2026-05-06: 3 lifetime paper trades, 0 wins, -$7.50 P&L, 1-source dependence (VitalLaw), 89 % of OBS-003 SKIPPED records show `edge +0.0000` (no informational advantage). Cycles 7-11 shipped deployment safety, observability, governance, and operator control — necessary, but NOT edge creation.

**Rule:** Treat "Wave-2 deploy ready" / "more feeds onboarded" / "tighter G1 threshold" / "more drift gates" as **operating-the-bot-cleanly progress**, NOT progress toward profit. Don't conflate them. Implementation Contract §16 codifies this: behavioral deploys (intake / classifier / blender / gates / sizing) require replayed-EV evidence; safety / observability / governance are exempt.

**Why:** Operator escalation 2026-05-06: "If we're not making money with the kalshi-bot, all of the work is literally for nothing. The entire purpose of this endeavour is to make money. All work needs to support that goal, period." Conversation between operator and Codex (forwarded to Claude) explicitly redirected the project from "ship Wave-2 / Wave-3" to "build replay harness first; gate Wave-2/3 on replay output."

**How to apply:**
- When proposing new tasks: ask "does this support edge creation, OR is it deploy safety / observability / governance / operator control?" Both are valid, but be explicit about category. Operator decides if a non-edge task is worth doing right now.
- When pre-staging a behavioral deploy: ask "what's the replayed-EV evidence?" If none, the deploy is speculation and IC §16 blocks it.
- Bug fixes with mechanical hypotheses (e.g. OBS-005 cooldown sentinel) are exempt — their value is mechanical correctness, not edge.
- "May increase trade rate" is NOT enough. "Would have produced positive EV on the last 30 resolved markets" IS.
- Negative replay evidence is also evidence — it triggers strategic-pivot conversation, not "ship anyway."
- Wave-1 (cycle 11.5 frame: cleanup / observability release) ships 2026-05-08 because work is done + OBS-005 is mechanical. Wave-2 + Wave-3 are HALTED pending replay.

**See also:**
- `docs/IMPLEMENTATION_CONTRACT.md` §16 (Replayed-EV Gate)
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` (full redirect doc)
- `docs/governance/edge-replay-cycle12-report.md` (FUTURE; Codex's Cycle-12 deliverable)
```

**Cross-reference flags:**
- Mentions specific file paths: `docs/IMPLEMENTATION_CONTRACT.md`, `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md`, `docs/governance/edge-replay-cycle12-report.md` (the third is explicitly flagged FUTURE — verify it exists now)
- Mentions specific symbol/flag names: `G1`, `OBS-003`, `OBS-005`, `IC §16`
- Contains specific dates: `2026-05-06`, `2026-05-08` (absolute — OK)
- References cycles/waves: `Cycles 7-11`, `Wave-1`, `Wave-2`, `Wave-3`, `cycle 11.5`, `Cycle-12`
- Stated point-in-time facts (lifetime trades, P&L, OBS-003 skip rate) are dated 2026-05-06 — these will rot quickly; verify before relying
- Likely overlaps another memory entry: `feedback_audit_scorer_before_verdict.md`, `feedback_market_implied_baseline.md` (all three relate to the edge-replay program)

---

### `feedback_market_implied_baseline.md`

**name:** Use market-implied baseline for replay win-rate analysis, not 50% coin-flip
**description:** When evaluating replay win rate on prediction markets, the baseline is `Σ p_yes_at_decision_time * 1[resolved=yes_side]`, NOT 50%; longshot YES selection makes naive 50% framing produce false anomaly readings
**type:** feedback
**file size:** 2813 bytes
**last modified:** 2026-05-07 UTC

**Body:**
```
When the replay scorer reports a win rate that looks anomalous, the baseline must be **market-implied expected wins**, not a 50% coin-flip. Prediction-market trading at low-cent prices is asymmetric: a YES bet at 5¢ on a market that resolves NO 95% of the time loses 95% of the time AND that is the market-implied expected outcome, not a sign of anti-correlation.

**Why:** 2026-05-07 Cycle-16D incident. Cycle-16D D8 reported 2/237 wins (0.84% win rate). Claude M6 appendix invoked "anti-correlated signal" hypothesis based on 50% coin-flip baseline (118.5 expected wins). **Both the baseline and the hypothesis were wrong.** Most raw trades were YES at low cents; market-implied expected wins = 9.463, not 118.5. Got 2 wins; underperforms by 7.463; not "99.16% wrong-direction."

Codex's Cycle-16E forensics (`c913ffd`) caught this with the explicit market-implied formula in `scorer_forensics_audit.py:market_implied_win_prob`:
- For YES-side trade: `p_win = market_yes_price / 100` (decimal probability implied by market price in cents).
- For NO-side trade: `p_win = (100 - market_yes_price) / 100`.
- Expected wins across N trades = `Σ p_win[i]`.
- Compare actual wins to this expected count, not to 0.5 * N.

Fixture test that locks the formula: `test_market_implied_win_prob_uses_price_not_coin_flip` in `tests/test_edge_replay_scorer_forensics_audit.py`.

**How to apply:** When reading any replay-harness win-rate output:

1. **Compute market-implied expected wins.** Sum `p_yes_at_decision_time` for YES-side trades + `(1 - p_yes_at_decision_time)` for NO-side trades. This is the baseline against which "actual wins" should be compared.
2. **Check the price distribution before invoking signal-anti-correlation hypothesis.** If most trades are at low cents (< 25¢ YES bets), the "random ~50%" framing is wrong by construction. Underperformance vs market-implied baseline is the real signal; near-baseline is the null.
3. **Distinguish three failure modes** when win rate looks low: (a) signal genuinely absent — actual wins ≈ market-implied; (b) signal anti-correlated — actual wins << market-implied with statistical confidence; (c) scorer / data bug — investigate before invoking signal hypotheses.
4. **Apply this rule to L6/M6/N6 verdict-acceptance steps** in any replay-harness cycle. Document the baseline calculation in the verdict appendix so future reviewers can audit the math without re-deriving.
```

**Cross-reference flags:**
- Mentions specific file paths: `scorer_forensics_audit.py`, `tests/test_edge_replay_scorer_forensics_audit.py`
- Mentions specific function/symbol names: `market_implied_win_prob`, `test_market_implied_win_prob_uses_price_not_coin_flip`, `p_yes_at_decision_time`
- Mentions a git commit SHA: `c913ffd` (verify exists in main)
- Contains specific date: `2026-05-07` (absolute — OK)
- References cycle labels: `Cycle-16D`, `Cycle-16E`, `D8`, `M6`, `L6`, `N6`
- References Codex (parallel agent)
- Likely overlaps another memory entry: `feedback_audit_scorer_before_verdict.md` (same incident, same scorer)
- Aligns with project CLAUDE.md instruction to keep probabilities between 0 and 1 — but does NOT directly conflict with any documented gotcha
- Domain-constraint adjacent: probability/price logic; verify against `~/.claude/rules/domain_constraints.md` "implied probability" rule

---

### `feedback_monitor_probe_vs_tasklist.md`

**name:** Monitor probe is TaskOutput, not TaskList
**description:** TaskList does NOT enumerate Monitor tasks; probing TaskList to decide whether to re-arm Monitor produces stale duplicate monitors
**type:** feedback
**file size:** 2031 bytes
**last modified:** 2026-05-05 UTC

**Body:**
```
In /loop dynamic mode (and any other context), do NOT call `TaskList` to decide whether a Monitor task is still running. `TaskList` enumerates only TodoWrite-managed tasks (TaskCreate IDs are integers like `#34`); Monitor task IDs use a different ID scheme (e.g. `bx9v2bme5`) and live in a separate namespace that `TaskList` does not see. An empty `TaskList` does NOT mean Monitor died.

The correct probe is `TaskOutput task_id:<id> block:false` — returns `status: running` if alive, or `completed` / `failed` if not. `TaskGet` also returns "not found" for Monitor IDs, so it's not a valid probe either.

**Why:** the bundled `/loop` skill says "call TaskList first and skip this step if a monitor is already running" — which is misleading for Monitor. Following that literally caused a session to spawn 6+ stale duplicate `tail -F` monitors over multiple /loop wakeups (2026-05-05/06 db-backup 06:00 fire watch). Each iteration TaskList returned empty → assumed dead → re-armed → 4 simultaneous monitors confirmed alive via `TaskOutput`. Cost: would have produced 4× duplicated event notifications when the actual fire hit; manual cleanup via `TaskStop` required.

**How to apply:**
- When entering /loop dynamic mode, remember the Monitor ID from the previous arm (it's in the prior `Monitor started (task <id>...)` line in conversation history).
- Probe with `TaskOutput task_id:<id> block:false timeout:3000`. If `status: running` → skip re-arm, just refresh ScheduleWakeup heartbeat. If not found / completed / failed → arm a fresh Monitor.
- If conversation context lost the Monitor ID (e.g. post-compaction) and you must arm a new one, that's the only legitimate "spawn fresh" case.
- Do NOT rely on `TaskList` for Monitor detection. Ever.
```

**Cross-reference flags:**
- Mentions tool names (harness/runtime, not project code): `TaskList`, `TaskOutput`, `TaskGet`, `TaskStop`, `TaskCreate`, `Monitor`, `TodoWrite`, `ScheduleWakeup`, `/loop`
- Contains specific dates: `2026-05-05/06` (absolute date range — OK)
- This is a harness/tooling lesson, NOT project code — does not reference any kalshi-bot file
- No overlap with other memory entries; orthogonal subject (Claude harness behavior)
- Cross-cuts global rules: relevant to `~/.claude/CLAUDE.md` "Working Style" / delegation, but not to any specific rule file

---

### `feedback_soak_acceleration_split.md`

**name:** Soak-acceleration split — calendar-floor cuts vs decision-policy cuts
**description:** When user asks to compress a shadow-soak window, split the proposal into (a) calendar-floor cut (low-risk) vs (b) cadence/policy-knob cut (mid-soak = invalidates measurement). Always recommend (a) now, (b) for next soak from cycle 1.
**type:** feedback
**file size:** 2585 bytes
**last modified:** 2026-05-04 UTC

**Body:**
```
When user proposes compressing a soak (shadow-mode governance soak, post-deploy validation soak, etc.):

**Always split into two questions:**
1. **Calendar-floor cut** (e.g., 14 d → 7 d). Affects only the duration gate; preserves data integrity.
2. **Cadence / decision-policy cut** (e.g., fast 2 h → 90 min, evidence-window 168 h → 84 h). Mid-soak application changes the LLM's input shape and makes accumulated decisions non-comparable to subsequent ones.

**Why:** mid-soak knob-tuning creates a mixed-policy study. The first N days run on policy X; the next N days run on policy Y. Aggregate stats blur the policies. The whole point of a shadow soak is to measure ONE configuration in calendar time — a sliding policy invalidates that.

**How to apply:**
- For a calendar-floor cut: propose if (i) volume gate already cleared, (ii) safety counters clean, (iii) no concrete failure mode the cut-period would have detected. Require a written §-addendum specifying the relaxed gate so future readers don't think the spec was silently rewritten.
- For cadence/policy knob cuts: defer to NEXT soak, applied from cycle 1. Document the intended next-soak knob change in the same addendum.
- **Never apply policy knob cuts mid-soak.** Same reasoning that holds for "no behavioural code change to the running bot during the measurement window."

**Specific knob asymmetry (governance Phase 2 example):**
- Fast/deep cadence halving (2 h → 90 min, 24 h → 12 h) is a load-test, not a decision-policy change. Defensible for a next-soak first-cycle change.
- Evidence-window halving (168 h → 84 h) IS a decision-policy change. A source with weekly cadence may flip disable/keep recommendation under 84 h that wouldn't under 168 h. Test as a separate, audited change against a fresh soak.

**Reason this pattern matters:** PROFIT-PHASE2-001 day-4 (2026-05-04) had volume gate cleared at 5.3× and 0 safety-counter events; user asked to compress 14 d → 7 d. Codex aligned on calendar-floor cut + policy-knob deferral. Decision recorded in `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` §8.5.1 + `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md`.
```

**Cross-reference flags:**
- Mentions specific file paths: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`, `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md`
- Mentions tracking ID: `PROFIT-PHASE2-001`
- Contains specific date: `2026-05-04` (absolute — OK)
- Mentions specific symbol/concept names: `fast cadence`, `deep cadence`, `evidence-window`, governance Phase 2
- Likely overlaps another memory entry: `feedback_soak_confirmation_cadence.md` (sibling soak-management lesson, both PROFIT-PHASE2-001-derived)
- Domain-constraint adjacent: `~/.claude/rules/domain_constraints.md` governs `/governance` changes; this rule augments behavior but does not conflict

---

### `feedback_soak_confirmation_cadence.md`

**name:** Soak confirmation cadence — 1x/day max
**description:** User does not want repeated mid-soak confirmation reports during PROFIT-PHASE2-001 (or successor soak windows); 1 per UTC day is the cap. Multi-snapshot-per-day cycles waste tokens.
**type:** feedback
**file size:** 1414 bytes
**last modified:** 2026-05-04 UTC

**Body:**
```
Mid-soak confirmation reports (day-N confirmation, snapshot-N, day-N-pending placeholder, etc.) cap at 1 per UTC day.

**Why:** During PROFIT-PHASE2-001 the cadence drifted to 2-5 snapshots per UTC day during the early days (snapshot-1/-2/-3/-4/-5 all on 2026-05-03). User flagged 2026-05-04: "limit the soak confirmations to 1x per day. I don't want to waste cycles on this task moving forward."

**How to apply:**
- If a day-N confirmation has already landed for the current UTC day, do NOT propose another one for the same UTC day. Skip; offer alternative tasks instead.
- Codex sanity-checks complement (do not duplicate) Claude's day-N confirmation. One-of-each-per-day is OK; same-agent duplicates are not.
- Snapshots between confirmations are OK ONLY when there's a real state-change escalation criterion (e.g., snapshot-4 fired because cycle gap was at the cadence threshold and operator needed to decide whether to escalate). Otherwise skip.
- Day-N-pending placeholders are OK if used to maintain audit-chain continuity across UTC date boundaries, but cap at 1 per UTC day per agent.
```

**Cross-reference flags:**
- Mentions tracking ID: `PROFIT-PHASE2-001`
- Contains specific dates: `2026-05-03`, `2026-05-04` (absolute — OK)
- Refers to "successor soak windows" — generalizes beyond PROFIT-PHASE2-001 (good — does not rot when that soak closes)
- References Codex (parallel agent)
- Likely overlaps another memory entry: `feedback_soak_acceleration_split.md` (same PROFIT-PHASE2-001 incident root, different lesson)
- No project file/code references; harness-cadence rule

---

## Stats

- **Total entries by type:** user=0, feedback=6, project=0, reference=0
- **Entries with relative dates** (will need absolute conversion or removal): **0** — all dates in all entries are absolute ISO-style (`2026-05-XX`); no "yesterday", "next week", "Thursday", etc. Note: "FUTURE" appears as a tag in `feedback_edge_priority_over_deploy_safety.md` but refers to a doc not yet written, not a relative date.
- **Entries referencing specific paths/symbols** (will need verification before Phase 8): **5**
  - `feedback_audit_scorer_before_verdict.md` — `would_have_traded`, `G1-G6`, `PROFIT-EDGE-010`
  - `feedback_edge_priority_over_deploy_safety.md` — 3 doc paths under `docs/`, `OBS-003`, `OBS-005`, `G1`, IC §16
  - `feedback_market_implied_baseline.md` — `scorer_forensics_audit.py`, `tests/test_edge_replay_scorer_forensics_audit.py`, function name `market_implied_win_prob`, fixture name, commit `c913ffd`
  - `feedback_soak_acceleration_split.md` — 2 doc paths under `docs/`, `PROFIT-PHASE2-001`
  - `feedback_soak_confirmation_cadence.md` — `PROFIT-PHASE2-001` (no file paths or code symbols)
- **Orphan entries** (in dir but not in MEMORY.md): **0**
- **Missing entries** (in MEMORY.md but not on disk): **0**

**Reconciliation:** 6 files on disk. 6 entries in index. 6 entries in catalog above. 6 = 6 = 6. Index and disk are in sync.

**Type-distribution observation:** all 6 entries are `feedback` type. No `user` (preference), `project` (project-state fact), or `reference` (lookup) entries exist. This is consistent with the auto-memory system being driven primarily by feedback-extraction hooks during sessions.

**Staleness flag observation:** the system injected a `<system-reminder>` "memory is N days old, verify against current code" warning on 4 of the 6 file reads (the 4 that were ≥2 days old at extraction time on 2026-05-08). Phase 6/7 reviewers should treat this as upstream signal that the auto-memory system itself acknowledges these entries can rot.
