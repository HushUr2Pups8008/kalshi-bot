# LLM Governance Agent — Design Spec

| Field | Value |
|---|---|
| Status | DRAFT — pending user review |
| Author | Claude |
| Date drafted | 2026-04-24 |
| Hardware target | Mac Studio M4 Max 128GB (arrival 2026-04-29 / 30, see `docs/_archive/studies/future_plans.md`) |
| Cross-references | `docs/_archive/studies/future_plans.md` (Phase 3 multi-agent, Phase 6 dynamic keyword weighting); `~/.claude/projects/-Users-Jake-vscode-kalshi-bot/memory/project_adaptive_governance_direction.md` (user direction memo) |
| Implements | The "LLM-driven adaptive pipeline governance" design direction articulated by the user 2026-04-24 |
| Blocks | None — net-new feature |
| Phase rollout | Approach 2 (phased, four phases — see §10) |

## 1. Context

The kalshi-bot pipeline today depends on two static, hand-curated lists:
- `DISABLED_NEWS_SOURCES` — a `set[str]` in `config.py` (currently 23 entries after P1.5.3, mostly Reddit)
- `GEOPOLITICAL_SIGNALS` — a list of dict groups in `config.py` defining keyword sets, directions, and strengths

Plus several tunable thresholds (`EARLY_MAX_NEWS_AGE_BY_SOURCE`, per-source credibility multipliers, match-score floors). All of these are governed by:

1. Operator runs a diagnostic script (`source_scorecard.py`, `keyword_promotion_report.py`, `source_market_alignment_audit.py`, `reddit_source_audit.py`, `flag_outcome_correlation.py`).
2. Operator reads the output, decides what to add/remove/tune.
3. Operator edits `config.py`, commits, restarts the bot.

This loop has reached a clear pain point — see the user's articulation captured in `project_adaptive_governance_direction.md`. Repeated diagnostic-edit-commit cycles consume operator attention that is better spent on higher-leverage work, and the cadence is too slow to catch fast-developing issues (e.g., a previously-good source going stale, a time-bounded keyword aging out).

**The fix is a governance agent: an LLM-driven process that consumes the same diagnostics the operator does, decides what to do, and applies its decisions to a runtime overrides file the bot reads at runtime — with safety scaffolding to ensure the agent cannot do harm.**

## 2. Goals

- Replace the operator-in-the-loop diagnostic→edit→commit→restart cycle with an LLM-driven loop that runs unattended on a schedule.
- Cover **enable/disable** decisions (sources, keywords) and **parameter tuning** (thresholds, strengths, credibility multipliers) — see §3 (B).
- Build with **safety as the load-bearing concern**: the agent's blast radius is bounded, every decision is auditable, every applied change is revertible.
- Architect for **future cross-bot reuse** (Polymarket waitlist, Alpaca Phase 2 from `future_plans.md`) without paying the full generalization cost up front — see §3 (decision 9).
- Ship **incrementally** with each phase independently shippable and useful — see §10.

## 3. Non-goals

- **C-stretch capabilities** (proposing entirely new keywords from miss corpora, evaluating new external source candidates, market-catalog discovery) are deferred.
- **Cross-bot generalization** beyond a thin adapter seam. We do not abstract the agent against a hypothetical equity-bot or polymarket-bot interface that doesn't yet have a defined contract.
- **Replacing the existing diagnostic scripts.** They remain first-class CLI tools for ad-hoc investigation and become library inputs for the agent.
- **Replacing the existing PROFIT-CAL-001 calibration loop.** Governance is independent of and orthogonal to calibration.
- **Real-time / event-triggered governance** beyond the periodic + daily-deep cadence. Event-triggered fast paths are explicitly C-stretch.

## 4. Decision log

These are the foundational decisions made during the brainstorming session. Each one was weighed against alternatives; the alternatives are recorded so future re-litigation has context.

| # | Decision | MVP | Stretch | Alternatives considered |
|---|---|---|---|---|
| 1 | Decision scope | Enable/disable + parameter tuning | Full governance (new keywords, source discovery) | Enable/disable only (rejected — underuses LLM) |
| 2 | Authority level | Staged auto-apply with human override | Fully autonomous + kill-switch + extensive testing | Advisory-only (rejected — preserves the pain point); fully autonomous (rejected for MVP — too much risk surface) |
| 3 | Cadence | Periodic every 2-4h fast path + daily deep review | Event-triggered + periodic safety net | Daily-only (rejected — slow); event-triggered alone (rejected — over-correction risk) |
| 4 | Model | Tiered: local Qwen3-class for bulk decisions + Claude API escalation for high-impact | (post-MVP retuning) | Shared Signal Assessment (rejected — contention); local-only (rejected — high-impact decisions warrant best judgment); Claude-only (rejected — cost, no fallback) |
| 5 | Evidence given to LLM | Metrics + recent headline samples + active market titles | + trends + peer comparisons | Metrics-only (rejected — underuses LLM); rich context (deferred — needs trend infra) |
| 6 | Output format | Single YAML overrides file + JSONL audit log, daily rotation matching `bot.log` pattern | — | Multiple YAML files (rejected — needless splitting); SQLite-backed (rejected — manual editing harder); hybrid YAML+SQLite (rejected — over-engineered) |
| 7 | Safety mechanisms | **All of:** confidence threshold, max-changes-per-run, two-level kill-switch, append-only audit log, blast-radius limiter, shadow-mode rollout, post-change auto-revert, weekly self-review, Claude-API confirmation for high-impact | (none — all in MVP per "100% confidence" bar) | Floor-only (rejected — insufficient for stated confidence bar); subset (rejected for same reason) |
| 8 | Hot-reload mechanism | Periodic poll every 10 min on the overrides file | — | Startup-only (rejected — incompatible with periodic agent); file-watch / FSEvents (rejected — cross-platform complexity for no latency benefit at governance cadence); explicit manual reload (rejected — incompatible with auto-apply authority) |
| 9 | Bot scope | Kalshi-first with refactor-friendly adapter seams | (post-MVP, when Polymarket/Alpaca arrive) | Kalshi-only hardcoded (rejected — leaves bad seams); fully bot-agnostic from day one (rejected — YAGNI) |
| 10 | Observability | Decision-count + outcome tracking with mandatory `predicted_effect` per decision | Quarterly Claude-API audit of past decisions vs outcomes | Decision-count-only (rejected — measures activity not quality) |

