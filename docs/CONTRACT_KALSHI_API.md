# Kalshi API contract — observed and depended-upon behavior

This document promotes the Kalshi-API hard-won-knowledge entries from
`CLAUDE.md` into a normative contract the bot depends on. **Every claim
here is paired with a pinned test, named in §6.** If any claim is later
falsified by Kalshi-side behavior, the failing test surfaces the
regression at PR time instead of producing a silent zero-trade incident.

The contract documents what we have observed Kalshi to do, and what the
bot is engineered to depend on. It is not a reproduction of Kalshi's
public API specification — it is the subset our runtime treats as
load-bearing. If Kalshi's behavior diverges from any item here, fix the
bot or revise this document; do not silently accept the drift.

Scope: `/markets` listing endpoint of the Kalshi v1 REST API and its
cursor-paginated response. Other endpoints (`/series`, `/markets/{ticker}`,
WebSocket subscriptions) are covered only where they intersect.

---

## §1 Request contract — `GET /markets`

**Status filter.** The `status` query parameter accepts the set `{"open",
"closed", "settled", ...}`. The value `"active"` is rejected with
`400 bad_request "invalid status filter"`. The bot must send
`status="open"` to enumerate tradeable markets. This was misread by the
v0.30.0 P-7 packet, which shipped `status="active"` and produced a
2726-error 400 storm in production within ~4 minutes of restart. The
hotfix `!14` restored `status="open"` and is the operative behavior on
`v0.30.1+`.

**Cursor.** Pagination is cursor-token-based. Pass `cursor=<token>` to
fetch the next page. The response includes a `cursor` field that is
either a non-empty string (more pages exist) or null/empty (the cursor
is exhausted). The bot must terminate pagination when the response
cursor is null, and must not assume a fixed-page-count truncation is
safe.

**Limit.** The `limit` query parameter caps the rows per page. The bot
uses `limit=200`. Kalshi may return fewer than the requested limit on
the final page; this is normal and not a contract violation.

**Targeted series fetch.** `series_ticker=<prefix>` filters the result
to markets whose series prefix matches. This is the only way to reach a
specific family independently of global response ordering. Used by
`_fetch_geo_markets` to fetch each geo/policy series after the catalog
discovery pass.

**Time-window filters.** `min_close_ts` and `max_close_ts` further
restrict the result to markets closing in a specified window. The bot
does not currently use these but they are part of the request surface.

---

## §2 Response contract

**Status field — distinct from the request filter.** Each market in
the response includes a `status` field that names the live tradeable
state. The bot has observed only `"active"` and `"finalized"` in
practice; `"active"` markets are tradeable, `"finalized"` markets have
resolved. **Do not conflate the request-side `status="open"` parameter
with the response-side `status="active"` field.** They are two
different vocabularies for two different stages of the market
lifecycle.

**Required fields the bot depends on.** `ticker`, `series_ticker`,
`title`, `close_time`, `status`, plus the cents-denominated price
fields after the v0.30 P-2 normalization (`yes_bid_cents`,
`yes_ask_cents`, `no_bid_cents`, `no_ask_cents`,
`last_price_cents`, `executed_price_cents`). The full normalization
contract lives in `kalshi/normalizer.py`.

**Optional/empty fields.** `series_ticker` can be empty even when the
market ticker has a recognizable prefix. `_series_prior` in
`analysis/regime_classifier.py` consults `series_ticker` first then
falls back to the market ticker — the bot must not assume either
field carries the full identity.

---

## §3 Cursor pagination semantics

**Cursor exhaustion is the only safe termination condition.** A
caller that stops before cursor exhaustion is operating on a partial
prefix of the universe. Whether that prefix is sufficient depends on
Kalshi's response ordering (see §4) and is **not** a property the
caller can assert.

**Cursor opacity.** The cursor token is an opaque protobuf-encoded
blob. Callers must treat it as a black-box string and pass it
verbatim. The bot must not introspect, edit, or persist cursor
tokens.

**Safety caps.** The bot enforces three independent caps on full-
universe walks (in `analysis/market_matcher.py`):
`_FETCH_MAX_PAGES=1000`, `_FETCH_MAX_ROWS=200_000`,
`_FETCH_TIMEOUT_SECONDS=60.0`. **Halting on any cap before cursor
exhaustion is an event, not a normal outcome:** the bot emits a
WARNING naming the cap, and the resulting cache is a Kalshi-order-
dependent prefix that cannot guarantee family coverage.

---

## §4 Response ordering is not a stable contract

