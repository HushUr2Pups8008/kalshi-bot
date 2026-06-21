# Market-First Fresh-Pass Signal Assignment Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Verdict

We have partial infrastructure for a market-first retrieval path, but the first draft was not safe to execute. The corrected direction is not "make every fresh pass executable." It is:

1. Preserve the full Kalshi market + series contract surface.
2. Extend the existing `kalshi/source_hints.py` shadow-only targeting layer.
3. Collect shadow assignment evidence without diluting executable trade logs.
4. Audit assignment precision and known-lost edge recovery before any behavior change.

Existing pieces:
- `kalshi/rest_client.py` already paginates `GET /markets`, filters by `series_ticker`, and fetches `GET /markets/{ticker}`.
- `kalshi/normalizer.py` preserves string fields whose names include `rules`, `source`, `resolution`, or `settlement` in `KalshiMarket.market_metadata`.
- `analysis/market_matcher.py` already builds an active market universe from Kalshi series and open markets.
- `feeds/search_news_monitor.py` already derives bounded targeted searches from active market titles.
- `kalshi/source_hints.py` already owns the shadow-only settlement-source targeting surface. Extend it; do not create a parallel query-planner module.
- Match, suppression, replay, and daily diagnostics already exist for downstream analysis.

Missing pieces:
- No typed series model or `get_series(series_ticker)` client path that preserves `settlement_sources`, `contract_url`, `contract_terms_url`, tags, category, update timestamp, `fee_multiplier`, `fee_type`, and `can_close_early`.
- No durable market/series metadata snapshot for replayable drift checks.
- Current targeted retrieval starts from market title tokens, not the full Kalshi contract surface: market rules plus series settlement sources.
- Fresh-item candidate assignment still uses existing matcher semantics and returns `(market, score, match_meta)` tuples; any shadow writer must unpack that shape explicitly.
- Replay tooling cannot audit unobserved retrieval/candidate-assignment counterfactuals because those candidates are not captured.
- No precision-labeled assignment audit dataset exists for fresh-item to ticker assignment.

Official Kalshi API facts used for this plan:
- `GET /markets` and `GET /markets/{ticker}` expose market tickers, title/subtitle, status, price data, and settlement/rules fields such as `rules_primary` and `rules_secondary`.
- `GET /series/{series_ticker}` exposes series metadata including `settlement_sources`, `contract_url`, `contract_terms_url`, tags, category, and update timestamp.
- The API gives official settlement metadata. It does not provide a complete retrieval, assignment, or precision-audit system.

## Files

- Modify: `kalshi/__init__.py`
- Modify: `kalshi/rest_client.py`
- Modify: `kalshi/normalizer.py`
- Create: `kalshi/series_metadata.py`
- Modify: `kalshi/source_hints.py`
- Modify: `analysis/market_matcher.py`
- Create: `analysis/candidate_assignment_shadow.py`
- Modify: `main.py`
- Create: `tasks/market_metadata_snapshot.py`
- Create: `scripts/market_first_assignment_audit.py`
- Create: `tests/test_series_metadata.py`
- Create: `tests/test_source_hints_market_first_queries.py`
- Create: `tests/test_candidate_assignment_shadow.py`
- Create: `tests/test_market_first_assignment_audit.py`
- Modify: `docs/profit_path_debt_log.md`

## Implementation Steps

- [x] **Step 1: Add typed series metadata**

  Create `kalshi/series_metadata.py`:

  ```python
  from __future__ import annotations

  from dataclasses import dataclass, field
  from typing import Any


  @dataclass(frozen=True)
  class SettlementSource:
      name: str = ""
      url: str = ""


  @dataclass(frozen=True)
  class KalshiSeriesMetadata:
      ticker: str
      title: str = ""
      category: str = ""
      tags: tuple[str, ...] = field(default_factory=tuple)
      settlement_sources: tuple[SettlementSource, ...] = field(default_factory=tuple)
      contract_url: str = ""
      contract_terms_url: str = ""
      last_updated_ts: str = ""
      fee_multiplier: str = ""
      fee_type: str = ""
      can_close_early: bool | None = None
      raw_payload: dict[str, Any] = field(default_factory=dict, compare=False)


  def normalize_series_metadata(payload: dict[str, Any]) -> KalshiSeriesMetadata:
      sources = tuple(
          SettlementSource(
              name=str(source.get("name") or ""),
              url=str(source.get("url") or ""),
          )
          for source in payload.get("settlement_sources", []) or []
          if isinstance(source, dict)
      )
      return KalshiSeriesMetadata(
          ticker=str(payload.get("ticker") or ""),
          title=str(payload.get("title") or ""),
          category=str(payload.get("category") or ""),
          tags=tuple(str(tag) for tag in payload.get("tags", []) or []),
          settlement_sources=sources,
          contract_url=str(payload.get("contract_url") or ""),
          contract_terms_url=str(payload.get("contract_terms_url") or ""),
          last_updated_ts=str(payload.get("last_updated_ts") or ""),
          fee_multiplier=str(payload.get("fee_multiplier") or ""),
          fee_type=str(payload.get("fee_type") or ""),
          can_close_early=payload.get("can_close_early")
          if isinstance(payload.get("can_close_early"), bool)
          else None,
          raw_payload=dict(payload),
      )
  ```