**Approach to building (§7-§10):** Approach 2 — phased rollout with natural cut points (Phase 1 plumbing → Phase 2 shadow agent → Phase 3 real-mode → Phase 4 tiered LLM + self-review).

## 5. Architecture

### 5.1 Process topology

Three logical processes, loosely coupled via filesystem contracts. All running on the Mac Studio post-arrival.

```
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│   kalshi-bot         │    │   governance-agent   │    │   diagnostic scripts │
│   (asyncio service)  │    │   (cron-invoked)     │    │   (CLI ad-hoc)       │
│                      │    │                      │    │                      │
│  + RuntimeOverrides- │    │  Imports diagnostic  │    │  source_scorecard,   │
│    Reader            │    │  scripts as libs     │    │  keyword_feedback,   │
│  + override-poll     │◄───┤  Invokes local LLM   │◄───┤  source_market_      │
│    asyncio task      │    │  Escalates to Claude │    │    alignment_audit,  │
│                      │    │  Writes YAML+JSONL   │    │  reddit_source_      │
└──────────┬───────────┘    └──────────┬───────────┘    │    audit, etc.       │
           │                            │                └──────────────────────┘
           │ reads                      │ writes
           │ /poll 10m                  │
           ▼                            ▼
   ┌─────────────────────────────────────────┐
   │ data/runtime_overrides.yaml             │
   │ logs/governance/decisions.jsonl(.YYYY..) │
   └─────────────────────────────────────────┘
```

Process boundaries enforce isolation: agent crashes do not crash the bot; bot restarts do not lose agent state; the overrides file is the only mutable contract between them.

### 5.2 Code organization

```
governance/                    # NEW top-level package (Phase 2+)
├── __init__.py
├── agent.py                   # entry point: python -m governance --cadence fast|deep|weekly_review
├── adapter.py                 # GovernanceAdapter protocol + KalshiGovernanceAdapter
├── decision.py                # Decision dataclass, schema validation
├── evidence.py                # composes prompt context from diagnostic scripts
├── prompts.py                 # system prompt + per-action templates
├── safety.py                  # SafetyConfig, KillSwitch, blast-radius, confidence threshold
├── llm.py                     # local Qwen3 wrapper + Claude API tiered escalation
├── audit.py                   # AuditLogger (append-only JSONL with daily rotation)
└── tests/
    ├── test_decision.py
    ├── test_safety.py
    ├── test_evidence.py
    ├── test_llm_routing.py
    └── fixtures/              # decision-quality test fixtures

utils/runtime_overrides.py     # NEW (Phase 1) — bot-side reader of YAML overrides
tasks/runtime_overrides_task.py # NEW (Phase 1) — asyncio poll task

scripts/__init__.py            # NEW (Phase 2) — make scripts/ importable as a package

docs/governance/
├── README.md                  # operator manual: emergency intervention, mode flips, rollback
└── prompts/                   # frozen historical prompt revisions for reproducibility

data/runtime_overrides.yaml    # NEW data file — written by agent, read by bot
logs/governance/decisions.jsonl # NEW log file — append-only, daily-rotated
```

### 5.3 One governance cycle (data flow)

A single invocation of `python -m governance --cadence fast`:

1. **Load current state.** Read `data/runtime_overrides.yaml` to know what overrides are already in force. Determine `mode` (shadow | real). Check kill-switches.
2. **Build evidence.** Adapter calls into diagnostic-script library functions and the trade log to compose, per decision candidate:
   - Aggregated metrics from `source_market_alignment_audit.aggregate()` etc.
   - 3-5 recent headline samples (from trade log)
   - Active market titles (from `paper_trades.db` or in-memory market_cache snapshot)
3. **Decide.** For each candidate:
   - Build prompt via `prompts.py` template
   - Invoke local Qwen3 → `Decision` object
   - If high-impact (Phase 4) → escalate to Claude API for confirmation; abstain on disagreement
   - Validate decision against safety floor (confidence ≥ threshold, action allowed, etc.)
4. **Apply blast-radius check.** Across the entire batch:
   - Count proposed source-disables, keyword changes, threshold tunings
   - If any cap exceeded, abort the entire batch (atomic), emit `GOVERNANCE_BATCH_ABORTED`, write nothing to `applied`
