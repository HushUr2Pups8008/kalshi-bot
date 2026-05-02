# Changelog

All notable changes to kalshi-bot are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.29.59] - 2026-05-02

### Added (PROFIT-EDGE-004 — pipeline simulation buildout)

Seven new behavioural-simulation harnesses landed as
[`scripts/simulations/`](scripts/simulations/) per the plan in
[`docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md`](docs/superpowers/plans/2026-04-26-pipeline-simulation-buildout-plan.md).
Each harness pins one previously-uncovered pipeline stage end-to-end
against the five canonical LLM-positive events from the EDGE-001
diagnosis (or, for governance / dossier creation, against
production-shaped synthetic fixtures). All harnesses are read-only —
they write only to tempdirs owned by the harness itself.

| Harness | Stage pinned | Smoke contracts |
|---|---|---|
| [`scripts/simulations/match_score_audit.py`](scripts/simulations/match_score_audit.py) | Match-score gate (`PAPER_MIN_MATCH_SCORE`) — first kill point in the pipeline | anchor in top-3 ≥ 0.06; CLI happy path; KXPSL cross-contamination guard against geo headlines |
| [`scripts/simulations/blend_task_integration.py`](scripts/simulations/blend_task_integration.py) | Full `BlendTask.process_fast_lane_result` with seeded slow-lane context for KXTRUMPIRAN | per-event BLEND_DECISION emission; KXTRUMPIRAN dossier non-zero disagreement contract; CLI text + JSON |
| [`scripts/simulations/paper_trade_roundtrip.py`](scripts/simulations/paper_trade_roundtrip.py) | Paper-trade INSERT path against a real `PaperTrader` over a temp DB | one row per accepted event; bankroll Δ == row.cost_dollars; source_multiplier persistence |
| [`scripts/simulations/trading_queue_handoff.py`](scripts/simulations/trading_queue_handoff.py) | Replicates `main._trading_queue_consumer_task` wiring | FIFO drain; back-pressure no-drop; CLI happy path |
| [`scripts/simulations/governance_fast_cycle.py`](scripts/simulations/governance_fast_cycle.py) | One fast-cadence governance cycle with `FakeLLM` against a temp filesystem | shadow-mode invariant (`applied=0`); audit JSONL append-only; `GOVERNANCE_READONLY` demotion of real → shadow; CLI happy path |
| [`scripts/simulations/resolution_calibration.py`](scripts/simulations/resolution_calibration.py) | YES-wins / NO-wins resolution + the PROFIT-CAL-001 calibration callback | bankroll credit math both outcomes; per-lane `record_calibration_check` callback contract; CLI happy path |
| [`scripts/simulations/dossier_creation.py`](scripts/simulations/dossier_creation.py) | Evidence → dossier flow via `AccumulationTask` against a temp `EvidenceStore` | empty-DB baseline (no dossier at N=0); eager creation at N=1; strict monotonic dossier_version + evidence_count; CLI happy path |

#### `scripts/simulations/_common.py`

Two new immutable fixtures captured from the MacBook trade-log
archive and used by `match_score_audit`:

* `LLM_POSITIVE_EVENT_HEADLINES_2026_04_26` — production headline that
  triggered each LLM-positive signal, keyed by event name (Events 3
  and 5 share `KXTRUMPIRAN-26MAY01` so a ticker-keyed dict is not
  representable).
* `LLM_POSITIVE_EVENT_MARKET_TITLES_2026_04_26` — production market
  title at match time, keyed by ticker.

#### Empirical baselines captured at v0.29.59

* `match_score_audit` — every anchor surfaces in top-3 well above
  `PAPER_MIN_MATCH_SCORE = 0.06` (KXSBUDGETRES 0.282, KXTRUMPIRAN
  0.176–0.268, KXPSL 0.147 only on its own headline).
* `blend_task_integration` — 4/5 events enqueue (KXPSL blocked at G1
  by post-fix sport-class regime); KXTRUMPIRAN dossier-seeded
  disagreement 0.05–0.11, well under G3 = 0.20 → no PROFIT-G3-001
  needed.
* `paper_trade_roundtrip` — 5/5 events INSERT a `paper_trades` row;
  bankroll Δ == row.cost_dollars exactly.
* `trading_queue_handoff` — FIFO holds; 8/8 enqueued candidates drain
  past `maxsize=2` without drop; producer back-pressure path
  exercised.
* `governance_fast_cycle` — 3/3 proposed and 0/0 applied across
  shadow×2 + real-mode-with-readonly cycles; audit log grows
  3.7KB → 7.4KB → 11.1KB; cycle-1 prefix preserved on subsequent
  writes.
* `resolution_calibration` — YES wins → +$5 payout, pnl=+$2.50,
  credibility wins=1; NO wins → $0 payout, pnl=−$2.50,
  credibility losses=1; 3 calibration callbacks per resolution
  (fast / accumulation / structural); error formula
  `|estimate − final_resolution|` confirmed.
* `dossier_creation` — dossier created EAGERLY on N=1 (no minimum-
  evidence threshold gate). PROFIT-EDGE-002's "12 of 847 active
  markets had dossiers (~1.4%)" observation is therefore a
  *coverage* problem (which markets receive evidence at all), not a
  creation-threshold problem.

#### Test surface

`tests/test_simulations_smoke.py` grew from 13 → 37 smoke tests
(+24 new pinning the contracts above). All pass on a clean checkout.

#### Notes

* Plan A1 specified the headline fixture as `dict[ticker, str]`;
  shipped as `dict[event_name, str]` because Events 3 and 5 share
  `KXTRUMPIRAN-26MAY01` and a ticker-keyed dict cannot represent
  both. Documented inline at the fixture site.
* Plan A4 #3 specified
  `test_roundtrip_source_stats_updated`; substituted with
  `test_roundtrip_source_multiplier_persists_to_row` because
  source-stats orchestration lives in `main.py` (called separately
  from `record_trade`), so the per-row contract is what
  `paper_trade_roundtrip` can faithfully pin. Documented in the
  smoke test docstring.

No production code paths changed in this release. The simulations
themselves are the deliverable.

---

## [0.29.58] - 2026-04-26

### Fixed (PROFIT-EDGE-003 — G1 scaled-confidence floor calibration)

Same shape of recalibration as PROFIT-EDGE-002 (v0.29.57) for G4, but
applied to G1. Post-fix simulation against v0.29.57 confirmed all 5
LLM-positive events from the PROFIT-EDGE-001 diagnosis still failed
the readiness gate at G1, with `scaled_confidence` (= `blended_conf`
× `regime_conf`) in the 0.05–0.10 range against the previous
`G1 = 0.35` requirement.

#### `tasks/trade_readiness_gate.py:G1_CONFIDENCE_THRESHOLD` lowered 0.35 → 0.05

The blender's `_effective_confidences()` attenuates each lane's
confidence by `lane_conf × (rw_lane × rc + (1-rc)/3)`, and
`blended_confidence` is the *mean* of those effective confidences. For
realistic LLM signal (`fast_conf = 0.85`) on the categorical priors
the regime classifier was designed to emit, achievable
`scaled_confidence` values are:

```
Sports prior (rc=0.528):           ~0.27
KXTRUMPIRAN prior (rc=0.27):       ~0.10
KXSBUDGETRES prior (rc=0.28):      ~0.06
Polling prior (rc=0.32):           ~0.07
```

`G1 = 0.35` would require `regime_confidence ≥ ~0.875` with realistic
`blended_confidence` — only the very-near-close (≤6h) sports markets
can achieve that, and we filter sports out of the trading universe by
design. So `G1 = 0.35` was *deterministically* zero-trade for the
bot's actual target market mix, regardless of any line-688 or G4
fixes upstream.

**Empirical confirmation** — distribution of `scaled_confidence`
across 154 production `BLEND_DECISION` records over the 9-day window
2026-04-17 → 2026-04-26 (paper mode):

```
percentile  scaled_confidence
median      0.026  (pure noise — uncategorized markets at near-uniform rc)
P75         0.035
P90         0.119  (natural cliff — strong-signal cluster)
P95         0.119
max         0.304  (KXVANCEPAKISTAN-26APR21-APR24 — best signal in 9 days)
```

Zero of 154 cleared `G1 = 0.35` — the threshold was 1.15× the very
best signal the bot has ever produced. Setting `G1 = 0.05` captures
the top ~14% of historical signals (the 22 records above 0.05 cluster
around the P90 inflection at 0.119) while still rejecting median
noise (0.026). Multi-lane high-quality signals at 0.30 clear with 6×
headroom.

#### `tasks/trade_readiness_gate.py:G1_FAILSAFE_CONFIDENCE_THRESHOLD` lowered 0.50 → 0.10

Failsafe G1 is set at 2× base, mirroring the pre-fix 0.50/0.35 = 1.43×
ratio but pushed slightly more conservative. With failsafe activation
still coupled to `rc < G4 = 0.20`, this branch only matters in the
`[failsafe-trigger, G4)` band — currently empty by construction. The
value moves for consistency in the event of a future failsafe / G4
threshold decoupling.

### Reasoning

- The defensibility argument mirrors PROFIT-EDGE-002 exactly:
  (a) closed-form math shows `G1 = 0.35` is unreachable for realistic
  blender outputs given the categorical priors the system was designed
  to emit; (b) production data over 9 days confirms zero pass rate;
  (c) the new threshold sits at the natural P90 inflection of the
  empirical distribution — captures top-decile signals, rejects
  median noise; (d) the change is a single constant pair, trivially
  reversible if post-deploy data shows we're trading noise.
- We did not lower G3 (disagreement threshold) or change the blender
  itself. Only the G1 floor moves. If multi-lane disagreement is
  excessive on any post-fix trade, G3 will still gate it.
- We explicitly chose to calibrate to the conservative side of the
  empirical distribution. P90 = 0.119; setting `G1 = 0.10` would have
  matched P90 directly but excluded the fast-only LLM-positive
  signals on KXTRUMPIRAN-style markets (which max ~0.10 in
  fast-only mode). `G1 = 0.05` keeps those open while still demanding
  ~2× the noise floor.

### Tests

- `tests/test_trade_readiness_gate.py::test_g1_threshold_is_calibrated_to_pass_top_decile_production_signals`
  — pins the contract: G1 must leave ≥ 50% headroom over the P90
  production scaled_conf (0.119) AND must be strictly above the
  median noise floor (0.026). Future bumps back above ~0.08 fail
  this test until accompanied by explicit recalibration of the
  regime priors, blender, or blended-confidence model.
- `tests/test_trade_readiness_gate.py::test_g4_regime_confidence_is_enforced_and_tightens_thresholds`
  updated `blended_confidence` from 1.0 → 0.50 so the test still
  exercises G1 failure under the new lower threshold.
- All other existing tests pass unchanged (1370 pass, 1 skipped).

### Stack of fixes 2026-04-26

PROFIT-EDGE-001 (v0.29.56) → PROFIT-EDGE-002 (v0.29.57) →
PROFIT-EDGE-003 (v0.29.58) form a single coordinated unblock for
the no-edge problem. Each was structurally necessary to expose the
next layer:

1. **EDGE-001:** line-688 `if not keywords:` no longer kills
   LLM-emitted signals.
2. **EDGE-002:** G4 lowered 0.40 → 0.20; categorical priors added
   for engaged series; sport-prefix gap (KXPSL) closed; structural-
   recompute log surfaces `__cause__`.
3. **EDGE-003:** G1 lowered 0.35 → 0.05 (failsafe 0.50 → 0.10).

Post-fix simulation predicts ~14% of historical BLEND_DECISIONs would
now have produced paper trades had the full stack been live during
the diagnosis window. Real production data starting v0.29.58 will
either confirm the calibration or surface the next layer.

---

## [0.29.57] - 2026-04-26

### Fixed (PROFIT-EDGE-002 — readiness-gate calibration + categorical-prior coverage)

The post-mortem of PROFIT-EDGE-001 (v0.29.56) revealed three additional
structural bugs gating the no-edge problem. Together with the line-688 fix
they form the actual end-to-end unblock for paper trades on the user's
target markets (geopolitical / domestic-policy event series).

#### 1. `analysis/regime_classifier.py:_SERIES_PRIORS` extended for engaged series

The categorical-prior table was authored 2026-04-18 (commit bdeeca5) with
sport, polling, central-bank, crypto, weather, entertainment, and Trump-say
priors. In the 7 days since, every series we have actually engaged has had
**zero** categorical prior:

| Series                | BLEND_DECISIONs (last 7d) | Categorical prior pre-fix |
|-----------------------|---------------------------|---------------------------|
| KXTRUMPIRAN           | 68                        | NO                        |
| KXMOCTRUMP25          | 50                        | NO                        |
| KXVANCEPAKISTAN       | 25                        | NO                        |
| KXFISAEXTEND          | 4                         | NO                        |
| KXPARDONSTRUMP / KXVOTESAVEAMERICA / KXELECTIONEMERGENCY / KXTRUMPCHINA / KXTRUMPENDORSE | 1–2 each | NO |

All these markets fell through `_series_prior` to `_time_prior`, which for
markets >= 7 days from close returns near-uniform weights (0.10, 0.45, 0.45).
That distribution gives `regime_confidence = 1 - H/log(3) ≈ 0.136`, which
trips both G4 and the G1 fail-safe. Twenty-one new categorical priors now
cover legislative / calendar markets (interpretation-dominant), macro
releases (structural-dominant, mirroring KXCBDECISION shape), event-driven
political / diplomatic markets (fast-dominant), and conflict / military
events (strongly fast-dominant). Each weight tuple was chosen so the
resulting `regime_confidence` clears G4=0.20; a calibration test in
`tests/test_regime_classifier.py::test_new_categorical_priors_clear_g4_threshold`
pins the contract.

#### 2. `tasks/trade_readiness_gate.py:G4_REGIME_CONFIDENCE_THRESHOLD` lowered 0.40 → 0.20

Mathematical analysis showed `G4 = 0.40` requires the dominant lane > ~0.80
in the regime weights, which excludes almost every categorical prior the
classifier was already designed to emit. Production data confirms: across
9 days of paper-mode operation only 2 of the 14 prior classes (sports —
which we filter out by design — and the ≤6h time fallback) cleared G4.
Polling, central-bank, crypto, weather priors *all* failed:

```
Sports          rc=0.528  PASS 0.40 — but filtered by SPORTS_BLOCKLIST
Polling         rc=0.321  fail 0.40 — DESIGNED tradeable, but blocked
Central bank    rc=0.280  fail 0.40 — DESIGNED tradeable, but blocked
Crypto          rc=0.251  fail 0.40 — DESIGNED tradeable, but blocked
Weather         rc=0.220  fail 0.40 — DESIGNED tradeable, but blocked
Trump-say       rc=0.157  fail 0.40
Time fallback (1-3d, 3-7d, 7-14d):  rc < 0.14  — uncategorized; should fail
```

`G4 = 0.20` is the cleanest separator: it passes the categorical priors
intentionally designed to be tradeable while still failing time-fallback
markets and weakly-categorical Trump-say / entertainment families
(correctly subjecting them to fail-safe). Inline rationale lives in
`tasks/trade_readiness_gate.py` above the constant. Note: lowering this
threshold also broadens the fail-safe activation band — markets in
`[0.20, 0.40)` now run normal (G1=0.35, G3=0.20) instead of fail-safe
(G1=0.50, G3=0.15). This is the intended effect: the categorical prior
*is* our knowledge about the market regime; demanding additional caution
beyond the standard gate is over-tight for known markets.

#### 3. `config.py:MARKET_SERIES_BLOCKLIST_PREFIXES` extended for sport-prefix gaps

KXPSL (Pakistan Super League cricket) was confirmed as a sports leak
during the 2026-04-26 diagnosis: its market `KXPSL-26-PZA` reached the
LLM-analysis pipeline. Audit of all ~9.8k Kalshi series surfaced ~336
sport prefixes not currently blocked. Added 33 high-confidence prefixes
covering international cricket (KXIPL, KXPSL, KXWPL, KXCRICKET, KXT20),
distinct tennis prefixes (KXATP, KXWTA), boxing / WBC, UFC variants,
women's basketball (KXWNBA), additional soccer leagues (KXEPL, KXEGYPL,
KXISL, KXCHNSL, KXCANPL, KXAFCC, KXUEFA), F1 (KXF1, distinct from
KXFORMULA), PGA, college sports (KXCFP, KXCFB, KXMARMAD), Winter Olympics
(KXWO, distinct from KXOLYMPIC), esports (KXEWC), additional basketball
leagues (KXCBA, KXNBL, KXEUROCUP), and sport-leader/draft/coaching markets
(KXLEADER, KXNEXTTEAM, KXNEXTCOACH, KXCOACHOUT, KXTEAMSIN, KXTRADEOFF,
KXRANKLIST). Comprehensive prefix maintenance is governance-agent
territory long-term; this is the manual interim fix.

#### 4. `tasks/structural_task.py:run_once` now surfaces `__cause__` of recompute failures

The previous warning logged `"... per-market recompute failed: %s" % r`,
which calls `str(r)` on a `StructuralComputationError("failed structural
recompute for X") from exc`. Because `__cause__` is not part of `str`,
this discarded the actual root cause — across all production logs the
underlying exception was never written to bot.log or errors.log, leaving
every structural failure silently undiagnosable. The warning now emits
the wrapper text plus `repr(__cause__)` plus an `exc_info` traceback.
Regression test in
`tests/test_structural_task.py::test_run_once_failure_warning_surfaces_underlying_cause`.

### Reasoning

- Together with the v0.29.56 line-688 fix, the four changes above form the
  necessary end-to-end unblock for the user's target markets. Pre-fix:
  LLM-emitted signals on uncategorized markets died at line 688; even when
  forced past, they would have hit G4 immediately. Post-fix: categorical
  priors raise `regime_confidence` above the (now lower) G4 floor, and the
  structural log finally emits cause traces for the secondary problem of
  dossier-and-recompute coverage.
- This is still possibly not *sufficient* to produce paper trades on every
  prior class — G1's `scaled_confidence ≥ 0.35` requirement remains the
  next-most-likely binding constraint for legitimately multi-lane markets.
  Post-fix BLEND_DECISIONs in production will surface where it actually
  binds, and the next iteration can target G1 with concrete data.
- The defensibility argument for the G4 threshold change rests on:
  (a) the math of `regime_confidence` (entropy-based) and `G4 = 0.40`
  forced lane dominance > 0.80, which conflicted with the regime
  classifier's own design;
  (b) production data showing zero paper trades over 9 days;
  (c) preserving the fail-safe shape (we did not lower G1 or G3, only
  G4); and
  (d) the calibration test pinning the new threshold against existing
  designed-tradeable priors.
- Sports-prefix maintenance and `_SERIES_PRIORS` maintenance are both
  surfaced in the project memory as future governance-agent capabilities
  (`project_governance_regime_priors.md`).

### Tests

- `tests/test_trade_readiness_gate.py::test_g4_threshold_is_calibrated_to_pass_existing_categorical_priors`
- `tests/test_trade_readiness_gate.py::test_g4_passes_at_threshold_boundary_in_normal_mode`
- `tests/test_regime_classifier.py::test_legislative_calendar_priors_are_interpretation_dominant`
- `tests/test_regime_classifier.py::test_macro_release_priors_are_structural_dominant`
- `tests/test_regime_classifier.py::test_event_driven_political_priors_are_fast_dominant`
- `tests/test_regime_classifier.py::test_conflict_priors_are_strongly_fast_dominant`
- `tests/test_regime_classifier.py::test_new_categorical_priors_clear_g4_threshold` (calibration contract)
- `tests/test_sports_blocklist.py` (44 parametrized cases, 11 negative)
- `tests/test_structural_task.py::test_run_once_failure_warning_surfaces_underlying_cause`

Existing `test_g4_regime_confidence_is_enforced_and_tightens_thresholds`
and `test_fast_lane_does_not_exempt_common_conditions` updated to use
the threshold constant rather than the literal 0.39.

Full suite green: 1369 passed, 1 skipped.

---

## [0.29.56] - 2026-04-26

### Fixed
- **`main.py:_process_candidate` no longer kills LLM-emitted signal when
  the keyword scoring step returns an empty list (PROFIT-EDGE-001).** The
  `if not keywords:` rejection at line 688 now also requires the LLM to be
  silent (`llm_mag is None or llm_mag == "none"`) before rejecting.
  Diagnosed on 2026-04-26 from the v0.29.54 paper-mode trade log: across
  9 days of unattended operation, 5 of 666 real-market
  `SIGNAL_ANALYSIS_DETAIL` events had `llm_useful=True` with non-trivial
  probability movement; all 5 were rejected at the no_keywords gate, and
  the bot recorded zero paper trades end-to-end. The Phase 2 governance
  agent (v0.29.55) cannot do its job — dynamically learning what keyword
  config is missing — while LLM-positive events are filed as
  `ANALYSIS_REJECTED reason=no_keywords` instead of surfacing as
  candidate evidence.

