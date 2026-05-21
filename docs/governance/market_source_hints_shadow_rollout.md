# MarketSourceHints shadow rollout — operator diagnostics

This runbook covers the shadow-only MarketSourceHints layer. It is diagnostic support only: the layer preserves Kalshi public market rules/resolution/source metadata, derives source-target plans, and emits in-memory diagnostic counters/log-record dictionaries. It does not affect admission, readiness, execution, or trading behavior.

## Safety contract

MarketSourceHints is safe for shadow rollout when all of the following remain true:

- Config defaults are fail-closed: `MARKET_SOURCE_HINTS_MODE=off` and `MARKET_SOURCE_HINTS_EMIT_RECORDS=false` unless an operator explicitly enables diagnostics.
- The only approved diagnostic modes are `off`, `shadow`, and `advisory`; invalid or promotion-style modes must be rejected at config validation.
- `off` mode does not build source-target plans, does not call feed/search URL builders, and emits no records.
- `shadow` and `advisory` modes remain operator diagnostics only; they keep `shadow_only=True` and must not fabricate source-hint hits.
- Runtime diagnostic wiring is confined to the candidate analysis diagnostic path after status/price/staleness guards and before probability estimation; it logs/summarizes source-hint plans but never changes the candidate, estimate, blend, watch, execution, or trading path.
- With `MARKET_SOURCE_HINTS_EMIT_RECORDS=false`, runtime shadow/advisory mode builds only in-memory diagnostics plus app-log visibility; it does not append structured trade-log records.
- With `MARKET_SOURCE_HINTS_EMIT_RECORDS=true`, runtime emits `MARKET_SOURCE_HINT_DIAGNOSTIC` records containing plan summaries and any shadow log-record dictionaries, all with `shadow_only=True`; these records are diagnostic-only and not consumed by readiness/admission/scoring/routing/trading code.
- `MarketSourceHints.shadow_only` and `MarketSourceTargetPlan.shadow_only` are `True`.
- `SourceTargetCounters.log_records()` emits records with `shadow_only=True` and `type="MARKET_SOURCE_HINT_SHADOW"`.
- The default search path remains unchanged: building a source target plan must not change `_markets_to_queries` output.
- source-target plans do not poll. They only construct validated query/feed targets for a future consumer.
- Any future consumer must use the existing bounded, rate-limited, cached fetch mechanisms rather than adding an unbounded polling path.
- A fetch, cache, or rate-limit failure leaves zero source-hint hits, does not update readiness/admission/execution/trading state, and is treated like an empty diagnostic observation.
- Missing or unvalidated source hints fail closed to empty targets or rejected labels; they must never broaden search or create executable signals.
- Hints are source-targeting priors for evidence discovery only; settlement-source text is not treated as evidence or as a trading signal.
- No runtime service, credential, account/API, DB write, admission/readiness threshold, paper-trading, or live-trading behavior is enabled by this doc.

## Relevant tests

Run the targeted MarketSourceHints suite:

```bash
pytest tests/test_market_source_hints.py -q
```

Run the broader relevant regression set used for the shadow rollout:

```bash
.venv/bin/python -m pytest tests/test_market_source_hints.py tests/test_kalshi_normalizer_p0.py tests/test_main_pipeline.py -q
```

Runtime diagnostic wiring coverage is in `tests/test_main_pipeline.py`:

```bash
.venv/bin/python -m pytest \
  tests/test_main_pipeline.py::test_market_source_hint_runtime_default_off_is_noop \
  tests/test_main_pipeline.py::test_market_source_hint_runtime_shadow_builds_in_memory_only \
  tests/test_main_pipeline.py::test_market_source_hint_runtime_advisory_emits_shadow_only_record_when_enabled \
  tests/test_main_pipeline.py::test_market_source_hint_runtime_failure_does_not_block_candidate \
  tests/test_paper_trader.py::TestMarketSourceHintsReportSection \
  tests/test_market_source_hints_diagnostics.py \
  -q
```

The operator-doc coverage test is:

```bash
.venv/bin/python -m pytest tests/test_market_source_hints.py::test_operator_shadow_rollout_doc_covers_diagnostics_and_safety_contract -q
```

## Diagnostic fields

Per-market source target plans expose:

- `ticker`: market ticker attached to the shadow plan.
- `shadow_only`: must be `True`; this is the primary safety flag.
- `targets`: validated source-specific search/feed targets.
- `rejected_labels`: labels rejected as unsafe targeting priors, such as photo credits, generic categories, or unvalidated local mastheads.

Each target includes:

- `source.canonical_name`: canonical source, e.g. Reuters or Associated Press.
- `source.domain`: domain used for `site:`-scoped query construction.
- `source.source_class`: `wire`, `national_publisher`, `official_government`, or `local_masthead`.
- `search_queries`: source-scoped queries such as `site:reuters.com "<market title>"`.
- `feed_urls`: optional feed/search URLs derived from query builders; empty is acceptable in pure planning mode.

