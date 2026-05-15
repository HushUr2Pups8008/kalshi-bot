# v0.30.x Data / Runtime Alignment Audit

**Date:** 2026-05-13
**Repo HEAD:** `1314da7` → audit ran at `4b8d231` (current local `main`)
**VERSION:** `0.30.1` (operative; `v0.30.0` tag is published-broken — see CLAUDE.md Kalshi-API gotcha)
**Bot:** PID 53447, started `2026-05-13T00:02:38.142311+00:00`, untouched during audit
**Cohort sentinel:** `bot_state.p0_price_fix_deployed_ts = 2026-05-12T23:50:04.422696+00:00` ✓
**Clean runtime start:** `2026-05-13T00:02:37Z`
**Failed-start carve-out:** `[2026-05-12T23:50:04Z, 2026-05-13T00:02:37Z)`
**Mode:** read-only audit; single workoff artifact; **no code or DB mutations**

---

## 1. Executive verdict

**Status: PARTIALLY ALIGNED.**

- **Producers (write paths):** ALIGNED. Single canonical `KalshiMarket` constructor (`kalshi/normalizer.py:_build_market`), fail-closed at every contract boundary, drift-halt sentinel write path correct, hotfix `?status=open` confirmed at `analysis/market_matcher.py:440,490`. The post-P0 contract is enforceable by construction.
- **Data stores:** ALIGNED on `paper_trades` (provenance columns + sentinel). NOT ALIGNED on `evidence_store.db` / `dossier_updates_post_fix.db` — these schemas are P0-blind; cohort cut depends entirely on the sentinel timestamp in a *different* DB file.
- **Consumers (replay / analytics):** PARTIALLY ALIGNED. The dedicated readiness watcher and silent-50 hardening landed (`!15`, `!16`, `!17`), but `build_replay_dataset.main()` does **not** call its own cohort filter; `score_counterfactual_pnl._assigned_side` mixes pre-P0 midpoint semantics with post-P0 executable-price semantics; `performance_analysis` lifetime aggregates blend pre/post-P0 cohorts; the `market_yes_price` column-name lie is documented but un-migrated.
- **Observability (operator surfaces):** NOT ALIGNED. The only auto-firing notifier (`scripts/bothealth.sh`, daily 08:00 MDT `osascript`) is drift-blind, cohort-blind, and trade-emission-blind. The `post_fix_new_readiness_status.py` watcher has **zero non-test callers**. There is no "zero new paper_trades in N hours since clean-start" alarm anywhere.

**Safe to keep bot running? YES — with caveats.**

The producer posture is fail-closed (verified by Subagent B): no reachable code path can write a contaminating row into `paper_trades` post-clean-start. The bot will either write a post-P0-clean row or skip with a warning log. The operational risk is **silent absence of trades**, not silent corruption of trades.

**Biggest silent-miss risks (top 3):**

1. **"Zero trades since clean-start" is currently indistinguishable from "all candidates silently dropped at the contract boundary."** The bot has been running ~5h post-clean-start with zero new `paper_trades` rows. `evidence_store` *is* writing fresh evidence + dossier-updates at `2026-05-13T00:29:17` (one ticker, `KXTRUMPIRAN-26JUN01`), so ingestion is healthy. The executor gate is either correctly rejecting low-EV signals or silently swallowing candidates at `executor._validate`'s legacy midpoint-fallback path (`executor.py:212-214`) which would log OPPORTUNITY in JSONL but never write to SQL. No surface correlates these two streams.
2. **`scripts/edge_replay/build_replay_dataset.main()` does NOT wire its cohort filter.** `filter_post_p0_rows` and `assert_corpus_quality_or_raise` are tested library helpers; `main()` (`:460-471`) emits a corpus without applying them. Any operator who reruns the replay assumes (reasonably, given the in-file docstring at `:5-7`) that the cohort cut is enforced. It is not.
3. **`bothealth.sh` (the only auto-firing operator notifier) is blind to every v0.30.x P0 semantic.** A drift-halt sentinel could materialize at 03:00 UTC; the daily 08:00 MDT toast still says "GREEN — bot alive". The runbook's "verify with `python scripts/botcheck.py`" is a pull check — there is no push channel.