### Reasoning
- This is necessary but possibly not sufficient. Once an event proceeds
  past line 688, it still has to survive BLEND/G1, readiness G2-G6, and
  executor E1-E12. Existing BLEND_DECISION evidence (173/173 G1-blocked
  in the diagnosis window) may extend to the new LLM-positive blends;
  the audit log post-fix is the actual evidence, and the next
  investigation iteration can target whatever gate becomes the new kill
  point. This ships the *necessary* part of the fix.
- The change preserves the CLAUDE.md anti-blend gotcha: keywords remain
  a gate when LLM is silent, and LLM result is *not* blended with
  keyword-derived probability — it's the sole signal source on the
  LLM-positive empty-keywords path.
- Test coverage:
  `test_process_candidate_proceeds_when_llm_emits_signal_despite_no_keywords`
  (new, PROFIT-EDGE-001 regression guard) and
  `test_process_candidate_still_rejects_when_neither_signal_source_speaks`
  (new, anti-bypass regression guard) pin both directions of the
  contract. Existing
  `test_process_candidate_returns_early_when_no_keywords` continues to
  pass unchanged — its mock has `llm_mag=None`, which is the
  no-LLM-signal case the fix preserves.

---

## [0.29.55] - 2026-04-25

### Added
- **Governance Agent Phase 2 — local-only governance agent in shadow mode.**
  New `governance/` modules: `agent.py`, `adapter.py`, `evidence.py`,
  `prompts.py`, `decision.py`, `llm.py`. CLI: `python -m governance
  --cadence fast|deep|weekly_review`. Cadence wired via launchd plists
  at `ops/launchd/com.kalshi.governance.{fast,deep}.plist` (not
  installed by this commit; install procedure in
  `docs/governance/PHASE2_RUNBOOK.md`).
- **`KalshiGovernanceAdapter`** implements the `GovernanceAdapter`
  Protocol — the cross-bot seam (spec decision 9). Wraps the four
  audit-script library functions (`source_market_alignment_audit`,
  `keyword_feedback`, `reddit_source_audit`, `freshness_diagnostics`)
  and normalizes their natural outputs (tuples, dataclass-keyed dicts)
  into the `{pairs, subs, candidate_phrases, sources}` shape the
  evidence builder consumes.
- **Decision dataclass** with strict `__post_init__` validation:
  decision_id / batch_id format, tz-aware timestamps, confidence
  ∈ [0, 1], action whitelist, mandatory predicted_effect for action
  decisions.