`SourceTargetCounters.snapshot()` returns per-source counters:

- `hits`: number of observed query/feed results for that source.
- `misses`: number of no-hit observations for that source.
- `freshest_age_seconds`: minimum observed age for hit results, or `None` if no fresh hit age has been recorded.

`SourceTargetCounters.log_records()` returns dictionaries intended for operator diagnostics:

- `type`: `MARKET_SOURCE_HINT_SHADOW`.
- `ticker`: market ticker.
- `source`: canonical source name.
- `domain`: source domain.
- `hit`: whether the source-target query/feed observation hit.
- `freshness_age_seconds`: observed age for hit results, or `None`.
- `shadow_only`: must be `True`.

## Daily report section

`python main.py --report` includes a read-only `MARKET SOURCE HINTS` section after `MATCH QUALITY` when it can read `trades.jsonl`:

- It scans only `MARKET_SOURCE_HINT_DIAGNOSTIC` records.
- It summarizes diagnostic record count, shadow-only count, observed modes, top hinted sources, top tickers, rejected labels, and child `MARKET_SOURCE_HINT_SHADOW` records.
- It ignores malformed lines and unrelated record types.
- It is diagnostic-only and not consumed by readiness, admission, scoring, routing, execution, paper trading, or live trading.

## Detailed diagnostic CLI

For a deeper read-only analysis of emitted records, run:

```bash
.venv/bin/python scripts/market_source_hints_diagnostics.py --path logs/trades/live/trades.jsonl --exclude-test
.venv/bin/python scripts/market_source_hints_diagnostics.py --path logs/trades/live/trades.jsonl --exclude-test --json
.venv/bin/python scripts/market_source_hints_diagnostics.py --path logs/trades/live/trades.jsonl --exclude-test --bucket safety_anomaly
.venv/bin/python scripts/market_source_hints_diagnostics.py --path logs/trades/live/trades.jsonl --exclude-test --json --bucket rejected_source_labels_present
```

The script scans only `MARKET_SOURCE_HINT_DIAGNOSTIC` records and reports lines scanned, malformed lines skipped, diagnostic/shadow-only counts, non-shadow safety warnings, target/rejection counts, observed modes, hinted sources/domains, tickers, rejected-label reasons, and recent examples. It uses the shared trade-log reader, supports `--since`, `--until`, `--top`, `--recent`, `--exclude-test`, `--json`, and `--bucket`, and remains read-only.

`--json` emits a schema-versioned machine-readable payload for dashboards or archived review artifacts. The payload includes `diagnostic_only: true` and `non_consumption: "not consumed by readiness/admission/scoring/routing/trading"`; it must not be used as a runtime input to readiness, admission, scoring, routing, execution, paper trading, or live trading.

`--bucket` filters only the recent examples section/payload to one operator review bucket. Aggregate counts and bucket totals remain unchanged, so bucket filtering is a review aid rather than a data transformation or behavioral selector.

The CLI also prints operator review buckets. These are deterministic summaries for human review only and are not consumed by readiness, admission, scoring, routing, execution, paper trading, or live trading:

- `healthy_shadow_signal`: shadow-only records with validated source targets and no rejected labels.
- `no_validated_source_hints`: shadow-only records that produced no validated source targets.
- `rejected_source_labels_present`: records with rejected source labels that may need metadata/extraction review.
- `safety_anomaly`: non-shadow MarketSourceHints diagnostic records, surfaced as warnings only.
- `low_coverage`: records with no validated source targets.

## What proves shadow mode is safe

Evidence is sufficient for operator review when:

1. The targeted test suite passes.
2. The broader regression command passes.
3. The default-path guard passes, especially the assertion that `_markets_to_queries([market])` remains unchanged after calling `build_market_source_target_plan(market)`.
4. Runtime diagnostics remain default-off. When enabled, failures are logged and ignored, `shadow` stays app-log/in-memory only unless record emission is explicitly enabled, and emitted `MARKET_SOURCE_HINT_DIAGNOSTIC` records are not consumed by behavioral paths.
5. Counter/log records are in-memory diagnostics only and are not wired into admission, readiness, execution, order placement, cancellation, or trading state transitions.
6. Rejected labels remain visible via `rejected_labels` so operators can identify unsafe or unvalidated source text without silently using it.

## Operator interpretation

- High `hits` with low `freshest_age_seconds` means the listed settlement/source prior can find fresh public evidence candidates.
- High `misses` means the source-targeting prior did not currently find usable candidates; it is not an admission failure by itself.
- `rejected_labels` are expected for generic source categories and photo/wire credits; rejection is a safety feature, not a runtime error.
- Missing targets for a market means no validated source priors were extracted from preserved metadata; the existing title-token/global search path remains the active behavior.