---

## 2. Current data state

| Surface | Value |
|---|---|
| `data/paper_trades.db` | 9 rows, all pre-P0 (`p0_contract_version=1`, `price_source='unavailable'`, `price_cents=50`, `market_yes_price=50.0`). Latest `ts = 2026-05-11T09:27:41`. **0 rows in carve-out. 0 rows post-clean-start.** |
| `data/evidence_store.db` | tables: `dossiers (50)`, `evidence (344)`, `dossier_updates`, `dossier_update_evidence (8272)`, `structural_priors (50)`. **1 evidence row + 1 dossier_update post-clean-start (`KXTRUMPIRAN-26JUN01` v32 @ `2026-05-13T00:29:17.794Z`).** 0 carve-out rows. Schema is P0-blind (no `p0_contract_version`, no `cohort_id`). |
| `data/dossier_updates_post_fix.db` | Frozen Cycle-15B C9 reingest snapshot; last touched `2026-05-06T18:50:41`. Contains `pre_fix_dossier_updates (279)` + post-fix `dossier_updates (279)` derived with `BASE_PROBABILITY=0.50`. Not a runtime DB. |
| `data/runtime/` | Empty. **No `kalshi_drift_halt.json` present** — bot is not halted. |
| `bot_state` (k/v) | `notional_bankroll=27.5`, `p0_price_fix_deployed_ts=2026-05-12T23:50:04.422696+00:00` ✓, `paper_start_time=2026-04-18T02:11:24.442824+00:00`. **No `cohort_id`, no `clean_start_ts`, no `drift_halt` keys.** |
| Drift halt | NOT ACTIVE (sentinel file absent). |
| Bot lock | `data/bot_runtime.lock` → PID 53447, started `2026-05-13T00:02:38Z` (matches clean-start). |

---

## 3. End-to-end dataflow map

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Kalshi REST  →  Normalizer  →  Matcher  →  Signal  →  Side  →  Exec    │
│                                                                          │
│  rest_client    normalizer    market_     signal_    side_     executor │
│  .py:140-244    .py:319-482   matcher     analyzer   selection .py:480- │
│                               .py:440,490 .py:1158   .py       590      │
│                                                                          │
│  raw JSON   →   KalshiMarket  →  filter   →  estim.  →  YES/NO  →  exec │
│                 + provenance     ?status=    prob       executed                                  │
│                                  open                   _cents          │
│                                                                          │
│  Drift halt path: normalizer.DriftCounter.record_drift                  │
│  →  atomic write data/runtime/kalshi_drift_halt.json                    │
│  →  no notification (pull-only via botcheck/daily_review)               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  DB / JSONL writes (post-P0 contract)                                   │
│                                                                          │
│  paper_trader.record_trade  →  paper_trades INSERT                      │
│  paper_trader.py:589-770    →  + market_yes_price (LIES: now executed)  │
│                             →  + price_source, price_method,            │
│                                price_retrieved_at, raw_payload_hash,    │
│                                p0_contract_version (DEFAULT 1)          │
│  evidence_store.add_*       →  evidence / dossier_updates               │
│  tasks/evidence_store.py    →  (NO provenance columns; sentinel-only)   │
│                                                                          │
│  Fail-closed branches:                                                  │
│    - price_available=False        → log.warning + return ""             │
│    - executed_price_cents=None    → log.warning + return ""             │
│    - validate(): floor/ceil       → trade_log.log_skipped JSONL         │
│  Silent paths (no DB row, log only):                                    │
│    - main._process_candidate price_unavailable → log.debug              │
│    - main._process_candidate status_not_active → log.debug              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Consumers (read paths)                                                 │
│                                                                          │
│  HONORS sentinel / carve-out:                                           │
│    - post_fix_new_readiness_status.py (orphaned: no caller)             │
│    - botcheck.py (manual; print-only)                                   │
│    - daily_review.py SECTION 0 (manual; no launchd plist)               │
│                                                                          │
│  DOES NOT honor sentinel:                                               │
│    - build_replay_dataset.main()  ← helpers exist, not wired            │
│    - performance_analysis.py (lifetime aggregates)                      │
│    - score_counterfactual_pnl.py (also mis-semantics)                   │
│    - scorer_forensics_audit.py, side_flip_counterfactual.py             │
│    - build_cycle17d_corpus.py, build_cycle17d_broader_corpus.py         │
│    - signal_edge_diagnostics.py                                         │
│                                                                          │
│  Auto-firing alert surface: bothealth.sh (P0-blind in every dimension)  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Fields that changed in v0.30.x:**
- New `paper_trades` columns: `price_source`, `price_method`, `price_retrieved_at`, `raw_payload_hash`, `p0_contract_version`
- Semantic change: `paper_trades.market_yes_price` now stores executed-side entry price (DT-2b alias mirror at `analysis/__init__.py:41-43`, persisted at `trading/paper_trader.py:715`)
- New `bot_state` key: `p0_price_fix_deployed_ts`
- New on-disk sentinel: `data/runtime/kalshi_drift_halt.json` (writer: `kalshi/normalizer.py:DriftCounter.record_drift`)