- **FakeLLM test double + LocalQwenLLM Ollama wrapper** behind one
  `LLMClient` Protocol. Phase 2 ships with both; production uses
  LocalQwenLLM (`qwen3:8b` on MacBook 18GB, `qwen3:14b` on Mac Studio
  via the launchd plist's `GOVERNANCE_LLM_MODEL` env var). The
  trading-bot signal analyzer remains on `qwen2.5:7b`; unification
  intentionally deferred and tracked as `PROFIT-LLM-001`.
- **`scripts/__init__.py`** — formalizes the audit-script package
  boundary so the agent's evidence builder can import library
  functions cleanly.
- **Shadow-mode invariant test coverage:** unit tests, integration
  test (3-candidate end-to-end with FakeLLM), chaos tests
  (kill-switch / malformed JSON / validation errors), and Hypothesis
  property test asserting `confidence < threshold ⇒ applied=False`
  across 30 randomly-generated `(confidence, threshold, mode)` tuples.
- **Operator runbook** at `docs/governance/PHASE2_RUNBOOK.md`:
  install, smoke test, kill switches, soak monitoring, common
  failures, uninstall.

### Changed
- (none — Phase 2 is purely additive on top of Phase 1; no existing
  code paths modified.)

### Reasoning
- Phase 2 is the trust-dataset accumulation phase. The agent runs
  for ≥14 days in shadow mode, never writing `applied`, while a
  ≥30-decision corpus accumulates for manual review. Phase 3 flips
  the mode to `real` only after that review confirms ≥85% reasonable
  decisions.
- `mode != "real"` is the only thing standing between the agent and
  live config changes during Phase 2. The Hypothesis property test
  in `tests/test_governance_agent_property.py` is the load-bearing
  guarantee.
- Two production-path bugs surfaced and fixed during execution
  (Task 6.5 audit-data shape normalization; Task 21 directory-aware
  trade-log reads). Both are documented in the plan as
  post-implementation notes for future agents.

---

## [0.29.54] - 2026-04-25

### Added
- **Governance Phase 1: runtime overrides plumbing.** New module
  `utils/runtime_overrides.py` reads `data/runtime_overrides.yaml`
  every 10 minutes and exposes `is_source_disabled`,
  `is_keyword_disabled`, `get_threshold_override` query helpers.
  Existing static-config call sites in `main.py`,
  `feeds/subreddit_selector.py`, `feeds/gdelt_monitor.py`, and
  `analysis/signal_analyzer.py` refactored to consult the runtime
  reader. New asyncio task `tasks/runtime_overrides_task.py` performs
  the periodic poll and hot-reload with failure isolation.
- New `governance/` package skeleton with `safety.py` (`SafetyConfig`
  + `KillSwitch` env-var emergency stop) and `audit.py` (`AuditLogger`
  JSONL writer with daily rotation matching `bot.log`). These are
  scaffolding for the agent itself which lands in Phase 2.
- New CLI shim: `python -m utils.runtime_overrides --status |
  --validate <path> | --revert-batch <batch_id>` for emergency
  operator intervention.
- New operator manual: `docs/governance/README.md`.

### Changed
- Bot processes that previously consulted `config.DISABLED_NEWS_SOURCES`
  directly now go through `utils.runtime_overrides.is_source_disabled`.
  Behavior is unchanged when no `data/runtime_overrides.yaml` file
  exists (graceful backward-compat — bot operates exactly as
  pre-Phase-1).
- `EARLY_MAX_NEWS_AGE_BY_SOURCE` lookups in `main.py` now respect
  runtime threshold overrides; static-config fallback preserved.

### New dependencies
- Runtime: `pyyaml>=6.0`
- Dev: `hypothesis>=6,<7`

### Tests
Phase 1 adds 104 new tests across schema validation, parsing, TTL
filtering, atomic-write race tests, reader query helpers, async poll
task, Hypothesis property test for the effective-config invariant,
backward-compat with no overrides file, source-disable and keyword-disable
refactor coverage, threshold-override consumer, module-level helpers,
CLI subprocess tests. Total project test count: 1204 (was 1100).

### Notes
- This entry was originally drafted as `[0.29.52]` and dated 2026-04-24
  (when implementation completed). Renumbered to `[0.29.54]` at merge
  time because the daily-review filter (`[0.29.53]`) merged to `main`
  ahead of this branch; SemVer-monotonic version sequencing requires
  the next release after 0.29.53 to be 0.29.54+. The 0.29.52 slot
  stays unused. Body content is unchanged from the original draft.

### Reference
Spec: `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md`
Phase 1 plan: `docs/superpowers/plans/2026-04-24-governance-agent-phase-1-plan.md`

---

## [0.29.53] - 2026-04-25

### Changed
- **Daily review section 1 — operationally-relevant filter.** The
  per-source freshness waterfall in `scripts/daily_review.py` is now
  cross-referenced with section 8's `source_scorecard` tier
  classification. Sources in `prune` / `remove immediately` /
  `disabled` tiers, plus those in the freshness `dead` and
  `insufficient data` buckets, are folded into a one-line summary
  pointing at section 8 instead of bloating the waterfall. On real
  current-window data this reduces section 1 from ~1840 lines to
  ~20 lines (1824 sources hidden, breakdown by tier/bucket
  preserved).

### Added
- **Status-changes block in section 1.** Day-over-day diff against a
  persisted tier baseline (`logs/reports/source_tier_state.json`,
  two-deep history). Reports tier regressions (`top performers ->
  watch / investigate`, etc.) and new silences (sources that were in
  `top performers` / `keep` / `watch / investigate` previously and
  produced no records this window). Improvements are intentionally
  not surfaced — the section is for operator triage of regressions.
- 16 new tests in `tests/test_daily_review.py` covering tier-map
  flattening, state-file load/save edge cases (corrupt JSON,
  same-day rerun, day-rollover), change-diff logic (degraded /
  silent / no-change / no-baseline / improvement-suppressed), and
  two end-to-end integration tests verifying the new section 1
  output shape.

### Reasoning
- Pre-change, section 1 was a ~1840-line wall of text dominated by
  long-tail sources with no operational relevance to current
  trading. Operators were skipping the section entirely.
- The cross-reference uses section 8's existing tier classifier
  (which already folds in observed_records, signals, paper_trades,
  win_rate, and P&L) rather than introducing a parallel notion of
  "source value." Single source of truth.
- The status-changes block addresses the inverse problem: sources
  that were operationally relevant yesterday and have degraded or
  gone silent today, which the unfiltered waterfall buried in noise.

### Notes
- Version skips 0.29.52, which was originally reserved for the
  governance Phase 1 plumbing MR. That MR ended up shipping as
  `[0.29.54]` after merge-order conflicts — see that entry's Notes
  block for context.

---

## [0.29.51] - 2026-04-24

### Changed
- **P1.5.3 — disable 10 zero-signal Reddit subreddits.** Added to
  `DISABLED_NEWS_SOURCES`: `r/Africa`, `r/China`, `r/EasternEurope`,
  `r/GlobalTalk`, `r/Israel`, `r/NorthKorea`, `r/Syria`, `r/Turkey`,
  `r/pakistan`, `r/taiwan`. Evidence: P1.5.2 audit (commit `7117620`,
  `scripts/reddit_source_audit.py`) showed these subs produced **zero
  MATCH_DIAGNOSTIC or SIGNAL_ANALYSIS_DETAIL events across the full
  trade-log archive** — ingestion is entirely in the `all_stale` band
  (posts too old to survive the 5-min `EARLY_MAX_NEWS_AGE_SECONDS`
  gate) or the `no_matches` band (fresh posts don't overlap any active
  market).

### Rationale
- Reclaims network and Reddit-rate-limit budget (Reddit has a
  documented concurrent-IP 403 constraint; every polled-but-dropped
  post still counts against that limit).
- Reversible via config revert if a later pipeline change (freshness
  threshold retune, keyword coverage expansion, market catalog shift)
  justifies re-enabling.
- **Not a blanket Reddit shutdown** — the remaining ~10 configured
  Reddit subs stay active for optionality. The broader
  "Reddit-architecturally-misaligned-with-5min-freshness-gate" finding
  documented in the P1.5.2 ROADMAP row is a known pipeline-shape issue
  that will be revisited after P0.5 resolves whether LLM anchoring is
  driven by prompt priming or market-scope ceiling.

### Tests
Full suite: 1100 passed, 1 skipped, 0 failures. Behaviour change is
limited to which subreddits the Reddit poller hits; no test covers
that directly (polling is network-gated).

---

## [0.29.50] - 2026-04-24

### Changed
- **Soften the `magnitude="none"` default prior in the LLM system prompt
  (P0.5 experiment).** In `_LLM_SYSTEM_PROMPT` at
  [analysis/signal_analyzer.py:474](analysis/signal_analyzer.py#L474),
  replace the leading rule:

  > Most headlines should result in magnitude="none".

  with:

  > Classify magnitude based on the evidence in the headline and summary —
  > do not default to "none" absent evidence of movement.

  The other two rules (`new_information=false → direction="neutral" and
  magnitude="none"`, `"Only major unexpected developments justify 'moderate'
  or 'large'"`) are unchanged — both are logically necessary constraints
  that prevent false positives.

### Rationale (P0-GATE / P0.5 experiment)
P0.4 (v0.29.48, 2026-04-24) tested whether the market price being visible
to the LLM was driving universal anchoring. The half-checkpoint verdict
at n=100 was **refuted at +0.01 pp** — no measurable shift from the
98.99% baseline when the price line was removed from the prompt. P0.5
tests the next-most-likely load-bearing prompt-side prime: the explicit
"default to none" instruction. Full design and interpretation bands in
`docs/ROADMAP.md` P0.3 Verdict AMENDMENT block (commit `0dfd0de`).

### Measurement plan
- Run ≥ 12h post-restart on current traffic (half-check at ~6h).
- Re-run `scripts/flag_outcome_correlation.py` on a post-v0.29.50 filter
  (same methodology as the P0.4 half-checkpoint).
- Interpret vs. v0.29.48's 99.00% anchor rate:
  - Drop to ≤ 70% → priming was a primary lever.
  - Drop to 70–90% → priming is a contributor.
  - Stays ≥ 95% → priming is not the cause; (c) market-scope ceiling is
    the correct read and edge recovery runs through P3.2 + P3.3.

### Rollback
Single-line revert of `_LLM_SYSTEM_PROMPT`. No data or schema migration.

### Sequencing
Pure prompt-text change; touches no keyword-gate, pre-LLM match, or
executor-selectivity code. Must land before P2.4's 3-day observation
window opens, same as P0.4. Compatible with the in-flight P3.2
diagnostic emission (no runtime path overlap).

### Tests
Full suite: 1100 passed, 1 skipped, 0 failures (no test asserts on
specific prompt wording).

---

## [0.29.49] - 2026-04-24

### Added
- **`market_specificity_score` now emitted on every `MATCH_DIAGNOSTIC`
  record (ROADMAP P3.2).** The score is a float in `[0.0, 1.0]` where
  1.0 = most specific, computed purely from `KalshiMarket.title`,
  `subtitle`, `ticker`, and `close_time`. Feeds later phases that will
  prioritize high-specificity matches (P3.3+); this release ships the
  measurement only — **no behavior change**.
- New module `analysis/market_specificity.py` with
  `compute_specificity_score(market)` and `specificity_components(market)`
  (the latter returns the per-feature breakdown for debugging). Weighted
  sum of six features: resolution-criteria token count, specific-verb
  presence, numeric-threshold presence, named-entity density,
  days-to-close proximity, and date-encoded ticker heuristic. Weights
  sum to 1.0 and are a starting point — retune from data once post-hoc
  validation (specificity vs anchor-rate correlation) is in hand.

### Context
P3.2 is the first substantive step on the "fix-path-for-universal-LLM-
anchoring" track. P0.3 (2026-04-21) diagnosed market-scope ceiling as
a root cause; P3.1 (2026-04-24, commit `1518085`) corroborated with a
98.99% flag-independent anchor rate. P3.2 produces the per-market
signal needed to *measure* scope objectively rather than argue about
it qualitatively.

### Validation plan
Once `MATCH_DIAGNOSTIC` records carrying the score accumulate in the
trade log, extend `scripts/flag_outcome_correlation.py` (or write a
sibling script) to bucket joined SIGNAL_ANALYSIS_DETAIL rows by
specificity-score quantile and compute per-bucket anchor rates with
Wilson 95% CIs. If high-specificity buckets show a lower anchor rate
than low-specificity buckets with non-overlapping CIs on meaningful n,
the score is earning its keep and becomes an input to future
prioritization work. If not, retune the weights or revisit the feature
set.

### Tests
- `tests/test_market_specificity.py` (40 cases): per-component units,
  narrow-vs-broad ordering assertions on realistic Kalshi-style
  title/subtitle pairs, edge-case handling.
- `tests/test_market_matcher.py::test_match_diagnostics_includes_market_specificity_score`:
  confirms the field is present on every emitted MATCH_DIAGNOSTIC
  record and is a float in `[0.0, 1.0]`.
- Full suite: 1100 passed, 1 skipped, 0 failures.

### Note on the circular-import refactor
`analysis/market_specificity.py` deliberately does *not* import from
`analysis/market_matcher` — the matcher now imports the scorer, which
would create a cycle. Instead, a frozen snapshot of the matcher's
`_GEO_NAMED_ENTITIES` plus a small local `_days_to_close` live in the
scorer module. The named-entity set has a few specificity-only
additions (`iaea`, `un`, `scotus`, `fbi`, `cia`, `opec`, `who`) that
are intentionally scoped to this module rather than pushed back into
the matcher, because the matcher's set is tuned for the news-to-market
keyword gate and additions there have behavioral consequences that
P3.2 must not introduce.

### Compatibility with in-flight P0.4 experiment
P3.2 landing mid-P0.4 is safe: it touches only diagnostic emission,
not the keyword-gate, pre-LLM match, executor-selectivity, or LLM
prompt paths. The P0.4 anchor-rate measurement already-running on
v0.29.48 is not invalidated.

---

## [0.29.48] - 2026-04-24

### Changed
- **LLM prompt no longer includes the market's current YES price.** The
  `CURRENT YES PRICE: {cents} ({probability})` line in `_build_user_msg`
  has been removed, and the `_LLM_SYSTEM_PROMPT` "you will be given"
  contract has been updated to match (`"MARKET: title, resolution
  criteria, current YES price"` → `"MARKET: title and resolution
  criteria"`). No other prompt content has changed.

### Rationale (P0-GATE / P0.4 experiment)
This is a falsifiable experiment to test the price-in-prompt anchoring
hypothesis surfaced in the 2026-04-24 P0.3 verdict amendment
(see [docs/ROADMAP.md](docs/ROADMAP.md) P0.3 Verdict AMENDMENT block):

- P3.1 (commit `1518085`) measured 98.99% overall anchor rate on n=199
  LLM-used rows across 2026-04-22 → 2026-04-24. Anchoring is
  flag-independent (any_flag 100%, no_flag 95.45%, Wilson 95% CIs
  overlap). This pattern is exactly what price-in-prompt priming
  produces and is not uniquely explained by the original P0.3 verdict
  (market-scope ceiling).
- The original P0.3 verdict (2026-04-21) ruled out prompt anchoring on
  the basis that "market price is not present in the LLM prompt" —
  this claim was factually wrong in the code. The amendment re-opens
  P0-GATE category (a).

### Measurement plan
- Run ≥ 12h on current traffic (first half-checkpoint at ~6h).
- Re-run `scripts/flag_outcome_correlation.py` on the post-change window.
- Interpret:
  - Overall anchor rate drops meaningfully (e.g., below ~80%, or
    CI-lower < current 96.41% baseline) → prompt anchoring is a
    contributor; primary fix direction confirmed.
  - Overall anchor rate stays ≥ 95% → prompt anchoring is not the
    (sole) cause; the (c) market-scope ceiling diagnosis still applies
    and P3.2 (`market_specificity_score`) is the correct next lever.

### Rollback
One-line revert of `_build_user_msg` restores the price line; the
`_LLM_SYSTEM_PROMPT` "you will be given" line should be reverted in
the same commit for consistency. No data migrations or schema changes.

### Tests
Full suite (1059 passed, 1 skipped) re-run to confirm no regression
from the prompt text change. No test asserted on prompt content.

---

## [0.29.47] - 2026-04-24

### Added
- **`CALIBRATION_CHECK` emission wired end-to-end (PROFIT-CAL-001 closed).**
  The calibration feedback loop was architecturally complete since v0.19.x
  but operationally inert — no runtime call site emitted `CALIBRATION_CHECK`
  events, so `CalibrationTask._state` was permanently empty and
  `BlendTask.get_scaling_factor()` always returned a neutral 1.0. This
  release closes the gap. On every paper-trade resolution, for each
  populated lane (fast / accumulation / structural),
  `PaperTrader.resolve_market` now emits one `CALIBRATION_CHECK` to the
  structured trade log and one `record_calibration_check` update to the
  injected `CalibrationTask`.
- Six nullable columns on `paper_trades` (`fast_lane_p`,
  `fast_lane_confidence`, `accumulation_p`, `accumulation_confidence`,
  `structural_p`, `structural_confidence`) capture per-lane estimates at
  trade time so resolution doesn't need to join log history. Populated
  from `analysis.signal_meta` which `BlendTask` already propagates via
  `TradeCandidate`. Historical rows remain null and are skipped at
  emission time — no backfill required.

### Changed
- **`PaperTrader.resolve_market` is now async.** The blocking DB work is
  factored into `_resolve_market_sync` and dispatched via
  `asyncio.to_thread` so the MAC-ASYNC-002 invariant (DB work off the
  event loop) is preserved. Call sites updated:
  - `main.py::_check_and_resolve` — plain `await` (internal dispatch
    handles off-loop execution).
  - `main.py` CLI `--resolve` path — wrapped with `asyncio.run(...)`.
- **`PaperTrader.__init__` takes an optional `calibration_task` kwarg**
  for constructor injection; defaults to `None` so existing test fixtures
  and in-process CLI utilities continue to work unchanged. `main.py` now
  constructs `CalibrationTask` before `PaperTrader` and injects the same
  instance into both `PaperTrader` (resolve-time emission) and
  `BlendTask` (scaling-factor consumer).
- Calibration-emission errors during resolve are caught and logged rather
  than propagated — the DB resolution has already committed by the time
  the calibration path runs, so a calibration-side failure must not
  corrupt resolved-trade state. Resolved rows remain resolved.

### Project management
- **ROADMAP P4.2** hard-blocker cross-reference to `PROFIT-CAL-001` struck.
  P4.2 is now gated only on the ≥10 resolved paper trades threshold; that
  threshold is itself downstream of P0-GATE (LLM market-anchoring), a
  separate debt item. Phase 4 dependencies updated accordingly.
- **`docs/profit_path_debt_log.md`** PROFIT-CAL-001 status OPEN → COMPLETE
  with resolution notes citing the two delivery commits (`186b495`,
  `74649c6`) and the Path A implementation summary. Open-count header
  decremented HIGH 4 → 3.

### Tests
- New `tests/test_paper_trader.py::TestCalibrationEmission` (7 cases)
  covers: column population from `signal_meta`, NULL handling for
  historical rows, three-lane fan-out, `CalibrationTask._state` update,
  swallowed-error survival (resolution still committed), and migration
  idempotence. 13 existing `resolve_market` test call sites converted to
  a sync `_run_resolve` helper that `asyncio.run()`s the async method.
- `tests/test_main_pipeline.py::test_resolve_market_called_off_event_loop_thread`
  rewritten to simulate the new architecture: the mock's `side_effect`
  internally dispatches via `asyncio.to_thread`, so the
  MAC-ASYNC-002 invariant is still asserted against the blocking sync
  portion rather than the async wrapper.
- Stale test fix (unrelated to PROFIT-CAL-001):
  `tests/test_source_scorecard.py` expected `"BBC News"` to be disabled,
  but BBC / France 24 / Deutsche Welle were re-enabled in the B3
  source-integration batch (commit `722196e`). Switched references to
  `"Foreign Policy"` which is currently in `DISABLED_NEWS_SOURCES`.
- Full suite: **1059 passed, 1 skipped, 0 failures.**

### Validation still pending
- **Live-traffic observation** of at least one `CALIBRATION_CHECK` event
  in `logs/trades/live/trades.jsonl` after the first paper-trade
  resolution post-fix. This is a production-soak confirmation separate
  from unit-test coverage and is blocked behind P0-GATE (the pipeline
  currently produces zero trades → zero resolutions → zero natural
  emission opportunities). Once P0-GATE is addressed and paper trades
  start resolving, the first resolved ticker with populated lane data
  will produce the observation.

---

## [0.29.46] - 2026-04-24

### Changed
- **P2.3 closed — `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION` default flipped from
  `"false"` to `"true"`** in `config.py`, and `.env.example` updated to reflect
  the new recommended baseline. Low-quality match suppression is now the project
  default; set the env var to `false` only to temporarily revert.

### Project management
- **P2.3 ROADMAP row marked COMPLETE** with retroactive open timestamp of
  2026-04-24T02:00:00 UTC (post-v0.29.44 / v0.29.45 boot state — the point at
  which source-config churn settled and continuous stable operation began).
- **Early-open override of the 24h post-P2.2-closure soak gate**, same
  bounded-exception pattern used to early-close P2.2 at 25.5h: (i) the 24h
  time gate (expiry 2026-04-24T13:45:13 UTC) was a proxy for post-closure
  stability; direct stability evidence from the 2026-04-24 daily review
  supersedes the proxy, (ii) no-change-scope clause of the 24h gate fully
  satisfied — zero keyword-gate / pre-LLM match / executor-selectivity code
  paths modified between the P2.2 closure commit (`8e18b91`, 2026-04-23T07:45:13
  -06:00) and this closure, (iii) four-check stability gate added in commit
  `941af75` (crash-free, feed reachability, Google News attribution
  end-to-end, analysis pipeline nominal) all observably passing per the daily
  review (14,790 records / 67/67 LLM success / 0 tracebacks / 0 failed
  fetches post-v0.29.45), (iv) suppression behavior directly validated —
  `scripts/match_suppression_audit.py` audited 12/12 in-window suppression
  candidates as "likely safe to suppress" (0/12 risky), confirming P2.3's
  enforcement decisions are observably correct.
- **Runtime reality check during close-out:** the user's `.env` already had
  `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION=true` set, so the flag had in fact been
  live in runtime — this release is documentation reconciliation (aligning
  the `NOT_STARTED` ROADMAP state with the `true` runtime state) plus
  project-default alignment rather than a behavior-change moment.

---

## [0.29.45] - 2026-04-24

### Removed
- **`https://mid.ru/en/rss/` (Russia MFA)** removed from `RSS_FEEDS` and its
  entry removed from `EARLY_MAX_NEWS_AGE_BY_SOURCE`. Investigation during the
  pre-P2.3-open smoke test found the domain is behind F5/Shape TSPD bot
  protection: default curl/feedparser/python-urllib user-agents get an
  immediate TCP reset at the proxy; browser-typed user-agents receive a
  JavaScript challenge HTML page (`bobcmn` / `TSPD_101_DID` payload) instead
  of RSS content. Reachable only by executing the JS challenge in a real
  browser engine — out of scope for a feedparser-based RSS client. Not a UA
  or header fix. Removed 17/17 polls of wasted `Failed to fetch ... Connection
  reset by peer` warnings per hour.

---

## [0.29.44] - 2026-04-23

### Changed
- **`google_news_query` re-enabled from `DISABLED_SOURCE_FAMILIES`** in
  `config.py`. Combined with v0.29.43's per-entry publisher attribution,
  the Google News search lane now flows items into the pipeline with
  real-publisher source strings (Reuters, AP News, BBC, Kyiv Independent,
  Al Jazeera, …) rather than per-query fragments. This is the primary
  path back to Reuters and AP News coverage after the dead direct-RSS
  URLs were removed in v0.29.39. `bing_news_query` stays disabled: live
  probe showed 9-12 items/query vs. Google's 100, no per-entry source
  attribution, 279h-stale newest item on a hot topic — not worth the
  polling cost.

### Notes
- `feeds/search_news_monitor.py` was already wired into `main.py`; it
  has been no-op'ing since v0.25.2 because both families were in
  `DISABLED_SOURCE_FAMILIES`. With this change the task starts polling
  25 queries per 300s cycle, AIMD-capped at 3-15 articles per query
  based on `news_queue` fill ratio. Queue-overflow protections from
  v0.12.0 (query dedup via frozenset token-sets, sports/economic/ticker
  content filters, shared dedup cache) remain in place.
- Source fit: historical probe showed the 4 active market categories
  (Trump approval, Trump/Iran, Ukraine/Russia, Israel/Gaza) all return
  100 items with diverse real-publisher sources including wires we
  can't otherwise access (Reuters, AP News).
- P2.3 observation-period constraint preserved: this is
  `DISABLED_SOURCE_FAMILIES` only, not keyword-gate / pre-LLM match /
  executor-selectivity code paths. P2.3 post-close soak timer (24h from
  commit 8e18b91) is not reset.
- No test regressions: 65 pass (9 rss_monitor + 56 main_pipeline).

## [0.29.43] - 2026-04-23

### Added
- **Per-entry publisher attribution for aggregator RSS feeds**
  (`feeds/rss_monitor.py::_entry_source`). News aggregators like Google
  News RSS expose the real publisher at the item level via
  `<source url="...">Publisher</source>`; feedparser surfaces this as
  `entry.source.title`. `poll_feed` now prefers that value for
  `NewsItem.source`, falling back to the feed-level title when an entry
  doesn't declare one. The change is backward-compatible: regular
  publisher RSS feeds (BBC, NYT, Guardian, Al Jazeera, …) don't populate
  `entry.source`, so their items still carry the feed-level label
  exactly as before.
- **`tests/test_rss_monitor.py`** covering `_entry_source`: attribute
  access, dict-style access, missing source, None source, empty string,
  whitespace-only title, whitespace-stripped title, non-string title
  (defensive coercion), and dict with missing `title` key.

### Notes
- Preparatory for re-enabling the Google News search lane
  (`DISABLED_SOURCE_FAMILIES["google_news_query"]` — lands in the next
  commit). Without this change, Google News items would all carry
  query-level source strings like `'"trump iran peace deal" - Google
  News'` with no per-publisher attribution, fragmenting
  `source_scorecard` stats and missing `SOURCE_PRIORITY_TIERS` /
  `EARLY_MAX_NEWS_AGE_BY_SOURCE` lookups that key on the real publisher
  name.
- No config changes in this commit. 65 tests pass (9 new + 56 existing
  main-pipeline tests).
- P2.3 observation-period constraint preserved: this is `feeds/`
  parsing code, not keyword-gate / pre-LLM match / executor-selectivity.
  P2.3 post-close soak timer (24h from commit 8e18b91) is not reset.

## [0.29.42] - 2026-04-23

### Added
- **Bellingcat** (`bellingcat.com/feed/`, feed.title `"bellingcat"` —
  lowercase) — Tier 2 OSINT investigative feed. Slow cadence (10 items,
  84.2h newest on probe) but high signal-to-noise for Russia/Ukraine
  and geopolitical investigations. Tier 2, Batch 3 of 3 per the
  2026-04-23 source-integration audit.

### Changed
- **Re-enabled three previously-disabled mainstream wires** after URL
  probe confirmed they're publishing current content:
  - **BBC News World** (`feeds.bbci.co.uk/news/world/rss.xml`,
    feed.title `"BBC News"`) — 39 items, 7.5h newest on probe.
  - **France 24 English** (`france24.com/en/rss`,
    feed.title `"France 24 - International breaking news, top stories
    and headlines"`) — 23 items, 0.9h newest on probe.
  - **Deutsche Welle World** (`rss.dw.com/rdf/rss-en-world`,
    feed.title `"World | Deutsche Welle"`) — 13 items, 5.2h newest on
    probe.

  These were in `DISABLED_NEWS_SOURCES` with the comment "re-enable
  one at a time with measurement." The 2026-04-23 pipeline-hygiene
  audit confirmed all three feeds are live and producing fresh items,
  and the config comment block now reflects this. `BBC News` is
  retained in `SOURCE_PRIORITY_TIERS` at tier 1 (wire service break-
  first signal). Each of the three has an `EARLY_MAX_NEWS_AGE_BY_SOURCE`
  override at 1800s for parity with other publisher-RSS feeds.

### Notes
- Three-batch source-integration work (B1 + B2 + B3) is now complete.
  The bot polls 22 RSS sources (up from 6 after the 2026-04-23 hygiene
  fix) with 22 matching per-source freshness overrides.
- No runtime code changes; config-only. `tests/test_main_pipeline.py`
  still passes 56/56.
- P2.3 observation-period constraint preserved across all three
  batches (same rationale as B1/B2 -- no keyword-gate / pre-LLM match /
  executor-selectivity code changes).

## [0.29.41] - 2026-04-23

### Added
- **Six institutional + industry RSS sources** (Tier 1, Batch 2 of 3).
  All URLs were live-probed before commit (HTTP 200 + current items):
  - **UN News (English)** (`news.un.org/feed/subscribe/en/news/all/rss.xml`,
    `"UN News - Global perspective Human stories"`) — 30-item / 13.1h
    feed; resolutions, ceasefires, conflict escalation coverage.
  - **European Commission press corner**
    (`ec.europa.eu/commission/presscorner/api/rss?language=en`,
    `"Press releases - RSS"`) — 10-item / 9.6h feed; EU sanctions +
    foreign-policy signal for Russia/Iran markets.
  - **IAEA** (`iaea.org/feeds/news`,
    `"Top Stories From the International Atomic Energy Agency"`) —
    150-item / 35.6h feed; slow cadence but authoritative nuclear /
    Iran signal.
  - **Russia MFA (English)** (`mid.ru/en/rss/`,
    `"The Ministry of Foreign Affairs of the Russian Federation"`) —
    10-item / 34.4h feed; often first signal on reciprocal foreign-
    policy actions.
  - **Defense News** (`defensenews.com/arc/outboundfeeds/rss/?outputType=xml`,
    `"Defense News"`) — 25-item / 6.1h feed; defense industry press.
  - **Breaking Defense** (`breakingdefense.com/feed/`,
    `"Breaking Defense"`) — 15-item / 4.8h feed; defense industry press.
- Per-source `EARLY_MAX_NEWS_AGE_BY_SOURCE` overrides set to 1800s for
  each (parity with other publisher-RSS overrides). Slow-cadence sources
  (IAEA, Russia MFA) are included specifically to catch *fresh* items
  when they're published; the pre-existing archive items will be
  correctly dropped on first poll.

### Notes
- No runtime code changes; config-only. `tests/test_main_pipeline.py`
  still passes 56/56.
- P2.3 observation-period constraint preserved (same rationale as B1).

## [0.29.40] - 2026-04-23

### Added
- **Six new RSS sources targeting the bot's active market set** (Tier 1,
  Batch 1 of 3 per the 2026-04-23 source-integration audit in
  `docs/_archive/studies/news_sources_evaluation.md` + ROADMAP Appendix A). All URLs
  were live-probed before commit (HTTP 200 + current items):
  - **Kyiv Independent** (`kyivindependent.com/news-archive/rss/`,
    feed.title `"The Kyiv Independent"`) — Ukraine coverage; URL discovered
    via HTML `<link rel="alternate">` scan (non-obvious path; `/rss/` and
    `/feed/` both 404).
  - **Kyiv Post** (`kyivpost.com/feed`, `"Kyiv Post"`) — Ukraine wire-style,
    100-item / 1.0h fresh feed.
  - **Iran International** (`iranintl.com/en/feed`, `"Iran International"`)
    — 100-item / 4.1h fresh Iran diaspora coverage; direct fit for
    KXTRUMPIRAN / KXIRAN markets.
  - **Times of Israel** (`timesofisrael.com/feed/`, `"The Times of Israel"`)
    — 15-item / 0.3h fresh Middle East coverage; direct fit for KXMIDEAST.
  - **Department of War** (`defense.gov/.../RSS.ashx?...Site=945`,
    `"Department of War News Feed"`) — US military action signal; feed
    title reflects the 2025 DoD rename.
  - **White House** (`whitehouse.gov/news/feed`,
    `"News – The White House"`, en-dash U+2013 in title) — executive-branch
    announcements for Trump markets.
- Per-source `EARLY_MAX_NEWS_AGE_BY_SOURCE` overrides set to 1800s for each
  new source (parity with other publisher-RSS feeds; default 300s would
  drop virtually everything given observed publication latencies).

### Notes
- No runtime code changes; config-only. `tests/test_main_pipeline.py`
  still passes 56/56.
- P2.3 observation-period constraint preserved: these additions are
  `RSS_FEEDS` / `EARLY_MAX_NEWS_AGE_BY_SOURCE` only, not keyword-gate /
  pre-LLM match / executor-selectivity code paths. The P2.3 post-close
  soak timer (24h) is not reset by this change.

## [0.29.39] - 2026-04-23

### Fixed
- **Pipeline hygiene — removed five dead RSS URLs from `config.RSS_FEEDS`**:
  Reuters (`feeds.reuters.com/reuters/{worldNews,topNews}`), AP News
  (`feeds.apnews.com/rss/apf-{topnews,intlnews}`), and Radio Free Europe
  (`www.rferl.org/api/zyqopjmxel`). Direct HTTP probes confirmed the four
  Reuters/AP URLs fail to connect (curl returns 000) and the RFE endpoint
  returns HTTP 404. Reuters discontinued public RSS in June 2020; AP News
  has no official public RSS endpoint. Prior to this cleanup, the scheduler
  was polling these URLs every 60s for zero content, and the bot
  misrepresented itself as a 9-source pipeline when only 4 sources were
  actually producing matches (documented in the 2026-04-23 pipeline-hygiene
  audit). For Reuters/AP-style wire coverage, re-evaluate
  `feeds/search_news_monitor.py` (google_news_query family, currently in
  `DISABLED_SOURCE_FAMILIES`) or an RSSHub-style aggregator; do not
  resurrect the dead direct URLs.

### Changed
- **Politico (`"Politics"` source) re-enabled from `DISABLED_NEWS_SOURCES`**:
  pipeline-hygiene audit confirmed the 127 `EARLY_STALE_DROP` events
  emitted for this source over the 60h post-fix window all carried
  `reason="disabled_source"` — not a parser bug as I initially suspected.
  Politico's RSS feed is live (HTTP 200, 30 items, newest 3.5h old), so the
  correct action is to let the source flow to analysis rather than short-
  circuit-reject at ingestion. A per-source freshness override
  (`EARLY_MAX_NEWS_AGE_BY_SOURCE["Politics"] = 1800`) lands alongside the
  re-enable to give the feed parity with other publisher-RSS overrides —
  at the default 300s threshold virtually nothing would pass (Politico
  publishes items 30min+ old). Measurement pending.

### Notes
- No runtime code changes; config-only cleanup. `tests/test_main_pipeline.py`
  still passes 56/56.
- The pipeline-hygiene audit also flagged Guardian Ukraine for "Remove
  Immediately" per `scripts/source_scorecard.py` — this recommendation was
  explicitly rejected per the don't-cut-off-a-source-when-the-failure-is-ours
  principle: Guardian Ukraine's 84% stale drops are because the feed itself
  publishes 1–7 day old items, not because of a pipeline bug, and the
  source does contribute 1 OPPORTUNITY in the 60h window. Left in place.

## [0.29.38] - 2026-04-22

### Added
- **`scripts/daily_review.py` — true single-command pipeline trace**:
  the daily review now sources from eight read-only summarizers instead
  of five, and every section header carries a `[source: scripts/<script>.py]`
  attribution tag so each metric traces back to the script that produced
  it. Three new summarizers integrated:
  - `match_suppression_audit.summarize()` — folded into section 2
    (MATCHING) as a sub-block reporting total suppression candidates,
    safe-vs-risky classification with percentages, and top risky-
    suppression sources.
  - `keyword_feedback.summarize()` — folded into section 3 (ANALYSIS)
    as a sub-block reporting `no_keywords` miss count, corroborating
    keyword-gate rows, unique candidate phrases, and the strongest
    specific miss candidates with count / source / ticker / category.
  - `source_scorecard.summarize()` — new section 8 (SOURCE SCORECARD)
    reporting tier counts (top performers / keep / watch / prune /
    remove immediately) with per-tier top-N rows showing observed
    records, signals, paper trades, resolved count, win rate, and P&L.
- **Restructured appendix** in the daily review splits scripts into
  two groups: *integrated* (maps each script to the section it feeds)
  and *deep-dive only* (run separately when investigating), so the
  reader knows at a glance which tools remain available outside the
  daily command.

### Changed
- **Stage count** reported by `--profile` in `scripts/daily_review.py`
  is now 8 (was 5).

### Notes
- No trading behavior change. No production code modified. This release
  is diagnostic / reporting tooling only.
- Scripts explicitly *not* integrated (with rationale documented in the
  appendix): `trade_log_summary.py` (overlaps ~95% with
  `decision_funnel_summary.py`), `performance_analysis.py`,
  `pipeline_impact_audit.py`, `replay_dossier.py`,
  `regime_weight_validation.py`, `observability_completeness_review.py`,
  `ollama_error_audit.py`, `keyword_shadow_eval.py`,
  `keyword_promotion_report.py`, `botcheck.py` — these remain as
  deep-dive tools and are referenced in the restructured appendix.

## [0.29.37] - 2026-04-22

### Added
- **Stage 5 Phase 2 — P2.1: `all_required` keyword override mode**
  (`analysis/signal_analyzer.py`, `config.py`): tightens the pre-LLM match
  gate's keyword override from `any_hit` (a single keyword bypasses the
  gate) to `all_required` (every `GEOPOLITICAL_SIGNALS` group must
  contribute at least one keyword hit before the override fires). The
  override is still only consulted when the gate is enforced; the gate
  itself remains off (`enable_pre_llm_match_gate=false`) and in
  diagnostics-only mode (`pre_llm_match_gate_diagnostics_only=true`), so
  the effective change is log fields only (`pre_llm_keyword_override_mode`
  on `SIGNAL_ANALYSIS_DETAIL` events).

### Changed
- **Default keyword override mode** is now `all_required` (was `any_hit`).
  The legacy `PRE_LLM_MATCH_GATE_KEYWORD_OVERRIDE_ANY_HIT` env var still
  resolves to `any_hit` when set explicitly to truthy; otherwise the new
  default applies. See `docs/ROADMAP.md` P2.1 semantic clarification for
  the rationale and rollback criteria (P2-GATE).

## [0.29.36] - 2026-04-22