**Kalshi does not document a stable ordering for `/markets` responses,
and observed behavior has shifted over time.** On 2026-05-24 the
first 1000 pages (200,000 rows) of an unfiltered `status="open"`
walk consisted entirely of sports markets in 32 distinct families
(99.9% `KXMVESPORTSMULTIGAMEEXTENDED` + `KXMVECROSSCATEGORY`). Policy
markets — `KXCPIYOY` (69 open), `KXMOCTRUMP25` (3 open),
`KXFISAEXTEND` (7 open), `KXTRUMPACT` (8 open), `KXAPRPOTUS` (8 open)
— existed in the catalog and were fetchable via `series_ticker=`
queries, but never appeared in the unfiltered walk within the safety
cap.

**The bot must not depend on response order.** Specifically:

- The bot may walk `/markets` to cursor exhaustion within safety
  caps for downstream callers that need a sample of the universe
  (currently only the fade signal in `_fetch_all_markets`).
- The bot must **not** assume the walk reached any specific family,
  series, or expiry bucket without explicit verification.
- For any family the bot cares about specifically (policy/macro/
  legislative), the bot must fetch it via `series_ticker=` query in
  `_fetch_geo_markets` rather than relying on the global walk.
- The bot emits a runtime WARNING if a family in
  `_EXPECTED_POLICY_SERIES` is present in the Kalshi series catalog
  but produces zero markets in the geo cache — operator-visible
  signal that the contract assumption may need revisiting.

---

## §5 Pagination invariants the bot enforces

These are derived from §3 and §4 above.

1. **Cursor-complete or capped, never silent.** The bot's pagination
   either runs to cursor exhaustion or emits a WARNING naming the
   cap that halted it.
2. **Targeted queries for required families.** Any family in
   `_EXPECTED_POLICY_SERIES` must reach the cache via a per-series
   query, not the global walk.
3. **Coverage warning is mandatory.** When the Kalshi catalog
   advertises an expected family but the cache contains zero
   markets in that family, the bot emits an operator-visible
   WARNING. False-alarm-safe: a family is only flagged when Kalshi
   advertises it.
4. **Structured log per fetch.** Every full-universe walk emits a
   single INFO line with `pages_fetched`, `markets_seen`,
   `cursor_exhausted`, `cap_reached`, `elapsed`. Operators and
   downstream tooling can grep these for triage.

---

## §6 Test pins — where each claim is locked

| Claim | Test |
|---|---|
| §1 Request sends `status="open"` (not `"active"`) | `tests/test_market_matcher.py::TestKalshiMarketsRequestFilterContract::test_fetch_geo_markets_sends_request_status_open` |
| §1 Cursor exhaustion terminates pagination | `tests/test_market_matcher.py::TestFetchAllMarketsPaginationContract::test_terminates_on_cursor_exhaustion` |
| §2 Response `status="active"` is the tradeable predicate | `tests/test_market_matcher.py::TestKalshiMarketsRequestFilterContract` (response-field sibling tests) |
| §3 Caps emit operator-visible warnings | `tests/test_market_matcher.py::TestFetchAllMarketsPaginationContract::test_max_pages_cap_emits_operator_visible_warning`, `::test_max_rows_cap_emits_operator_visible_warning` |
| §4 Response ordering not depended on (sports-first regression) | `tests/test_market_matcher.py::TestFetchAllMarketsPaginationContract::test_sports_first_ordering_reaches_policy_markets_after_page_10` |
| §5.2 Targeted queries reach expected families | `tests/test_market_matcher.py::TestExpectedPolicyFamilyCoverage::test_no_warning_when_all_expected_families_present_in_intake` |
| §5.3 Coverage warning fires on missing family | `tests/test_market_matcher.py::TestExpectedPolicyFamilyCoverage::test_warning_fires_when_catalog_lists_family_but_intake_drops_it` |
| §5.3 Coverage warning is false-alarm-safe | `tests/test_market_matcher.py::TestExpectedPolicyFamilyCoverage::test_no_warning_when_family_genuinely_retired_from_catalog` |
| §5.4 Structured INFO emits on fetch | covered inside `test_sports_first_ordering_reaches_policy_markets_after_page_10` (asserts `pages_fetched`, `cursor_exhausted`, `cap_reached`) |

Adding a new claim to the contract requires a paired test or a one-line
note explaining why a test is not feasible. A claim without a test pin
is documentation, not a contract.

---

## §7 Historical incidents this contract closes

- **PROFIT-API-001 / v0.30.0 P-7 status-filter regression.** Shipped
  `status="active"` as the request parameter. Kalshi rejected with
  400 storm. Fixed by hotfix `!14` (`1b0f441`). Locked by §1 tests.
- **2026-05-12 → 2026-05-24 zero-trade collapse.** `_fetch_all_markets`
  silently capped at 10 pages × 200 = 2000 rows. Once Kalshi sports
  MVE listings crossed 2000 the cache became sports-only and no
  policy markets reached downstream consumers from that path. Fixed
  by cursor-complete pagination + safety-cap warnings + expected-
  family coverage check. Locked by §3, §4, §5 tests.
