# Dead Code Inventory — 2026-05-08

**Scope:** `analysis/`, `feeds/`, `governance/`, `tasks/`, `utils/`, `scripts/`  
**Excluded:** `trading/`, `tests/`, `mac_archive/`, `windows_archive/`, `data/`, `logs/`  
**Tools:** vulture 60–100% confidence + ruff F401/F841 (clean) + grep cross-reference  

Notes on false-positive filtering:
- `row_factory` attribute assignments (`self._conn.row_factory = sqlite3.Row`) are SQLite configuration side-effects, not Python attributes vulture can track as "read". All are false positives.
- Protocol parameter names flagged as "unused variables" by vulture (e.g., `new_evidence_score` in Protocol `__call__` signatures) are required interface contracts, not dead code.
- Items used only in `tests/` are retained but confidence is adjusted to **medium** since they have no production callers.

---

## analysis/

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `analysis/dossier_builder.py:274` | `identify_superseded` | function | medium | No callers in production code; test-only (`test_dossier_forgetting.py`). Main pipeline does not call it. | Low — pure function, no side effects. Removal requires test cleanup. |
| `analysis/dossier_builder.py:295` | `clear_on_resolution` | function | medium | No callers in production code; test-only (`test_dossier_forgetting.py`). | Low — same as above. |
| `analysis/fade_signal.py:40` | `detect_fade_pattern` | function | low | Vulture flagged; `main.py:891` imports and calls it via dynamic local import. False positive. | N/A — live |
| `analysis/fade_signal.py:54` | `detect_price_fade` | function | low | Vulture flagged; `main.py:1007` imports and calls it. False positive. | N/A — live |
| `analysis/kelly.py:65` | `kelly_bet` | function | low | `trading/executor.py` and `trading/paper_trader.py` both consume this module. False positive. | N/A — live |
| `analysis/kelly.py:156` | `contracts_from_dollars` | function | low | Same consumers. False positive. | N/A — live |
| `analysis/kelly.py:167` | `expected_pnl` | function | low | Same consumers. False positive. | N/A — live |
| `analysis/keyword_stats.py:43` | `KeywordStats` | class | low | `analysis/signal_analyzer.py` and `main.py` reference it. False positive. | N/A — live |
| `analysis/keyword_stats.py:131` | `KeywordStats.refresh` | method | medium | No grep hits outside the class definition and its own tests. `signal_analyzer.py` imports the class but no call to `.refresh()` found in production code. | Low — verify against `signal_analyzer.py` call sites before removal. |
| `analysis/market_matcher.py:700` | `find_all_candidates` | method | low | `main.py:898` awaits it. False positive. | N/A — live |
| `analysis/market_matcher.py:747` | `refresh_cache` | method | low | `main.py:1244` awaits it. False positive. | N/A — live |
| `analysis/source_credibility.py:49` | `SourceCredibility` | class | low | `trading/paper_trader.py` and `main.py` use it. False positive. | N/A — live |
| `analysis/source_credibility.py:89` | `SourceCredibility.get_stats` | method | low | `tests/test_source_credibility.py` and `trading/paper_trader.py` call it. False positive. | N/A — live |
| `analysis/source_credibility.py:144` | `SourceCredibility.record_outcome` | method | low | `trading/paper_trader.py:745` calls it. False positive. | N/A — live |
| `analysis/source_credibility.py:207` | `SourceCredibility.format_table` | method | low | `main.py:1875` and `trading/paper_trader.py:952` call it. False positive. | N/A — live |
| `analysis/source_stats.py:45` | `FLUSH_INTERVAL` | variable | **high** | Defined as module constant but never referenced in code logic — appears only in docstrings/comments. No caller grep hits outside the module. Flush cadence is driven externally, not by this value. | Low — removing the constant changes no behavior. It documents intent; a plain comment would suffice. |
| `analysis/source_stats.py:71` | `SourceStats` | class | low | `main.py` and `feeds/subreddit_selector.py` use it extensively. False positive. | N/A — live |
| `analysis/source_stats.py:118–133` | `increment_posts`, `increment_signals`, `increment_opportunities`, `increment_trades` | methods | low | All four called from `main.py` (lines 630, 712, 789, 1732). False positive. | N/A — live |

---

