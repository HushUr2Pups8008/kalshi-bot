# Python Code-Quality Review
**Date:** 2026-05-08  
**Scope:** `analysis/`, `feeds/`, `governance/`, `tasks/`, `kalshi/`, `utils/`, `main.py`, `config.py`  
**Tool baseline:** `ruff check` (selects `F` + `E9`) — **clean, zero findings**  
**Intentional patterns NOT flagged:** RSA-PSS signing, `_normalize_pem()`, websockets version-detect, `LocalQwenLLM think=False`, `MAX_BET_HARD_CAP`, `_backoff[x] = time.monotonic() + delay`, `os.path` workarounds with explicit Windows/Python-3.14 comments

---

## P0 — Correctness / Security

No P0 findings. Ruff (pyflakes + E9) is clean. No `eval`/`exec` misuse, no `yaml.unsafe_load`, no `subprocess(shell=True)` with user input, no SQL injection via f-strings, no hardcoded secret patterns detected.

---

## P1 — Maintainability Blockers

| File:Line | Issue | Recommendation |
|-----------|-------|----------------|
| `utils/logger.py:789` | `log_signal_analysis_detail` accepts **46 keyword-only parameters** (lines 789–837). Any caller change requires updating 46 named args; static analysis cannot catch positional drift. | Introduce a `SignalAnalysisDetail` `TypedDict` or `@dataclass` as the single parameter. Callers build the struct; the function takes one arg. |
| `feeds/subreddit_selector.py:136` | `_update_probe_ts`: `except Exception: pass` silently swallows SQLite write failures. Probe counts are lost without any diagnostic trace. | Narrow to `except (sqlite3.OperationalError, sqlite3.DatabaseError)` and add `logger.warning("probe ts update failed: %s", e, exc_info=True)`. `logger` is already available in this file. |
| `feeds/subreddit_selector.py:152` | `_mark_candidate_suppressed`: same silent-failure pattern. Suppression writes fail without trace, allowing suppressed subreddits to re-enter rotation. | Same fix: narrow exception + `logger.warning`. |
| `analysis/signal_analyzer.py:649` | `_ollama_estimate_detailed` is **223 lines** (lines 649–871). The function conflates retry orchestration, JSON extraction, and probability blending into a single body. | Extract: `_call_ollama_with_retry()` (HTTP + retry), `_extract_llm_json()` (JSON scan), and keep `_ollama_estimate_detailed` as a coordinator ≤ 60 lines. |
| `analysis/signal_analyzer.py:1001` | `estimate_probability` is **263 lines** (lines 1001–1263). The function handles LLM selection, keyword fallback, blending, and telemetry in one async body. | Extract keyword-gate path into `_keyword_fallback_estimate()` and LLM dispatch into `_run_llm_estimate()`. Main function becomes a ≤ 60-line coordinator. |
| `main.py:640` | `_process_candidate` is **207 lines** (lines 640–846). Mixes staleness checks, blending, safety gates, order placement, and trade logging. | Extract `_check_staleness()`, `_blend_and_gate()`, and `_submit_order()` helpers. Reduces cyclomatic complexity and simplifies test surface. |

---

## P2 — Type Annotation Gaps and Idiom Issues