5. **Write outputs.**
   - Append every decision (applied or proposed) to `logs/governance/decisions.jsonl`
   - If shadow mode, write all decisions to `proposed` section; if real mode, write high-confidence to `applied`, low-confidence to `proposed`
   - Atomic write: serialize to `runtime_overrides.yaml.tmp`, `os.rename` to final path
6. **Snapshot for auto-revert.** Capture `pre_change_metrics` baseline + `evaluate_at` for the next cycle's check. Write `last_applied_batch` block to YAML.
7. **Outcome evaluation (Phase 3+).** Before deciding, evaluate any `last_applied_batch` whose `evaluate_at` has passed. If post-change metrics regress beyond threshold, emit `GOVERNANCE_AUTO_REVERT`, drop those overrides from `applied`, lock agent in read-only for one cycle.
8. **Exit.** Process terminates; cron will invoke again at next scheduled time.

## 6. Data contracts

### 6.1 `data/runtime_overrides.yaml`

Written exclusively by the governance-agent (and by humans during emergency intervention). Read on startup and every 10 min by kalshi-bot. Two top-level sections enforce shadow/real-mode separation.

```yaml
# Managed by governance-agent. See docs/governance/README.md for manual-edit protocol.
# The bot reads ONLY the `applied` section. `proposed` is human-review queue.

version: 1
updated_at: 2026-05-02T14:30:00Z
updated_by: governance-agent-v0.2.1
mode: shadow    # "shadow" = agent writes to proposed only; "real" = agent writes to applied
                # Controlled by env var GOVERNANCE_MODE; agent never flips this itself.

applied:
  disabled_sources:
    - source: "r/Turkey"
      reason: "100% stale_rate over 7d, 0 matches, 408 ingestion events"
      confidence: 0.94
      decided_at: 2026-05-02T14:30:00Z
      decided_by: governance-agent-v0.2.1
      decision_id: gd_2026-05-02_0042
      expires_at: null            # null = indefinite
      predicted_effect:
        metric: "reddit_rate_limit_budget_consumed_daily"
        baseline: 0.12
        predicted_post_change: 0.08
        evaluate_at: 2026-05-09T14:30:00Z

  disabled_keywords:
    - keyword: "trump may deadline"
      reason: "Time-bounded phrase; will be stale after May 1"
      confidence: 0.82
      decided_at: 2026-05-02T14:30:00Z
      decided_by: governance-agent-v0.2.1
      decision_id: gd_2026-05-02_0043
      expires_at: 2026-05-02T00:00:00Z
      predicted_effect:
        metric: "no_keywords_miss_rate"
        baseline: 0.23
        predicted_post_change: 0.21
        evaluate_at: 2026-05-09T14:30:00Z

  threshold_overrides:
    - path: "EARLY_MAX_NEWS_AGE_BY_SOURCE.Top Stories From the International Atomic Energy Agency"
      value: 21600
      reason: "IAEA publishes ~weekly; 1800s drops every item"
      confidence: 0.71
      decided_at: 2026-05-02T14:30:00Z
      decided_by: governance-agent-v0.2.1
      decision_id: gd_2026-05-02_0044
      expires_at: null
      predicted_effect:
        metric: "iaea_fresh_pass_count_weekly"
        baseline: 0
        predicted_post_change: 2
        evaluate_at: 2026-05-09T14:30:00Z

proposed:
  # Same structure as `applied`. Bot ignores this section entirely.
  disabled_sources: []
  disabled_keywords: []
  threshold_overrides: []

last_applied_batch:
  batch_id: gb_2026-05-02_0012
  committed_at: 2026-05-02T14:30:00Z
  pre_change_metrics:
    overall_anchor_rate: 0.9902
    active_source_count: 28
    daily_match_rate_per_1000_ingested: 4.3
  evaluate_at: 2026-05-02T18:30:00Z
```

**Schema rules:**
- Bot reads only `applied`. `proposed` is a human-review queue.
- Mode field is read-only for the agent; agent observes and abides but never flips it.
- Every entry has a `decision_id` cross-referenced to the JSONL audit log.
- Every entry's `expires_at` is checked on every bot reload; expired entries drop out of in-memory state.
- `predicted_effect` is mandatory for all entries; an entry without one is a bug in the agent.

### 6.2 `logs/governance/decisions.jsonl`

Append-only, daily-rotated (`decisions.jsonl.YYYY-MM-DD` for archives, gzip-compressed after 7 days, matching `bot.log` rotation). One JSON record per line.

```json
{
  "type": "GOVERNANCE_DECISION",
  "decision_id": "gd_2026-05-02_0042",
  "batch_id": "gb_2026-05-02_0012",
  "decided_at": "2026-05-02T14:30:00Z",
  "decided_by": "governance-agent-v0.2.1",
  "cadence": "fast",
  "action": "disable_source",
  "target": "r/Turkey",
  "proposed_change": {
    "before": "source_active",
    "after": "source_disabled",
    "expires_at": null
  },
  "model_used": "qwen3-14b-instruct",
  "escalated_to_claude": false,
  "claude_response": null,
  "confidence": 0.94,
  "reasoning": "Over 7 days: 408 ingestion events; 100% stale_rate; 7 fresh passes produced 0 MATCH_DIAGNOSTIC events. The subreddit posts geopolitical content but the headlines do not overlap any active market title. Consistent with P1.5.2 audit classification 'all_stale' / 'no_matches'. Disabling reclaims Reddit rate-limit budget.",
  "evidence_summary": {
    "source_market_alignment_audit_window_hours": 168,
    "ingestion_events": 408,
    "fresh_pass_count": 7,
    "match_count": 0,
    "recent_headline_sample": [
      "r/Turkey - Discussion of AKP economic policy",
      "r/Turkey - Istanbul mayoral election analysis",
      "r/Turkey - NATO exercises this week",
      "r/Turkey - Lira exchange rate debate",
      "r/Turkey - Erdogan speech reactions"
    ],
    "active_market_count": 287,
    "active_market_themes_top": ["trump_iran", "trump_china", "elections_us"]
  },
  "predicted_effect": {
    "metric": "reddit_rate_limit_budget_consumed_daily",
    "baseline": 0.12,
    "predicted_post_change": 0.08,
    "evaluate_at": "2026-05-09T14:30:00Z"
  },
  "outcome": null,
  "applied": true,
  "shadow_mode": false,
  "safety_checks_passed": {
    "confidence_threshold": true,
    "max_changes_per_run": true,
    "blast_radius": true,
    "kill_switch": true
  }
}
```