**Boundary invariants** (currently aspirational — most are not enforced by tests/watchers):
- Every post-sentinel `paper_trades` row must have `price_source ∈ {"rest_list","rest_detail"}` and `raw_payload_hash IS NOT NULL`.
- Every post-sentinel `paper_trades` row must have `price_cents ≈ ROUND(market_yes_price)` (alias-mirror invariant).
- No row may be written when `ts ∈ [sentinel, clean_start)`.
- Drift-halt must exist if recent `paper_trades` show `unsupported_payload_contract`-adjacent provenance.
- `apply_historical_prices` must not write `market_yes_price` for rows originating post-sentinel.

---

## 4. Findings table

| ID | Sev | Layer | File / surface | Evidence | Failure mode | Impact | Recommended fix | Branch | Blocking | primary_agent | second_agent_review_required | operator_gate_required | recommended_workflow | why_this_assignment | safe_while_bot_running |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-01 | Critical | observability | `scripts/bothealth.sh` | `:251-274` verdict ladder; full-file grep clean for `drift\|halt\|sentinel\|cohort\|kalshi_drift` | Daily 08:00 MDT toast shows GREEN while drift-halt sentinel exists or cohort empty | Operator never paged for halt/drift/zero-trades | Add `kalshi_drift_halt.json` existence check + `p0_price_fix_deployed_ts` echo to verdict ladder; promote RED on halt | `fix/bothealth-p0-aware-verdict` | yes | Either | yes | yes (operator owns launchd) | medium-risk | Affects only operator-notification surface; no execution path. Shell scripting + alerting tradeoff judgment makes either agent fine | yes |
| F-02 | Critical | consumer | `scripts/edge_replay/build_replay_dataset.py:460-471` | `main()` never calls `filter_post_p0_rows` (`:319`) or `assert_corpus_quality_or_raise` (`:377`); docstring `:5-7` admits later-packet wiring | Replay CLI emits pre-P0 contaminated corpus tagged as if post-fix | Any future POST_FIX_NEW edge verdict is silently invalid | Wire `filter_post_p0_rows` + `assert_corpus_quality_or_raise` into `main()`; integration test that mocks pre-P0 rows and asserts they are excluded | `fix/replay-corpus-cohort-wiring` | yes | Either | yes | no (offline tool) | medium-risk | Already-tested library helpers; wiring is mechanical; tests must catch regression | yes |
| F-03 | High | consumer | `scripts/edge_replay/score_counterfactual_pnl.py:47-49` | `_assigned_side` uses `model_prob >= market_yes_price/100.0` post-P0 where column carries asks, not midpoint | Side-assignment biased by ask spread; any rerun produces silently wrong PnL | Strategy decisions made against wrong PnL | Rewrite side assignment to use `executed_price_cents` + `side`; semantic test against a known-asymmetric ask fixture | `fix/counterfactual-pnl-side-semantics` | no | Either | no | no | low-risk | Bounded single-function change; new test pins semantics; no production runtime impact | yes |
| F-04 | High | observability | `scripts/edge_replay/post_fix_new_readiness_status.py` | `rg post_fix_new_readiness_status` returns only test + debt-log mention; no auto-firing caller | NOT_READY verdict never reaches operator unless they remember to run it | The whole readiness-gate concept is unenforced | Add to `bothealth.sh` daily run; emit JSON output; promote to RED when NOT_READY | `feat/bothealth-wire-readiness-watcher` | yes | Either | yes | yes (operator owns launchd) | medium-risk | Same surface as F-01; can land together. Pairs with adding the watcher to launchd | yes |
| F-05 | High | consumer | `scripts/performance_analysis.py:921,1078,1161` | Lifetime aggregates over full `paper_trades` table without sentinel cut; current data = 9 pre-P0 rows | Every "lifetime win-rate / Kelly fit / source attribution" is 100% pre-P0 contaminated | Operator reads stats that don't reflect post-fix bot | Add `WHERE ts >= (SELECT value FROM bot_state WHERE key='p0_price_fix_deployed_ts')` to lifetime queries; display "pre-P0 (frozen)" + "post-P0 cohort" tables separately | `fix/perf-analysis-cohort-split` | no | Either | no | no | low-risk | Read-only consumer; no runtime impact | yes |
| F-06 | High | observability | `scripts/bothealth.sh` + `scripts/botcheck.py:566-580` | No trade-emission-rate alarm; `botcheck` `_summarize_p0_contract_version_drift` treats `no_recent_events` as `"expected if bot has not yet emitted a PAPER_TRADE post-deploy"` | 5h post-clean-start with zero trades silently rendered as normal | Cannot distinguish "low edge day" from "executor silently swallowing candidates" | Add watcher: count(`OPPORTUNITY` JSONL events post-clean-start) vs count(`paper_trades` rows post-clean-start); alarm if N-hour ratio exceeds threshold | `feat/opportunity-vs-trade-drift-watcher` | no | Claude Code | yes | no | medium-risk | Requires read of JSONL trade-log + paper_trades + correlation; multi-file + needs adversarial review for false-positive rate | yes |
| F-07 | High | data | `data/evidence_store.db` + `dossier_updates_post_fix.db` schemas | Neither schema carries `p0_contract_version`, `cohort_id`, or provenance hash | Cohort cut depends entirely on `bot_state.p0_price_fix_deployed_ts` in a different DB file; restore-from-snapshot of evidence_store alone silently mixes cohorts | Replay correctness depends on cross-DB consistency that nothing enforces | Add `p0_contract_version` (or equivalent cohort tag) to `evidence` + `dossier_updates` tables; backfill pre-fix rows with `0`; update `tasks/evidence_store.py` writers | `feat/evidence-store-cohort-column` | no | Claude Code | yes | yes (DB schema migration) | high-risk | Schema migration touches active live writer; needs adversarial review for migration safety and writer-update synchronization | no — needs restart |
| F-08 | High | producer | `trading/executor.py:212-214,267-270` | Legacy fallback: `_validate` accepts `market.yes_price` midpoint when `executed_price_cents is None` | Silent attrition: candidate passes validate → logs OPPORTUNITY in JSONL → record_trade skips → no SQL row | From SQL-only surfaces (botcheck, daily_review, readiness watcher) indistinguishable from "no opportunities seen" | Tighten `_validate` to require `executed_price_cents` non-null when `price_available=True`; emit structured `trade_log.log_skipped` reason on the rejection | `fix/executor-validate-require-executed-cents` | no | Either | yes | yes (executor change, paper-to-execution path) | high-risk | Touches the validation gate on execution path. Adversarial review required for invariant correctness across blend re-fetch, news path, fade path | no — needs restart |
| F-09 | Medium | consumer | `scripts/edge_replay/build_replay_dataset.py:298-310` (`apply_historical_prices`) | Writes `row["market_yes_price"] = price` (YES-midpoint semantic) and recomputes `edge = model_prob - price/100` | Pre-P0 replay rows backfilled here are mathematically clean but semantically wrong; merging with post-P0 rows yields ambiguous corpus | Hybrid replay outputs poisoned silently | Skip rows whose origin is post-sentinel; or rewrite to compute executed-side edge per `side` | `fix/replay-historical-prices-cohort-aware` | no | Either | no | no | low-risk | Replay-only; offline | yes |
| F-10 | Medium | consumer | `scripts/edge_replay/scorer_forensics_audit.py:234`, `side_flip_counterfactual.py:139` | `(market_yes_price or 0.0)` for `price_delta` | Silent-0 on missing price; spurious anomaly readings | Replay diagnostics misleading | Either replace with explicit None-skip + audit counter, or use `price_cents` | `fix/replay-silent-zero-price-delta` | no | Either | no | no | low-risk | Replay-only; offline | yes |
| F-11 | Medium | data | `paper_trades.market_yes_price` column-name lie | Documented in `docs/governance/2026-05-13-market-yes-price-alias-inventory.md` (64 hits across repo); inventory complete, migration deferred | Any new reader joining on column name without consulting `side` silently mis-prices NO rows | Slow-burn correctness drift; future-author footgun | Execute P1-A → P1-B → P1-C migration sequence per inventory doc | `feat/market-yes-price-rename-p1a` | no | Claude Code | yes | yes (DB schema rename) | high-risk | Schema rename + cross-file rename; coordination cost matches blast radius | no — needs restart |
| F-12 | Medium | observability | `scripts/bothealth.sh` osascript notification | `:267` only push channel; never reads sentinel/halt/cohort | Operator's daily toast is unreliable as alert | Push paths inadequate | Extend osascript args to include `kalshi_drift` and `p0_cohort_status` text | (with F-01) | no | Either | yes | yes | medium-risk | Same surface as F-01 | yes |
| F-13 | Medium | docs | `docs/governance/2026-05-13-kalshi-drift-halt-runbook.md` | Runbook claims operator-visible heartbeats via `botcheck` but does not state that there is no auto-push channel | Documentation drift; operator may assume push is configured | Mis-set expectations during incident | Add explicit "pull-only; no auto-notification" callout | `docs/runbook-pull-only-callout` | no | Either | no | no | low-risk | Pure doc clarification | yes |
| F-14 | Medium | docs | `README.md:9` / `CHANGELOG.md:12` | README says "Status (2026-05-12 — v0.30.0 P0 release)"; only CHANGELOG + CLAUDE.md state v0.30.0 is broken / v0.30.1 is operative | A reader of README alone may not realize v0.30.0 is the regressed tag | New-contributor / future-self confusion | Rewrite README status line to lead with "v0.30.1 (v0.30.0 published-broken)" | `docs/readme-v0301-operative` | no | Either | no | no | low-risk | Pure doc | yes |
| F-15 | Low | producer | `main.py:675-686` | Skip events emitted at DEBUG: `price_unavailable`, `status_not_active` | At INFO log level these are invisible | Quiet path; combined with F-06 makes silent attrition hard to diagnose | Upgrade to INFO, or emit a structured `trade_log.log_skipped` with reason | `fix/main-skip-events-info-level` | no | Either | no | no | low-risk | Logging tweak | yes |
| F-16 | Low | consumer | `tests/test_main_pipeline.py:121` + sim harnesses | Tests construct `SignalAnalysis` with `market_yes_price=market.yes_price` (YES-midpoint) instead of through `executed_price_cents` | Tests legitimize a pattern that re-asserts legacy semantics | Future code copies the test pattern | Add type/lint guard preventing direct `market_yes_price` set without canonical mirror; update test patterns | `fix/test-signalanalysis-canonical-construction` | no | Either | no | no | low-risk | Test-only | yes |
| F-17 | Info | observability | Cross-check vs `docs/profit_path_debt_log.md` | No "open" items found whose code is already complete; no "closed" items whose code is incomplete. Debt log accurate to code state. | n/a | n/a | n/a (informational) | n/a | no | — | n/a | n/a | n/a | n/a | yes |
| F-18 | Info | observability | `scripts/daily_review.py` | No launchd plist; only `bothealth.sh` runs on schedule | Operator must remember to run daily_review by hand to see SECTION 0 P0 lines | Pull-only surface | Optional: add `com.jake.kalshi-daily-review` launchd plist | `feat/daily-review-launchd-plist` | no | Either | no | yes | low-risk | Optional ergonomics | yes |

