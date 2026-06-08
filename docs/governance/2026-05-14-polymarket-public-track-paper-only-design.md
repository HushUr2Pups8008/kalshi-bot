# Polymarket US — Public-Developer Track, Paper-Only Design

**Date:** 2026-05-14 (UTC)
**Repo HEAD at design time:** `1314da7`
**VERSION at design time:** `0.30.1`
**Bot:** PID `92951`, untouched during authoring.
**Authority:** Scoped planning artifact under `docs/governance/`. Per `CLAUDE.md` R-9 / R-10 (One Document, No New Tracking Files), tracking content lands in `docs/profit_path_debt_log.md` as a pointer only — this file proposes work, it does not author tracking entries.
**Companion artifact:** [`docs/governance/2026-05-14-polymarket-us-integration-investigation.md`](2026-05-14-polymarket-us-integration-investigation.md) (the investigation this design implements).
**Mode:** Design-only. No code created, no code edited, no DB touched, no credentials used, no live host called.

---

## 1. Executive Summary

This design takes the Polymarket US integration investigation from research into a concrete, ordered, operator-gated rollout. The headline decision is to ship a **read-only-REST plus simulated-paper plumbing** slice first and treat the first order-capable REST POST as a future, deliberately-gated milestone — not the natural next PR.

The reuse classification from the investigation is preserved verbatim: **6 (a) / 12 (b) / 6 (c) / 1 (d)** across the 25 modules audited (investigation § 1, § 4). The shape of the decision-and-belief layer is far more reusable than the exchange-touching surfaces; that asymmetry is the structural reason a paper-only first slice can be small.

Explicit non-goals for this design:

- **Institutional Auth0 / Private-Key-JWT / FIX / gRPC track.** Mentioned only as a documented non-goal (investigation § 2.2). The bot integrates with `api.polymarket.us` (public-developer Ed25519 track) exclusively.
- **Live trading capability in the first implementation slice.** The first PR cannot place a real order even by accident; the live-capable POST path is a future operator-gated milestone with dual-agent adversarial review (§ 11).
- **Sandbox dependence.** None exists for the public-developer track (investigation § 2.13 / § 10.1 Q1). PAPER-ONLY is structural, not preferential.
- **Refactoring `kalshi/rest_client.py` into a `KalshiSignedHttpClient` subclass in the first PR.** That is a follow-on once Polymarket lands behind the abstraction; the first PR ships only the Polymarket implementation and the no-op base.

The single largest design tension is flagged in § 14: the prior-art `kalshi/rest_client.py` pattern wants to keep its venue-specific identity, while the cleaner abstraction wants to lift signing to a shared base. The design defers the Kalshi-side refactor to keep the first PR's blast radius small, at the cost of one transient inconsistency that the operator approves explicitly.

---

## 2. Module Map (First-PR Scope, Restated and Made Actionable)

The classifications below are copied verbatim from the investigation § 4 (legend: **(a)** reusable as-is, **(b)** reusable behind a thin abstraction, **(c)** Kalshi-specific behavior that needs an exchange-specific replacement, **(d)** delete or replace wholesale). The added **First PR scope** column maps each row to one of `untouched`, `imported only`, `extracted to base`, or `new module`. Together, this is the bridge from the investigation's general reuse classification to the per-PR work scoping in § 9.