- [x] **Step 2: Test series metadata against real-field shape**

  Add `tests/test_series_metadata.py`:

  ```python
  from kalshi.series_metadata import normalize_series_metadata


  def test_normalize_series_metadata_preserves_settlement_sources_and_terms_url():
      series = normalize_series_metadata(
          {
              "ticker": "KXTRUMPIRAN",
              "title": "Trump Iran",
              "category": "Politics",
              "tags": ["Iran", "Trump"],
              "settlement_sources": [
                  {"name": "The Associated Press", "url": "https://apnews.com/"}
              ],
              "contract_url": "https://kalshi.com/markets/KXTRUMPIRAN",
              "contract_terms_url": "https://kalshi.com/markets/KXTRUMPIRAN/terms",
              "last_updated_ts": "2026-05-11T00:00:00Z",
              "fee_multiplier": "1",
              "fee_type": "quadratic",
              "can_close_early": True,
          }
      )

      assert series.ticker == "KXTRUMPIRAN"
      assert series.tags == ("Iran", "Trump")
      assert series.settlement_sources[0].name == "The Associated Press"
      assert series.contract_terms_url.endswith("/terms")
      assert series.can_close_early is True
  ```

- [x] **Step 3: Add `KalshiRestClient.get_series()` and cache cadence**

  Modify `kalshi/rest_client.py`:

  ```python
  from kalshi.series_metadata import KalshiSeriesMetadata, normalize_series_metadata

  def get_series(self, series_ticker: str) -> KalshiSeriesMetadata | None:
      try:
          data = self._request("GET", f"/series/{series_ticker}")
      except Exception as exc:
          log.warning("get_series(%s) failed: %s", series_ticker, exc)
          return None
      payload = data.get("series") or {}
      if not isinstance(payload, dict):
          log.warning("get_series(%s) returned malformed series payload", series_ticker)
          return None
      return normalize_series_metadata(payload)
  ```

  Do not call this once per fresh item. Fetch at most once per series per market-cache refresh. Before adding detail fetches, inspect whether `get_all_series()` list payloads already carry `settlement_sources`; if they do, hydrate from the list response and avoid detail calls.

- [x] **Step 4: Preserve explicit market rules without duplicating text**

  Update `KalshiMarket` in `kalshi/__init__.py`:

  ```python
  rules_primary: str = ""
  rules_secondary: str = ""
  settlement_timer_seconds: int | None = None
  early_close_condition: str = ""
  expected_expiration_time: str = ""
  expiration_time: str = ""
  ```

  Update `_build_market()` in `kalshi/normalizer.py` to populate these fields. When later building hint text, do not join `rules_*` twice from both typed fields and `market_metadata`.

- [x] **Step 5: Extend `kalshi/source_hints.py` instead of creating parallel planner modules**

  Add a `MarketContractContext` dataclass to `kalshi/source_hints.py`:

  ```python
  @dataclass(frozen=True)
  class MarketContractContext:
      market_ticker: str
      series_ticker: str
      market_title: str
      series_title: str = ""
      rules_text: str = ""
      tags: tuple[str, ...] = field(default_factory=tuple)
      settlement_sources: tuple[SourceHint, ...] = field(default_factory=tuple)
      contract_terms_url: str = ""
  ```

  Add a builder that accepts a `KalshiMarket` plus optional `KalshiSeriesMetadata`, canonicalizes settlement source labels through the existing registry, and rejects generic/self-referential sources:

  ```python
  _PLACEHOLDER_SOURCE_DOMAINS = {"kalshi.com"}


  def build_market_contract_context(
      market: object,
      series: object | None,
      *,
      registry: SourceRegistry | None = None,
  ) -> MarketContractContext:
      registry = registry or SourceRegistry.default()
      metadata = getattr(market, "market_metadata", {}) or {}
      typed_rules = [
          getattr(market, "rules_primary", "") or "",
          getattr(market, "rules_secondary", "") or "",
      ]
      metadata_rules = [
          value
          for key, value in metadata.items()
          if not key.lower().startswith(("rules_", "settlement_"))
      ]
      hints: list[SourceHint] = []
      for source in getattr(series, "settlement_sources", ()) or ():
          hint = registry.lookup(getattr(source, "name", "") or "")
          if hint and hint.domain not in _PLACEHOLDER_SOURCE_DOMAINS:
              hints.append(hint)
      return MarketContractContext(
          market_ticker=getattr(market, "ticker"),
          series_ticker=getattr(market, "series_ticker", ""),
          market_title=getattr(market, "title", ""),
          series_title=getattr(series, "title", "") if series else "",
          rules_text="\n".join(part for part in (*typed_rules, *metadata_rules) if part),
          tags=tuple(getattr(series, "tags", ()) or ()) if series else (),
          settlement_sources=tuple(dict.fromkeys(hints)),
          contract_terms_url=getattr(series, "contract_terms_url", "") if series else "",
      )
  ```