### Fixed
- **`main._structural_recompute_task` — guaranteed yield point**: the inner
  `while True` recompute loop now starts every iteration with
  `await asyncio.sleep(0)`. Zero-delay in production (real `run_periodic`
  already awaits work), but prevents a hot-spin if `run_periodic` ever
  returns synchronously — which is how a bare `AsyncMock()` in a test
  caused pytest to accumulate unbounded mock call history (~3 GB RSS) and
  get SIGKILL'd mid-run. Paired regression test
  (`test_structural_recompute_yields_even_if_run_periodic_returns_instantly`
  in `tests/test_main_pipeline.py`) uses wall-clock elapsed time as the
  signal: a regressed defensive yield causes the test to fail within ~0.6s
  with a clear diagnostic, rather than hanging until OOM.

## [0.29.35] - 2026-04-22

### Added
- **Stage 5 Phase 1 observability fixes (P1.1 / P1.2 / P1.3)** — diagnostic
  and reporting additions only; no trading behavior change.
  - P1.1 — `MATCH_DIAGNOSTIC` events now include `publish_ts` (ISO 8601 UTC
    from `news.published`) and `age_at_match_seconds` (delta from publication
    to match evaluation), enabling staleness-at-match analysis distinct from
    freshness-at-ingestion. `utils/logger.log_match_diagnostic` accepts the
    new fields; `analysis/market_matcher.find_candidates` computes and passes
    them for every emitted diagnostic.
  - P1.2 — daily report INGESTION section now appends a per-source freshness
    waterfall (fast_operational / near_threshold / chronically_late / dead /
    insufficient) by reusing `scripts/freshness_diagnostics.bucket_sources()`
    and `format_compact_rows()`, ensuring label consistency with the
    standalone script.
  - P1.3 — daily report MATCHING section now shows `pre_llm_would_block`
    as a global count, plus per-source and per-market drilldowns, surfacing
    which sources and markets are filtered before the LLM gate.
    `scripts/match_quality_diagnostics.summarize()` tracks the new counters
    so the standalone script benefits from the same data.
  - Tests added/updated in `tests/test_market_matcher.py`,
    `tests/test_match_quality_diagnostics.py`, and `tests/test_daily_review.py`.

## [0.29.34] - 2026-04-20

### Fixed
- **`scripts/botcheck.py` — shell alignment and macOS `etime` fix**:
  - Process table was silently empty on macOS because `ps` was called with
    `etimes=` (Linux-only, seconds); macOS `ps` rejects it, `run_command`
    returned `""`, so all detection reported "not running". Fixed by switching
    to `etime=` (macOS `[[dd-]hh:]mm:ss` format) and adding `_parse_etime()`
    to convert to seconds.
  - Bot and caffeinate detection now mirrors `~/.zshrc` shell helpers exactly:
    `_bot_pids()` / `_caffeinate_pids()` / `print_bot_section()` /
    `print_caffeinate_section()` replace the old relationship-inference code.
    Caffeinate is detected independently by exact command match (no PPID gate),
    matching `_kalshi_caffeinate_pids()`'s `pgrep -f` semantics.
  - Tests (`tests/test_botcheck.py`) rewritten to cover: launchd PID priority,
    caffeinate-as-child detection, Python.app deep-framework path, exact command
    match rejection, independent caffeinate sections (20 tests total).

## [0.29.33] - 2026-04-21

### Fixed
- **Structural recompute crash loop** (`tasks/structural_task.py`, `main.py`):
  the bot was restarting every ~13 minutes because `StructuralComputationError`
  (wrapping `sqlite3.OperationalError: unable to open database file`) propagated
  uncaught from `run_once` → `run_periodic` → `_structural_recompute_task` →
  `asyncio.gather(*tasks)` in `run()`, killing the entire process. launchd
  immediately restarted it; no shutdown marker was written, making botcheck
  report all sessions as "unpaired".
  Two-layer fix:
  1. `run_once` (`structural_task.py`): uses `asyncio.gather(..., return_exceptions=True)`
     so per-market failures log a warning and are excluded from the return value
     instead of aborting the entire 855-market gather.
  2. `_structural_recompute_task` (`main.py`): wraps `run_periodic` in a retry
     loop so an unexpected exception from the task body logs and retries after
     60 s rather than crashing the process.
  - Test: `test_run_once_market_failure_does_not_propagate` added to
    `tests/test_structural_task.py`.

## [0.29.32] - 2026-04-20

### Fixed
- **Log rotation guarantee** (`utils/logger.py`, `main.py`): daily midnight UTC
  rotation was silently skipped when the Python 3.14 `doRollover()` early-return
  path fired (destination archive transiently exists → `os.path.exists(dfn)`
  returns True → rotate() never called, no archive created, file not truncated).
  Two-layer fix:
  1. `_log_rotation_task()` (new background task, `main.py`): wakes 5 s after
     each `rolloverAt` epoch and calls `rotate_logs()` if `shouldRollover()`
     still returns True, guaranteeing rotation ≤ 10 s after UTC midnight
     independent of emit timing.
  2. `_maybe_rotate_stale()` content-based fallback (`utils/logger.py`): on bot
     restart, reads the first non-comment log line timestamp; if it predates
     `period_start`, forces rotation even when mtime appears current-period
     (catches run-through-midnight failure discovered at Apr 19 → Apr 20 UTC).
  - `_first_log_timestamp()` helper added to `utils/logger.py`.
  - Tests: `test_first_log_timestamp_parses_correctly`,
    `test_rotation_guard_scenario_force_rollover_resolves_missed_rotation`,
    `test_startup_rotation_uses_first_log_timestamp_when_mtime_is_current`
    added to `tests/test_logger_rotation.py` (10 rotation tests total).

---

## [0.29.31] - 2026-04-19

### Docs
- `PLATFORMS.md` (new, MAC-DOC-003): platform support matrix for Windows /
  macOS / Linux covering runtime, automation, scripts, and persistence. All
  Windows-only scripts listed with macOS equivalents. Linked from README.md.
- `README.md`: added link to PLATFORMS.md.

---

## [0.29.30] - 2026-04-19

### Tests
- `tests/test_logger_rotation.py` (MAC-TEST-004): added two boundary edge-case
  tests for `_maybe_rotate_stale()` — `mtime == period_start` must NOT rotate
  (file belongs to current period); `mtime == period_start - 1` MUST rotate
  (file belongs to previous period). Pins the strict `<` comparison and guards
  against macOS APFS nanosecond-boundary regressions.

---

## [0.29.29] - 2026-04-19

### Tests
- `tests/test_instance_guard.py` (new, MAC-TEST-003): 3 tests verifying
  `_RuntimeInstanceGuard` correctly acquires the lock over a stale lock file
  (dead PID content) and overwrites it with the current PID. Simulates macOS
  SIGKILL / OOM-kill scenario where the lock file is not cleaned up.

---

## [0.29.28] - 2026-04-19

### Fixed
- `main.py`: MAC-DB-005 — added `PRAGMA wal_checkpoint(RESTART)` to
  `_log_maintenance_task()` so the WAL file is checkpointed once per 24-hour
  maintenance cycle. Prevents unbounded WAL growth on long-running macOS
  sessions where NSSM daily restarts don't occur. Added `import sqlite3` to
  top-level imports.

---

## [0.29.27] - 2026-04-19

### Fixed
- `trading/paper_trader.py`: MAC-DB-004 — replaced `executescript(_DDL)` /
  `commit()` in `initialize()` with a `with self._conn:` block that splits the
  DDL on `;` and executes each statement individually. A single `BEGIN`/`COMMIT`
  now wraps all CREATE TABLE statements; a mid-DDL failure rolls back cleanly
  instead of leaving the DB partially migrated.

---

## [0.29.26] - 2026-04-20

### Fixed
- `main.py`: MAC-PLAT-001 — replaced both `os.name == "nt"` guards in
  `_RuntimeInstanceGuard._lock_handle()` / `_unlock_handle()` with the
  idiomatic `sys.platform == "win32"`.
- `utils/logger.py`: MAC-LOG-001 — `_rotate_live_to_archive()` now re-raises
  `PermissionError` on non-Windows platforms instead of silently falling back
  to copy+truncate; added `import sys`. Windows copy+truncate fallback is
  preserved.
- `main.py`: MAC-FS-001 / MAC-DOC-001 — NSSM service log archive cleanup block
  wrapped in `if sys.platform == "win32":` with inline comment; docstring
  entries annotated as Windows-only. Dead globs no longer run on macOS.
- `tests/test_trade_log_store.py`: updated `PermissionError` test:
  `test_trade_log_store_permission_error_fallback_preserves_records` is now
  skipped on non-Windows; new
  `test_trade_log_store_permission_error_raises_on_non_windows` asserts
  re-raise behavior on macOS/Linux.

---

## [0.29.25] - 2026-04-20

### Added
- `scripts/setup_launchd.sh`: MAC-CLI-001 — macOS launchd agent installer for
  `daily_review.py`. Accepts `--time HH:MM` (default 09:00) and `--uninstall`.
  Generates `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist`, loads the
  agent via `launchctl load`, and logs to `logs/launchd_daily_review*.log`.

### Fixed
- `scripts/setup_daily_task.ps1`: MAC-DOC-002 — added Windows-only platform
  notice with reference to `scripts/setup_launchd.sh`.
- `scripts/daily_review.ps1`: MAC-CLI-002 / MAC-DOC-002 — added Windows-only
  platform notice with reference to `daily_review.py` direct invocation.

---

## [0.29.24] - 2026-04-20

### Added
- `tests/test_evidence_store_concurrency.py`: MAC-TEST-002 — three regression
  guard tests for `EvidenceStore` concurrent writes:
  `test_concurrent_writes_to_20_markets_no_operational_error` (20 concurrent
  `asyncio.gather()` writes to 20 distinct tickers; all must succeed and be
  readable), `test_concurrent_writes_same_market_are_serialised` (10 concurrent
  writes to one ticker validate per-market lock), and
  `test_wal_mode_is_active_after_first_write` (reads `PRAGMA journal_mode`
  and asserts `wal`; fails if MAC-DB-001 PRAGMA is removed). Closes the final
  Pre-Go-Live gate test item.

---

## [0.29.23] - 2026-04-19

### Fixed
- `tasks/evidence_store.py`: MAC-DB-001 — `_connect()` now sets
  `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`. WAL mode allows
  concurrent readers during writes and eliminates the global write-lock
  contention that serializes concurrent `asyncio.to_thread()` evidence writes
  during multi-market news events.
- `trading/paper_trader.py`: MAC-DB-002 — SQLite connection now uses
  `timeout=30.0`, matching `evidence_store._connect()` and preventing a silent
  5-second failure if the DB is held open by an interactive session.

---

## [0.29.22] - 2026-04-19

### Fixed
- `main.py`: MAC-ASYNC-002 — five blocking PaperTrader / SQLite calls on the
  event loop thread are now dispatched via `asyncio.to_thread()`:
  - `_daily_report_task()`: `self.paper.daily_summary()`,
    `self.paper.generate_report()`, and `report_path.write_text()` (file I/O)
    each wrapped with `await asyncio.to_thread(...)`.
  - `_check_and_resolve()`: direct `self.paper._conn.execute(...).fetchall()`
    query wrapped in a lambda and dispatched via `to_thread`; loop-body
    `self.paper.resolve_market()` and post-loop `self.paper.get_notional_bankroll()`
    both wrapped with `await asyncio.to_thread(...)`.
- `tests/test_main_pipeline.py`: `TestMainAsyncBlocking` — five new MAC-ASYNC-002
  regression guard tests verifying each of the above calls is dispatched off the
  event loop thread.

---

## [0.29.21] - 2026-04-19

### Fixed
- `trading/executor.py`: MAC-ASYNC-001 — `_execute_paper()` now dispatches
  `record_trade()` and `get_notional_bankroll()` via `asyncio.to_thread()`
  instead of calling them directly on the event loop thread. Both SQLite
  operations are batched in a single `to_thread` closure to eliminate two
  separate thread dispatches and close the race window between the write and
  the post-trade bankroll read.
- `tests/test_executor.py`: `TestPaperExecutionAsync` — two new tests:
  `test_record_trade_called_off_event_loop_thread` (MAC-TEST-001 regression
  guard; fails if `record_trade` reverts to a direct blocking call) and
  `test_execute_paper_returns_correct_trade_id_and_logs` (functional
  end-to-end check).

---

## [0.29.20] - 2026-04-19

### Fixed
- `utils/logger.py`: Four macOS rollover correctness fixes.
  1. **Python 3.14 `rolloverAt` hotloop** — `_rollover_self()` now advances
     `rolloverAt` via `computeRollover()` after `super().doRollover()` returns
     early when the destination archive already exists (e.g. after a prior
     `--rotate-logs` run). Without this fix `shouldRollover()` returns `True`
     forever, causing every `emit()` to trigger a no-op `doRollover()` cycle.
  2. **Startup stale-content rotation** — new `_maybe_rotate_stale()` helper
     compares the active log file's `mtime` to the current UTC-period start
     (`rolloverAt - interval`). If the file was last written before that
     boundary it is archived via `doRollover()` before new content is appended.
     Called in `_ensure_file_handlers()` for both `bot.log` and `errors.log`.
     Handles the macOS dev-machine pattern where the bot stops before midnight
     and the scheduled rotation never fires.
  3. **`rotate()` error visibility** — copy and truncate failures now print to
     `stderr` with source/dest paths and the exception message instead of being
     silently discarded.
  4. **`utc=True` alignment** — `_app_fh` and `_err_fh` now created with
     `utc=True` so rotation fires at UTC midnight, consistent with UTC-formatted
     log content and the existing test configuration.
- `tests/test_logger_rotation.py`: Three new tests — `test_startup_rotation_archives_stale_log_content`,
  `test_rollover_at_advances_after_archive_already_exists`,
  `test_forced_rollover_cycle_no_hotloop`.

---

## [0.29.19] - 2026-04-19

### Changed
- `main.py`: Wire multi-lane runtime integration (S4.5 root-cause fix).
  `_process_candidate` now routes fast-lane `SignalAnalysis` through
  `BlendTask.process_fast_lane_result()` instead of directly calling
  `executor.execute()`. Evidence is also submitted to `AccumulationTask`
  via `_evidence_queue`. Three new background tasks launched at startup:
  `accumulation` (evidence ingestion), `blend_consumer` (trading queue
  drain → executor), and `structural` (hourly structural prior recompute).
  Adds `_signal_to_evidence()` helper to convert `SignalAnalysis` to
  `Evidence`. `source_stats.increment_trades` moved to consumer task.
- `tests/test_main_pipeline.py`: Update stub and assertions to match
  new routing: `executor.execute` assertions replaced with
  `_blend_task.process_fast_lane_result` assertions.

---

## [0.29.18] - 2026-04-19

### Added
- `scripts/regime_weight_validation.py`: S4.4 backtesting script that loads
  `windows_archive` trade events, classifies each of 169 markets by regime
  (fast / structural / interpretation), and compares regime-weighted vs
  unweighted blending on 5 resolved markets. Key findings: regime classifier
  functions correctly by ticker prefix; fast-lane signals show lower mean
  |edge| in structural markets (supports attenuation); only 5 resolved markets
  in the archive (insufficient for statistical significance — recommend S4.5 to
  accumulate CALIBRATION_CHECK events via S3.6). No architectural revision
  indicated; proceed to S4.5.
- `docs/ROADMAP.md`: S4.4 marked COMPLETE.

---

## [0.29.17] - 2026-04-19

### Added
- `scripts/observability_completeness_review.py`,
  `tests/test_observability_completeness_review.py`:
  Added S4.2 read-only observability completeness review for `BLEND_DECISION`
  required-field population and evidence → dossier → structural → blend → outcome
  traceability checks. Current paper log review found no blend/accumulation/structural
  events, so the 90% target is not met/evaluable for the available data window.
- `scripts/budget_manager_stress.py`, `tests/test_budget_manager_stress.py`:
  Added S4.3 synthetic budget-manager stress harness. The default profile drives
  265 requests against production limits, admits exactly 60, opens the circuit breaker
  at queue depth 180 (3× global budget), emits one `BUDGET_PRESSURE` event, and proves
  no runaway admission.
- `docs/ROADMAP.md`: S4.2 and S4.3 marked COMPLETE.

---

## [0.29.16] - 2026-04-19

### Added
- `scripts/replay_dossier.py`, `tests/test_replay_dossier.py`:
  Added S4.1 read-only dossier replay utility. The CLI replays persisted evidence rows
  by market using deterministic `(ingested_ts, evidence_id)` ordering, feeds each event
  through the existing dossier builder, prints the belief trajectory, and compares the
  final replayed dossier to stored current state. Replay fails loudly when persisted
  evidence is missing replay-critical `raw_payload_json.implied_probability`.
- `docs/ROADMAP.md`: S4.1 marked COMPLETE.

---

## [0.29.15] - 2026-04-19

### Added
- `analysis/calibration_monitor.py`, `tasks/calibration_task.py`,
  `tests/test_calibration_monitor.py`, `tests/test_calibration_task.py`:
  Added S3.6 cross-lane calibration monitoring. Consumes `CALIBRATION_CHECK` events to
  maintain per-lane rolling Brier scores. Detects drift when a lane's MSE exceeds 1.5×
  the fast-lane baseline (≥5 samples each). Emits `[CALIBRATION_DRIFT]` warning on first
  detection. Auto-scales the drifting lane's effective confidence (floor 0.2).
- `tasks/blend_task.py`: Added `CalibrationLike` protocol and `calibration` parameter to
  `BlendTask`. When supplied, lane confidences are multiplied by the calibration scaling
  factor before blending. Default (None) is no-op — no behavior change.
- `analysis/__init__.py`: Declared `signal_meta: dict | None = None` as an explicit
  dataclass field on `SignalAnalysis` (was previously a dynamic attribute).
- `tasks/blend_task.py`: Fixed `_readiness_input()` source-lane classification — dossier
  with `current_estimate=None` now correctly applies fast-lane gate conditions (G1/G3/G4
  only), not accumulation gate conditions.
- `trading/executor.py`: Added explanatory comment on shape-based candidate detection in
  `_analysis_from_candidate()`.
- `docs/ROADMAP.md`: S3.6 marked COMPLETE.

---

## [0.29.14] - 2026-04-19

### Added
- `trading/executor.py`, `trading/paper_trader.py`, `utils/logger.py`,
  `tests/test_executor.py`:
  Added S3.5 executor compatibility for blended `TradeCandidate` inputs. The executor now
  normalizes blended candidates into the existing validation/execution path, preserves legacy
  `SignalAnalysis` behavior, logs blended `signal_meta`, and applies only
  `readiness_gate_min_edge_override` to the min-edge threshold. Existing paper/live gates,
  sizing, pricing, duplicate, concentration, and loss-limit protections remain intact.
- `docs/ROADMAP.md`: S3.5 marked COMPLETE.

---

## [0.29.13] - 2026-04-19

### Added
- `tasks/structural_task.py`, `tasks/evidence_store.py`, `docs/evidence_store_schema.*`,
  `tests/test_structural_task.py`, `tests/test_evidence_store*.py`:
  Added S3.2 structural-prior orchestration and persistence support. Structural priors are
  stored in `evidence_store.db` via the `structural_priors` table, recomputed only when no
  prior exists or dossier evidence is newer, and logged through `STRUCTURAL_PRIOR_RECOMPUTE`.
- `tasks/blend_task.py`, `tests/test_blend_task.py`:
  Added S3.4 blend-lane orchestration. Fast-lane results trigger lane context reads,
  `decision_blender.blend(...)`, readiness-gate evaluation, `BLEND_DECISION` emission for
  both ready and blocked candidates, and queue insertion only for approved `TradeCandidate`s.
  No trading or executor behavior is changed in this step.
- `docs/ROADMAP.md`: S3.2 and S3.4 marked COMPLETE.

---

## [0.29.12] - 2026-04-19

### Added
- `analysis/decision_blender.py`, `tests/test_decision_blender.py`:
  Decision blender implementing S3.3. Pure function `blend(...) -> BlendResult` with
  `LaneInput` and `BlendResult` frozen dataclasses. Implements DER-1 (confidence-weighted
  blend with RHR-3 regime interpolation), DER-2 (dominance rule: single lane >2× others
  adopts its p directly), DER-3 (structural fail-safe Tier 1: high-confidence structural
  divergence + active fast signal → pass with doubled min-edge override), and DER-4
  (Tier 2: stable structural veto when no fast signal). Disagreement score computed per
  CL-9 (confidence-weighted std-dev). Zero-eff-confidence fallback avoids division by
  zero. 34 new tests, 880 total passing.
- `docs/ROADMAP.md`: S3.3 marked COMPLETE.

---

## [0.29.11] - 2026-04-19