| Module | Class (a/b/c/d) | First PR scope | Justification |
|---|---|---|---|
| `feeds/rss_monitor.py` | (a) | `untouched` | Venue-neutral RSS ingestion; emits `NewsItem`. No exchange coupling. |
| `feeds/reddit_monitor.py` | (a) | `untouched` | Reddit ingestion is venue-neutral; the one-IP-per-instance gotcha is operator policy. |
| `feeds/dedup.py` | (a) | `untouched` | Hash-based dedup over `NewsItem.item_id`. No exchange coupling. |
| `feeds/__init__.py` | (a) | `untouched` | Defines venue-neutral `NewsItem`. |
| `analysis/signal_analyzer.py` | (b) | `untouched` (PR-1 through PR-5); `imported only` (PR-6) | Currently imports `KalshiMarket`; the Polymarket-side wrapper in PR-6 will pass an exchange-tagged candidate without changing this module's behavior. The `JSONDecoder.raw_decode()` LLM-extraction gotcha (`CLAUDE.md`) is preserved untouched. |
| `analysis/market_matcher.py` | (c) | `untouched` (PR-1 through PR-5); a **sibling** `analysis/polymarket_matcher.py` is `new module` (PR-6) | The PROFIT-API-001 P-7 hotfix at lines 440/490 stays load-bearing for Kalshi; Polymarket discovery has a different shape (Series → Events → Markets) and gets its own matcher. |
| `analysis/decision_blender.py` | (a) | `untouched` | Pure function layer; venue-neutral. |
| `analysis/regime_classifier.py` | (b) | `untouched` (PR-1 through PR-5); a Polymarket-prefix sibling table added in `imported only` mode (PR-6) | Existing `KXxxx`-prefixed series-prior table for Kalshi stays; Polymarket gets its own slugs-prefix lookup beside it. |
| `analysis/structural_prior.py` | (a) | `untouched` | Pure function layer; venue-neutral. |
| `analysis/fade_signal.py` | (d) | `untouched` (Kalshi-only forever) | `@Kalshi` tweet patterns have no Polymarket analog; not ported. |
| `analysis/kelly.py` | (b) | `untouched` (PR-1 through PR-6); `imported only` (PR-7) | Math is identical for binary YES contracts; PR-7 calls the existing helper after converting Polymarket money-object price to integer cents. |
| `analysis/source_credibility.py` | (a) | `untouched` | Venue-neutral credibility scoring. |
| `analysis/side_selection.py` | (b) | `untouched` (PR-1 through PR-6); `imported only` (PR-7) | Two-sided executable-EV algorithm is venue-neutral. PR-7 invokes it via the `Market` protocol once `PolymarketMarket` is in place. |
| `tasks/blend_task.py` | (b) | `untouched` (PR-1 through PR-6); `imported only` (PR-7) | Orchestration is venue-neutral; the `market_ticker:` parameter is treated as opaque `market_id` from the Polymarket caller's side. |
| `tasks/structural_task.py` | (b) | `untouched` (PR-1 through PR-6); `imported only` (PR-7) | Orchestration is venue-neutral. |
| `tasks/trade_readiness_gate.py` | (a) | `untouched` (PR-1 through PR-6); `imported only` (PR-7) | Stateless G1–G6 evaluation; venue-neutral. |
| `tasks/evidence_store.py` | (b) | `untouched` (PR-1 through PR-3); minimal additive change in PR-4 (`extracted to base`-shape: accept an `exchange` parameter; default `'kalshi'`) | Schema needs an `exchange` discriminator to support cross-venue analytics; default preserves Kalshi-only behavior. |
| `trading/executor.py` | (c) | `untouched` (PR-1 through PR-6); minimal additive change in PR-7 (`extracted to base` shape — route by candidate's exchange tag) | Kill-switch logic (`_check_live_loss_limit`, `LIVE_TRADING_ENABLED` gate at `main.py:2093`) is venue-neutral and **extends, never weakens**. PR-7 adds a routing-invariant assertion (§ 4) before any side selection. |
| `trading/paper_trader.py` | (c) | `untouched` (PR-1 through PR-3); `extracted to base` (PR-4 schema migration); `imported only` (PR-7 — Polymarket paper trader is a thin wrapper writing exchange-tagged rows) | Highest-risk single module. The `_resolve_market_sync()` atomicity gotcha (`CLAUDE.md`) is preserved. |
| `trading/portfolio.py` | (c) | `untouched` (PR-1 through PR-3); `extracted to base` (PR-4: add `exchange` field on `Position`, key `Portfolio` on `(exchange, ticker)`) | Cross-exchange position confusion is structurally prevented at the Portfolio key level. |
| `kalshi/rest_client.py` | (c) | `untouched` (defer Kalshi-side refactor; § 13 explicit non-decision) | The shared `SignedHttpClient` ABC is introduced in PR-1, but `kalshi/rest_client.py` does not become a subclass in this rollout. Refactor is a separate later PR. |
| `kalshi/websocket_client.py` | (c) | `untouched` (defer Kalshi-side refactor; § 13 explicit non-decision) | Same posture as `kalshi/rest_client.py`. The `_WS_HEADER_KWARG` library-kwarg detection helper at lines 25–27 is **mirrored, not extracted**, in PR-5 to keep the first PR's blast radius small. |
| `kalshi/normalizer.py` | (c) | `untouched` (PR-1 through PR-2); `imported only` as the pattern template for `polymarket/normalizer.py` in PR-3 (`new module`) | The fail-closed-at-parse-boundary pattern is the single most reusable lesson; `polymarket/normalizer.py` mirrors `_invariants_hold` / `UnsupportedPayloadContractError` shape but with Polymarket-specific fields (`bestBid`, `bestAsk`, `active`, `closed`). |
| `kalshi/__init__.py` (KalshiMarket dataclass) | (b) | `untouched` (PR-1 through PR-3); minimal additive in PR-4 (`exchange: str = "kalshi"` field) | New `PolymarketMarket` dataclass is a sibling in PR-3 (`new module`), not a refactor of `KalshiMarket`. |
| `governance/agent.py` | (a) | `untouched` | Decision-policy authority unchanged; real-mode flip authority preserved per `~/.claude/rules/domain_constraints.md`. |
| `governance/adapter.py` | (a) | `untouched` | Adapter unchanged. |
| `governance/decision.py` | (a) | `untouched` | Decision dataclass unchanged. |
| `governance/llm.py` | (a) | `untouched` | LLM-client wrapper with `think: False` gotcha (PROFIT-GOV-001) preserved; anchor_rate polarity block at `governance/prompts.py:27-31` (PROFIT-GOV-002) preserved. |
| `config.py` | (b) | `extracted to base` (PR-1: add `POLYMARKET_API_KEY_ID`, `POLYMARKET_API_KEY_SECRET` env keys; no `POLYMARKET_LIVE_TRADING_ENABLED` until the live-capable PR) | The `dynamic_max_bet(notional)` helper stays venue-neutral. `LIVE_TRADING_ENABLED` remains a global hard-off. |
| `main.py` | (b) | `untouched` (PR-1 through PR-6); `extracted to base` (PR-7: instantiate Polymarket clients behind `if cfg.polymarket_enabled:`) | Startup-flow safety scaffolding (`--go-live`, `LIVE_TRADING_ENABLED`, typed `CONFIRM`) is venue-neutral and extends. |
| `utils/logger.py` | (a) | `untouched` (PR-1 through PR-3); minimal additive in PR-4 (`exchange` added to canonical fields) | Purely additive; existing Kalshi log lines gain an `exchange=kalshi` tag. |

Five additional new modules introduced by this design (not in the 25-module audit because they do not exist today):

| New Module | Class | First PR scope | Justification |
|---|---|---|---|
| `polymarket/__init__.py` (PolymarketMarket dataclass) | n/a — new | `new module` (PR-3) | Sibling to `KalshiMarket`; carries Polymarket-specific fields. |
| `polymarket/rest_client.py` | n/a — new | `new module` (PR-1 skeleton; PR-2 read-only GET methods) | `PolymarketSignedHttpClient(SignedHttpClient)`. `post()` raises `NotImplementedError` until the live-capable PR. |
| `polymarket/websocket_client.py` | n/a — new | `new module` (PR-5) | `PolymarketSignedWebSocketClient(SignedWebSocketClient)`; markets stream only, no private stream. |
| `polymarket/normalizer.py` | n/a — new | `new module` (PR-3) | Fail-closed-at-parse-boundary mirror of `kalshi/normalizer.py`. |
| `exchange/base.py` (abstract bases) | n/a — new | `new module` (PR-1) | Hosts `SignedHttpClient` and `SignedWebSocketClient` ABCs and the `_sign()` interface. |

---

## 3. Proposed Client Interfaces

Every interface claim below traces to an investigation citation or a Kalshi-module file-path-and-line where the prior-art pattern lives.

### 3.1 `SignedHttpClient`

The abstract base class shape (Python `Protocol` or `ABC` — recommendation: `ABC` so the `NotImplementedError` raise is constructor-enforceable). Signatures only, no implementation:

```python
class SignedHttpClient(ABC):
    @abstractmethod
    def get(self, path: str, params: Optional[dict] = None) -> dict: ...

    @abstractmethod
    def post(self, path: str, body: Optional[dict] = None) -> dict: ...

    @abstractmethod
    def _sign(self, method: str, path: str, body: str = "") -> dict[str, str]: ...
```

**Invariant for the Polymarket implementation in the first PR.** `PolymarketSignedHttpClient.post()` raises `NotImplementedError("Polymarket POST is operator-gated; see § 11 of the paper-only design doc")` until the operator-gated live-capable PR lands. This is enforced as a code-comment-level invariant in the module docstring and as a real `raise` in the method body. A `_allow_post: bool = False` constructor kwarg defaults to `False`; no caller passes `True` until the live-capable PR.

**Signing happens at the wrapper layer.** `get()` and `post()` each construct the canonical request string and pass it to `_sign()`. This mirrors the Kalshi prior art at `kalshi/rest_client.py:97-127` where `_sign()` is called from `_headers()` (line 129) which is called from `_request()` (line 140) — signing is never the caller's responsibility, and the test surface is the `_sign()` method directly.

**Kalshi-side refactor explicitly deferred.** `kalshi/rest_client.py:KalshiRestClient` will eventually become `KalshiSignedHttpClient(SignedHttpClient)`, but this is a separate later refactor PR. The first Polymarket PR ships only `PolymarketSignedHttpClient(SignedHttpClient)` and the no-op `SignedHttpClient` base. No Kalshi production code changes in the first PR. This trade-off is named explicitly in § 13.

### 3.2 `SignedWebSocketClient`

Same pattern. Signatures only:

```python
class SignedWebSocketClient(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, topic: str, payload: dict) -> None: ...

    @abstractmethod
    async def run(self) -> None: ...

    @abstractmethod
    def _sign_handshake(self, path: str) -> dict[str, str]: ...
```

Two subclasses planned: `KalshiSignedWebSocketClient` (existing behavior, future refactor — not in this rollout) and `PolymarketSignedWebSocketClient` (handshake-time `X-PM-*` headers, subscribe/heartbeat/reconnect per investigation § 2.12). The first PR ships only the Polymarket client and the abstract base.

**Per-stream subscription shapes** (investigation § 2.12):

- **Markets stream:** `wss://api.polymarket.us/v1/ws/markets`. Subscribe payload: `{"subscribe": {"requestId": "...", "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA", "marketSlugs": [...]}}`.
- **Private stream:** `wss://api.polymarket.us/v1/ws/private`. Not used in the first PR (no orders, no positions to track). Documented as deferred to the live-capable milestone.

**Reuse the websockets-library-version detection helper.** The `_WS_HEADER_KWARG` detection at `kalshi/websocket_client.py:25-27` (alternating `extra_headers` vs `additional_headers` based on the websockets package version, per `CLAUDE.md` Kalshi WebSocket gotcha) is **mirrored verbatim** in `polymarket/websocket_client.py` in PR-5. Lifting it into a shared `exchange/ws_compat.py` helper is a follow-on cleanup; the first Polymarket PR carries the duplication intentionally to keep the diff small. The duplication is the same shape as the `_normalize_pem()` duplication called out in `CLAUDE.md` ("two identical copies … any bug fix to one must propagate to the other") — explicitly accepted as transient debt.

### 3.3 The `_sign(payload) -> headers` Interface

**Polymarket-specific signing shape** (investigation § 2.1):

```
canonical_string = f"{timestamp_ms}{method}{path}"   # body excluded
signature_bytes  = ed25519_private_key.sign(canonical_string.encode())
signature_b64    = base64.b64encode(signature_bytes).decode()
```

Returns three headers: `X-PM-Access-Key`, `X-PM-Timestamp` (milliseconds since epoch, must be within 30 seconds of server time), `X-PM-Signature` (base64-encoded Ed25519 signature).

**Sign-failure must RAISE, not return partial headers** (PROFIT-SEC-001 lesson). The Kalshi prior art at `kalshi/rest_client.py:118-121` raises `KalshiSigningError` on signing failure rather than returning unsigned or partial headers; the Polymarket equivalent must follow this pattern verbatim. Cite the lesson in the docstring of `polymarket/rest_client.py:_sign()` so a future reviewer cannot weaken the contract without confronting the prior-art rationale.

**Body-inclusion difference vs Kalshi.** Kalshi signs `ts + METHOD + path + body` (`kalshi/rest_client.py:107`); Polymarket signs `ts + METHOD + path` with the body **excluded** (investigation § 2.1 / § 3 table). Copying the Kalshi `_sign()` verbatim would produce 401s for any Polymarket request with a non-empty body. The `polymarket/rest_client.py:_sign()` test in PR-1 includes a fixture that signs a request with a non-empty body and asserts the body is **not** in the canonical string.

### 3.4 Library Choice for Ed25519

Recommend `cryptography` (specifically `cryptography.hazmat.primitives.asymmetric.ed25519`). Reuse justification:

- The package is already in `requirements.txt` because Kalshi RSA-PSS depends on it (`from cryptography.hazmat.primitives.asymmetric import padding as asym_padding` at `kalshi/rest_client.py:17`).
- The Ed25519 API is small and stable: `Ed25519PrivateKey.from_private_bytes(seed[:32])` plus `.sign(message_bytes)`. No additional dependency, no version constraint to add.
- The Polymarket docs (investigation § 2.1) show the exact same library in their Python example: `private_key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode("YOUR_SECRET_KEY")[:32])`.
- Avoids pulling in `PyNaCl` or `nacl.signing` as a parallel crypto stack with overlapping responsibility.

---

## 4. Exchange Discriminator Plan for Persistence and Portfolio State

The first-class invariant: **every persisted row, every in-memory position, every emitted log line, every replay/edge-replay cohort filter carries an `exchange` tag.** Cross-exchange position confusion is structurally prevented at the schema level, not at the operator-runbook level.

### 4.1 Schema Deltas

**`paper_trades` table** (`trading/paper_trader.py:55-86` — DDL):

- Add `exchange TEXT NOT NULL DEFAULT 'kalshi'` column.
- Backfill existing rows with `'kalshi'` (the default keeps pre-Polymarket rows safe).
- This is an `ALTER TABLE` — operator-gated per § 11.

**`bot_state` table** (`trading/paper_trader.py:88-91`):

- New keys are scoped with the `polymarket_` prefix. Example: `polymarket_clean_start_ts` (analog of `p0_price_fix_deployed_ts` referenced in `CLAUDE.md` Kalshi cohort sentinel).
- The existing `p0_price_fix_deployed_ts` sentinel remains Kalshi-specific. Polymarket gets its own sentinel — they are not merged.
- The first PR plants the `polymarket_clean_start_ts` sentinel even though it has no rows to gate yet, so future replay cohort cuts are well-defined from day one.

**`evidence_store` and related tables** (`tasks/evidence_store.py`):

- Add `exchange TEXT NOT NULL DEFAULT 'kalshi'` column to each table that persists per-market state (`evidence`, `dossiers`, `dossier_updates`, `structural_priors`).
- Backfill existing rows with `'kalshi'`.

### 4.2 In-Memory State

**`Position` dataclass** (`trading/portfolio.py:19-46`):

- Add `exchange: str = "kalshi"` field (default keeps Kalshi readers safe through PR-4).
- The default is removed in a later PR once all readers are migrated; in the first paper-only rollout the default stays.

**`Portfolio` class** (`trading/portfolio.py:48-205`):

- Internal key changes from `dict[str, list[Position]]` keyed by `ticker` to `dict[tuple[str, str], list[Position]]` keyed by `(exchange, ticker)`.
- Public method signatures gain `exchange:` parameter where they currently take only `ticker:` (e.g. `exposure(ticker)` → `exposure(exchange, ticker)`).
- Hydration in `load_from_db()` reads the new `exchange` column; pre-PR-4 rows hydrate as `'kalshi'` via the column default.
- Operator gate on the schema change (§ 11).

### 4.3 JSONL Trade-Log Emission

Every event written to `data/trade_log.jsonl` (via `utils/logger.py:trade_log`) gains a top-level `exchange` field. This applies to all event types: paper-buy, live-buy, resolution, skip, error. The F-15 INFO-level skip events also get tagged.

### 4.4 Replay / Edge-Replay Consumers

Existing replay cohort-cut helpers (`scripts/edge_replay/build_replay_dataset.main()`, `score_counterfactual_pnl`, `performance_analysis`) must accept an `exchange:` parameter and filter rows accordingly. Default is `exchange='kalshi'` for backward compatibility on existing analyses. A future analysis that wants both venues passes `exchange=None` (the explicit "no filter" sentinel) — never relies on absence-of-flag as a meaning.

### 4.5 Day-1 Routing Invariant

In `trading/executor.py`, before any side selection or order construction, an assertion:

```python
assert candidate.exchange == self._exchange, (
    f"executor_routing_invariant: candidate.exchange={candidate.exchange!r} "
    f"vs executor.exchange={self._exchange!r}"
)
```

A mismatched routing tag raises loudly rather than silently routing the candidate to the wrong venue. This is the structural prevention; the operator-runbook audit is **not** a substitute. The assertion is added in PR-7 (the paper-trader PR), not PR-4 (the schema migration PR), because PR-4 has no execution path. PR-7 also adds the test that exercises this assertion with a deliberately-mismatched candidate.

---

## 5. Market Identity Model and Schema Impacts

### 5.1 Primary Identifier

Polymarket markets have both an `id` (UUID-stable) and a `slug` (URL-safe) (investigation § 2.3, § 2.4). The `PolymarketMarket` dataclass uses **`id` as the primary identifier** (stable across renames) and carries `slug` as a secondary searchable field. Rationale: slugs are mutable and human-rewritable; IDs are not. The Kalshi prior art uses `ticker` (a string) as the primary identifier (`kalshi/__init__.py:9`), and that field name is Kalshi-exclusive — Polymarket does not have a "ticker".

### 5.2 Hierarchy Inversion

Kalshi: `Series → Markets → Events` with `series_ticker` as a Markets-level foreign-key-shaped field (`kalshi/__init__.py:18`). Polymarket: `Series → Events → Markets` (investigation § 2.3 verbatim: "Every prediction on Polymarket US is structured around three levels: series, events, and markets."). The tradeable instrument sits at the bottom of a 3-level tree on Polymarket and one level below Series on Kalshi — these are different shapes.

**Design decision:** The in-process `Market` abstraction does **not** try to unify the hierarchy in the first rollout. Per-exchange match logic stays separate (`analysis/market_matcher.py` for Kalshi; a sibling `analysis/polymarket_matcher.py` for Polymarket, introduced in PR-6). The shared abstraction is at the **tradeable-instrument level only**: both `KalshiMarket` and `PolymarketMarket` present a `tradeable_id`, `question_text`, `yes_ask_cents`, `no_ask_cents`, `close_time`, `is_tradeable()`. The Series/Events tree is consumed entirely inside the per-exchange `MarketCache` and is not exposed to downstream code.

### 5.3 Field Map (Side-by-Side)

The fields the bot actually consumes from a market, with translation requirements:

| Bot consumer | Kalshi field | Polymarket field | Translation required? |
|---|---|---|---|
| Tradeable identifier | `ticker: str` | `id: str` (UUID) | Yes — different field name, different domain shape; the `Market` protocol exposes `tradeable_id: str`. |
| Human-readable question | `title: str` | `question: str` | Yes — rename only; the `Market` protocol exposes `question_text: str`. |
| Status / tradeability | `status: str` ("active" on response after `?status=open` request, per `CLAUDE.md` Kalshi gotcha) | `active: bool` AND `closed: bool` (both must be evaluated; investigation § 2.4) | Yes — different shape (one string field vs two bools). `Market.is_tradeable()` implements the venue-specific predicate. |
| YES ask price | `yes_ask_cents: int` (cents, post-P0) | `bestAsk: decimal` × 100 → cents | Yes — money-object envelope `{value: "0.55", currency: "USD"}` (investigation § 2.6) translates to integer cents via `Price.from_polymarket_money_object()`. |
| NO ask price | `no_ask_cents: int` | Derived: `1.0 - bestBid` × 100 → cents (Polymarket markets are tradeable bidirectionally via `marketSides`; investigation § 2.7) | Yes — Polymarket's directional `SIDE_BUY/SIDE_SELL` over `marketSides[]` encodes YES/NO differently from Kalshi's `side="yes"/"no"`. |
| Close / expiration time | `close_time: str` | `gameStartTime: str` (sports-context only); otherwise from event/series schema | Yes — different field origin; translation lives in `polymarket/normalizer.py`. |
| Resolution / settlement result | `KalshiMarket.result` (string, parsed by `kalshi/normalizer.py`) | `GET /v1/markets/{slug}/settlement` (dedicated endpoint; investigation § 2.10) | Yes — different access pattern (field-on-market vs separate-endpoint). `SettlementSource` protocol abstracts this in PR-8. |

### 5.4 No Separate `polymarket_market_id` Column

The recommended trade-off: the existing `ticker` column in `paper_trades`, `Position`, etc. holds the Polymarket ID (a string) for Polymarket rows; the `(exchange, ticker)` composite key disambiguates across venues. **Reasoning:**

- A second column (`polymarket_market_id`) would require every read path to conditionally select between two columns based on the `exchange` field — error-prone and read-multiplying.
- The existing `ticker TEXT` column is already string-typed and has no Kalshi-format validation in the schema (it's just text). A Polymarket UUID fits.
- Cross-exchange queries can `SELECT exchange, ticker FROM paper_trades` and treat the pair as the unique identifier.

**Trade-off accepted:** the column name `ticker` is mildly misleading for Polymarket rows (Polymarket has no concept of "ticker"). A future rename to `market_id` would be cleaner but is explicitly deferred (§ 13).

---

## 6. Paper-Only Order Lifecycle

### 6.1 Decision Flow

Polymarket flow mirrors Kalshi from "executor" inward. From "candidate construction" outward, the flow is:

1. **Signal arrives** (RSS/Reddit, via `feeds/*`). Venue-neutral.
2. **Polymarket signal-matcher** (PR-6) matches the signal against the Polymarket market vocabulary. Produces an exchange-tagged candidate.
3. **Analysis** (`analysis/signal_analyzer.py`) runs unchanged — it accepts the candidate's `Market`-protocol view.
4. **Side selection** (`analysis/side_selection.py:select_side`) runs unchanged — operates on the `Market` protocol's `yes_ask_cents` / `no_ask_cents`.
5. **Readiness gate** (`tasks/trade_readiness_gate.py`) runs unchanged.
6. **Executor** (`trading/executor.py`) routes by candidate's `exchange` tag (§ 4.5 routing invariant). Paper branch calls the Polymarket paper trader; live branch raises (in the first rollout).
7. **Polymarket paper trader** (PR-7) writes a `paper_trades` row with `exchange='polymarket'`. Fail-closed F-08 pattern: missing `executed_price_cents` on the `SignalAnalysis` produces an explicit `executor_skip` event with reason `executed_price_unavailable`, not a silent default.

### 6.2 At the Executor Boundary

The Kalshi paper-trader's DDL (`trading/paper_trader.py:55-86`) is reused with the additional `exchange` column. The Polymarket paper trader writes the same shape of row but with `exchange='polymarket'`. Fields like `keywords_matched`, `reasoning`, `signal_headline`, `signal_source` carry through unchanged from the venue-neutral `SignalAnalysis` dataclass (`analysis/__init__.py:8-29`).

### 6.3 Resolution

Polymarket settlement uses a dedicated endpoint (`GET /v1/markets/{slug}/settlement`; investigation § 2.10), unlike Kalshi where `result` lives on the market itself. PR-8 implements a settlement reconciler:

- **Polling cadence:** Initial recommendation is to piggyback on the daily-review window the bot already runs, querying `get-market-settlement` for every open `exchange='polymarket'` row in `paper_trades`. A dedicated reconciler task with a faster cadence (e.g., every 30 minutes) can replace the daily check in a later PR if latency becomes operationally relevant. The first PR-8 ships the daily cadence.
- **Schema mapping:** Settlement endpoint response → `paper_trades.resolved = 1`, `paper_trades.resolved_yes = <0 or 1>`, `paper_trades.pnl_dollars = <computed>`. The mapping mirrors `_resolve_market_sync()` in `trading/paper_trader.py` — same atomicity discipline (the `_resolve_market_sync()` atomicity gotcha from `CLAUDE.md` is load-bearing).
- **Cross-reference:** The `SettlementSource` protocol (investigation § 5) presents a unified `async def get_settlement(market_id) -> Optional[SettlementResult]` to the resolution loop. The Kalshi-side reads `KalshiMarket.result`; the Polymarket-side fetches from REST. Implementations are per-exchange; consumers are venue-neutral.

### 6.4 Hard Invariant

**The Polymarket `SignedHttpClient.post()` raises `NotImplementedError` in this rollout.** The paper-trader generates a synthetic `trade_id` (UUID4, same as the Kalshi paper-trader pattern in `trading/paper_trader.py` — see the `uuid` import at line 19) and persists state internally. Replay of the same paper-trade against the real Polymarket account is **operator-gated and post-MVP**. The design does not provide a "replay paper trades to live" feature in the first rollout, and adding one would itself be operator-gated.

---

## 7. WebSocket Subscription / Auth Design

### 7.1 Auth

Signed `X-PM-*` headers on the HTTP upgrade (investigation § 2.12 / § 10.1 Q7: "WebSocket connections use the same API key authentication as the REST API. Include these headers in the connection handshake."). This is structurally identical to the Kalshi WS-auth pattern at `kalshi/websocket_client.py:71-108` (`_build_ws_auth_headers()`) — only the signature primitive (Ed25519 not RSA-PSS), header names (`X-PM-*` not `KALSHI-ACCESS-*`), and canonical-string excluded-body convention change. The handshake topology and the library-kwarg-detection scaffolding are identical.

### 7.2 Markets Stream

- **Endpoint:** `wss://api.polymarket.us/v1/ws/markets`.
- **Subscription topic:** `SUBSCRIPTION_TYPE_MARKET_DATA` with `marketSlugs: [...]` (investigation § 2.12).
- **Message shape:** book and trade updates carry `marketSlug`, `price.value/currency` (decimal-dollar money-object envelope), `quantity.value/currency`, `tradeTime`, `maker/taker.side/intent` blocks.
- **Reconnect protocol:** Mirror the Kalshi WS exponential-backoff pattern at `kalshi/websocket_client.py:46-49` (`_INITIAL_RECONNECT_DELAY = 2`, `_MAX_RECONNECT_DELAY = 60`). Client owns subscription state (investigation § 2.12: "no resume-token semantics; the client owns subscription state"). On reconnect, the client (i) re-signs the handshake with a fresh timestamp (within 30s), (ii) re-sends every subscribe payload, (iii) treats any in-flight `requestId` as potentially lost.

### 7.3 Private Stream

**Not used in the first PR.** No orders are placed (POST raises), so there are no order-state updates to consume; no positions are held, so there is no position stream to subscribe to. The private stream is documented as deferred to the live-capable milestone.

### 7.4 Heartbeat Protocol (Q-new-1 from Investigation)

The numeric heartbeat interval is not stated in the public Polymarket docs (investigation § 2.12, § 10.3 left this as a remaining open gap after Q1-Q3 resolution). The design accommodates either server-sent or client-sent without committing:

```
heartbeat_watchdog():
    last_msg_ts = now()
    while connected:
        msg = await ws.recv()                  # any server message resets the watchdog
        last_msg_ts = now()
        if msg.type == "heartbeat":
            continue                            # absorb silently
        else:
            await dispatch_message(msg)

    # parallel timer:
    while connected:
        await sleep(heartbeat_interval)         # initial guess: 30s, tunable
        if (now() - last_msg_ts) > 3 * heartbeat_interval:
            await force_reconnect()
```

The watchdog is sized to tolerate the most conservative-realistic heartbeat interval (anywhere from 5s to 60s server-sent). The investigation's open-question Q-new-1 ("What is the numeric heartbeat interval?") is restated in § 12; resolution is operationally smaller than Q1-Q3 were and does not block the first PR.

---

## 8. No-Sandbox Risk Controls

### 8.1 Day-1 Invariants

Every invariant in this subsection must be enforceable **before** PR-1 merges. If an invariant requires a later PR to land first, it is named "Day-1+" with the prerequisite PR called out.

1. **`PolymarketSignedHttpClient.post(...)` raises `NotImplementedError`** with a message naming the operator-gate that lifts it. (Enforceable in PR-1.)
2. **`PolymarketSignedHttpClient` constructor takes `_allow_post: bool = False`**, defaulting to `False`. No caller in the first PR passes `True`. (Enforceable in PR-1.)
3. **Live-mode environment variable `POLYMARKET_LIVE_TRADING_ENABLED` does not exist in `config.py`.** Introducing it requires a separate operator-gated PR (the live-capable PR, deferred). (Enforceable in PR-1 — by absence.)
4. **Kill-switch invariant extended.** The existing kill-switch requires `LIVE_TRADING_ENABLED=true` AND `--go-live` AND typed `CONFIRM` (`main.py:2093,2017,2136`; see `CLAUDE.md` confirmation pattern). The Polymarket extension requires **all of the original three** AND `POLYMARKET_LIVE_TRADING_ENABLED=true` AND a Polymarket-specific typed confirmation. **Exact wording:** the operator must type the literal token `CONFIRM POLYMARKET` (case-sensitive, no quotes) to proceed past the prompt — this is distinct from the Kalshi-only `CONFIRM` token, so a misplaced confirmation cannot cross-route. (Enforceable as Day-1+ — depends on the live-capable PR landing.)
5. **Cohort sentinel planted at first Polymarket deploy.** `bot_state.polymarket_clean_start_ts` is written exactly once at the moment Polymarket integration first lands in production, analogous to `p0_price_fix_deployed_ts`. The first PR plants the sentinel even though it has no rows to gate yet — replay tools refuse to span the sentinel boundary unless explicitly asked. (Enforceable in PR-1.)
6. **Routing invariant in executor** (§ 4.5). Mismatched candidate-exchange / executor-exchange raises. (Enforceable in PR-7; Day-1+ in that sense.)

### 8.2 Sign-In-Method Coupling (Operator-Runbook Only)

The Polymarket warning "Always sign in with the same method (Apple, Google, or email). Switching between sign-in methods may break your API key access" (investigation § 2.1 verbatim) has **no Kalshi analog and no code mitigation**. It is documented as a CLAUDE.md-class entry to add in a later docs MR (the design recommends an entry under "Critical Gotchas → Polymarket"). No code policy can defend against an operator signing in via a different identity provider; the only mitigation is an operator-runbook check at startup that surfaces the most recent sign-in method.

### 8.3 First-Touch Protocol When the Live-Capable PR Eventually Lands

(Restating investigation § 2.13 for completeness.) Because no sandbox exists on the public-developer track, the first live Polymarket REST POST is by construction against real bankroll. The first-touch protocol is operator-executed at the keyboard with the agent in advisory mode only:

1. **Authenticated read-only first.** `GET /v1/health/check` (no auth), then `GET /v1/portfolio/positions` (authenticated, no side effects). Verifies Ed25519 signing end-to-end against the live host without touching the order book.
2. **Single-contract BUY on a high-liquidity ~50¢ market.** Minimum quantity, on a market with `bestBid` between 0.45 and 0.55 (where decimal-dollar contamination would jump out by an order of magnitude). One contract, BUY-only.
3. **Verify the fill round-trips** through `paper_trades`-equivalent persistence with `exchange='polymarket'` AND through `GET /v1/portfolio/positions` showing the new position.
4. **Pause for operator review.** No second live order placed without operator confirmation that the first round-tripped cleanly.

Dual-agent pre-touch review is required per `~/.claude/rules/agent_collaboration.md` "moves system from paper into live" trigger. The agent does not execute the cutover.

---

## 9. Rollout Plan — Safe PR Sequence

Each PR has a single sentence of scope, an explicit "files NOT touched" list, tests required, operator gate (Y/N), depends-on (Y/N), recommended primary agent, second-agent review required (Y/N), and recommended execution mode. The (a)/(b)/(c)/(d) classifications attached to each PR trace back to § 2 and ultimately to investigation § 4.

Implementation status note (2026-06-08): the landed Codex rollout split this original 10-PR plan into smaller reviewable PRs #84-#96 and uses the existing repository term `venue` with value `polymarket_us` instead of this draft's older `exchange='polymarket'` wording. The current work-state record is `docs/profit_path_debt_log.md` § 2.0a.1. This design remains the safety reference for operator gates: PR-10 is still operator-only paper soak, and live-capable POST remains deferred until after soak acceptance.

### PR-1 — Scaffolding Only

- **Scope:** Add config keys (`POLYMARKET_API_KEY_ID`, `POLYMARKET_API_KEY_SECRET`) and env-var documentation; create `exchange/base.py` with `SignedHttpClient` / `SignedWebSocketClient` ABCs; create `polymarket/rest_client.py` skeleton with `_sign()` implementation and `post()` raising `NotImplementedError`. No callers anywhere wire it up. Plant `bot_state.polymarket_clean_start_ts` sentinel migration.
- **Files touched:** `config.py` (b), `exchange/base.py` (new), `polymarket/__init__.py` (skeleton, new), `polymarket/rest_client.py` (new), `tests/polymarket/test_signing.py` (new).
- **Files NOT touched:** all `kalshi/*`, all `feeds/*`, all `analysis/*`, all `tasks/*`, all `governance/*`, all `trading/*`, `main.py`, `utils/logger.py`.
- **Tests required:** Signing-correctness against synthetic Ed25519 keypair vectors (no live calls, no credentials). `NotImplementedError` raise test for `post()`. Sentinel-planting idempotency test (in-memory SQLite).
- **Operator gate required:** Yes (operator approves the new file layout and the schema-migration plan for the sentinel).
- **Depends on previous PR:** No (this is the first PR).
- **Recommended primary agent:** Either (Claude Code or Codex). Low blast radius.
- **Second-agent review required:** No (low-risk scaffolding; operator review suffices).
- **Recommended execution mode:** Standard PR.

### PR-2 — Read-Only REST Client

- **Scope:** Implement GET endpoints on `PolymarketSignedHttpClient`: market discovery (`GET /v1/markets`, `GET /v1/events`, `GET /v1/series`), get-market-by-id, get-market-by-slug, get-market-bbo, get-market-book, get-market-settlement. No writes. No callers in production code.
- **Files touched:** `polymarket/rest_client.py` (extend), `tests/polymarket/test_rest_client.py` (new), `tests/fixtures/polymarket/` (captured doc-example payloads).
- **Files NOT touched:** `polymarket/normalizer.py` (does not exist yet — PR-3), all `kalshi/*`, all `trading/*`, all `analysis/*`.
- **Tests required:** Against captured doc fixtures only — no fetches against `api.polymarket.us` or `gateway.polymarket.us`. Round-trip GET test asserts `X-PM-*` headers present and correctly formed.
- **Operator gate required:** No.
- **Depends on previous PR:** Yes (PR-1).
- **Recommended primary agent:** Either.
- **Second-agent review required:** No.
- **Recommended execution mode:** Standard PR.

### PR-3 — `PolymarketMarket` Dataclass and Normalizer

- **Scope:** Author `polymarket/__init__.py:PolymarketMarket` dataclass; port the parse-boundary pattern from `kalshi/normalizer.py` (fail-closed `UnsupportedPayloadContractError`-equivalent, `_invariants_hold`-equivalent, drift-halt sentinel adapted) into `polymarket/normalizer.py`.
- **Files touched:** `polymarket/__init__.py` (new), `polymarket/normalizer.py` (new), `tests/polymarket/test_normalizer.py` (new).
- **Files NOT touched:** `kalshi/normalizer.py`, `kalshi/__init__.py`, `trading/*`, `analysis/*`.
- **Tests required:** Contract-version pinning — the normalizer must accept only the documented money-object envelope `{value: "0.55", currency: "USD"}` and fail-closed on a bare-int64-string price (the Q2 / Q3 lesson from investigation § 10.1). Two-boolean tradeability test (`active=true closed=false` is tradeable; `active=true closed=true` is not).
- **Operator gate required:** No.
- **Depends on previous PR:** Yes (PR-2).
- **Recommended primary agent:** Either.
- **Second-agent review required:** No.
- **Recommended execution mode:** Standard PR.

### PR-4 — Exchange-Tagging DDL + Position/Portfolio Updates

- **Scope:** Add `exchange TEXT NOT NULL DEFAULT 'kalshi'` to `paper_trades`, `evidence`, `dossiers`, `dossier_updates`, `structural_priors` tables; backfill existing rows with `'kalshi'`; add `exchange: str = "kalshi"` to `Position` dataclass; key `Portfolio` on `(exchange, ticker)`; add `exchange` to canonical fields in `utils/logger.py`.
- **Files touched:** `trading/paper_trader.py` (c) (DDL), `trading/portfolio.py` (c), `tasks/evidence_store.py` (b), `utils/logger.py` (a, additive), `tests/test_portfolio.py` (extend), `tests/test_paper_trader_migration.py` (new).
- **Files NOT touched:** All `polymarket/*`, all `kalshi/*`, all `analysis/*`, all `feeds/*`, all `governance/*`, `main.py`.
- **Tests required:** Schema migration test (in-memory SQLite; assert column default `'kalshi'`, assert backfill correctness on simulated pre-PR-4 rows). `Portfolio.exposure(exchange='kalshi', ticker=...)` returns the same value as the previous `Portfolio.exposure(ticker=...)` for all existing Kalshi rows. Cross-exchange key isolation test (two `(exchange, ticker)` pairs with the same `ticker` substring do not collide).
- **Operator gate required:** Yes (schema migration is irreversible without rollback; restart-required).
- **Second-agent review required:** Yes (persistence-layer change; second-agent reviews the migration plan adversarially).
- **Depends on previous PR:** Yes (PR-3).
- **Recommended primary agent:** Whichever agent did not implement PR-3 (independence on the persistence-touching change).
- **Recommended execution mode:** Operator-gated restart window.
- **Restart-required:** **Yes.**

### PR-5 — Polymarket WebSocket Markets-Stream Client

- **Scope:** Implement `polymarket/websocket_client.py:PolymarketSignedWebSocketClient` with markets-stream subscribe, exponential-backoff reconnect (mirrored from `kalshi/websocket_client.py:46-49`), and heartbeat watchdog (§ 7.4). No private stream. No callers in production code yet.
- **Files touched:** `polymarket/websocket_client.py` (new), `exchange/base.py` (extend with `SignedWebSocketClient` ABC), `tests/polymarket/test_websocket_client.py` (new).
- **Files NOT touched:** `kalshi/websocket_client.py`, `trading/*`, `analysis/*`.
- **Tests required:** Handshake mock — assert `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature` headers are present in the upgrade request and correctly formed against a synthetic Ed25519 keypair. Reconnect mock — assert exponential backoff is observed across simulated disconnects. Heartbeat-watchdog test — assert reconnect fires after 3× heartbeat-interval silence.
- **Operator gate required:** No (no execution path; no callers).
- **Second-agent review required:** No.
- **Depends on previous PR:** Yes (PR-4 — landing exchange field first means the WS client can tag downstream events from day one).
- **Recommended primary agent:** Either.
- **Recommended execution mode:** Standard PR.

### PR-6 — Polymarket Signal Matcher

- **Scope:** Implement `analysis/polymarket_matcher.py` — match news against the Polymarket market vocabulary. Reuses `analysis/signal_analyzer.py` unchanged. Polymarket-specific keyword vocabularies live alongside the Kalshi prefix table in `analysis/regime_classifier.py`.
- **Files touched:** `analysis/polymarket_matcher.py` (new), `analysis/regime_classifier.py` (additive — Polymarket prefix table sibling), `tests/analysis/test_polymarket_matcher.py` (new).
- **Files NOT touched:** `analysis/market_matcher.py` (the Kalshi matcher; the P-7 hotfix at lines 440/490 is preserved), `analysis/signal_analyzer.py`, `analysis/decision_blender.py`, all `trading/*`.
- **Tests required:** Match-quality regression test against a captured set of news items and Polymarket markets (fixtures only). Jaccard-similarity helper extraction is **not** part of PR-6 — the existing Kalshi-side scoring is duplicated as a starting point; lifting into a shared `analysis/match_scoring.py` is a follow-on.
- **Operator gate required:** No.
- **Second-agent review required:** No.
- **Depends on previous PR:** Yes (PR-5).
- **Recommended primary agent:** Either.
- **Recommended execution mode:** Standard PR.

### PR-7 — Polymarket Paper Trader

- **Scope:** Implement a Polymarket paper trader that writes `paper_trades` rows with `exchange='polymarket'`. Fail-closed F-08 pattern (missing `executed_price_cents` → explicit skip event with reason `executed_price_unavailable`, never silent default). No POST capability — the live path raises `NotImplementedError`. Wire the executor's routing invariant (§ 4.5) so a candidate's exchange tag is asserted against the executor's exchange before side selection.
- **Files touched:** `polymarket/paper_trader.py` (new — thin wrapper around the existing `trading/paper_trader.py` schema, parameterized by `exchange='polymarket'`), `trading/executor.py` (additive — routing invariant assertion), `main.py` (b) (instantiate Polymarket clients behind `if cfg.polymarket_enabled:`), `tests/polymarket/test_paper_trader.py` (new).
- **Files NOT touched:** `trading/paper_trader.py` schema (PR-4 already migrated it), `kalshi/*`, governance.
- **Tests required:** End-to-end paper-decision-to-DB-row test (synthetic Polymarket candidate → row written with `exchange='polymarket'`). F-08 fail-closed test (missing `executed_price_cents` → skip event, no row written). Routing-invariant test (Kalshi candidate routed to Polymarket executor raises loudly).
- **Operator gate required:** Yes (first runtime path that exercises the new schema; operator schedules a bot bounce).
- **Second-agent review required:** Yes (persistence + execution path; second-agent reviews adversarially).
- **Depends on previous PR:** Yes (PR-6).
- **Recommended primary agent:** Whichever agent did not implement PR-4.
- **Recommended execution mode:** Operator-gated restart window.
- **Restart-required:** **Yes.**

### PR-8 — Settlement Reconciler

- **Scope:** Periodically poll `GET /v1/markets/{slug}/settlement` for open `exchange='polymarket'` positions; resolve them via the existing `_resolve_market_sync()` atomicity pattern. Initial cadence: daily-review window (per § 6.3); the cadence is a config value so the operator can shorten it post-soak.
- **Files touched:** `polymarket/settlement_reconciler.py` (new), `polymarket/__init__.py` (extend with `SettlementSource` protocol shape), `tests/polymarket/test_settlement_reconciler.py` (new).
- **Files NOT touched:** `trading/paper_trader.py:_resolve_market_sync()` (reused as-is via the `SettlementSource` protocol).
- **Tests required:** Synthetic settlement payloads — happy path (resolves to `resolved_yes=1`), tie/unresolved path (`Settlement not found` 404 → no-op), malformed payload (drift halt fires).
- **Operator gate required:** No (no schema migration; new code path is additive).
- **Second-agent review required:** No.
- **Depends on previous PR:** Yes (PR-7).
- **Recommended primary agent:** Either.
- **Recommended execution mode:** Standard PR.

### PR-9 — Observability Surfaces

- **Scope:** Update `bothealth`, `daily_review`, F-06 silent-attrition watcher, post-fix-new readiness watcher to read `exchange='polymarket'` rows. Emit per-exchange verdicts (Kalshi PnL section + Polymarket PnL section, side-by-side).
- **Files touched:** `tasks/bothealth.py`, `tasks/daily_review.py`, `tasks/f06_watcher.py` (or equivalent), `tasks/post_fix_new_readiness.py` (or equivalent), `tests/tasks/test_bothealth_exchange_split.py` (new).
- **Files NOT touched:** `polymarket/*`, `kalshi/*`, `trading/*`.
- **Tests required:** Per-exchange split test — synthetic mixed-exchange `paper_trades` rows produce two distinct verdict sections, neither contaminates the other.
- **Operator gate required:** No.
- **Second-agent review required:** No.
- **Depends on previous PR:** Yes (PR-8).
- **Recommended primary agent:** Either.
- **Recommended execution mode:** Standard PR.

### PR-10 — Paper-Mode Soak Window

- **Scope:** No code change. Operator-driven N-day soak (recommended 14 days, mirroring `PROFIT-PHASE2-001`). Acceptance criterion: N post-clean-start Polymarket paper trades with stable PnL math, no drift-halt fires, no F-08 fail-closed events on a recurring basis.
- **Files touched:** None.
- **Files NOT touched:** All.
- **Tests required:** None (operator-driven monitoring).
- **Operator gate required:** Yes (operator owns the start, the duration, and the accept/reject verdict at the end).
- **Second-agent review required:** No.
- **Depends on previous PR:** Yes (PR-9).
- **Recommended primary agent:** Operator.
- **Recommended execution mode:** Operator-only.

### Future / GATED — Live-Capable PR

- **Scope:** Lift `NotImplementedError` from `PolymarketSignedHttpClient.post()`; introduce `POLYMARKET_LIVE_TRADING_ENABLED` env var in `config.py`; extend `--go-live` flow and add `CONFIRM POLYMARKET` typed token; wire the executor's live branch.
- **Operator gate required:** Yes (hard).
- **Second-agent review required:** Yes (dual-agent adversarial review — this is the canonical case for `~/.claude/rules/agent_collaboration.md` "moves system from paper into live" trigger).
- **Recommended execution mode:** Operator-gated cutover with operator-executed first-touch (§ 8.3).
- **Not designed in detail here.** Deferred until after PR-10 acceptance.

### Restart-Required Summary

The PRs that require a bot bounce: **PR-4** (schema migration on `paper_trades`, `evidence`, etc.) and **PR-7** (executor routing invariant + Polymarket paper-trader wiring + Polymarket client instantiation in `main.py`). Every other PR in the rollout is hot-deployable (or operator-driven without code change in PR-10's case).

---

## 10. Tests Required Before Any Implementation

Test pyramid mapped per PR:

- **Signing-correctness tests with known Ed25519 vectors.** PR-1. Synthetic keypair, deterministic test vectors. No credentials, no live calls.
- **REST client tests against captured docs.polymarket.us example payloads.** PR-2. Fixtures recorded by hand from the doc-page code examples (no fetches against live endpoints). Each fixture carries a sidecar `.meta.json` with capture URL, capture timestamp, and any parameters needed to reproduce.
- **Normalizer tests pinning the parse-boundary contract.** PR-3. Contract-version drift halt — money-object envelope is the only accepted trading-price shape; bare-int64 strings fail closed. Two-boolean tradeability matrix.
- **WebSocket handshake tests.** PR-5. Mock the HTTP upgrade; verify `X-PM-Access-Key`, `X-PM-Timestamp`, `X-PM-Signature` headers are present and correctly formed. Mock reconnect + heartbeat-watchdog.
- **Schema migration tests.** PR-4. In-memory SQLite; assert default values, backfill correctness, constraint shape (`NOT NULL DEFAULT 'kalshi'`).
- **Cross-exchange routing-invariant tests.** PR-7. A candidate routed to the wrong executor (mismatched `exchange` field) raises loudly. No silent re-routing.
- **Paper-trader end-to-end test on a synthetic decision.** PR-7. Decision flow → row written with `exchange='polymarket'`. F-08 fail-closed reason fires on missing `executed_price_cents`.
- **Cohort sentinel test.** PR-1. Planting `bot_state.polymarket_clean_start_ts` is idempotent — second invocation is a no-op, not a duplicate write.

No live API calls in any test, consistent with the Kalshi side (which has no live integration tests in CI per investigation § 8). Every Polymarket interaction in test code is fixture-driven.

---

## 11. Operator Gates for Any Future Live-Capable Action

Explicit list. The boundary that no agent may cross without operator approval:

1. **First successful `POST` against the Polymarket REST API.** Read-only fails fine without auth and is bounded; POST is the boundary.
2. **Schema migrations on `paper_trades`** (any `ALTER TABLE`). PR-4 is the first such gate.
3. **Cohort sentinel planting or reset.** Planting (PR-1) is operator-gated; resetting (any future change) is operator-gated.
4. **Any change to the kill-switch invariant or its parameters** (`LIVE_TRADING_ENABLED`, `--go-live`, typed `CONFIRM`, `POLYMARKET_LIVE_TRADING_ENABLED`, `CONFIRM POLYMARKET`).
5. **Lifting the `NotImplementedError` raise in `PolymarketSignedHttpClient.post()`.** This is the central code-level gate; the live-capable PR is the only authorized lifter.
6. **Adding `POLYMARKET_LIVE_TRADING_ENABLED` to `config.py`.** Until the live-capable PR, the variable does not exist (by absence; § 8.1 invariant 3).
7. **First live order** (one-contract BUY, ~50¢ liquid market, operator-executed only). Per § 8.3 first-touch protocol.

---

## 12. Open Design Questions

Carried forward from the investigation (§ 10.3) and supplemented with one new question surfaced by this design pass:

- **Q-WS-HB (was Q-new-1):** What is the numeric WebSocket heartbeat interval? Public docs do not state. The watchdog in § 7.4 absorbs any reasonable value; first PR ships a conservative default (`30s` initial; reconnect after `90s` silence). Resolution evidence needed: a documented number, a worked example, or a vendor confirmation.
- **Q-TICK (was Q2 in the investigation):** Per-market tick-size reconciliation. The `orderPriceMinTickSize` field is per-market (investigation § 2.4) but how it interacts with the bot's existing integer-cents pricing model on the Kalshi side is undefined when the Polymarket tick is finer than 1¢ (e.g., 0.5¢). Recommendation deferred to PR-7 design review.
- **Q-MINORD (was Q4 in the investigation):** Documented minimum order size in dollars. No `minNotional` field surfaced in the public-developer corpus. First PR conservatively floors at `$1` per order (one contract at any price under 100¢); revisit when documentation surfaces.
- **Q-QSCALE (was Q-new from this design pass):** Quantity scaling. The Polymarket SDK quickstart shows `quantity: 100` as a bare integer (investigation § 2.6 verbatim) — is that 100 contracts or 100 USD-notional? The decimal-dollar money-object envelope on `price` argues for 100 contracts (with `price` in dollars giving the cost), but the docs are not explicit. Conservative interpretation: 100 contracts. Test with a single-contract first-touch (§ 8.3) to verify.
- **Q-SUBENUM:** `subscriptionType` enum form. The investigation shows verbatim `SUBSCRIPTION_TYPE_MARKET_DATA` (§ 2.12), but the full enum (which other values are valid?) is not exhaustively listed in the fetched corpus. Conservative posture: PR-5 only subscribes to `SUBSCRIPTION_TYPE_MARKET_DATA` and treats any other server-sent type as a no-op-with-WARN.
- **Q-FROMEP3 (was Q6 in the investigation):** What does `fromEp3: boolean` on `GET /v1/markets/{slug}/settlement` do? Undefined acronym in the docs. PR-8 defaults to omitting the parameter (server default behavior) and surfaces both options as a config knob if the operator later wants to experiment.
- **Q-RATELIMIT (was Q2 in the investigation):** Documented numeric per-second / per-minute REST rate limit. Not published for the public-developer track. PR-2 ships with a `_MIN_REQUEST_INTERVAL` analogous to the Kalshi-side guard at `kalshi/rest_client.py:51` (`0.12s`); revisit if 429 responses are observed.
- **Q-IDEMPOT (was Q5 in the investigation):** Idempotency-window TTL for `clordId`. Not stated. Live-capable PR populates `clordId` on every POST (FIX-style, per investigation § 2.7) but the bot does not rely on a specific TTL; retries assume the server may either deduplicate or reject — both are acceptable.
- **Q-KEYROT (was Q9 in the investigation):** Public-developer key-rotation procedure. Documented for institutional track only. No code policy can defend; operator runbook only.

---

## 13. What This Design Deliberately Does NOT Decide

Explicit non-decisions, each with the default behavior:

- **Whether to refactor `kalshi/rest_client.py` into `KalshiSignedHttpClient` in PR-1.** Default: **no, deferred.** The first Polymarket PR ships only `PolymarketSignedHttpClient(SignedHttpClient)` and the no-op `SignedHttpClient` ABC. The Kalshi-side refactor is a separate, later, low-risk PR. Trade-off: one transient inconsistency where Kalshi has a venue-specific client class and Polymarket inherits from the shared ABC. Accepted to keep the first PR's blast radius small.
- **Whether to refactor `kalshi/websocket_client.py` into `KalshiSignedWebSocketClient` in PR-5.** Default: **no, deferred.** Same reasoning. The `_WS_HEADER_KWARG` library-detection helper is **mirrored, not extracted**, in PR-5 (the same accepted-duplication pattern as the `_normalize_pem` gotcha from `CLAUDE.md`).
- **Whether governance shadow-soak (Phase 2/3) gets Polymarket awareness.** Default: **no.** Per the investigation's (d) classification, `governance/*` is exchange-neutral and remains so — the governance LLM does not need to know which exchange a decision was made on, because its `decision_blender` inputs are venue-neutral.
- **Whether the Polymarket signal vocabulary uses LLM-prompt customization.** Default: **no.** Same prompts as the Kalshi side. Polymarket-specific market families (sports, crypto, politics) may need vocabulary tuning post-soak, but that is a PR-6+1 follow-on, not part of PR-6 itself.
- **Whether replay scripts get Polymarket cohorts in v1.** Default: **no.** Deferred to a P1-C-equivalent follow-on. The exchange-tagged rows are written from PR-4 onward, so the data is captured; only the consumer-side filter is deferred.
- **Whether to rename the `ticker` column in `paper_trades` to `market_id`.** Default: **no.** Polymarket UUIDs fit in the existing `TEXT` column; the rename is cosmetic and a separate PR. Trade-off: column name is mildly misleading for Polymarket rows.
- **Whether to introduce a shared `analysis/match_scoring.py` helper module.** Default: **no.** The Kalshi-side scoring is duplicated into `analysis/polymarket_matcher.py` in PR-6; lifting is a follow-on.

---

## 14. Single Biggest Design Tension

**Where the Kalshi prior art wants its own venue-specific client classes versus where the cleaner abstraction wants a shared `SignedHttpClient` / `SignedWebSocketClient` base.**

The Kalshi prior art at `kalshi/rest_client.py:58-127` and `kalshi/websocket_client.py:71-108` is well-shaped, battle-tested, carries hard-won gotcha knowledge (PROFIT-SEC-001 fail-fast posture; PROFIT-API-001 status-filter discipline; `_WS_HEADER_KWARG` library-version detection), and has zero observed defects under the v0.30.1 lineage. Refactoring it as part of the Polymarket rollout would:

- Couple a low-risk Polymarket-addition PR with a high-touch Kalshi-refactor PR.
- Concentrate blast radius on the running bot's most load-bearing surface (`KalshiRestClient` is invoked from every signal-to-trade path).
- Either delay Polymarket arrival or rush the Kalshi refactor.

The cleaner abstraction wants `KalshiSignedHttpClient(SignedHttpClient)` from PR-1 onward, but the cost-vs-benefit math is unfavorable for the first slice. The design defers the Kalshi-side refactor to a separate later PR, at the cost of one transient inconsistency: Polymarket inherits from the shared ABC; Kalshi does not. This is named explicitly in § 13 so the operator can revisit. The accepted-debt analog is the existing `_normalize_pem()` duplication called out in `CLAUDE.md` ("two identical copies … any bug fix to one must propagate to the other") — duplication is the cheaper option until the abstraction has earned its keep across two real implementations.

---

## 15. Reporting Hooks

Per the design contract: this artifact is the **single deliverable**. It does not author a debt-log pointer; it proposes one (recommended text follows, for the operator to add if and when this design graduates from research to active work):

> **Polymarket US public-developer track — paper-only design (2026-05-14):** scoped design artifact at [`docs/governance/2026-05-14-polymarket-public-track-paper-only-design.md`](2026-05-14-polymarket-public-track-paper-only-design.md). Companion to the 2026-05-14 investigation. Eleven-PR rollout (PR-1 scaffolding through PR-10 paper-mode soak; live-capable PR explicitly gated). Restart-required PRs: **PR-4** (schema migration) and **PR-7** (executor + Polymarket paper-trader wiring). Largest design tension: Kalshi-side `kalshi/rest_client.py` refactor deferred to keep first PR small (§ 14). Status: research-only; awaiting operator decision on PR-1 authorization.

Per `CLAUDE.md` R-9 / R-10, the operator (not the agent) authors the debt-log line. This artifact is sanctioned as the single landing surface for this work.
