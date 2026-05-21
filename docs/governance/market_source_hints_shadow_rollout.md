# MarketSourceHints shadow rollout — operator diagnostics

This runbook covers the shadow-only MarketSourceHints layer. It is diagnostic support only: the layer preserves Kalshi public market rules/resolution/source metadata, derives source-target plans, and emits in-memory diagnostic counters/log-record dictionaries. It does not affect admission, readiness, execution, or trading behavior.

## Safety contract

MarketSourceHints is safe for shadow rollout when all of the following remain true:

- Config defaults are fail-closed: `MARKET_SOURCE_HINTS_MODE=off` and `MARKET_SOURCE_HINTS_EMIT_RECORDS=false` unless an operator explicitly enables diagnostics.
- The only approved diagnostic modes are `off`, `shadow`, and `advisory`; invalid or promotion-style modes must be rejected at config validation.
- `off` mode does not build source-target plans, does not call feed/search URL builders, and emits no records.
- `shadow` and `advisory` modes remain operator diagnostics only; they keep `shadow_only=True` and must not fabricate source-hint hits.
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

## What proves shadow mode is safe

Evidence is sufficient for operator review when:

1. The targeted test suite passes.
2. The broader regression command passes.
3. The default-path guard passes, especially the assertion that `_markets_to_queries([market])` remains unchanged after calling `build_market_source_target_plan(market)`.
4. Counter/log records are in-memory diagnostics only and are not wired into admission, readiness, execution, order placement, cancellation, or trading state transitions.
5. Rejected labels remain visible via `rejected_labels` so operators can identify unsafe or unvalidated source text without silently using it.

## Operator interpretation

- High `hits` with low `freshest_age_seconds` means the listed settlement/source prior can find fresh public evidence candidates.
- High `misses` means the source-targeting prior did not currently find usable candidates; it is not an admission failure by itself.
- `rejected_labels` are expected for generic source categories and photo/wire credits; rejection is a safety feature, not a runtime error.
- Missing targets for a market means no validated source priors were extracted from preserved metadata; the existing title-token/global search path remains the active behavior.