### Added
- `analysis/evidence_types.py`: `PriorEstimate` frozen dataclass — structural lane output type
  used across S3.1, S3.2, and S3.3/S3.4.
- `analysis/structural_prior.py`, `tests/test_structural_prior.py`:
  Structural prior synthesis implementing S3.1. Pure function `compute_structural_prior(market,
  context) -> PriorEstimate`. Estimate authority: LLM synthesis > market midprice. Confidence
  from three additive components: structural regime weight (max 0.25), evidence depth (max 0.25,
  saturates at 8 records), LLM synthesis bonus (0.45). Maximum confidence without LLM is 0.50,
  ensuring structural fail-safe tiers (DER-3/DER-4, threshold 0.70) require LLM synthesis.
  33 new tests, 846 total passing.
- `docs/ROADMAP.md`: S3.1 marked COMPLETE.
- `docs/IMPLEMENTATION_CONTRACT.md`: Section 14 (Phase 3 Implementation Clarifications, CL-1
  through CL-10) resolving all Phase Gate conditions; G4 Detail clarification on evaluation model.

---

## [0.29.10] - 2026-04-19

### Added
- `analysis/dossier_builder.py`, `tests/test_dossier_forgetting.py`:
  Forgetting mechanisms (S2.6): `decay_weight` (BSR-4 continuous decay), `recency_score`
  (G6 formula), `half_life_for_regime` (market-type-specific TTLs: fast=1d, interpretation=4d,
  structural=14d), `identify_superseded` (same-class high-overlap supersession), and
  `clear_on_resolution` (belief reset on resolution). 35 new tests, 777 total passing.
- `docs/ROADMAP.md`: S2.6 marked COMPLETE.

---

## [0.29.9] - 2026-04-19

### Added
- `analysis/evidence_types.py`: Shared frozen dataclasses `Evidence`, `EvidenceScore`, and
  `Dossier` used across S2.3, S2.4, and Codex's S2.2/S2.5.
- `analysis/evidence_scorer.py`, `tests/test_evidence_scorer.py`:
  Evidence quality scorer implementing BSR-5 (same-class diminishing returns) and BSR-7
  (source class + bigram overlap independence approximation). `score_evidence()` returns an
  `EvidenceScore` with quality, original weight, duplicate flag, and independence result. (S2.3)
- `analysis/dossier_builder.py`, `tests/test_dossier_builder.py`:
  Dossier belief update engine implementing BSR-1 through BSR-7. `classify_update()` returns
  `'state'` or `'confidence'`; `update_dossier()` applies displacement cap (BSR-2), drift
  detection and freeze (BSR-3), confidence evolution with contradiction penalty (BSR-6), and
  recovery mode with half-life expiry. All 38 tests including full synthetic drift cycle pass. (S2.4)
- `docs/ROADMAP.md`: S2.3 and S2.4 marked COMPLETE.

---

## [0.29.8] - 2026-04-18

### Added
- `kalshi/__init__.py`, `analysis/market_matcher.py`, `tests/test_market_matcher.py`:
  Added `regime_weights` to discovered `KalshiMarket` objects and populated it during market
  cache discovery using the existing regime classifier, without changing routing, matching,
  prioritization, or trading logic. (S1.4)
- `utils/logger.py`, `tests/test_structural_prior_log_schema.py`:
  Added `STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS` tuple and `log_structural_prior_recompute`
  method — telemetry schema for the structural prior layer. (S1.5)
- `utils/logger.py`, `tests/test_calibration_check_schema.py`:
  Added `CALIBRATION_CHECK_REQUIRED_FIELDS` tuple and `log_calibration_check` method —
  per-lane prediction error telemetry for cross-lane drift detection. (S1.6)
- `docs/ROADMAP.md`: S1.4, S1.5, S1.6 marked COMPLETE.

---

## [0.29.7] - 2026-04-18

### Added
- `main.py`, `analysis/signal_analyzer.py`, `utils/logger.py`, `tests/test_main_startup.py`:
  Added an isolated startup observability probe that emits exactly one tagged
  `SIGNAL_ANALYSIS_DETAIL` self-test record, validates required pre-LLM diagnostic fields at
  startup, and logs explicit `[STARTUP_PROBE][PASS]` / `[STARTUP_PROBE][FAIL]` markers without
  entering the live ingest, opportunity, or trading paths.
- `scripts/pipeline_impact_audit.py`, `scripts/signal_edge_diagnostics.py`,
  `tests/test_pipeline_impact_audit.py`: Expanded the existing pipeline audit with a dedicated
  pre-LLM match-gate section, gate/status consistency check, false-positive suppression review,
  top gate-reason aggregation, and a compact Phase 3 planning summary while excluding startup
  probe rows from main metrics.

### Changed
- `analysis/market_matcher.py`, `analysis/signal_analyzer.py`, `config.py`, `utils/logger.py`,
  `tests/test_market_matcher.py`, `tests/test_signal_analyzer.py`: Implemented the pre-LLM
  match gate end to end. Phase 1 adds diagnostics-only semantic-overlap metadata and startup
  observability fields; Phase 2 enforces suppression of weak-match, no-keyword LLM attempts
  while preserving existing keyword fallback behavior; Phase 3 refines gate-only token filtering,
  adds configurable keyword-override modes (`any_hit`, `min_signal`, `disabled`), and keeps the
  default override behavior backward-compatible.

## [0.29.6] - 2026-04-18

### Added
- `utils/diagnostics_script_helpers.py` (new): Shared date/window/test-record/CLI helper
  module for the read-only diagnostics script family. Consolidates duplicated UTC parsing,
  inclusive end-date handling, common argparse flags, and test-record detection without
  changing operator-facing script behavior.
- `utils/keyword_diagnostics_helpers.py` (new): Shared keyword-diagnostics helper module
  for tokenization, phrase matching, miss-corpus loading, and shadow-evaluation scoring
  used by the keyword audit/reporting scripts.
- `utils/diagnostic_reporting_helpers.py` (new): Shared reporting primitive module for
  percentage/money formatting, top-counter rendering, and standard trade-log header output.
- `tests/test_diagnostics_script_helpers.py`, `tests/test_keyword_diagnostics_helpers.py`,
  `tests/test_diagnostic_reporting_helpers.py` (new): Focused regression tests locking in
  the behavior of the newly shared diagnostics helper layer.

### Fixed
- `main.py`: Eliminated duplicate initial market-cache warmup on startup. Previously
  `_market_refresh_task()` and `_warm_ws_subscriptions()` could both trigger the expensive
  initial cache population path, causing duplicate `/series` scans and extending the
  early `0 active markets` window. Startup now performs one warmup path and logs explicit
  cache-ready timing.
- `main.py`: Added startup observability for market cache initialization, including INFO-level
  warmup start/ready logs with elapsed seconds and loaded market count.
- `feeds/subreddit_selector.py`, `feeds/reddit_monitor.py`: Disabled Reddit sources are now
  filtered before polling rather than fetched and then dropped later at intake. This reduces
  wasted cold-start polling, public API noise, and expected disabled-source drop spam.
- `feeds/search_news_monitor.py`, `main.py`: Search-family polling now respects disabled
  source-family policy before fetch. When Google/Bing query families are disabled, the
  monitors skip that upstream work instead of fetching items that would be discarded later.
- `feeds/gdelt_monitor.py`: GDELT monitor startup now respects the existing disabled-source
  policy and skips polling entirely when `GDELT` is disabled at intake.
- `scripts/trade_log_summary.py`, `scripts/decision_funnel_summary.py`,
  `scripts/freshness_diagnostics.py`, `scripts/match_quality_diagnostics.py`,
  `scripts/match_suppression_audit.py`, `scripts/signal_edge_diagnostics.py`,
  `scripts/pipeline_impact_audit.py`, `scripts/validate_trade_log_cutover.py`,
  `scripts/source_scorecard.py`, `scripts/keyword_feedback.py`,
  `scripts/keyword_shadow_eval.py`, `scripts/keyword_promotion_report.py`:
  Reduced duplicated internal helper logic via shared modules while preserving CLI behavior
  and verified report output.

## [0.29.5] - 2026-04-17

### Fixed
- `scripts/daily_review.py` (`DEFAULT_TRADES_LOG_PATH`): Changed default from `logs/trades`
  (full directory scan including all archive partitions) to `logs/trades/live/trades.jsonl`.
  Daily review is a current-state report; it was scanning 334k+ historical records instead of
  only the active live log. Also added `--path` argument so users can pass `logs/trades` when
  historical analysis across day boundaries is needed.
- `scripts/freshness_diagnostics.py` (`DEFAULT_LOG_PATH`): Same fix -- freshness is a
  current-state metric; default now targets the active live file.
- `scripts/match_quality_diagnostics.py` (`DEFAULT_LOG_PATH`): Same fix.
- `scripts/decision_funnel_summary.py` (`DEFAULT_LOG_PATH`): Same fix.
- `scripts/signal_edge_diagnostics.py` (`DEFAULT_LOG_PATH`): Same fix.
- `scripts/pipeline_impact_audit.py` (`DEFAULT_LOG_PATH`): **Kept as `logs/trades` root.**
  This script compares a current window against the previous equal-length window across day
  boundaries; the previous window requires archive data. Changing to live-only would silently
  break the historical comparison.
- `tests/test_report_default_paths.py` (new): 8 tests locking in the correct default for each
  script and confirming `daily_review` now accepts a `--path` override.

## [0.29.4] - 2026-04-16

### Fixed
- `tests/conftest.py` (new): Added `pytest_configure` / `pytest_unconfigure` hooks that set
  `KALSHI_LOG_ROOT` to a per-session temp directory before test collection begins. This ensures
  module-level singletons in `utils/logger.py` (`_app_fh`, `_err_fh`, `APP_LOG_FILE`, etc.) are
  initialized against the temp dir, never against `logs/app/bot.log`. The temp dir is removed
  automatically on test session exit.
- `tests/test_log_isolation.py` (new): 4 tests proving isolation -- `LOGS_DIR` is not the
  runtime default during test runs; `APP_LOG_FILE` is not under `logs/`; runtime default path is
  unchanged in a subprocess without `KALSHI_LOG_ROOT` set.

## [0.29.3] - 2026-04-16

### Added
- `analysis/signal_analyzer.py`: Granular LLM latency instrumentation -- `_llm_meta()` now
  captures `total_stage_ms`, `queue_wait_ms`, `http_round_trip_ms`, `parse_ms`, `http_status`,
  `contention_observed`, and `in_flight_at_entry` alongside the existing `latency_ms`. Requires
  Ollama 0.4+ HTTP API (existing endpoint, new timing fields only).
- `config.py`: Three new env-var-backed fields on `BotConfig` -- `ollama_request_timeout_seconds`
  (default 60), `ollama_stage_budget_seconds` (default 45), `ollama_probe_timeout_seconds`
  (default 5) -- with validation in `validate()`. Also added `KALSHI_LOG_ROOT` env var for test
  log redirection (see v0.29.4).
- `utils/logger.py` (`log_signal_analysis_detail`): Seven new optional keyword args for the
  granular latency fields; written into the `SIGNAL_ANALYSIS_DETAIL` trade log record when present.
- `scripts/signal_edge_diagnostics.py`: Collects and aggregates the new latency field samples
  (`total_stage_ms_samples`, `queue_wait_ms_samples`, `http_round_trip_ms_samples`,
  `parse_ms_samples`, `contention_observed`, `max_in_flight_at_entry`) from trade log records.
- `scripts/daily_review.py`: Surfaces the new per-stage latency breakdowns in the LLM
  observability section of the daily report.
- `scripts/pipeline_impact_audit.py`: Same latency fields surfaced in audit report.

### Fixed
- `utils/logger.py` (`_rotate_live_to_archive`): Create archive subdirectory before calling
  `os.replace()` -- previously `FileNotFoundError` was raised on the first day-boundary rotation
  because the `archive/YYYY/MM/` path did not yet exist.

## [0.29.2] - 2026-04-16

### Fixed
- `trading/executor.py`: Execution mode is now frozen at `TradeExecutor` construction time
  (`self._is_paper = cfg.is_paper_trading`). Previously, every executor decision re-read the
  mutable global `cfg.is_paper_trading`, meaning any code that ran `cfg.set_paper_mode()` after
  construction (e.g. a test fixture creating a second PaperTrader with `go_live_confirmed=true`)
  could silently switch a running executor from paper to live mid-process. All 14 call sites in
  executor.py now use `self._is_paper` instead of `cfg.is_paper_trading`.
- `trading/executor.py` (`_execute_live`): Added `[LIVE_GUARD]` hard gate at the top of
  `_execute_live`. If called on an executor whose `self._is_paper=True`, it logs an error and
  returns None without placing any order. Belt-and-suspenders against incorrect routing.
- `trading/executor.py` (`_check_live_loss_limit`): Added paper-mode guard -- returns False
  immediately for paper-mode executors without seeding `_session_start_balance`. This eliminates
  spurious `[LOSS_LIMIT] Session start balance recorded` log entries appearing in paper-mode
  processes when tests or CLI commands exercise the live code path.
- `trading/executor.py` (`__init__`): Added `[EXECUTOR_STATE]` diagnostic log at construction
  with `pid`, `mode`, and `bankroll_source` to make execution-mode ambiguity instantly visible
  in logs at startup.
- `tests/test_executor.py`: Added `TestExecutorModeSafety` class with 7 tests covering:
  mode frozen at construction for paper and live executors; `execute()` routing to paper only;
  `[LIVE_GUARD]` blocking `_execute_live` on paper executors; loss-limit not seeded in paper
  mode; `[EXECUTOR_STATE]` log emitted at init.

---

## [0.29.1] - 2026-04-16

### Changed
- `analysis/market_matcher.py`: Tightened low-quality match suppression with a second suppression
  path (Path B) that is score-independent. Path A (original) required `near_threshold_score` AND
  structural weakness. Path B (new) fires on `single_named_entity_only` AND `minimal_overlap` alone,
  without requiring the score to be near-threshold. This catches single-entity matches where the
  geopolitical boost inflates scores above the near-threshold band, which Path A could not reach.
  The ticker guard (match token in ticker symbol) still blocks both paths, preserving topic-aligned
  markets (e.g. KXTRUMP-* remains unblocked for Trump-entity headlines).
- `tests/test_market_matcher.py`: Added 3 targeted tests for Path B suppression behavior:
  `test_path_b_suppresses_pure_single_entity_without_near_threshold` (Path B fires independently),
  `test_path_b_blocked_by_ticker_guard` (ticker guard protects Path B), and
  `test_multi_token_overlap_not_suppressed_by_path_b` (Path B does not fire for multi-token overlap).

---

## [0.29.0] - 2026-04-16

### Added
- `utils/reporting_helpers.py`: Lightweight utilities for long-running reporting scripts. `ProgressTracker`
  counts records and prints a progress line every 10k records to stderr with elapsed time. `stage_timer`
  context manager prints stage start and completion time. Both utilities are opt-in via CLI flags and do
  not affect report output.
- `scripts/daily_review.py`: New CLI flags `--progress` and `--profile` for optional progress reporting.
  `--progress` prints record counts every 10k records during each summarize stage. `--profile` prints
  per-stage elapsed time and a total elapsed time for all 5 stages. Both outputs go to stderr; stdout
  report remains unchanged. Default behavior (no flags) is identical to prior version.
- `scripts/pipeline_impact_audit.py`: Same `--progress` and `--profile` flags. Reports per-stage timing
  and total elapsed for 6 stages (3 per window x 2 windows).

### Changed
- `scripts/freshness_diagnostics.py`, `scripts/signal_edge_diagnostics.py`,
  `scripts/match_quality_diagnostics.py`, `scripts/decision_funnel_summary.py`: Added optional
  `progress_tracker=None` keyword-only parameter to `summarize()` functions. Inner loop calls
  `progress_tracker.tick()` immediately after record is read, before any filtering. Backward
  compatible: when `progress_tracker=None` (default), no progress output is produced.

---

## [0.28.1] - 2026-04-16

### Fixed
- `utils/trade_log_reader.py`: Complete Windows/Python 3.14 pathlib hardening. Replaced all
  pathlib method/property access (`.is_dir()`, `.is_file()`, `.exists()`, `.suffix`, `.rglob()`,
  `/` operator) with equivalent `os.path.*()` functions to prevent internal caching machinery
  hangs on Windows. Functions `_iter_candidate_files()`, `_iter_records_from_file()`, and
  `iter_trade_records()` now use `os.fspath()`, `os.path.join()`, `os.path.isdir()`,
  `os.path.exists()`, and `os.walk()` for all path operations. Archive discovery changed from
  `.rglob()` to `os.walk()` with manual suffix filtering. Resolves `pipeline-audit.py` hang at
  `path.is_dir()` call inside record iteration loop.

---

## [0.28.0] - 2026-04-16

### Added
- `utils/trade_log_reader.py`: Shared reader abstraction for trade logs with support for both
  legacy monolithic `trades.jsonl` and new day-partitioned archive format. `iter_trade_records()`
  yields structured records with date filtering. `TradeLogReadStats` tracks parse statistics.
- `utils/app_log_reader.py`: Structured parser for `bot.log` with field extraction (`ts`, `level`,
  `task`, `message`). `iter_app_log_records()` yields parsed records; `_parse_app_log_line()` 
  handles individual lines.
- `utils/logger.py`: `TradeLogStore` class for day-based archive rotation. Appends new records
  to `logs/trades/live/trades.jsonl`; closes completed UTC days to `logs/trades/archive/YYYY/MM/DD.jsonl`.
  Handles out-of-order records gracefully. Includes `_parse_trade_ts()` helper for timestamp normalization.

### Changed
- `utils/logger.py`: Added `LEGACY_TRADE_LOG_FILE` path (`logs/trades/trades.jsonl`) to support
  backward-compatible access during cutover validation. File layout documentation updated to
  reflect new partition strategy.
- `main.py`: Log retention rules refactored to scan `logs/service/` directory for NSSM archives
  (service_*, ollama_*) with 30-day TTL. Maintains backward compatibility with legacy paths in
  `logs/` root.
- 13 diagnostic scripts migrated to shared reader: `freshness_diagnostics.py`, `signal_edge_diagnostics.py`,
  `match_quality_diagnostics.py`, `trade_log_summary.py`, `source_scorecard.py`, `decision_funnel_summary.py`,
  `keyword_feedback.py`, `keyword_shadow_eval.py`, `match_suppression_audit.py`, `performance_analysis.py`,
  `pipeline_impact_audit.py`. All now use `iter_trade_records()` instead of ad-hoc JSON parsing and file scanning.
- `scripts/ollama_error_audit.py`: Refactored to use `app_log_reader` for structured line parsing.
  Replaced ad-hoc regex with centralized `_parse_app_log_line()`. Simplifies archive handling via
  `iter_app_log_records()` pattern.

### Technical Details
- Trade-log reader supports both single-file and directory paths transparently.
- Day partitioning follows `YYYY/MM/DD.jsonl` convention under `logs/trades/archive/`.
- Out-of-order records (older than live file date) are appended to their partition instead of
  holding up the active log.
- App log reader uses standardized timestamp parsing via `_parse_app_log_line()`.
- All readers yield generators for memory efficiency on large logs.

---

## [0.27.4] - 2026-04-15

### Added
- `analysis/signal_analyzer.py`: per-attempt LLM latency measurement. `_llm_meta()` now
  accepts `latency_ms: int | None`; `_ollama_estimate_detailed()` wraps the HTTP request
  with `time.monotonic()` and captures elapsed time on every return path (success, HTTP
  error, timeout, connector error, parse error). `_anthropic_estimate_detailed()` does the
  same around `client.messages.create()`.
- `utils/logger.py`: `log_signal_analysis_detail()` accepts optional `llm_latency_ms: int | None`
  and writes it to the `SIGNAL_ANALYSIS_DETAIL` JSON record when present. Non-LLM rows and
  rows where the LLM was not reached (circuit open, no provider) do not emit the field.
- `scripts/signal_edge_diagnostics.py`: `summarize()` collects `llm_latency_ms` from
  `SIGNAL_ANALYSIS_DETAIL` rows into `llm_observability["latency_ms_samples"]`. New helpers
  `_p95()` and `fmt_latency_stats()` compute avg / median / p95 / max / n and render them
  as a single line. `print_summary()` surfaces latency stats in the LLM Path Observability
  section.
- `scripts/daily_review.py`: Section 3 (ANALYSIS) now includes `LLM latency` line showing
  `avg / median / p95 / max / n` from the same samples collected by `signal_edge_diagnostics`.
- `tests/test_llm_latency_observability.py`: 19 tests covering `_llm_meta` latency field,
  `log_signal_analysis_detail` conditional write, `summarize()` sample collection, keyword-
  only-with-latency rows, empty-log edge case, `fmt_latency_stats()` arithmetic, and `_p95()`
  correctness.