---

## 5. Workoff table (priority order, smallest safe PR grouping)

| Rank | Group | Findings | Files touched | Tests needed | Bot restart? | DB mutation? | Safe while bot running? | primary_agent | second_agent_review_required | operator_gate_required | recommended_execution_mode |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **P0 — Observability triage** (smallest safe; biggest operator-visibility win) | F-01, F-04, F-12 | `scripts/bothealth.sh`, optionally a small `scripts/bothealth_helpers.sh` | Shell test fixture asserting verdict promotes to RED on halt sentinel; assert osascript args carry P0 fields | no | no | yes | Either | yes (independence valuable; small surface) | yes (operator owns launchd reload) | branch+MR |
| 2 | **P0 — Replay cohort wiring** | F-02, F-09 | `scripts/edge_replay/build_replay_dataset.py`, tests | Integration test: mock pre-P0 + post-P0 rows; assert pre-P0 excluded from emitted corpus; assert `assert_corpus_quality_or_raise` raises | no | no | yes | Either | yes | no | branch+MR |
| 3 | **P0 — Opportunity-vs-trade drift watcher** | F-06 | new `scripts/observability/opportunity_trade_drift.py`, wired into `bothealth.sh` | Unit test: synthesize JSONL `OPPORTUNITY` events without matching `paper_trades` rows; assert alarm | no | no | yes | Claude Code | yes | yes (operator owns launchd cadence) | branch+MR |
| 4 | **P0 — Counterfactual PnL semantics** | F-03 | `scripts/edge_replay/score_counterfactual_pnl.py` + new test | Semantic test against asymmetric ask fixture | no | no | yes | Either | no | no | branch+MR |
| 5 | **P0 — Performance-analysis cohort split** | F-05 | `scripts/performance_analysis.py` | Test: pre-P0 rows present + post-P0 rows present; assert sectioned output | no | no | yes | Either | no | no | branch+MR |
| 6 | **P1 — Producer hardening** | F-08, F-15 | `trading/executor.py`, `main.py` | New test for `_validate` requiring `executed_price_cents` non-null; updated existing executor tests | **yes** | no | no | Claude Code | yes (high-blast-radius) | yes | branch+MR + operator runbook |
| 7 | **P1 — Evidence-store cohort column** | F-07 | `tasks/evidence_store.py`, `docs/evidence_store_schema.sql`, migration script, callers | Migration test with frozen pre-fix DB; verify writers tag new rows | **yes** | **yes (additive)** | no | Claude Code | yes (high-blast-radius schema) | yes | operator-runbook |
| 8 | **P1 — market_yes_price rename (P1-A)** | F-11 | per alias inventory P1-A list (~7 runtime files) | Full runtime test suite | yes | no (P1-A is dataclass/logs only) | no | Claude Code | yes | yes | branch+MR + operator runbook |
| 9 | **P2 — Replay silent-zero / historical-prices cohort** | F-10, F-09 (if not landed in #2) | `scripts/edge_replay/scorer_forensics_audit.py`, `side_flip_counterfactual.py` | Per-file unit tests | no | no | yes | Either | no | no | branch+MR |
| 10 | **P2 — Test pattern hygiene** | F-16 | tests/sim harnesses | n/a (test refactor) | no | no | yes | Either | no | no | direct |
| 11 | **Docs** | F-13, F-14, F-18 | `docs/governance/...drift-halt-runbook.md`, `README.md`, optional launchd plist | n/a | no | no | yes | Either | no | yes (launchd plist only) | branch+MR |

---

## 6. Do not do

- **Do not** delete or move the tag `v0.30.0` even though it points at a published-broken release. Per `docs/governance/v0300-broken-tag-dispatch.md` policy: leave it as the historical anchor. Operative release is `v0.30.1`.
- **Do not** reset, overwrite, or re-plant `bot_state.p0_price_fix_deployed_ts`. The sentinel is load-bearing for every consumer that does cohort segregation; changing it invalidates all post-fix data accounting.
- **Do not** delete rows from `paper_trades` to "clean" the 9 pre-P0 rows. They are the negative-control evidence that the silent-50 contamination existed.
- **Do not** mutate `data/dossier_updates_post_fix.db` — it is a frozen Cycle-15B C9 reingest snapshot.
- **Do not** `VACUUM` / `REINDEX` any live DB while the bot is running.
- **Do not** create a `data/runtime/kalshi_drift_halt.json` by hand for testing — it would halt the live bot. Use a unit-test sandbox.
- **Do not** broaden `?status=open` back to `?status=active` (the v0.30.0 P-7 regression) under any circumstances. The two-contract gotcha is canonical in CLAUDE.md.
- **Do not** modify `analysis/__init__.py:41-43` (the DT-2b alias mirror) without coordinated updates across all callers documented in `docs/governance/2026-05-13-market-yes-price-alias-inventory.md`.
- **Do not** change `governance/prompts.py:27-31` (anchor_rate polarity block) — already documented in CLAUDE.md as load-bearing for governance LLM. Out of scope for this audit but listed defensively because the producer-path consideration tempts edits here.
- **Do not** create parallel tracking docs. New tracking content lands as a section in `docs/profit_path_debt_log.md`.
- **Do not** broadcast the artifact path or audit findings to any external system (Slack, GitHub Issues) — operator-internal only.

---

## 7. Recommended next prompt (top-priority workoff item only)

Top item is workoff group #1 — bothealth verdict-ladder P0-awareness. Smallest blast radius, biggest operator-visibility win, safe while bot is running.

```
Implement bothealth.sh P0-awareness so the daily 08:00 MDT osascript
notification reflects v0.30.x semantic state.

Constraints:
- Read-only on data/ and bot runtime. Do not stop or restart the bot.
- All changes confined to scripts/bothealth.sh (and optionally a small
  scripts/bothealth_helpers.sh). No new dependencies; pure POSIX shell.
- Do not modify the bot, the executor, the launchd plist itself, the
  daily_review script, or any DB schema.

Requirements:
1. Read data/runtime/kalshi_drift_halt.json existence.
   - If present, promote verdict to RED with message
     "DRIFT HALT — kalshi contract drift; bot fail-closed".
   - Include the sentinel file mtime in the toast.
2. Read bot_state.p0_price_fix_deployed_ts from data/paper_trades.db
   via `sqlite3 -readonly`.
   - If missing, promote verdict to YELLOW with message
     "P0 sentinel missing".
   - Otherwise print "p0_cohort=<value>" in the local log section only.
3. Count rows in paper_trades with
   ts >= bot_state.p0_price_fix_deployed_ts.
   - If zero AND bot has been up > 6h since clean-start, promote
     verdict to YELLOW with message "No new paper_trades since
     clean-start (Nh elapsed)".
   - Use the bot_runtime.lock started_utc to compute elapsed.
4. Existing checks (launchctl state, bankroll, governance counters,
   exception classes, open PROFIT-* items) remain. Verdict ladder
   precedence: RED > YELLOW > GREEN with first-trip wins.
5. osascript args must include the verdict and the most-recent
   semantic reason (one line). Keep the current title format.

Tests:
- Add tests/shell/test_bothealth_verdict.sh (bash + sqlite3) that
  builds a temp data/ tree with synthesized fixtures and asserts each
  verdict transition (GREEN/YELLOW/RED).
- Update CLAUDE.md "Critical Gotchas" if any new invariant is
  introduced.
- Update docs/governance/2026-05-13-kalshi-drift-halt-runbook.md to
  state that bothealth.sh now surfaces drift halt automatically and
  remove the implication that operator-pull is the only path.

Risk note (high-assurance workflow not required, but second-agent
review IS required):
- This change affects operator-notification semantics. The bot is
  not touched. DB is not touched. launchd config is not touched
  (cadence stays daily 08:00 MDT). Operator owns the launchctl reload
  after merge.
```

---

## 8. Recommended debt-log pointer (for operator approval — DO NOT add yet)

Per `CLAUDE.md` § "Continuous Improvement" the One Document (`docs/profit_path_debt_log.md`) is the unified tracking surface. Recommended addition (operator approval required before append):

```markdown
### 2026-05-13 — v0.30.x data/runtime alignment audit

Audit artifact: `docs/governance/2026-05-13-v030x-data-runtime-alignment-audit.md`

Verdict: PARTIALLY ALIGNED. Producer paths fail-closed; observability surfaces
P0-blind on the only auto-firing notifier. Bot may continue running.

Open workoff (priority order — see artifact §5):
1. bothealth verdict P0-awareness (F-01, F-04, F-12) — smallest safe; biggest win
2. build_replay_dataset cohort wiring (F-02, F-09)
3. Opportunity-vs-trade drift watcher (F-06)
4. Counterfactual PnL side-semantics (F-03)
5. performance_analysis cohort split (F-05)
6. Executor validate require executed_price_cents (F-08, F-15) — needs restart
7. Evidence-store cohort column (F-07) — needs restart + schema migration
8. market_yes_price rename P1-A (F-11) — needs restart
9. Replay silent-zero / historical-prices cohort (F-09, F-10)
10. Test pattern hygiene (F-16)
11. Docs (F-13, F-14, F-18)
```

---

## 9. Final policy sentence

**Peer agents, assigned by blast radius. Independence when risk is high. Human owns live-state authority. Process overhead scales with consequence.**

---

## Audit validation receipt

- Artifact path: `docs/governance/2026-05-13-v030x-data-runtime-alignment-audit.md` (this file).
- `git status` after artifact write: only this artifact + pre-existing untracked `logs/edge_replay/cycle17c-e3/`.
- DB mtimes:
  - `data/paper_trades.db` 2026-05-12T20:40:09 → 2026-05-12T20:47:06 — **change caused by live bot bot_state writes (PID 53447), not by the audit.** Audit queries used `sqlite3 -readonly` only.
  - `data/evidence_store.db` 2026-05-12T19:12:07 → 2026-05-12T19:12:07 (unchanged on main file; bot uses WAL).
  - `data/dossier_updates_post_fix.db` 2026-05-06T18:50:41 → 2026-05-06T18:50:41 (unchanged).
- Bot PID unchanged: 53447, started 2026-05-13T00:02:38.142311+00:00.
- Cohort sentinel unchanged: 2026-05-12T23:50:04.422696+00:00.
- No drift-halt file created by audit (none present pre-audit, none present post-audit).
- Subagent disagreements: none. Subagents A–E findings are mutually consistent.

**Bot can keep running.** Producer posture is fail-closed; current zero-trades state is observationally consistent with both "no signals firing edge gate" and "silent attrition at executor._validate midpoint fallback" — workoff item F-06 (opportunity-vs-trade drift watcher) is the artifact that resolves this ambiguity.