- [x] **Step 6: Redesign market-first queries around entity/topic terms and existing budgets**

  Keep query construction in `kalshi/source_hints.py`. Do not emit `site:{domain} "{full market question}"` as the primary query. It is too exact for real generic-publisher settlement sources.

  Add:

  ```python
  def build_market_first_queries(
      context: MarketContractContext,
      *,
      max_queries: int,
  ) -> tuple[str, ...]:
      terms = _tokenize_query_terms(
          " ".join(
              part
              for part in (
                  context.series_title,
                  context.market_title,
                  " ".join(context.tags),
                  context.rules_text,
              )
              if part
          )
      )
      topic = " ".join(terms[:4])
      queries: list[str] = []
      for hint in context.settlement_sources:
          if len(queries) >= max_queries:
              break
          queries.append(f"site:{hint.domain} {topic}")
      if not queries and topic:
          queries.append(topic)
      return tuple(queries)
  ```

  `_tokenize_query_terms` should reuse the same stop-word/off-topic style already used by `feeds/search_news_monitor.py`, or import a shared helper after extracting it. The test fixture should use a real generic-publisher shape such as tags `("Iran", "Trump")` and `The Associated Press`, not a fabricated government-clerk source.

- [x] **Step 7: Wire market-first query planning only as default-off shadow input**

  Modify `feeds/search_news_monitor.py` or its caller so market-first query candidates can be included only when `ENABLE_MARKET_FIRST_QUERY_SHADOW=true`.

  The shadow query producer must:
  - Use `SEARCH_MAX_QUERIES` as a hard total budget, not per-source budget.
  - Prefer proven edge series before broad series.
  - Skip `kalshi.com` placeholder sources.
  - Log query basis separately from current title-derived queries.
  - Never route these results directly to executable signal rows.

- [x] **Step 8: Add shadow assignment records with explicit tuple unpacking**

  Create `analysis/candidate_assignment_shadow.py`:

  ```python
  from __future__ import annotations

  from dataclasses import dataclass
  from typing import Any

  from analysis.market_matcher import MarketMatcher
  from feeds import NewsItem
  from kalshi import KalshiMarket


  @dataclass(frozen=True)
  class ShadowAssignment:
      headline: str
      source: str
      candidate_count: int
      top_ticker: str
      top_score: float | None
      assigned: bool
      malformed: bool = False
      malformed_reason: str = ""
      assignment_mode: str = "shadow"


  async def assign_fresh_item_shadow(
      matcher: MarketMatcher,
      item: NewsItem,
  ) -> ShadowAssignment:
      candidates = await matcher.find_candidates(item)
      if not candidates:
          return ShadowAssignment(
              headline=item.headline,
              source=item.source,
              candidate_count=0,
              top_ticker="",
              top_score=None,
              assigned=False,
          )

      try:
          top_market, top_score, _match_meta = _unpack_candidate(candidates[0])
      except (TypeError, ValueError) as exc:
          return ShadowAssignment(
              headline=item.headline,
              source=item.source,
              candidate_count=len(candidates),
              top_ticker="",
              top_score=None,
              assigned=False,
              malformed=True,
              malformed_reason=str(exc),
          )
      else:
          return ShadowAssignment(
              headline=item.headline,
              source=item.source,
              candidate_count=len(candidates),
              top_ticker=top_market.ticker,
              top_score=float(top_score),
              assigned=True,
          )


  def _unpack_candidate(
      candidate: tuple[KalshiMarket, float, dict[str, Any]],
  ) -> tuple[KalshiMarket, float, dict[str, Any]]:
      market, score, match_meta = candidate
      if not market.ticker:
          raise ValueError("shadow assignment candidate missing ticker")
      return market, score, match_meta
  ```

  No `getattr(..., default)` masking on tuple/object shape. Shape errors must fail tests and surface in logs.
  Gate this path separately from query-shadow mode: add `ENABLE_FRESH_PASS_ASSIGNMENT_SHADOW=false` as the default, and do not run `assign_fresh_item_shadow()` unless the operator explicitly enables it.