**Other event types in the same log:**

```json
{"type": "GOVERNANCE_CYCLE_START", "cycle_id": "...", "cadence": "fast", "started_at": "..."}
{"type": "GOVERNANCE_CYCLE_END", "cycle_id": "...", "duration_sec": 87, "decisions_made": 12, "decisions_applied": 8, "decisions_proposed": 4, "batch_aborted": false}
{"type": "GOVERNANCE_BATCH_ABORTED", "cycle_id": "...", "reason": "blast_radius_exceeded", "details": {...}}
{"type": "GOVERNANCE_AUTO_REVERT", "batch_id": "gb_...", "reason": "anchor_rate_regression", "metric_before": 0.99, "metric_after": 0.995, "reverted_decision_ids": [...]}
{"type": "GOVERNANCE_OUTCOME_EVALUATION", "decision_id": "gd_...", "predicted": {...}, "actual": {...}, "prediction_correct": true}
{"type": "GOVERNANCE_KILL_SWITCH_TRIPPED", "trigger": "env_var_GOVERNANCE_DISABLED", "at": "..."}
```

### 6.3 Versioning

- YAML `version: 1`. Schema-breaking changes bump the version with a documented migration path in the runtime-overrides reader.
- Agent refuses to write a YAML version newer than it understands (forward-compat guard).
- Agent refuses to read a YAML version older than its minimum supported (backward-compat boundary, expected only after long quiescence).

## 7. Phase 1 — Plumbing (the foundation)

**Goal:** Build the runtime-overrides infrastructure and safety primitives. Useful even with no agent — humans can edit the overrides file directly for hot-reload of config without restarting the bot.

### 7.1 New components

- **`utils/runtime_overrides.py`** — `RuntimeOverridesReader` singleton in the bot.
  - `load_from_disk()` — reads YAML, validates schema, atomic-swaps in-memory state, returns diff for logging.
  - `is_source_disabled(source) -> bool`, `is_keyword_disabled(keyword) -> bool`, `get_threshold_override(path) -> Any | None`
  - `current_state_snapshot() -> dict`
  - Schema validation: required fields, type checks, value-range checks, TTL sanity (`expires_at` not in the past at write time), `decision_id` format.

- **`tasks/runtime_overrides_task.py`** — asyncio task `run_runtime_overrides_poll(reader, interval_secs=600)`. Sleep-reload-diff-log loop. Catches all exceptions; never propagates. Schema validation failure leaves prior valid state in place.

- **Refactor existing call sites.** `if source in DISABLED_NEWS_SOURCES` → `if reader.is_source_disabled(source)` everywhere. Sites in `analysis/market_matcher.py`, `analysis/signal_analyzer.py`, `feeds/rss_monitor.py`, `feeds/reddit_monitor.py`, `main.py`. (Discovered via repo-wide grep at implementation time.)

- **Safety-floor primitives** (in `governance/safety.py`, used in Phases 2-4 but built and tested in Phase 1):
  - `SafetyConfig` dataclass — confidence threshold, max changes per run, blast-radius percentages.
  - `KillSwitch` class — checks `GOVERNANCE_DISABLED` and `GOVERNANCE_READONLY` env vars.
  - `AuditLogger` — append-only JSONL writer, daily rotation reusing `utils/logger.py` rotation primitives where possible.

- **CLI shim for emergency intervention** (`python -m utils.runtime_overrides`):
  - `--status` — prints current loaded state.
  - `--validate <path>` — validates a YAML file without touching live state.
  - `--revert-batch <batch_id>` — drops all overrides applied by a given batch.

### 7.2 Tests

- Schema validation tests (parametrized) — every required field, every type, every range, every TTL edge case. Target ≥30 cases.
- Atomic-write race tests — agent simulator + bot reader running in parallel on real filesystem.
- TTL-expiry tests — entries with `expires_at` in the past filtered correctly on load.
- Kill-switch tests — env vars short-circuit all override application paths.
- Diff-detection tests — reader correctly detects added/removed/changed entries.
- Backward-compat test — bot with overrides file deleted continues to work identically to pre-feature.
- Property test — `effective_config == static_config UNION applied_unexpired_overrides` for any sequence of valid override files.

### 7.3 Acceptance criteria