## feeds/

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `feeds/dedup.py:47` | `HeadlineDedup` | class | low | `main.py` and tests import it. False positive. | N/A — live |
| `feeds/gdelt_monitor.py:119` | `run_gdelt_monitor` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/google_news_monitor.py:113` | `run_google_news_monitor` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/reddit_monitor.py:302` | `run_reddit_monitor` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/rss_monitor.py:151` | `run_rss_monitor` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/search_news_monitor.py:200` | `run_search_news_monitor` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/subreddit_discovery.py:136` | `run_discovery_pass` | function | low | `main.py` calls it. False positive. | N/A — live |
| `feeds/subreddit_selector.py:156` | `select_subreddits` | function | low | `main.py` calls it. False positive. | N/A — live |

---

## governance/

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `governance/agent.py:48` | `kill_switch_disabled` | dataclass field | medium | Set to `False` at construction (`agent.py:91`) and asserted in unit tests, but no production code reads this field to branch on it — `kill_switch_readonly` appears to be the operative field. | Medium — could be an intentional sentinel; confirm against spec before removing. |
| `governance/audit.py:91` | `AuditLogger.compress_old_archives` | method | medium | Only called in `tests/test_governance_audit.py:131`. No production call site found. Docstring describes it as "a separate idempotent step." | Low — pure archiving utility; no side effects on live data. |
| `governance/decision.py:162` | `Decision.to_disabled_source` | method | medium | Production call site in `governance/agent.py:235` is **commented out**. Only referenced in `tests/test_governance_decision.py`. | Medium — marked as Task 4 planned interface in docstring; may be reserved for future wiring. |
| `governance/decision.py:190` | `Decision.to_disabled_keyword` | method | medium | Same — commented-out production usage. Test-only callers. | Medium — same future-wiring risk. |
| `governance/decision.py:218` | `Decision.to_threshold_override` | method | medium | Same — commented-out production usage. Test-only callers. | Medium — same future-wiring risk. |
| `governance/prompts.py:61` | `LLM_OUTPUT_SCHEMA` | variable | medium | Only referenced in `tests/test_governance_prompts.py`. No production code imports it. | Low — schema validation artifact; removing requires test cleanup. |
| `governance/prompts.py:184` | `dump_prompt_revision` | function | medium | Only referenced in `tests/test_governance_prompts.py`. No CLI or production caller found. | Low — prompt versioning utility; no runtime dependency. |
| `governance/safety.py:26–28` | `blast_radius_max_*` fields | dataclass fields | low | Used in `governance/safety.py:42–44` (own validation loop) and tests. The class reads them internally. False positive. | N/A — live |
| `governance/safety.py:79` | `KillSwitch.may_apply` | method | low | Core predicate called extensively in `tests/test_governance_safety.py`. False positive. | N/A — live |

---

## tasks/

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `tasks/accumulation_task.py:54` | `new_evidence_score` (in `DossierBuilder.__call__`) | Protocol parameter | low — false positive | Vulture treats Protocol `__call__` parameter names as unused variables. Formal interface parameter. | N/A |
| `tasks/accumulation_task.py:64` | `new_evidence_score` (in `UpdateClassifier.__call__`) | Protocol parameter | low — false positive | Same as above. | N/A |
| `tasks/blend_task.py:94` | `TradeCandidate.blended_probability` | dataclass field | low | `trading/executor.py:316,318` reads this field. False positive. | N/A — live |
| `tasks/blend_task.py:110` | `BlendTaskResult.ready` | property | medium | Only used in `tests/test_blend_task.py`. No production code reads `.ready` on a `BlendTaskResult`. | Low — convenience predicate; removing breaks tests but not runtime. |
| `tasks/budget_manager.py:40` | `QueuedLLMRequest.requested_at` | dataclass field | medium | Set on construction (`budget_manager.py:131`) but never read back in any computation. Test iteration does not access the attribute directly. | Low — observability metadata; no computation depends on it. |
| `tasks/budget_manager.py:104` | `BudgetManager.pending_requests` | method | medium | Only iterated in `tests/test_budget_manager.py:99`. No production caller. | Low — diagnostic/draining utility; no runtime path calls it. |
| `tasks/calibration_task.py:25` | `CalibrationTask` | class | low | `trading/paper_trader.py`, `main.py`, and multiple tests use it. False positive. | N/A — live |
| `tasks/calibration_task.py:32` | `CalibrationTask.record_calibration_check` | method | low | `trading/paper_trader.py` calls it. False positive. | N/A — live |
| `tasks/calibration_task.py:63` | `CalibrationTask.get_calibration_summary` | method | low | `trading/paper_trader.py` calls it. False positive. | N/A — live |
| `tasks/runtime_overrides_task.py:41` | `run_runtime_overrides_poll` | function | low | `main.py` calls it. False positive. | N/A — live |
| `tasks/structural_task.py:168` | `StructuralTask.run_periodic` | method | low | `main.py:1756` awaits it. False positive. | N/A — live |

---

## utils/

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `utils/logger.py:47` | `EVIDENCE_INGESTION_REQUIRED_FIELDS` | variable | low | Used in `tests/test_accumulation_log_schemas.py`. Test-facing schema constant. False positive. | N/A — live in tests |
| `utils/logger.py:58` | `DOSSIER_UPDATE_REQUIRED_FIELDS` | variable | low | Same. False positive. | N/A — live in tests |
| `utils/logger.py:72` | `STRUCTURAL_PRIOR_RECOMPUTE_REQUIRED_FIELDS` | variable | low | Same. False positive. | N/A — live in tests |
| `utils/logger.py:100` | `CALIBRATION_CHECK_REQUIRED_FIELDS` | variable | low | Same. False positive. | N/A — live in tests |
| `utils/logger.py:123` | `LOG_REPORTS_DIR` | variable | low | `main.py:1136` imports and uses it. False positive. | N/A — live |
| `utils/logger.py:201` | `TradeLogger.rotate` | method | medium | No grep hits outside the file and `tests/test_logger_rotation.py`. No direct production caller. Rotation triggered via module-level `rotate_logs()` function; this method may be called indirectly by it — confirm before removal. | Medium — removing without updating `rotate_logs()` would silently break log rotation. |
| `utils/logger.py:312` | `emit_startup_banner` | function | low | `main.py:1770` calls it. False positive. | N/A — live |
| `utils/logger.py:334` | `rotate_logs` | function | low | `main.py:96` imports and calls it. False positive. | N/A — live |
| `utils/logger.py:530–1245` | `log_signal`, `log_opportunity`, `log_paper_trade`, `log_paper_resolution`, `log_live_order`, `log_skipped`, `log_analysis_rejected`, `log_early_stale_drop`, `log_early_fresh_pass`, `log_position_drift`, `log_new_market`, `log_calibration_check` | methods | low | All called from `analysis/signal_analyzer.py`, `trading/paper_trader.py`, `trading/executor.py`, and/or `main.py`. False positives. | N/A — live |
| `utils/runtime_overrides.py:586` | `set_global_reader` | function | low | `main.py` calls it. False positive. | N/A — live |
| `utils/runtime_overrides.py:640` | `get_threshold_override` (module-level) | function | low | `main.py` calls it. False positive. | N/A — live |
| `utils/trade_log_reader.py:53` | `dirs` | variable | medium | Assigned but apparently not read after assignment in function scope. Vulture 60% confidence. | Low — local variable only; confirm by inspecting the function body. |

---

## scripts/

Scripts are standalone analysis/replay utilities. None are imported by production code. Stale script-local items are noted below; entire-script staleness is out of scope for this inventory.

| Path | Symbol | Kind | Confidence | Evidence | Risk |
|------|--------|------|------------|----------|------|
| `scripts/edge_replay/cycle15b_common.py` | (whole module) | module | medium | Only imported by other `scripts/edge_replay/` scripts (`sub_fix_selection.py`, `llm_prompt_audit.py`, `suppression_trace.py`, `keyword_direction_audit.py`). No test covers it directly. Cycle-15b replay work is several cycles behind current (17C). | Low — if the importing cycle-15b scripts are themselves archived, this becomes dead. Otherwise retain for historical replay fidelity. |
| `scripts/edge_replay/fetch_historical_prices.py:90` | `fetch_trade_prices` | function | **high** | No grep hit outside the file itself. Not imported or called anywhere in the repo. | Low — pure data-fetch utility; no side effects on live state. |
| `scripts/migrate_trade_logs.py:81` | `_holding_path` | function | **high** | No callers found anywhere in the repo. | Low — one-shot migration helper. If migration is complete the whole script may be stale. |
| `scripts/regime_weight_validation.py:169` | `_mean_calibration_error` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/regime_weight_validation.py:247` | `_fmt_weights` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/signal_edge_diagnostics.py:128` | `fmt_avg` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/signal_edge_diagnostics.py:134` | `fmt_median` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/ollama_error_audit.py:118` | `parse_log_line` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/paper_performance_drilldown.py:103` | `format_counter_lines` | function | **high** | No callers anywhere in the repo. | Low — dead script utility. |
| `scripts/simulations/resolution_calibration.py:75` | `record_calibration_check` | method | medium | No callers in the repo. Appears to duplicate `tasks/calibration_task.CalibrationTask` behavior as a simulation stub. | Medium — confirm against simulation usage pattern before removal. |
| `scripts/llm_eval.py:101` | `notes` | variable | medium | Assigned but not used in function scope. Script-local only. | Low — local script variable. |
| `scripts/performance_analysis.py:363` | `db_by_id` | variable | medium | Assigned but not read. Script-local dead variable. | Low — local script variable. |
| `scripts/reddit_source_audit.py:108,111` | `rej_disabled`, `rej_other` | variables | medium | Assigned but not read in function scope (lines 108,111). Also appear as class attributes (lines 235,241) where set but not consumed in any output path. | Low — script-local diagnostics. |