- [x] **Step 9: Emit shadow rows to a partitioned shadow log**

  Do not dilute `logs/trades/live/trades.jsonl`. Add a dedicated writer for `logs/trades/shadow/fresh_pass_assignment_shadow.jsonl` or equivalent partition under the output-path contract.

  Each row must include:
  - `type="FRESH_PASS_ASSIGNMENT_SHADOW"`
  - `headline`
  - `source`
  - `candidate_count`
  - `top_ticker`
  - `top_score`
  - `assigned`
  - `malformed`
  - `malformed_reason`
  - `query_basis` when produced from market-first query shadow mode

  Consumer-loop handling: exceptions from `matcher.find_candidates()` itself should be logged and skipped so the ingest loop stays alive. Candidate-shape errors after a candidates list is returned should emit `malformed=true` rows as shown above, so the audit can count them instead of silently losing the defect.

- [x] **Step 10: Add assignment audit with false-clean guards**

  Create `scripts/market_first_assignment_audit.py` that reads only `logs/trades/shadow/` by default and reports:

  ```python
  {
      "shadow_rows": 0,
      "assigned_rows": 0,
      "rows_assigned_without_ticker": 0,
      "malformed_rows": 0,
      "assignment_rate": None,
      "top_tickers": [],
  }
  ```

  Failing conditions:
  - `assigned=true` with blank `top_ticker`
  - `top_score` missing for assigned rows
  - malformed rows present unless explicitly allowed by `--allow-malformed`

  Add a known-match test where a fresh item matches a market and the audit proves `top_ticker` is non-empty.

- [x] **Step 11: Capture suppressed candidates or narrow the gate language**

  `find_candidates()` returns post-suppression survivors. If the plan needs to measure suppression false positives, add a parallel diagnostic path that records suppressed candidates, or read existing `MATCH_SUPPRESSED` rows into the audit.

  If no suppressed-candidate capture is added, state the acceptance gate as:

  - "Shadow assignment must not regress existing suppression diagnostics."

  Do not claim the shadow assignment corpus measures suppression false-positive rate unless suppressed candidates are included.

- [x] **Step 12: Put durable metadata snapshots in `tasks/`**

  Create `tasks/market_metadata_snapshot.py`, not a writer in `analysis/`.

  The snapshot task should:
  - Fetch series metadata at most once per series refresh.
  - Store snapshots under `logs/state/derived/market_metadata_snapshot.json` or another existing derived-state path.
  - Include payload hash and `last_updated_ts` for drift detection.
  - Keep `analysis/` functions pure.

- [x] **Step 13: Use realistic acceptance gates**

  Before behavior change:

  - Shadow rows collected for at least 7 calendar days or at least 1,000 fresh passes, whichever comes first.
  - Assignment precision manually audited on a stratified sample of at least 200 shadow rows, including edge-series, generic-publisher source, and no-candidate strata.
  - Malformed rows are zero.
  - Rows assigned without ticker are zero.
  - Existing match-suppression diagnostics are not degraded.
  - Query budget stays within `SEARCH_MAX_QUERIES`.
  - Any Replay-EV result is explicitly labeled best-effort because un-fetched counterfactual news cannot be literally replayed.
  - Operator approval remains the dominant gate for any transition from shadow rows to executable signal rows.

## Non-Goals

- Do not loosen freshness gates.
- Do not lower readiness, G1/G2/G4, or paper/live safety gates.
- Do not make every fresh item executable.
- Do not treat high `assigned_count` or high `assignment_rate` as success.
- Do not route match score, source presence, or query basis into probability or EV math.
- Do not create parallel source-targeting modules when `kalshi/source_hints.py` already owns the shadow-only targeting surface.
- Do not treat absence of Kalshi settlement-source metadata as permission to broaden generic search.
- Do not use this plan to create live or paper orders without operator approval.

## Self-Review

- Spec coverage: The corrected plan answers whether infrastructure exists, identifies present and missing pieces, starts from Kalshi API metadata, and keeps all behavior-changing work shadow-only until precision and operator gates pass.
- Review reconciliation: The plan now fixes the `find_candidates()` tuple-shape bug, avoids exact full-title site queries, extends `source_hints.py` instead of duplicating it, moves persistence to `tasks/`, routes shadow rows to a shadow log, and makes audit false-clean states explicit failures.
- Placeholder scan: No placeholder markers remain.