### Changed
- `tests/test_signal_analyzer.py`: Updated two `assert_called_once_with()` assertions to
  include `llm_latency_ms=None` following the new kwarg addition to `log_signal_analysis_detail`.

---

## [0.27.3] - 2026-04-16

### Added
- `scripts/ollama_error_audit.py`: read-only diagnostic that parses `logs/app/bot.log`
  for Ollama HTTP error lines, classifies them into five buckets (`model_not_found`,
  `timeout_or_connection`, `server_error`, `bad_request`, `other`), and prints a concise
  report with per-bucket examples, most-recent-10 errors, time-gap / burst analysis, and
  a plain-English interpretation of the dominant failure mode. Accepts `--rotated` to scan
  archived log files and `--show-all` to remove per-bucket truncation. No live Ollama
  required; reads only the log file.
- `tests/test_ollama_error_audit.py`: 36 tests covering line parsing (new WARNING format,
  old DEBUG format, timeout lines), all five classify() branches, file I/O edge cases,
  aggregation, report rendering, gap analysis (burst detection), and multi-file collect.

---

## [0.27.2] - 2026-04-15

### Fixed
- `main.py`: `_check_llm_health()` now emits a WARNING at startup when the configured
  `OLLAMA_MODEL` is not present in Ollama's available model list. Previously the mismatch
  was visible in the INFO log (`model=qwen2.5:3b | available=['qwen2.5:7b']`) but silent --
  every subsequent inference attempt returned HTTP 404 without any startup alert.
- `config.py`: Changed `OLLAMA_MODEL` default from `qwen2.5:3b` to `qwen2.5:7b`. The 3b
  model was never pulled on this machine; 7b has been the confirmed working model since the
  initial Ollama setup. This prevents fresh-deploy regressions when the env var is not set.

### Changed
- `scripts/daily_review.py`: Section 3 (ANALYSIS) now includes a concise LLM path health
  block: `LLM attempted`, `LLM result used` with success rate percentage, `LLM fallback to
  keyword`, and a per-status breakdown (e.g. `ollama_http_error: 14`). Data comes from the
  `llm_observability` dict already produced by `signal_edge_diagnostics.summarize()`.

---

## [0.27.1] - 2026-04-15

### Fixed
- `analysis/signal_analyzer.py`: Removed `repetition_penalty` from the Ollama OpenAI-compat
  payload (`/v1/chat/completions`). This field is native to Ollama's `/api/generate` endpoint and
  is not a standard OpenAI Chat Completions parameter. Ollama's compat layer returned HTTP 422 for
  unrecognized fields, producing the `ollama_http_error` status on every inference attempt -- the
  dominant non-success LLM failure mode observed via the new observability fields. Fix: field
  removed; a comment documents where to re-introduce it correctly if rep-penalty is needed later.
- `analysis/signal_analyzer.py`: `ollama_http_error` branch now logs at WARNING level with the
  HTTP status code and response body snippet (up to 200 chars). Previously logged at DEBUG only,
  making transient non-200 responses invisible in production logs.

---

## [0.27.0] - 2026-04-15

### Added
- `analysis/signal_analyzer.py`: `llm_estimate_detailed()` returns `(result, meta)` with structured
  LLM observability metadata (`attempted`, `status`, `provider`, `result_used`). Backward-compat
  `llm_estimate()` wrapper preserved. Internal `_llm_meta()` helper centralises metadata construction.
- `analysis/signal_analyzer.py`: LLM is now attempted BEFORE the keyword gate. Headlines without
  keyword matches can now generate signals if the LLM finds them relevant. When LLM is unavailable,
  behavior falls back to the original keyword gate. This enables LLM-driven signals on headlines
  that keyword scoring alone would have suppressed.
- `utils/logger.py`: `log_signal_analysis_detail()` gains four optional fields (`llm_attempted`,
  `llm_result_used`, `llm_result_status`, `llm_provider`) written to `SIGNAL_ANALYSIS_DETAIL` events
  in `trades.jsonl` for downstream observability.
- `scripts/signal_edge_diagnostics.py`: LLM Path Observability section in the report --
  `llm_attempted`, `llm_result_used`, `llm_fallback`, and per-status counts surfaced from
  `SIGNAL_ANALYSIS_DETAIL` events.
- `analysis/market_matcher.py`: `_structure_quality_flags()` extracted from `_match_quality_flags()`
  so structural flags can be used for pre-threshold penalty scoring.
- `analysis/market_matcher.py`: `_weak_match_penalty_multiplier()` applies a 10-25% score penalty
  to structurally weak matches (single named entity + minimal overlap) before thresholding. Penalty
  is applied before the `min_score` gate, allowing it to suppress genuinely weak matches cleanly.
- `analysis/market_matcher.py`: `_is_excluded_test_market()` filters KXTEST-prefixed markets from
  both geo-series and full-market cache fetches, preventing test markets from entering shared state.
- `scripts/daily_review.py`: Refactored from subprocess runner to direct module import. Report now
  follows the observable pipeline flow (Ingestion -> Matching -> Analysis -> Edge -> Execution)
  instead of a flat list of subscript outputs.
- `scripts/freshness_diagnostics.py`: `--show-all` flag; `bucket_sources()` segments sources into
  Fast/Operational, Near Threshold, Chronically Late, Dead, and Insufficient Data buckets; hidden
  long tail suppressed by default to reduce noise. `format_compact_rows()` replaces the verbose
  `format_rows()` for operational summary sections.
- `scripts/keyword_feedback.py`: Phase 5 keyword feedback audit (new script, surface missed-keyword
  candidate phrases for human review from `ANALYSIS_REJECTED(no_keywords)` corpus).
- `scripts/keyword_promotion_report.py`: Passive governance layer over shadow evaluation --
  produces explicit Promote / Watch / Reject recommendations for handpicked shadow phrases.
- `scripts/pipeline_impact_audit.py`: Before/after audit for recent pipeline-quality changes;
  compares current window against the immediately preceding equal-length window.

### Changed
- `config.py`: Promoted `strait hormuz` and `hormuz blockade` to live `GEOPOLITICAL_SIGNALS`
  (shadow-validated via Phase 6A/6B; 9 and 5 miss-corpus hits respectively, multi-source,
  multi-ticker, no concentration flag). Minor inline comment whitespace fixes throughout.
- `.gitignore`: Added `tests/_tmp_*.jsonl` to exclude stray flat test-fixture files alongside
  the existing `tests/_tmp_*/` directory exclusion.

### Fixed
- `analysis/signal_analyzer.py`: `_ollama_estimate_detailed()` and `_anthropic_estimate_detailed()`
  now return structured meta on every exit path (circuit open, probe fail, HTTP error, timeout,
  parse error, unavailable) rather than bare `None`, so callers always have observability context.

## [0.26.3] - 2026-04-12

### Added
- `scripts/keyword_shadow_eval.py`: Phase 6B -- promotion-readiness scoring and recommendation
  buckets. New `score_phrases()` pure function adds `score`, `bucket`, and `reason` to each phrase
  result after `evaluate_phrases()`. Score formula: `hits*4 + sources*6 + tickers*3 - 10*(conc_flag)
  - overlap_hits*2`. Three conservative buckets: `promote candidate` (score >= 20, hits >= 5,
  sources >= 2, no concentration flag), `continue shadowing`, `reject for now`. Report now groups
  phrases by bucket instead of flat list. New `overlap_hits` field added to each phrase in
  `evaluate_phrases()` output. 11 new tests in `TestScorePhrases` class. No live runtime changes.

## [0.26.2] - 2026-04-13

### Added

- `scripts/keyword_shadow_eval.py` -- Phase 6A: offline shadow evaluation for a
  handpicked set of high-specificity candidate phrases. Reads
  `ANALYSIS_REJECTED(reason=no_keywords)` events from trades.jsonl, checks each
  miss against the shadow phrases using tokenised consecutive-subsequence matching
  (same tokenisation as keyword_feedback.py), and reports per-phrase hit count,
  source/ticker coverage, concentration risk, example headlines, and phrase overlap.
  Default phrases: "strait hormuz", "hormuz blockade", "blockade iran".
  Passive only -- no live config mutation, no runtime behavior change.
- `tests/test_keyword_shadow_eval.py` -- 24 tests covering evaluate_phrases()
  (pure computation: hits, overlap, concentration, dedup, caps) and
  load_miss_corpus() (I/O: filtering, date window, exclude-test, malformed lines).

---

## [0.26.1] - 2026-04-12

### Fixed

- `analysis/market_matcher.py` -- Phase 3.1: add token-in-ticker guard to suppression
  criteria. Suppression no longer fires when any matched token is a substring of the
  market ticker (case-insensitive). Prevents valid topic-aligned weak matches from being
  dropped (e.g. "iran" headline against KXTRUMPIRAN market). Fourth condition added to
  `_meets_suppression_criteria`; debug log and suppression block remain in sync via the
  shared local boolean. No behavior change when `ENABLE_LOW_QUALITY_MATCH_SUPPRESSION`
  is off (default).
- `analysis/market_matcher.py` -- fix pre-existing em dash in two `log.debug()` runtime
  strings (lines 495 and 546) that would silently crash Windows NSSM cp1252 log handlers.

### Tests

- `tests/test_market_matcher.py` -- updated two existing suppression tests to use ticker
  `KXMIL-25A` (matched token not in ticker) so suppression fires correctly under new
  criteria. Added `test_suppression_skips_when_token_in_ticker` validating the Iran case:
  flags injected, suppression on, but token "iran" in ticker -> candidate preserved.
  323 tests passing.

---

## [0.26.0] - 2026-04-12

### Added
- **Phase 1 -- Fast-lane source prioritization:** `SOURCE_PRIORITY_TIERS` dict in `config.py`
  maps named sources to queue priority tiers (1=wire services, 2=RSS default, 3=Reddit).
  `_source_priority()` in `main.py` expanded from 2-tier to 3-tier lookup with
  case-insensitive fallback, mirroring the `EARLY_MAX_NEWS_AGE_BY_SOURCE` pattern. Reuters
  confirmed tier-1; AP left untiered until source string confirmed from live logs. Reddit
  moved from priority 2 to 3 to open room for the tier-1 fast lane. Debug log emitted for
  every tier-1 item enqueued (`[FAST_LANE]`).
- **Phase 2 -- Match quality diagnostics in daily report:** `_match_quality_report_section()`
  added to `trading/paper_trader.py`. Reads `trades.jsonl` at report time and appends a
  compact MATCH QUALITY block showing candidate count, low-quality flagged rate, top flag
  reasons, and top flagged tickers. Infrastructure (`log_match_diagnostic()`, `_match_quality_flags()`,
  `scripts/match_quality_diagnostics.py`) was already in place; this closes the last gap
  by surfacing the data in the daily report.
- **Phase 3 -- Conservative low-quality match suppression:** Config-gated suppression step
  in `analysis/market_matcher.py` (`ENABLE_LOW_QUALITY_MATCH_SUPPRESSION`, default off).
  Suppresses a candidate before LLM invocation only when all three conditions hold:
  `near_threshold_score` AND (`minimal_overlap` OR `single_named_entity_only`). Every
  suppressed candidate emits a `MATCH_SUPPRESSED` event to `trades.jsonl` via new
  `log_match_suppressed()` in `utils/logger.py`. `MATCH_DIAGNOSTIC` is always logged
  regardless of suppression state.
- **Phase 3 validation -- Suppression debug observability:** `ENABLE_MATCH_SUPPRESSION_DEBUG`
  flag (default off) logs `MATCH_SUPPRESSION_CANDIDATE` events for every match meeting
  suppression criteria, whether or not suppression is enabled. Lets operators inspect
  would-be suppressions safely before flipping the suppression flag. New
  `log_match_suppression_candidate()` in `utils/logger.py`. Validated live: first real
  candidate was "Iran War Live Updates: U.S. to Blockade Ships" -> KXTRUMPIRAN -- correctly
  identified as topic-aligned weak match, informing future criteria refinement.
- **4 new suppression tests** in `tests/test_market_matcher.py` covering: flag off leaves
  candidates through, flag on drops criteria-meeting candidates, `MATCH_DIAGNOSTIC` always
  logged, high-quality matches never suppressed.

### Fixed
- **Non-ASCII runtime strings in `trading/paper_trader.py`:** Replaced cent sign (U+00A2)
  with `c` and em dashes (U+2014) with `--` in `generate_report()` output strings. These
  characters silently crashed Windows log handlers (cp1252 encoding) and were dropped from
  NSSM service output.

---

## [0.24.0] - 2026-04-07

### Added
- **Live loss limit circuit breaker (CRITICAL safety):** `executor.py` now tracks the
  session-start Kalshi balance on the first live order attempt. If cumulative session loss
  exceeds `LIVE_LOSS_LIMIT_PERCENT` (default 10%) of bankroll, `_live_halted` is set, the
  shutdown callback fires, and all subsequent `_validate()` calls short-circuit with a
  "HALTED" message without touching the exchange. Added `set_shutdown_callback()` method to
  `TradeExecutor` so `main.py` can wire an async shutdown coroutine at startup.
- **`LIVE_LOSS_LIMIT_PERCENT` config var:** Added to `config.py` (default `0.10`) and
  documented in `.env.example`. Controls the session drawdown threshold.
- **Order retry with exponential backoff:** `_execute_live()` now retries up to 3 times on
  transient failures (429, 5xx, timeout, connection reset, network errors) with 1s and 2s
  delays. Permanent errors (400, 403) abort immediately after one attempt. Prevents missed
  trades from single network hiccups.
- **Automated test suite -- 74 tests, 0 failures:** New `tests/` directory with `pytest.ini`
  (`asyncio_mode = auto`). Five test modules covering the critical path:
  - `test_kelly.py` (31 tests): time discount, Kelly sizing, min-edge gate, cap enforcement,
    source multiplier, contracts-from-dollars conversion.
  - `test_paper_trader.py` (10 tests): bankroll atomicity regression, P&L accounting (win YES,
    loss YES, win NO), keyword_outcomes written on resolution, Kelly shadow column.
  - `test_signal_analyzer.py` (16 tests): `_extract_json` edge cases including brace-in-preamble
    and last-valid-object behavior; `_parse_llm_response` probability mapping and clamping;
    `_keyword_score` with and without KeywordStats multiplier.
  - `test_market_matcher.py` (11 tests): tokenization, stopword removal, Jaccard similarity
    with geopolitical boost.
  - `test_executor.py` (13 tests): loss limit seeding/breach/persistence/callback, retry
    transient/permanent discrimination, 3-attempt exhaustion.

### Changed
- `main.py`: Wires shutdown callback to executor after construction so loss limit breach
  triggers a clean async shutdown via `asyncio.create_task(_shutdown(self))`.

---

## [0.23.0] - 2026-04-07

### Fixed
- **Bankroll desync bug (CRITICAL):** `_credit_bankroll(total_payout)` was called outside
  the `with self._conn:` atomic block in `resolve_market()`. A process crash between the
  trade resolution commit and the bankroll credit would permanently understate the notional
  bankroll. Moved inside the block so both writes are committed atomically (`paper_trader.py`).

### Added
- **Defensive market object copy:** Three sites in `main.py` that mutated `KalshiMarket`
  objects pulled from the shared cache now call `dataclasses.replace(market)` before any
  field write. Eliminates a latent race condition where a concurrent WebSocket price update
  could see a partially-mutated object.
- **Kelly shadow sizing:** `paper_trades` gains a `kelly_contracts` column (INTEGER) via
  migration. Every paper trade now also computes and stores what Kelly sizing would have
  recommended (without applying it -- flat 5 still used for paper). Enables retrospective
  comparison of flat-5 vs Kelly P&L trajectories before going live (`paper_trader.py`).
- **Section 7e -- Kelly Shadow Sizing** in `scripts/performance_analysis.py`: shows flat-5
  net P&L, capital deployed, and ROI vs what Kelly shadow would have returned on the same
  resolved trades, with a Delta column.

### Changed
- Paper trade log line now includes `kelly_shadow=N` annotation when in paper mode, showing
  what Kelly would have sized for that trade.

---

## [0.22.0] - 2026-04-07

### Added
- `analysis/keyword_stats.py`: new `KeywordStats` class reads per-(keyword, series_ticker)
  accuracy from the `keyword_outcomes` table (written since v0.19.0 but never consumed).
  Returns a multiplier in [0.5, 1.5] applied to each keyword's base strength in
  `_keyword_score()`. Loaded at startup, refreshed every 6h, thread-safe. Closes the
  keyword feedback loop that was the largest "Phase 3" gap found in the v0.22.0 audit.
- Seven new columns in `paper_trades` schema (added via ALTER TABLE migration, zero data
  loss): `series_ticker`, `resolved_ts`, `signal_type`, `match_score`, `llm_direction`,
  `llm_magnitude`, `llm_confidence`. Historical `series_ticker` backfilled from
  `json_extract(market_snapshot, '$.series_ticker')`.
- `SignalAnalysis` dataclass extended with `match_score`, `signal_type`, `llm_direction`,
  `llm_magnitude`, `llm_confidence` fields (`analysis/__init__.py`).
- `signal_type` tagging: news pipeline signals tagged `"news"`, price-fade signals tagged
  `"price_fade"`, fade-tweet signals tagged `"fade_tweet"` (`main.py`).
- `match_score` now flows from `find_candidates()` through `_process_candidate()` into
  `SignalAnalysis` and is persisted to `paper_trades` (`main.py`, `paper_trader.py`).
- Raw LLM fields (`direction`, `magnitude`, `confidence`) now returned from
  `_parse_llm_response()` / `estimate_probability()` and stored in each trade record,
  enabling future LLM calibration analysis (`signal_analyzer.py`, `paper_trader.py`).
- Three new sections in `scripts/performance_analysis.py`:
  - **7b. Per-series win rate**: win rate and P&L grouped by `series_ticker`.
  - **7c. Keyword accuracy**: per-(keyword, series_ticker) accuracy table with applied
    multiplier; aggregate per-keyword table for overview.
  - **7d. Match score calibration**: win rate by match_score band with advisory if
    empirical data suggests a better `PAPER_MIN_MATCH_SCORE` threshold.

### Changed
- `_keyword_score()` now accepts optional `keyword_stats` and `series_ticker` args;
  applies per-(keyword, series_ticker) accuracy multiplier when sufficient data exists
  (>= 10 samples). Falls back to 1.0 (neutral) below threshold (`signal_analyzer.py`).
- `estimate_probability()` signature extended to accept `keyword_stats` and returns a
  7-tuple including raw LLM fields. All callers updated (`main.py`).
- `resolve_market()` now populates `resolved_ts` on every resolution (`paper_trader.py`).
- Two pre-existing em dashes in `paper_trader.py` log strings replaced with `--` to
  comply with Windows cp1252 logging safety rules.

## [0.21.0] - 2026-04-07

### Added
- **Subreddit discovery loop** (`feeds/subreddit_discovery.py` NEW,
  `feeds/subreddit_selector.py`, `main.py`, `config.py`,
  `trading/paper_trader.py`, `scripts/performance_analysis.py`) -- closes the
  second half of the source quality feedback loop. v0.20.0 pruned zero-signal
  subreddits; v0.21.0 finds new ones. Reddit's public post search API is queried
  every 6h for active market topics; subreddits generating that discussion are
  inserted as candidates and probed automatically. source_stats evaluates quality
  the same way as any other source -- good ones stay, bad ones are suppressed.