| File:Line | Issue | Recommendation |
|-----------|-------|----------------|
| `analysis/signal_analyzer.py:395` | `keyword_estimate(…, keyword_stats=None)` — `keyword_stats` has no annotation. `KeywordStats` is importable from `analysis.keyword_stats` without creating a circular import. | Add `keyword_stats: Optional[KeywordStats] = None`. |
| `analysis/signal_analyzer.py:932` | `llm_estimate_detailed` — both `news` and `market` parameters and the return value are unannotated. `NewsItem` and `KalshiMarket` are already imported at module level. | Add `(news: NewsItem, market: KalshiMarket) -> tuple[float \| None, dict[str, Any]]`. |
| `analysis/signal_analyzer.py:994` | `llm_estimate` — both parameters and return unannotated. Same imports already present. | Add `(news: NewsItem, market: KalshiMarket) -> float \| None`. |
| `analysis/signal_analyzer.py:1001` | `estimate_probability` — `keyword_stats` parameter unannotated (same issue as line 395). | Add `keyword_stats: Optional[KeywordStats] = None`. |
| `main.py:640` | `_process_candidate` — `market` parameter unannotated. `KalshiMarket` is imported at module level. | Add `market: KalshiMarket` to the signature. |
| `main.py:1325` | `_log_maintenance_task` is **182 lines** (lines 1325–1506). Combines metric collection, formatting, and log emission. | Extract per-subsystem metric collectors as private helpers. |
| `main.py:1615` | `_run_startup_observability_probe` is **109 lines** (lines 1615–1723). | Extract probe-per-subsystem into named helpers. |
| `feeds/subreddit_selector.py:156` | `select_subreddits(…, source_stats=None, db_path: Path = None)` — two annotation issues: (a) `source_stats` has no type annotation; (b) `db_path: Path = None` is a **misleading annotation** — the declared type `Path` excludes `None` but the default is `None`. mypy would flag this as `Incompatible default for argument`. | Change to `source_stats: Optional[SourceStats] = None, db_path: Optional[Path] = None`. `SourceStats` is importable from `feeds.source_stats` (verify no circular dep). |
| `governance/decision.py:162` | `to_disabled_source(self)` — missing return annotation. The local import pattern (`from utils.runtime_overrides import DisabledSource`) prevents a module-level import but `from __future__ import annotations` is already present, enabling a `TYPE_CHECKING` guard. | Add `from __future__ import annotations` (already present) + `if TYPE_CHECKING: from utils.runtime_overrides import DisabledSource` at top; annotate `-> DisabledSource`. |
| `governance/decision.py:190` | `to_disabled_keyword(self)` — same missing return annotation. | Same fix: `-> DisabledKeyword` under `TYPE_CHECKING` guard. |
| `governance/decision.py:218` | `to_threshold_override(self)` — same. | Same fix: `-> ThresholdOverride`. |
| `governance/prompts.py:184` | `dump_prompt_revision(*, revision_label: str, out_dir)` — `out_dir` unannotated; no return annotation. `pathlib` and `datetime` are local imports (intentional for a constants module) but the function's interface should still be typed. | Add `out_dir: str \| Path` and `-> Path`. |
| `governance/evidence.py:142` | `compose_evidence_for_candidate(candidate, adapter)` — `adapter` unannotated (comment reads `# GovernanceAdapter`). `from __future__ import annotations` is already present. | Add `TYPE_CHECKING` guard: `if TYPE_CHECKING: from governance.adapter import GovernanceAdapter` and annotate `adapter: GovernanceAdapter`. |
| `utils/runtime_overrides.py:640` | `get_threshold_override(path: str)` — no return annotation. Returns `o.value` (typed `Any`) or `None` implicitly. | Add `-> Any \| None`. |
| `analysis/market_matcher.py:271` + `analysis/market_specificity.py:99` + `analysis/regime_classifier.py:152` | `_days_to_close` is **copied verbatim** across three modules. Each copy uses `except Exception: return None` — too broad; `dp.parse` raises `dateutil.parser.ParserError` (subclass of `ValueError`) and `OverflowError` on extreme dates. A bug fix in one copy will not propagate. | Move to a shared utility (e.g., `analysis/_date_utils.py`). Narrow the exception to `except (ValueError, OverflowError)`. |

---

## P3 — Style and Consistency

| File:Line | Issue | Recommendation |
|-----------|-------|----------------|
| `main.py:457` | `from pathlib import Path as _Path` inside a method body with comment "Path not imported at module level". The leading underscore alias is used only to signal "local import" — but this leaves every other method in the class importing `Path` ad-hoc or not at all. | Add `from pathlib import Path` to the module-level imports. The `_Path` alias at line 457 can be dropped. |
| `utils/reporting_helpers.py:84` | `is_trade_log_root_path(path: Path)` accepts a `Path` but immediately converts to `os.fspath(path)` and uses `os.path.*` for all checks. Other files using this pattern include explicit Windows/Python-3.14 comments; this file has none. Without the comment, future readers may "fix" it to pathlib and reintroduce the Windows regression. | Either add an explanatory comment (matching the pattern in `trade_log_reader.py:_is_legacy_root_file`) or confirm the rationale does not apply here and convert to `path.is_dir()` / `(path / "archive").exists()`. |

---

## Type Hint Coverage

Coverage is measured per public function (non-`_` prefix): a function is fully annotated when both the return type and all non-`self` parameters carry annotations.

| Package | Fully Annotated | Public Functions | Coverage |
|---------|-----------------|------------------|----------|
| `kalshi/` | 17 | 17 | **100%** |
| `tasks/` | 40 | 41 | **98%** |
| `utils/` | 68 | 69 | **99%** |
| `analysis/` | 47 | 51 | **92%** |
| `feeds/` | 12 | 13 | **92%** |
| `governance/` | 34 | 39 | **87%** |

The governance gap is primarily the three `to_*` methods on `Decision` (P2 above). The analysis gap is concentrated in `signal_analyzer.py`.

---

## Repo-Wide Patterns

- **`except Exception` as a catch-all** appears in four distinct places (`feeds/subreddit_selector.py` ×2, `analysis/market_matcher.py`, `analysis/market_specificity.py`, `analysis/regime_classifier.py`). Two of these silently swallow write failures (P1); three are in a duplicated `_days_to_close` utility where the broadness is unnecessary.

- **Duplicated private helper `_days_to_close`** exists verbatim in three analysis modules. This is the only code-duplication pattern found. A single shared `analysis/_date_utils.py` would eliminate drift risk.

- **Function-length distribution**: the longest functions are concentrated in two files (`signal_analyzer.py` and `main.py`). All other modules stay under the 50-line threshold. The hotspot is structurally contained.

- **`print()` in runtime paths**: none found outside intentional boundaries (logging handler `rotate()`, CLI entry points, `diagnostic_reporting_helpers.py` stdout report output). All runtime logic paths use `logging`.

- **Encoding compliance**: all `open()` calls in in-scope packages include `encoding="utf-8"` explicitly. No violations found.