- [ ] All schema validation tests pass.
- [ ] Atomic-write race tests pass under concurrent write/read.
- [ ] All existing kalshi-bot tests still pass after refactor.
- [ ] CLI shim commands all work; documented in `docs/governance/README.md`.
- [ ] Manual smoke test: start bot, hand-edit overrides file, observe diff in `bot.log` within one poll interval, observe pipeline behavior change.
- [ ] Phase 1 commits land on a feature branch, get an ultrareview pass, then merge to main.

### 7.4 Estimated scope

400-600 LOC implementation + 600-1000 LOC tests. ~1 week active development.

## 8. Phase 2 — Local-only governance agent in shadow mode

**Goal:** Produce real governance decisions without applying them. Build the trust dataset.

### 8.1 New components

- **`governance/`** new top-level package (see §5.2 for layout).
- **`governance/agent.py`** — entry point. CLI: `python -m governance --cadence fast|deep`. Loads adapter, builds evidence, runs decision loop, writes outputs, exits.
- **`governance/adapter.py`** — `GovernanceAdapter` protocol + `KalshiGovernanceAdapter` implementation. The cross-bot seam (decision 9): adding Polymarket / Alpaca later means a new adapter class implementing the same protocol.
- **`governance/evidence.py`** — composes prompt context. Imports `scripts/source_market_alignment_audit.aggregate()`, `scripts/keyword_feedback.score_phrases()`, `scripts/reddit_source_audit.collect()`, etc. Requires `scripts/__init__.py` refactor.
- **`governance/prompts.py`** — system prompt + per-decision-type templates. Prompts in code (git-versioned with the agent), not external files.
- **`governance/decision.py`** — `Decision` dataclass with mandatory `predicted_effect` validation.
- **`governance/llm.py`** — local Qwen3 wrapper. Claude API client present but not invoked in Phase 2 (escalation arrives in Phase 4). Local-model selection per `llmfit` (https://github.com/AlexsJones/llmfit) at implementation time — likely Qwen3-14B-class.

### 8.2 Cadence wiring

- launchd plist invokes `python -m governance --cadence fast` every 2h.
- Daily plist invokes `--cadence deep` at a fixed UTC time.
- Wrapper script captures exit code, stderr, stdout to `logs/governance/cycle.log`.

### 8.3 Mode handling

- Phase 2 default: `mode: shadow` in the YAML. Every decision lands in `proposed` regardless of confidence. Bot ignores.
- `mode: real` exists in the schema but is not entered for Phase 2.

### 8.4 Tests

- **Synthetic decision-quality fixtures** (≥30): `(audit_data, headline_samples, market_titles) → expected_decision`. Built incrementally as we observe shadow-mode decisions.
- **Prompt regression tests** — snapshot rendered prompts. Future prompt edits show clear diffs.
- **Adapter contract tests** — `KalshiGovernanceAdapter` satisfies `GovernanceAdapter` protocol (mypy structural + runtime assertions).
- **Property tests** for safety primitives (threshold, max-per-run, kill-switch) integrated end-to-end with the agent loop.

### 8.5 Acceptance criteria

- [ ] Phase 2 runs nightly for ≥ 14 days in shadow mode without writing to `applied` (**OR** the early-close criteria in §8.5.1 are met; see addendum).
- [ ] At least 30 decisions accumulated in `proposed` and the audit log.
- [ ] Manual review of all 30+ decisions confirms ≥ 85% are reasonable (subjective gate, owner: user).
- [ ] Prediction-tracking accumulates baseline data for Phase 3's auto-revert design.
- [ ] `governance/adapter.py` audited to confirm zero kalshi-specific imports leak past the adapter boundary.

### 8.5.1 Early-close addendum (added 2026-05-05 during PROFIT-PHASE2-001)

The 14-day floor in §8.5 is a **calendar floor for confidence**, not a derivation from any specific cadence. PROFIT-PHASE2-001's day-4 mid-soak confirmation (`docs/_archive/governance/2026-05-04-day-4-mid-soak-confirmation.md`) showed the volume gate (≥ 30 decisions) cleared by 5.3× (158 decisions) and all four safety counters at zero. The marginal information from days 8-14 is bounded.

**Early-close gates (all must hold for a day-N close where 7 ≤ N < 14):**

1. **Volume**: ≥ 30 GOVERNANCE_DECISION records (the original §8.5 floor).
2. **Calendar floor relaxed**: ≥ 7 days of continuous shadow-mode runtime (half the 14-day default; preserves a non-trivial calendar window for low-frequency anomaly detection).
3. **Safety counters**: 0 KILL_SWITCH, 0 `batch_aborted=True`, 0 VALIDATION_ERROR through the entire window.
4. **PARSE_ERROR trailing window**: 0 PARSE_ERROR events in the trailing 72 h before close (background errors from earlier in the soak are acceptable; recurrence is not).
5. **Cadence stability**: all fast cycles within ±10 % of the 2.0 h cadence; all deep cycles within ±10 % of the 24 h cadence; no inter-cycle gap > 3 h.
6. **Manual review pass**: ≥ 85 % reasonable on review of the full decision sample (the original §8.5 manual-review gate; not relaxed).
7. **No mid-soak code change to the running bot**: the soak invariant is preserved end-to-end (this is implicit in the original §8.5; restating for clarity).
8. **Written close-criteria attestation**: an operator-signed close record at `docs/governance/PROFIT-PHASE2-001-early-close-attestation.md` documenting which day the close fires and which gates verified clean.

**What gates 1-7 do NOT relax:**

- The 168 h evidence-window in `governance/evidence.py` stays constant through the soak.
- Cadence (fast 2 h, deep 24 h) stays constant through the soak.
- Decision policy stays constant — the LLM model, prompt, and gating thresholds do not change mid-soak (subject to §8.5.2 policy-equivalence carve-out).

**Cadence / evidence-window changes are reserved for the NEXT shadow soak after `PROFIT-PHASE2-001` closes**, where they apply from cycle 1 (no mixed-policy contamination). See `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` §3 for the next-soak-onboarding cadence-tune notes.

### 8.5.2 Policy-equivalence carve-out (added 2026-05-05 during PROFIT-PHASE2-001)

Gate 7 (no mid-soak code change to the running bot) is the strictest of the §8.5.1 gates. In practice, hot-fixes to the governance LLM's input shape (prompt edits, model behaviour fixes) may land mid-soak with the operator's knowledge — e.g., the PROFIT-GOV-002 SYSTEM_PROMPT cycle that landed during PROFIT-PHASE2-001 on 2026-05-03T15:28Z.

A strict reading of gate 7 invalidates the early-close path on any such hot-fix. A pragmatic reading allows for **policy-equivalence carve-outs** where the hot-fix can be empirically demonstrated to NOT have shifted decision distribution on the soak's actual candidate mix.

**Policy-equivalence requirements (all must hold to invoke the carve-out):**

1. **Empirical evidence-coverage analysis.** For each behavioural commit gate-7 surfaces, identify the specific evidence fields the change governs. Compute what fraction of the soak's GOVERNANCE_DECISION records have those fields populated (= the affected slice). If the affected slice is < 5 % of total decisions, the change is empirically a non-event for the soak's outcome.
2. **Affected-slice manual review.** Decisions in the affected slice (whether populated pre-change or post-change) must be reviewed and shown to be consistent with both pre-change and post-change interpretations of the relevant evidence. Bias toward the post-change interpretation if the slice is N=1; require N ≥ 3 with consistent verdicts before invoking the carve-out for slices > 1 %.
3. **Written attestation.** The early-close attestation must document each invoked policy-equivalence carve-out by commit hash, affected-slice fraction, and evidence-coverage analysis result. This becomes part of the §8.5 audit trail.

**Counter-examples that DO NOT qualify for the carve-out:**

- Cadence changes (the cycle-rate is global; affects every decision)
- Evidence-window changes (the lookback window is global; affects every decision's input shape)
- Gating-threshold changes (LLM verdicts may shift even on previously-decided candidates)
- Model swaps (same input → different model → different output distribution; not equivalent by any reasonable measure)

**Concrete example: PROFIT-GOV-002 A5 SYSTEM_PROMPT hot-fix (commit `b47ca71`, 2026-05-03T15:28Z).** A5 added 6 lines instructing the LLM how to interpret `evidence_summary.anchor_rate` for `disable_source` decisions. Empirical analysis: 241/242 GOVERNANCE_DECISION records in PROFIT-PHASE2-001 had `anchor_rate=null`; the A5 lines have nothing to apply. The 1 anchor-rate-active decision (`gd_2026-05-04_0049`) fired entirely in the post-A5 regime. Affected slice: 0.4 %; the change is empirically a non-event. **Carve-out invoked.**

### 8.6 Estimated scope

800-1200 LOC implementation + ~600 LOC tests. ~1.5 weeks active dev + 14 days shadow soak.

## 9. Phase 3 — Real-mode + auto-revert

**Goal:** Flip from shadow to real, with auto-revert as the safety net.

### 9.1 Mode-flip protocol

- Edit `data/runtime_overrides.yaml` by hand, change `mode: shadow` → `mode: real`. Bot picks up on next reload (10 min).
- Subsequent agent cycles write high-confidence decisions to `applied`; low-confidence still go to `proposed`.
- Document the flip in `CHANGELOG.md` — this is a real-behavior turn-on.

### 9.2 Auto-revert mechanism

- Every `applied` batch records `last_applied_batch.pre_change_metrics` and `evaluate_at`.
- Next governance cycle on or after `evaluate_at` computes post-change metrics on the same definitions.
- If any tracked metric regresses beyond a per-metric threshold (e.g., `overall_anchor_rate` increased >5pp; `daily_match_rate_per_1000_ingested` dropped >30%; `active_source_count` dropped >20%), emit `GOVERNANCE_AUTO_REVERT`, remove all entries with that `batch_id` from `applied`, lock agent in read-only for one cycle.

### 9.3 Blast-radius limiter

- Per-batch caps: at most 5 source-disables, 5 keyword changes, 3 threshold-tunings.
- Population caps: a single batch cannot disable more than 20% of currently-active sources.
- Limit violation = abort the entire batch atomically, log `GOVERNANCE_BATCH_ABORTED`, write nothing to `applied`.

### 9.4 Tests

- Auto-revert end-to-end test: synthetic batch with deliberately-bad changes, simulate metric regression, assert revert + audit log.
- Blast-radius chaos test: randomized batches stress-test the limiter; never let a violation through.
- State-machine test: mode transitions (shadow ↔ real ↔ read-only-after-revert) preserve state correctly.

### 9.5 Acceptance criteria

- [ ] Two weeks of shadow-mode running cleanly first.
- [ ] Auto-revert tested in synthetic conditions before real-mode flip.
- [ ] Mode flip gated on a written sign-off (CHANGELOG entry + explicit user OK).
- [ ] First week post-flip: at least one auto-revert opportunity tested in a controlled way (deliberately-poor synthetic decision injected, confirmed reverted).

### 9.6 Estimated scope

400-600 LOC + ~600 LOC tests (weighted toward state-machine + chaos). ~1 week active dev + 1 week real-mode soak.

## 10. Phase 4 — Tiered LLM + self-review

**Goal:** Fill out the MVP safety list with Claude-API escalation and weekly self-review.

### 10.1 Tiered LLM (escalation E)

- "High-impact" defined precisely: any decision affecting >100 ingestion events, >5 keywords at once, or threshold change >2× current value.
- High-impact decisions get a second invocation against Claude API with the same prompt.
- Local Qwen3 + Claude agree on action and direction → apply with combined confidence.
- Disagree → write to `proposed` with `escalation_disagreement: true`; never apply automatically.
- Cost ceiling: hard daily cap on Claude API spend (default $1/day). Past cap, agent halts escalation and writes warning to log.

### 10.2 Self-review cycle (D)

- Weekly cron: `python -m governance --cadence weekly_review`.
- Reads `decisions.jsonl` for past 30 days, joins with current outcome data.
- For each `prediction_correct: false` decision, agent considers proposing a reversal.
- Self-review decisions write to `proposed` only — never auto-apply, ever (self-correction is too high-stakes).

### 10.3 Acceptance criteria

- [ ] Tiered escalation tested with mocked Claude responses (agreement + disagreement).
- [ ] Cost cap enforced — synthetic test of Claude call counts vs cap.
- [ ] Self-review tested against a 30-day synthetic decision history.
- [ ] Phase 4 ships only after Phase 3 has been stable in real-mode for ≥ 2 weeks.

### 10.4 Estimated scope

500-700 LOC + ~400 LOC tests. ~1 week active dev + 2 weeks tiered soak.

## 11. Testing strategy

### 11.1 Test taxonomy

- **Unit tests** — fast, deterministic, isolated. Schema validators, kill-switches, TTL expiry, blast-radius math, prediction-tracking serialization, prompt rendering against fixtures, decision-class invariants. Target ≥90% line coverage on `governance/` and `utils/runtime_overrides.py` files.
- **Property tests** (Hypothesis) — `effective_config == static_config UNION applied_unexpired`; auto-apply only above confidence threshold; batch-violations always atomic; outcome evaluation never errors.
- **Integration tests** — real filesystem, real subprocess. Bot reader + agent simulator racing. End-to-end agent invocation with stubbed LLM.
- **Chaos tests** — adversarial inputs, graceful failure modes. Malformed YAML, disk full mid-write, kill-switch trip mid-cycle, clock skew, LLM timeout, adversarial LLM output.
- **Decision-quality tests** (Phase 2+) — `~30 hand-curated fixtures`, snapshot tests on rendered prompts.
- **End-to-end soak tests** (Phases 2-4) — 14-day shadow, 1-week real-mode, 2-week tiered.

### 11.2 Test discipline rules

- Tests written first or alongside implementation, not after.
- Phase 1 has zero LLM mocking (no LLM in Phase 1). Phases 2+ use a `FakeLLM` test double with canned-response-by-prompt-hash.
- **No flaky tests merged.** A test that passes 99/100 times is broken.
- Every safety mechanism gets a chaos test.

### 11.3 Coverage requirements per phase

| Phase | Unit | Property | Integration | Chaos | Decision-quality | Soak |
|---|---|---|---|---|---|---|
| Phase 1 | ≥90% LOC | required | required | required | n/a | manual |
| Phase 2 | ≥90% LOC | required | required | required | ≥30 fixtures | 14-day shadow |
| Phase 3 | ≥90% LOC | required | required | **expanded** | n/a | 1-week real-mode |
| Phase 4 | ≥90% LOC | required | required | required | n/a | 2-week tiered |

### 11.4 Pre-merge gates

For every phase's feature branch:
1. Full local test suite green.
2. ultrareview pass.
3. Manual smoke test of relevant CLI shims.
4. Soak time (where required) before declaring the phase COMPLETE.

### 11.5 Failure discipline (load-bearing rule)

**Test failure = stop, root-cause analysis, fix. No proceed until fixed. No exceptions. No workaround commits.**

A failing test in any of these categories is operational tripwire:
- Atomic-write race (data corruption risk)
- TTL-expiry (stale state risk)
- Kill-switch (no escape hatch)
- Blast-radius (cascade failure risk)
- Auto-revert (no recovery from bad decisions)

Failures here do not get worked around. They get root-cause-fixed. This rule is non-negotiable for the lifetime of the governance feature.

## 12. Safety mechanisms (consolidated reference)

All in MVP per decision 7. Every mechanism's failure mode is "lock the agent into read-only" rather than "let the agent keep operating with one safety off."

| # | Mechanism | What it prevents | Failure mode |
|---|---|---|---|
| Floor-1 | Confidence threshold | Low-confidence decisions auto-applied | Below-threshold = `proposed` not `applied` |
| Floor-2 | Max changes per run | Single bad run causes massive churn | Cap exceeded = abort batch |
| Floor-3 | Two-level kill switch (`GOVERNANCE_DISABLED`, `GOVERNANCE_READONLY`) | No emergency stop | Env var trip = exit cleanly, no writes |
| Floor-4 | Append-only audit log | Decisions hidden from operator | Failure to write log = abort cycle |
| A | Blast-radius limiter | Cascade failure (all sources disabled) | Limit exceeded = abort batch atomic |
| B | Shadow-mode rollout | Untrusted agent applies changes | First N days = always `proposed`, never `applied` |
| C | Post-change auto-revert | Bad decision lives indefinitely | Metric regression = drop overrides + lock read-only |
| D | Weekly self-review | Wrong decisions never re-examined | Wrong outcome = `proposed` reversal (never auto-applied) |
| E | Claude-API confirmation | High-impact decision wrong with no second opinion | Disagreement = `proposed`, never `applied` |

## 13. Cross-cutting concerns

### 13.1 Hardware fit (Mac Studio, post-2026-04-29)

- 128GB unified memory, 546 GB/s bandwidth (`docs/_archive/studies/future_plans.md` Phase 1).
- Phase 3 agent stack budget (`future_plans.md`): ~58GB for trading agents + macOS + KV cache, ~70GB free headroom.
- Governance agent's Qwen3-14B-class local model: ~9GB. Comfortably within free headroom.
- Claude API escalation: tiered cost-bounded per §10. Default $1/day cap.

### 13.2 Compatibility with existing trading-path observation windows

- During any P2.x or S4.5x observation window with no-change-scope discipline, governance is paused (`GOVERNANCE_DISABLED=true`) for the duration.
- This is documented in `docs/governance/README.md` operator manual.
- Governance agent honoring observation windows is a design constraint; the agent does not enforce or know about ROADMAP windows itself — operator responsibility.

### 13.3 Versioning semantics

- Per-phase merges are minor patch bumps (`0.29.x`).
- Phase 3 mode-flip from shadow to real is a real-behavior turn-on, deserves its own minor patch and CHANGELOG entry.
- The governance agent's own version (`governance-agent-v0.X.Y` in YAML metadata) tracks separately from the kalshi-bot version. A governance-agent v0.1 decision can be inspected long after kalshi-bot v0.30 ships.

### 13.4 Cross-bot future (decision 9)

- The `KalshiGovernanceAdapter` boundary is the future-bot extension point.
- When Polymarket waitlist clears or Alpaca Phase 2 starts, add `PolymarketGovernanceAdapter` / `AlpacaGovernanceAdapter` classes implementing `GovernanceAdapter` protocol.
- The agent core (`governance/agent.py`, `governance/decision.py`, `governance/safety.py`, `governance/llm.py`, `governance/audit.py`) does not change.
- A separate governance-agent process per bot (parallel cron schedules), or a single multi-bot agent — that decision is deferred to when the second bot is operational.

## 14. Open questions / deferred decisions

- **Specific local model.** Decided per `llmfit` at implementation time. Likely Qwen3-14B-class.
- **Specific local-model invocation library.** Ollama vs. LM Studio vs. raw transformers — defer to implementation time. Adapter pattern in `governance/llm.py` makes the choice swappable.
- **Cost cap exact value for Claude escalation.** Default proposed: $1/day. Tunable via env var.
- **Auto-revert metric thresholds.** Default proposed: anchor rate > +5pp, match rate < -30%, active source count < -20%. Tunable in `governance/safety.py` config block; will need empirical retune after Phase 3 soak.
- **Self-review window length.** Default proposed: 30 days. Tunable.
- **Decision-quality fixture growth strategy.** Built incrementally during Phase 2 shadow soak; specifics decided when we see what the agent actually produces.

## 15. Cross-references to existing project docs

- `docs/ROADMAP.md` — overall project roadmap; governance feature does not currently appear there. Should be added under a new top-level section (suggest "Governance Agent (Phase 7+)") once the design is approved and writing-plans produces a phased plan.
- `docs/_archive/studies/future_plans.md` — Phase 3 multi-agent architecture and Phase 6 dynamic keyword weighting. Governance agent is structurally a 6th agent but operates on diagnostic-batch cadence rather than trade-path latency.
- `docs/IMPLEMENTATION_CONTRACT.md` — Section 13 calibration emission criteria are unrelated; governance is a separate concern.
- `docs/profit_path_debt_log.md` — no current entries directly relate to governance.
- `~/.claude/projects/-Users-Jake-vscode-kalshi-bot/memory/project_adaptive_governance_direction.md` — the user direction memo this spec implements.

## 16. Appendix — terminology

- **Governance agent** — the new LLM-driven process that decides which sources/keywords/thresholds to add/remove/tune.
- **Override** — a single entry in `data/runtime_overrides.yaml` representing a specific governance decision (a disabled source, a disabled keyword, a tuned threshold).
- **Batch** — the set of decisions written by a single governance cycle. Batches are the unit of revert.
- **Shadow mode** — agent runs but writes only to `proposed` section; bot ignores. Default for Phase 2.
- **Real mode** — agent writes high-confidence decisions to `applied` section; bot reads them. Phase 3+.
- **Blast radius** — the operational impact of a batch (number of decisions × per-decision impact). Bounded by limits in `governance/safety.py`.
- **Decision-quality fixture** — a hand-curated `(input → expected output)` test case for the agent's judgment.
- **High-impact decision** — a decision exceeding any of: 100 ingestion events affected, 5+ keywords changed at once, or threshold change >2× current. Triggers Phase 4 Claude escalation.
- **The KalshiGovernanceAdapter boundary** — the seam between agent core and bot-specific data access. The future-bot extension point.