- **New module `feeds/subreddit_discovery.py`** -- `run_discovery_pass()` queries
  Reddit post search for up to SUBREDDIT_DISCOVERY_MAX_QUERIES active market topic
  queries, extracts subreddit names, filters against known subs and a generic
  content blocklist, and inserts new candidates into `subreddit_candidates` DB table.
  Rate-limited to 1 request per 2 seconds (well within Reddit's public API limits).

- **New table `subreddit_candidates`** in `paper_trades.db` (`trading/paper_trader.py`)
  -- tracks discovered subreddits with probe_count, last_probed, discovered_via, and
  status (candidate/suppressed). Added via `CREATE TABLE IF NOT EXISTS` -- safe on
  existing DBs.

- **Tier 3 subreddit selection** (`feeds/subreddit_selector.py`) -- `select_subreddits()`
  now accepts `db_path` and fills remaining capacity slots with candidate probes.
  Candidates are selected by probe_count ASC (never-probed first), then oldest.
  Per-candidate 3h cooldown prevents re-probing already-evaluated subs. Suppressed
  candidates are marked in DB and permanently excluded.

- **Discovery scheduled task** (`main.py` `_subreddit_discovery_task()`) -- runs
  every SUBREDDIT_DISCOVERY_INTERVAL_SECS (default 6h) with a 5-minute startup
  delay to let the market cache warm. Candidate count logged at INFO level.

- **4 new env-configurable constants** (`config.py`):
  SUBREDDIT_DISCOVERY_INTERVAL_SECS (default 21600),
  SUBREDDIT_PROBE_COOLDOWN_SECS (default 10800),
  SUBREDDIT_PROBE_SLOTS (default 3),
  SUBREDDIT_DISCOVERY_MAX_QUERIES (default 10).

- **Candidate subreddits section** (`scripts/performance_analysis.py` section 6b)
  -- shows discovered subs with probe count, last probed timestamp, and
  status (candidate/suppressed).

- **Plan archiving convention** (`docs/plans/` NEW) -- non-trivial implementation
  plans archived as Architectural Decision Records. Added rule to both project
  `CLAUDE.md` and global `~/.claude/CLAUDE.md`. First two ADRs archived:
  `docs/_archive/plans/v0.20.0_source_quality_feedback_loop.md` and
  `docs/_archive/plans/v0.21.0_subreddit_discovery_loop.md` (relocated 2026-04-26 — see `docs/_archive/README.md`).

## [0.20.0] - 2026-04-06

### Added
- **Source quality feedback loop** (`analysis/source_stats.py` NEW, `main.py`,
  `feeds/subreddit_selector.py`, `config.py`, `trading/paper_trader.py`,
  `scripts/performance_analysis.py`) -- closes the "garbage subreddit" problem.
  The pipeline previously had no visibility into what happened before a SIGNAL event,
  so zero-signal subreddits consumed poll slots indefinitely. Now every post,
  signal, opportunity, and trade is counted per source.

- **New module `analysis/source_stats.py`** -- SourceStats class tracking the full
  quality funnel (posts_seen -> signals -> opportunities -> trades) per source.
  Uses signal_rate (signals / posts_seen) as the primary quality metric, available
  within 24-48 hours vs the existing win_rate credibility system which needs 10+
  resolved trades (weeks). Writes are batched in-memory and flushed every 30 minutes
  (piggybacked on the auto-resolve task) to avoid SQLite write contention.

- **Subreddit suppression** (`feeds/subreddit_selector.py`) -- zero-signal subreddits
  (>= SOURCE_STATS_ZERO_SIGNAL_POSTS posts, 0 signals) are skipped from topic-driven
  selection. Topic subreddits are sorted by signal rate so high-quality sources fill
  the REDDIT_MAX_SUBREDDITS cap first. Core subreddits (worldnews, geopolitics, etc.)
  are always exempt from suppression and are never quality-gated.

- **New DB table `source_stats`** in `data/paper_trades.db` -- schema added via
  `CREATE TABLE IF NOT EXISTS` in `trading/paper_trader.py` DDL (safe migration on
  existing DB). Columns: source, posts_seen, signals, opportunities, trades,
  last_signal, last_updated.

- **Source quality funnel section** in `scripts/performance_analysis.py` -- new
  section 6 ("SOURCE QUALITY FUNNEL") shows per-source signal rate, opportunity rate,
  and quality label (Good / Low / SUPPRESSED / ?) using the source_stats table.

- **3 new env-configurable constants** (`config.py`):
  SOURCE_STATS_MIN_POSTS (default 100), SOURCE_STATS_LOW_SIGNAL_RATE (default 0.005),
  SOURCE_STATS_ZERO_SIGNAL_POSTS (default 200).

### Changed
- `_process_candidate()` in `main.py` now captures the return value of
  `executor.execute()` so trade placements can be counted per source.

## [0.19.0] - 2026-04-06

### Added
- **Market feedback loop — Phase 1 and 2** (`main.py`, `trading/paper_trader.py`,
  `utils/logger.py`, `config.py`, `docs/market_feedback_loop_roadmap.md`) --
  closes the one-way news->trade pipeline into four feedback loops. See
  `docs/market_feedback_loop_roadmap.md` for architecture and Mac Studio upgrade path.

- **Loop A: Price-velocity-driven targeted news search** (`main.py`) -- when a geo
  market price moves >= PRICE_MOVE_THRESHOLD_CENTS (10c, env-configurable) within a
  5-minute rolling window, immediately fetch Google News RSS + GDELT for that
  specific market's tokens. Inverts discovery: volatile markets proactively hunt for
  news rather than waiting for RSS. Rate-limited to PRICE_SEARCH_COOLDOWN_SECS (30min)
  per ticker. Uses existing `_markets_to_queries()` and `poll_feed()` -- no new deps.

- **Loop B: Keyword outcome tracking** (`trading/paper_trader.py`) -- new
  `keyword_outcomes` table in `paper_trades.db`. On every trade resolution, one row
  per keyword that fired on that trade is written with: keyword, its declared direction,
  the side we bet, resolved_yes, and a `correct` flag (1 if keyword pointed the right
  way). Enables per-series keyword accuracy queries: e.g. "invasion" on KXTRUMP* series
  has 10% correct rate -- the foundation for Phase 3 dynamic signal weighting.
  Schema added via `CREATE TABLE IF NOT EXISTS` (safe migration on existing DB).

- **Loop C: Open position price drift logging** (`main.py`, `utils/logger.py`) --
  when a WS price update arrives for a ticker with an open paper position, emit a
  `POSITION_DRIFT` event to `trades.jsonl` if price has moved >= DRIFT_ALERT_CENTS
  (15c, env-configurable) from entry. Rate-limited to DRIFT_LOG_COOLDOWN_SECS (1h)
  per ticker. On Mac Studio this will trigger LLM re-analysis; now it builds the
  data trail for position health monitoring.

- **Loop D: New market detection** (`main.py`, `utils/logger.py`) -- at each 30-min
  market cache refresh, compare the new ticker set against the previous. Any ticker
  not seen before emits a `NEW_MARKET` event to `trades.jsonl` and immediately
  triggers a Loop A targeted news search. New Kalshi listings are discovered within
  30 minutes of appearing rather than waiting for RSS to bring a matching story.

- **5 new env-configurable constants** (`config.py`): `DRIFT_ALERT_CENTS`,
  `DRIFT_LOG_COOLDOWN_SECS`, `PRICE_MOVE_THRESHOLD_CENTS`, `PRICE_SEARCH_COOLDOWN_SECS`,
  `PRICE_VELOCITY_WINDOW_SECS`.

- **Architecture roadmap** (`docs/market_feedback_loop_roadmap.md`) -- committed to
  repo so it travels to Mac Studio on `git pull`. Documents all four loops,
  Phase 3 dynamic weighting implementation sketch, and multi-agent vision.

---

## [0.18.0] - 2026-04-06

### Changed
- **Paper ticker cooldown 4h -> 2h** (`.env` `PAPER_TICKER_COOLDOWN=7200`) -- historical
  analysis showed 34 of 178 skips were cooldown blocks. The 4h window was blocking
  legitimate follow-on signals (ceasefire news after airstrike news, ~2-6h later).
  2h still prevents intra-burst spam (bot polls every 60s, so 2h = 120 suppression cycles)
  but allows real signal diversity. No code change; `cfg.paper_ticker_cooldown` reads from env.
- **Staleness guard 5min -> 10min** (`.env` `MAX_NEWS_AGE_SECONDS=600`) -- with
  max_candidates=3 and 20-40s/inference, articles can be 3-5 min in queue before LLM
  evaluation. The old 300s window was discarding valid articles during busy inference periods.
  10 min is still within alpha window for 24h Kalshi markets.
- **Same-side duplicate guard: flat block -> prob+price delta** (`trading/executor.py:134-138`) --
  the paper phase was blocking ALL same-side re-entry on a ticker regardless of how much
  the probability estimate shifted. 11 historical skips used this path. Now allows
  re-entry when estimated_prob has shifted >=0.07 OR market price has moved >=5c since
  the open position. This lets the LLM accumulate on genuinely updated signals while
  still blocking informationally-identical ones.

### Added
- **Senate confirmation + Trump action keyword groups** (`config.py` `GEOPOLITICAL_SIGNALS`) --
  active Kalshi markets include KXSENATECONFIRM (Bondi, Gabbard, Hegseth), KXBONDITESTIFY,
  and various Trump executive action markets. The keyword gate had no terms for confirmation
  hearings ("confirmation hearing", "senate confirms", "nominee confirmed", "testifies before")
  or Trump-specific actions ("trump fires", "trump pardons", "fired by trump"). These markets
  were generating zero SIGNAL events despite active news flow. Two new keyword groups
  cover these categories at strength 0.12 and 0.10 respectively.

---

## [0.17.1] - 2026-04-06

### Changed
- **`PAPER_MAX_CANDIDATES` 1 -> 3** (`config.py`) -- the bot was evaluating only the
  single top Jaccard match per article. With 822+ cached markets, the top match is
  frequently the wrong market (e.g. "Trump fires Bondi" -> China visit at score 0.033).
  The LLM correctly returned neutral on these mismatches, suppressing real signals.
  Raising to 3 lets the LLM evaluate the top 3 conceptual candidates and find genuine
  relevance. CPU constraint: at 20-40s/call this is ~120s max per article. On Mac Studio
  M4 Max (<5s inference) raise to 8-10.
- **`PAPER_MIN_MATCH_SCORE` 0.03 -> 0.06** (`config.py`) -- empirical analysis of match
  score distribution showed scores below 0.06 are almost always wrong-market noise.
  Real relevance starts at ~0.06. The old floor of 0.03 was passing garbage matches
  to the LLM unnecessarily. This pairs with the candidates increase: fewer but better
  candidates per article.

---

## [0.17.0] - 2026-04-06

### Added
- **Performance analysis script** (`scripts/performance_analysis.py`) -- repeatable
  end-to-end analysis covering the full signal pipeline. Reads `logs/trades.jsonl`
  and `data/paper_trades.db` to produce a dated report in `logs/analysis_YYYYMMDD_HHmm.txt`.
  Sections: signal pipeline funnel (signal->opportunity->trade conversion rates), placed
  trades performance (win rate, P&L, ROI, edge calibration), skip reason breakdown,
  missed opportunities with counterfactual P&L for those with known resolutions,
  per-source performance, edge calibration vs actual outcomes, go-live readiness.
  Optional `--enrich` flag fetches Kalshi API resolutions for skipped tickers and caches
  them to `logs/market_resolution_cache.json`. Date window via `--since`/`--until`
  (default: last 30 days). Designed for daily/weekly/monthly cadence.

---

## [0.16.0] - 2026-04-03

### Added
- **Source credibility time decay** (`analysis/source_credibility.py`) -- added
  `_time_decayed_accuracy()` method. Rather than computing accuracy as a flat win/loss
  ratio, accuracy is now a time-weighted average: each resolved outcome is weighted by
  `exp(-ln2 / half_life * age_days)` where half_life defaults to 30 days. An outcome
  from 60 days ago counts 25% as much as one from today. Prevents stale performance
  data from permanently locking a source into a high or low multiplier. The half-life
  is configurable via `CREDIBILITY_HALF_LIFE_DAYS` in `config.py`.
- **Config-driven go-live gates** (`config.py`, `main.py`) -- added three new `BotConfig`
  fields controlling readiness checks before live trading confirmation:
  `go_live_min_resolved` (default 20), `go_live_min_win_rate` (default 0.52),
  `go_live_max_drawdown_pct` (default 0.20). All three are overridable via env vars.
  `_check_go_live_gates()` in `main.py` evaluates them before the CONFIRM prompt and
  prints each gate's FAIL/pass status. Gate values are logged at startup.
- **Config-driven ticker cooldowns** (`config.py`, `trading/executor.py`) -- moved
  `_LIVE_TICKER_COOLDOWN = 600` and `_PAPER_TICKER_COOLDOWN = 14400` from hardcoded
  module constants in `executor.py` to `BotConfig` fields (`live_ticker_cooldown`,
  `paper_ticker_cooldown`) overridable via `LIVE_TICKER_COOLDOWN` and
  `PAPER_TICKER_COOLDOWN` env vars. Operator can tune cooldowns without code changes.

---

## [0.14.0] - 2026-04-03

### Added
- **Startup validation gate** (`config.py`) -- `BotConfig.__post_init__()` validates all
  critical env vars at import time. Checks: `KALSHI_API_KEY_ID` non-empty,
  `KALSHI_API_KEY_SECRET` is a loadable RSA PEM key, `KALSHI_ENV` is 'demo' or 'prod',
  `BANKROLL` is positive, `KELLY_FRACTION` is in (0, 1]. On failure, prints clear error
  messages to stderr and exits immediately -- no more silent failures hours later on first
  trade attempt.
- **Feedparser timeout guard** (`feeds/rss_monitor.py`) -- wrapped `feedparser.parse()` in
  `asyncio.wait_for()` with a 30-second timeout. A hanging feed server can no longer stall
  the entire RSS poll cycle indefinitely; the feed is skipped with a warning and the cycle
  continues.

### Fixed
- **REST client error log sanitization** (`kalshi/rest_client.py`) -- HTTP error response
  bodies are now checked for sensitive patterns (auth keys, signatures) before logging.
  If detected, the body is redacted. Defense in depth for live trading mode.
- **Non-ASCII in log strings** -- replaced em dash in `rss_monitor.py` log message and
  right arrow in `rest_client.py` log message with ASCII equivalents. Prevents Windows
  cp1252 logging failures under NSSM.

---

## [0.13.0] - 2026-04-03

### Changed
- **AIMD query-limit controller for GDELT** (`feeds/gdelt_monitor.py`) -- replaced the
  static `GDELT_MAX_QUERIES=15` constant with an adaptive `_gdelt_query_limit` variable
  (starts at 5, range 1-25). Signal: HTTP response codes from GDELT itself. On any 429
  in a cycle: halve the limit (multiplicative decrease). On a clean cycle with at least
  one successful response: increment by 1 (additive increase). The limit self-calibrates
  to GDELT's actual tolerance ceiling without guessing. Timeouts are treated as network
  noise and do not adjust the limit. Removed the now-redundant `GDELT_MAX_QUERIES`
  constant.
- **AIMD articles-per-query controller for search** (`feeds/search_news_monitor.py`) --
  replaced the static `SEARCH_MAX_ARTICLES_PER_QUERY=5` constant with an adaptive
  `_search_articles_cap` variable (starts at 3, range 1-15). Signal: news queue fill
  ratio passed in via a new `queue_depth_fn` parameter. Queue >60% full: decrement cap
  (back off ingestion -- consumer is falling behind). Queue <20% full: increment cap
  (consumer has headroom -- feed it more). Renamed `SEARCH_MAX_ARTICLES_PER_QUERY` to
  `_search_articles_cap`; the old constant is gone. Cap snapshot is taken per-query to
  avoid mid-gather inconsistency.
- **`run_search_news_monitor` signature** (`feeds/search_news_monitor.py`) -- added
  optional `queue_depth_fn: Callable[[], float] | None = None` parameter. When None
  (default), the cap holds steady at its current AIMD value. Main wires it as
  `lambda: queue.qsize() / queue.maxsize`.
- **`main.py` task wiring** -- passes `queue_depth_fn` lambda to `run_search_news_monitor`
  so the search AIMD controller receives live queue fill ratio each cycle.

---

## [0.12.0] - 2026-04-03

### Fixed
- **Sports/blocklisted market filter in query generator** (`feeds/search_news_monitor.py`) --
  `_markets_to_queries()` now skips any market whose series_ticker or ticker prefix matches
  `MARKET_SERIES_BLOCKLIST_PREFIXES`. Previously, sports and crypto markets that slipped through
  the geo cache would generate irrelevant queries like "tiger woods dui" or "royal challengers
  bengaluru", flooding the queue with celebrity golf and IPL cricket articles.
- **Per-query article cap** (`feeds/search_news_monitor.py`) -- added `SEARCH_MAX_ARTICLES_PER_QUERY = 5`
  constant and switched the gather pattern from all-at-once to per-query with a capped callback.
  A single hot query can no longer dump 20+ articles in one burst; max is now 5 new articles
  across both engines per query per cycle (25 queries x 5 = 125 max/cycle, down from ~500).
  This is the primary fix for the 8,700+ queue overflow drops observed overnight.
- **News queue maxsize** (`main.py`) -- increased from 500 to 2000 to absorb legitimate
  high-volume news events (e.g. breaking geopolitical stories across many sources) without dropping.
- **GDELT 429 rate-limit backoff** (`feeds/gdelt_monitor.py`) -- added module-level
  `_gdelt_backoff_until` / `_gdelt_backoff_secs` state mirroring the Reddit backoff pattern.
  On HTTP 429: enters backoff, skips the remainder of the current cycle, doubles the delay
  (60s initial, max 900s). On next successful response: resets to 60s. Previously GDELT
  returned 429 with no recovery, retrying immediately on the next cycle.

---

## [0.11.1] - 2026-04-02

### Changed
- **Search/GDELT query prioritization** (`feeds/search_news_monitor.py`) -- `_markets_to_queries()`
  now ranks markets by `open_interest * (1 - |price - 50| / 50)` instead of open_interest alone.
  This weights query slots toward contested markets (price near 50c) with meaningful volume,
  where fresh news is most likely to create exploitable edge. Fully-decided markets (price
  near 0 or 100) score near zero regardless of volume. Applies to both the search monitor
  (Google + Bing) and GDELT, which both import `_markets_to_queries` from this module.

---

## [0.11.0] - 2026-04-02

### Added
- **Multi-engine search monitor** (`feeds/search_news_monitor.py`) -- replaces
  `google_news_monitor.py`. Now fetches each query from both Google News RSS and Bing
  News RSS in a single `asyncio.gather` call (50 fetches/cycle at 25 queries x 2 engines).
  Shared dedup cache suppresses cross-engine duplicates. No new dependencies.
- **GDELT monitor** (`feeds/gdelt_monitor.py`) -- new async task querying the GDELT
  Document 2.0 geopolitical event database. Free, no API key, updates every 15 minutes.
  Generates up to 15 queries/cycle from active market titles (reusing `_markets_to_queries`
  from search_news_monitor), fetches sequentially with a 2s stagger, emits NewsItem
  via the same callback chain. Poll interval 900s to match GDELT's indexing cadence.
- **`_make_market_getter()`** on `TradingBot` (`main.py`) -- replaces `_make_gnews_getter()`;
  shared by both search and GDELT monitors.

### Changed
- **`TradingBot.run()`** (`main.py`) -- replaced `gnews` task with two concurrent tasks:
  `search` (Google + Bing RSS, 300s) and `gdelt` (GDELT JSON API, 900s). Total async
  tasks now 10 (11 when fade_tweets is configured).
- `feeds/google_news_monitor.py` superseded by `feeds/search_news_monitor.py` (file kept
  for git history; no longer imported).

---

## [0.10.0] - 2026-04-02

### Added
- **Google News RSS monitor** (`feeds/google_news_monitor.py`) -- new async task that
  generates targeted Google News RSS queries from active Kalshi market titles and fetches
  them in parallel every 300s. No API key required. Indexes within minutes of publication.
  Markets are sorted by open_interest so the most-traded topics get query priority. Up to
  25 distinct queries per cycle (cap configurable via `GNEWS_MAX_QUERIES`). Reuses
  `poll_feed()` from `rss_monitor.py` for feedparser, dedup, and NewsItem creation.
  Feeds the same news consumer callback as RSS and Reddit -- all three sources run
  concurrently as independent async tasks.
- **`_make_gnews_getter()`** method on `TradingBot` (`main.py`) -- sync callable that
  reads `self.matcher._cache._markets` and passes it to the Google News monitor each cycle.

### Changed
- **`TradingBot.run()`** (`main.py`) -- added `gnews` as a 9th concurrent async task
  alongside rss, reddit, news_consumer, websocket, ws_warm, daily_report, market_refresh,
  and auto_resolve.

---

## [0.9.1] - 2026-04-02

### Fixed
- **Reddit stagger sleep wasted on backed-off subreddits** (`feeds/reddit_monitor.py`) --
  the poll loop now checks `_backoff` before calling `_poll_subreddit` and skips the
  entire subreddit (including the `asyncio.sleep(stagger)`) when the sub is in backoff.
  Previously the 10s stagger ran even for subs that `_fetch_subreddit()` immediately
  short-circuited. Saves 10s per backed-off subreddit per cycle in public mode.

---

## [0.9.0] - 2026-04-02

### Added
- **Adaptive subreddit selection** (`feeds/subreddit_selector.py`, `config.py`) -- Reddit
  polling is now vocabulary-driven instead of always querying all 35 hardcoded subreddits.
  Each poll cycle selects up to 20 subreddits: 5 core (worldnews, geopolitics,
  InternationalNews, CredibleDefense, ArmedConflicts) are always included; topic-specific
  subreddits (12 topics: military, elections, trade, nuclear, sanctions, US domestic,
  and 6 regional buckets) are added only when open Kalshi market titles match their
  keyword sets. This reduces steady-state request volume and targets subreddits to the
  markets actually being traded.
- **`REDDIT_CORE_SUBREDDITS`**, **`REDDIT_SUBREDDIT_TOPIC_MAP`**,
  **`REDDIT_TOPIC_KEYWORDS`**, **`REDDIT_MAX_SUBREDDITS`** constants in `config.py` --
  fully configurable; topic map and keyword sets can be expanded without touching logic.

### Changed
- **`run_reddit_monitor()`** (`feeds/reddit_monitor.py`) -- `subreddits` parameter now
  accepts a static `list[str]`, an async callable, or `None` (falls back to full
  `REDDIT_SUBREDDITS` list). The subreddit list is re-evaluated at the top of each
  poll cycle when an async callable is passed.
- **`TradingBot.run()`** (`main.py`) -- wires the Reddit monitor with
  `_make_subreddit_getter()`, an async callable that reads the live market cache
  (`self.matcher._cache._markets`) and calls `select_subreddits()` each cycle.

---

## [0.8.2] - 2026-04-02

### Fixed
- **Startup crash after v0.8.1** (`main.py:532`) -- startup log referenced
  `cfg.bet_pct_bankroll` which was renamed to `cfg.max_bet_pct_bankroll` in v0.8.1.
  Caused `AttributeError` on every startup, putting the NSSM service into a crash loop.
  Fixed by updating the one reference in the startup log message.

---

## [0.8.1] - 2026-04-02

### Changed
- **Confidence-scaled bet sizing** (`analysis/kelly.py`) -- LLM confidence is now a
  first-class multiplier on the Kelly fraction. Previously confidence only affected the
  probability estimate (via magnitude scaling); now it directly scales the bet:
  `f_adjusted = f_full * kelly_fraction * confidence * source_multiplier * time_discount`.
  A 0.95-confidence signal bets 95% of what Kelly says; a 0.50-confidence signal bets 50%.
  Keyword-only fallback confidence is already capped at 0.70, so fallback trades are
  automatically reduced relative to LLM-backed trades.
- **Time discount for long-duration bets** (`analysis/kelly.py`) -- new `_time_discount()`
  function applies exponential decay to bets that lock up capital far into the future.
  Markets closing within 3 days get full sizing (1.0x). Beyond that, discount decays with
  a 14-day half-life toward a 0.20 floor (60+ day markets get at most 20% of base sizing).
  Half-life and floor are configurable via `TIME_DISCOUNT_HALF_LIFE` and `TIME_DISCOUNT_FLOOR`
  env vars.
- **Bankroll-proportional bet ceiling** (`config.py`) -- replaced the fixed `$25` hard cap
  with `MAX_BET_PCT_BANKROLL * bankroll` (default 15% = $75 on $500, grows with bankroll).
  The old hard cap is retained as a safety backstop raised to $200 (`MAX_BET_HARD_CAP`).
  `dynamic_max_bet()` now uses `max_bet_pct_bankroll` as the operating ceiling.
- **`kelly_bet()` call site updated** (`main.py`) -- now passes `confidence` and
  `days_to_close` (extracted from `market.close_time` via `_days_to_close()`) into
  `kelly_bet()`. Defaults to 14 days when close time is unavailable.

## [0.8.0] - 2026-04-02

### Added
- **Auto-resolution task** (`main.py`) -- new 6th concurrent async task
  `_auto_resolve_task()` polls Kalshi every 30 minutes for settled markets.
  `_check_and_resolve()` queries all open paper trade tickers, calls
  `rest_client.get_market()` for each, and auto-calls `paper_trader.resolve_market()`
  when status is `finalized`/`settled` with a `yes`/`no` result. Eliminates the need
  for manual `--resolve` commands and unblocks the daily performance report.
- **`result` field on `KalshiMarket`** (`kalshi/__init__.py`, `kalshi/rest_client.py`) --
  both `get_market()` and `get_markets()` now parse the `result` field from the API
  response (`"yes"`, `"no"`, or `""` if not yet settled). Required by the auto-resolver.

### Changed
- **Expanded signal vocabulary** (`config.py`, `analysis/market_matcher.py`) -- added
  trade/economic policy, foreign policy, and domestic policy terms across all four
  vocabulary layers:
  - `GEOPOLITICAL_SIGNALS` (+6 new keyword categories for tariffs, trade deals, diplomatic
    events, and domestic US policy triggers)
  - `_GEOPOLITICAL_BOOST` (+30 terms: tariff, trade, import, export, embargo, diplomatic,
    executive order, shutdown, impeachment, cabinet, confirmation, etc.)
  - `_GEO_NAMED_ENTITIES` (+20 terms: additional country demonyms, current officials
    Vance/Rubio/Waltz/Macron/Scholz/Starmer, institutions NATO/Pentagon/Kremlin/Congress/Senate)
  - `_GEO_SERIES_KEYWORDS` (+35 terms for series discovery: tariffs, trade war, trade deal,
    liberation day, embargo, customs, debt ceiling, executive order, recession, treasury,
    deportation, border, and more country names)
  Goal: capture Trump tariff/trade policy markets and broader domestic/foreign policy
  markets that were previously invisible to the signal pipeline.

---

## [0.7.1] - 2026-03-30

### Fixed
- **errors.log rotation stall** (`utils/logger.py`) -- `errors.log` (WARNING+ only) failed to
  rotate at midnight when no warnings were emitted around that time. `TimedRotatingFileHandler`
  only checks `shouldRollover()` on `emit()`, so the errors handler was never triggered during
  quiet periods. Fixed by adding a peer-nudge mechanism: when `bot.log` (DEBUG+, always active)
  rotates at midnight, it now also triggers `shouldRollover()` on the errors handler, ensuring
  both files rotate in lockstep regardless of message volume.

---

## [0.7.0] - 2026-03-29

### Added
- **Reddit OAuth2 support** (`feeds/reddit_monitor.py`, `config.py`) -- when `REDDIT_CLIENT_ID`
  and `REDDIT_CLIENT_SECRET` are set in `.env`, the Reddit monitor authenticates via OAuth2
  `client_credentials` grant and uses `oauth.reddit.com` (600 req/10 min). Without credentials,
  falls back to the public JSON API (current behavior, aggressively rate-limited).
- **`_RedditAuth` token manager** (`feeds/reddit_monitor.py`) -- handles token acquisition,
  caching, auto-refresh (60s before expiry), HTTP 401 retry, and permanent credential failure
  detection. Zero new dependencies (uses existing `aiohttp`).
- **OAuth-aware circuit breaker** (`feeds/reddit_monitor.py`) -- under OAuth, HTTP 403 means
  private/quarantined subreddit (not IP block) and does not count toward the global circuit
  breaker. Only 429s trigger the global pause. OAuth mode uses a shorter 10-min global backoff
  (vs 30-min for public mode) and 3s stagger between subreddits (vs 10s).
- **Reddit OAuth env vars** (`.env.example`) -- documented `REDDIT_CLIENT_ID`,
  `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` with setup instructions.
- **`reddit_oauth_available` property** (`config.py`) -- convenience check for whether Reddit
  OAuth credentials are configured.

### Fixed
- **Reddit 403 storm** -- the public JSON API was producing ~2,600 HTTP 403s/week with
  escalating IP blocks lasting 6-14+ hours. OAuth2 eliminates this entirely.

---

## [0.6.7] - 2026-03-23

### Added
- **Startup banner** (`utils/logger.py`) — `emit_startup_banner(version, model, env)` writes
  a one-line context header (`# ===== v0.6.7 | env=demo | model=qwen2.5:7b | py=3.14.x =====`)
  to both `bot.log` and `errors.log` on startup. The banner is stored in each handler and
  re-emitted automatically after every midnight rotation, so every archived log file is
  self-describing without needing to search back to the beginning of the run.

### Fixed
- **Multiple file handlers per log file** (`utils/logger.py`) — each `get_logger()` call
  previously created new `_DailyRotatingFileHandler` instances. With ~10 named loggers,
  ~10 separate handlers were all writing to and trying to rotate `bot.log` simultaneously
  at midnight. Fixed by promoting `_app_fh` and `_err_fh` to module-level singletons shared
  across every logger — exactly one write and one rotation attempt per record.
- **Em dash encoding** (`main.py`) — three pre-existing `--` (em dash U+2014) characters in
  `log.warning()`, `log.info()`, and `print()` calls. Windows cp1252 log handlers silently
  drop messages containing non-ASCII and dump a fake crash traceback. Replaced with `--`.

---

## [0.6.6] - 2026-03-23

### Added
- **Daily log rotation** (`utils/logger.py`) — replaced the size-based
  `_WindowsSafeRotatingFileHandler` with `_DailyRotatingFileHandler` (midnight rotation,
  90-day retention, named `bot.log.YYYY-MM-DD`). Uses copy+truncate strategy so the base
  log path never moves — open file handles (VS Code, tail) continue uninterrupted. Works
  identically on Mac and Windows.
- **`errors.log`** (`utils/logger.py`) — WARNING+ only log file written alongside `bot.log`.
  Enables fast triage without grepping the full DEBUG output.
- **NSSM `service_stderr.log` rotation** — configured `AppRotateFiles=1`,
  `AppRotateOnline=1`, `AppRotateBytes=5242880` via registry. Rotation now fires at 5MB
  while the service is running, not just on restart.

---

## [0.6.5] - 2026-03-23

### Fixed
- **Ollama circuit breaker permanently open** (`analysis/signal_analyzer.py`) — circuit
  probes were full 60s inference calls. When Ollama was slow, each probe timed out,
  incremented the failure counter, and reset the 5-minute lockout window — the circuit
  could never close. Added `_ollama_ping()`: a cheap `GET /api/version` call with a 5s
  timeout. At probe time, ping first. A failed ping extends the lockout timer without
  touching the failure counter. A successful ping resets the counter and unlocks inference.

---

## [0.6.4] - 2026-03-23

### Changed
- **Fade signal source: tweet feed replaced with WebSocket price detector**
  (`analysis/fade_signal.py`, `main.py`, `config.py`) — rsshub.app Twitter routes all
  returned 404 after X blocked public RSSHub instances. Replaced with a price-crossing
  detector on the existing authenticated Kalshi WebSocket connection (no new dependency).
  `detect_price_fade()` fires when a geo market crosses above 85c or below 15c, with a
  1c hysteresis buffer to suppress boundary noise. `_warm_ws_subscriptions()` subscribes
  to all geo market tickers at startup. Trade reasoning is prefixed `[PRICE_FADE...]`
  for SQL win-rate queries.

### Removed
- `FADE_TWEET_FEED_URLS`, `_on_fade_tweet()`, `_process_fade_tweet()`,
  `_account_from_rsshub_url()` — tweet feed infrastructure no longer needed.

---

## [0.6.3] - 2026-03-19

### Fixed
- **Same-side duplicate positions blocked** (`trading/executor.py`, `config.py`) — the
  multi-position guard only blocked same-side trades within a +-2% probability delta,
  allowing duplicate NO positions on the same ticker when estimates differed by >2%.
  Added `PAPER_BLOCK_SAME_SIDE_DUPLICATE = True` in paper mode: any existing same-side
  open position on a ticker now unconditionally blocks a new one. Live mode retains the
  narrow delta guard. Fixes 2 open NO positions on `KXZELENSKYYOUT-26APR01`.
- **NSSM log rotation now active while running** (`setup_service.ps1`) — `AppRotateOnline`
  was 0, so NSSM only rotated logs at service restart. With 48h uptime, `service_stderr.log`
  grew to 10MB unchecked. Set `AppRotateOnline 1` so rotation fires at 10MB regardless
  of uptime.
- **Double market cache refresh debounced** (`analysis/market_matcher.py`) — two code paths
  could trigger `_refresh()` within seconds of each other (scheduled task + TTL expiry on
  an incoming signal). Added a 60-second debounce inside `_refresh()` and `_refresh_all()`:
  if a refresh completed within the last 60s, the second call returns immediately.

### Removed
- **"Just In News" (The Hill) RSS feed** (`config.py`) — feed re-served articles up to
  45 days old as new items, contributing 301 of 740 stale-skipped queue entries over 48h.
  Zero tradeable signals produced. Removed from `RSS_FEEDS`.

---

## [0.6.2] - 2026-03-17

### Fixed
- **Windows log rotation** (`utils/logger.py`) — `RotatingFileHandler.doRollover()` was
  calling `os.rename()` which fails with `PermissionError: [WinError 32]` when any process
  holds `bot.log` open (VS Code, tail -f, etc.). After failure, `self.stream` was left `None`
  and every subsequent log emit was silently dropped. Replaced with a
  `_WindowsSafeRotatingFileHandler` subclass that uses copy+truncate instead of rename —
  the file keeps the same path/handle, so open processes are unaffected.
- **Em dash encoding trap** (`signal_analyzer.py`, `reddit_monitor.py`) — new log strings
  introduced in v0.6.1 contained em dashes (`—` U+2014). Windows log handlers default to
  cp1252, causing `handleError()` to dump fake crash tracebacks to `service_stderr.log`
  while silently dropping the message. Replaced with `--`.

### Added
- Windows ASCII-only log string rule documented in `CLAUDE.md` — enforced by pre-commit grep.

---

## [0.6.1] - 2026-03-17

### Added
- **Ollama circuit breaker** (`analysis/signal_analyzer.py`) — after 3 consecutive
  connection/timeout failures, skips the 60s timeout window and returns `None` immediately.
  Probes every 5 minutes. Auto-recovers on first success. Prevents the bot burning 60s per
  signal when Ollama is down.
- **Reddit global circuit breaker** (`feeds/reddit_monitor.py`) — if >=50% of subreddits
  fail in a single poll cycle, suspends all Reddit polling for 30 minutes. Previously only
  individual subreddits were backed off; a mass 403 storm from running concurrent instances
  was not handled.
- **Reddit 403 backoff** — 403 responses now trigger a 60s per-subreddit backoff (was
  previously ignored). Reddit's API blocks non-OAuth bots on private subreddits; this
  prevents hammering blocked endpoints.

---

## [0.6.0] - 2026-03-17

### Added
- **Portfolio state object** (`trading/portfolio.py`) — single source of truth for open
  paper positions. Previously open-position checks queried SQLite directly on every signal;
  now the in-memory portfolio object handles deduplication and cooldown enforcement. Required
  before go-live to avoid double-entry on service restart.

---

## [0.5.8] - 2026-03-17

### Added
- `market_snapshot` JSON column in `paper_trades` table — stores the full market state
  (yes/no prices, volume, days to close) at decision time. Enables post-resolution analysis
  of whether the bot had accurate pricing information when it placed the trade.

---

## [0.5.7] - 2026-03-17

### Changed
- Replaced `asyncio.Queue` with `asyncio.PriorityQueue` in `main.py` signal pipeline.
  RSS feeds (Reuters, AP, BBC, Al Jazeera) are assigned higher priority than Reddit, so
  authoritative news sources are processed first during bursts.

---

## [0.5.1] - 2026-03-17

### Added
- **LLM semaphore** — caps concurrent Ollama calls to 1, preventing queue backup when
  multiple signals arrive simultaneously (each call takes 20-40s on CPU).
- **Staleness guard** — signals older than 10 minutes are discarded before LLM analysis.
  Prevents stale queue items from generating trades on outdated news.
- **Queue observability** — logs queue depth on each dequeue so backpressure is visible
  in bot.log.

### Fixed
- `UnknownTimezoneWarning` for EST/PST timezone strings in RSS feeds and `_days_to_close()`.
- `CancelledError` on shutdown in Python 3.14 — suppressed cleanly instead of propagating.
- Duplicate `PaperTrader` initialization in `main.py`.
- Deprecated `asyncio.get_event_loop()` calls in `main.py` and `market_matcher.py`.

---

## [0.5.0] - 2026-03-17

### Added
- Version control rule in `CLAUDE.md` — `VERSION` file bumped on every feature/fix commit.
- Project self-improvement system: `CLAUDE.md` (auto-loaded by Claude Code), `tasks/lessons.md`
  (hard-won runbook), `tasks/todo.md` (authoritative backlog).

### Changed
- LLM JSON parsing made robust — handles malformed responses, missing keys, extra whitespace.
- Cross-source headline deduplication — same story from Reuters + AP within 15 minutes is
  processed only once, cutting redundant LLM calls by ~30-50%.

### Fixed
- Reddit 429 backoff — was not being respected, causing the monitor to hammer the API.
- DB transaction isolation — concurrent writes were occasionally causing `SQLITE_BUSY`.

---

## [0.4.0] - 2026-03-17

### Added
- **Fade signal for multiple accounts** — `executor.py` now routes fade signals to
  configurable account list (`@Kalshi`, `@Polymarket`, `@PolymarketMoney`). Each account
  tracked separately in paper trades for win-rate analysis.
- **Fade-the-Kalshi-tweet signal** — monitors Kalshi's official social accounts for
  announcement posts and fades the implied move (documented but paper-only until validated).

---

## [0.3.0] - 2026-03-16

### Added
- Semantic versioning introduced (`VERSION` file, `config.py` version constant).
- Actor standing + reasoning consistency rules added to LLM prompt — reduces hallucinated
  high-confidence outputs on irrelevant news.
- Multi-position guard fixed: now checks ALL open trades on a ticker, not just the most
  recent, before allowing a new entry.

### Fixed
- `NameError` on missing `asyncio` import in `signal_analyzer.py`.
- Three low-priority signal quality bugs (see `tasks/completed.md`).
- Duplicate trades on service restart (`executor.py`) — now checks DB state on init.

---

## [0.2.0] - 2026-03-12 to 2026-03-13

*Pre-versioning sprint — major architecture work before semantic versioning was introduced.*

### Added
- **Ollama integration** — local LLM (qwen2.5:7b) as primary probability estimator.
  Startup health check confirms model is loaded before first signal.
- **Categorical LLM design** — replaced float probability approach (was hallucinating 0.98
  on every call) with structured JSON: `relevant`, `new_info`, `direction`, `magnitude`,
  `confidence`. Magnitude maps to price shift: none=0.0, small=0.08, moderate=0.15, large=0.25.
- **Deterministic inference** — `temperature=0`, `repetition_penalty=1.05` for stable
  JSON schema classification.
- **Inference timeout raised** to 60s (from 30s) to handle 7B CPU inference latency.
- **Tiered headline gate** — requires at least one headline token in market title to prevent
  body-only spurious matches (e.g. body mentions "Korea" matching "Bank of Korea" market).
- **Geo-coherence edge suppression** — filters edges where geographic context is inconsistent.
- **Market series-title discovery** — replaced static blocklist with keyword search across
  all ~9k Kalshi series; discovers ~250-443 open geopolitical markets. Blocklist now only
  covers sports leagues.
- **Keywords expanded** in `market_matcher.py` — +143 additional geopolitical series.
- **Paper trade noise reduction** — blocklist CB/TRUMPSAY markets, fan-out capped at 1,
  4-hour cooldown per ticker. Goal: maximize resolved trades on true geo markets.

### Fixed
- Windows-1252 em dash `SyntaxError` in `signal_analyzer.py` (commit ea196e0).
- WebSocket header kwarg compatibility across websockets library versions (v10-14+).
- Kalshi API URL migration and aiohttp Python 3.14 compatibility.
- Market status check to accept Kalshi's `'active'` status string.

---

## [0.1.0] - 2026-03-09 to 2026-03-11

*Initial build.*

### Added
- Core async bot (`main.py`) with 5 concurrent tasks: RSS monitor, Reddit monitor,
  WebSocket price feed, daily reporter, market cache refresh.
- Kalshi REST client (`kalshi/rest_client.py`) with RSA-PSS signing.
- Kalshi WebSocket client (`kalshi/websocket_client.py`) — real-time price feed.
- RSS monitor — Reuters, AP, BBC, Al Jazeera (60s poll).
- Reddit monitor — r/worldnews, r/ukraine, r/geopolitics (public JSON, no OAuth).
- Signal analyzer (`analysis/signal_analyzer.py`) — keyword scoring pipeline.
- Half-Kelly bet sizing (`analysis/kelly.py`) — 5% bankroll cap, $25 hard cap.
- Market matcher (`analysis/market_matcher.py`) — Jaccard token similarity + geo boost.
- Source credibility tracker (`analysis/source_credibility.py`) — per-source win/loss
  multiplier (0.5x to 1.5x).
- Paper trading engine (`trading/paper_trader.py`) — SQLite-backed, flat 5 contracts.
- Paper/live execution gate (`trading/executor.py`) — `--go-live` + `CONFIRM` required.
- Source credibility system — win/loss tracking per feed, applied as signal multiplier.
