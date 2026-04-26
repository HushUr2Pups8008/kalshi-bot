# Polymarket — Trading Venue Integration Investigation

| Field | Value |
|-------|-------|
| Status | INVESTIGATION (analysis-only; no code changes; no schedule commitment) |
| Date drafted | 2026-04-22 |
| Author | Claude |
| Scope | Polymarket as a **second trading venue** parallel to Kalshi (execution lane), NOT as a news/signal source |
| Companions | [`docs/ROADMAP.md`](../ROADMAP.md) Appendix A; [`docs/plans/news_sources_evaluation.md`](news_sources_evaluation.md) §3.2 |
| Supersedes | The "Polymarket cross-reference" news-source entries in the two companion docs (kept for historical context, but venue-as-trading-lane now takes precedence) |
| Execution window | Earliest post-live-trading stabilization on Kalshi (post-P4-GATE); see §9 for sequencing |

---

## 1. Scope pivot — why this doc exists separately from the news-sources evaluation

Existing docs (`ROADMAP.md` line 317; `news_sources_evaluation.md` §3.2, §7) classify Polymarket as a specialized **parallel prediction-market news signal** — a `PredictionMarketQuote` type emitted by a new `feeds/polymarket_monitor.py`, distinct from `NewsItem` but still ultimately a signal-lane input.

The user's directive (2026-04-22) redefines the scope: **Polymarket should be treated as an entirely separate trading venue**, analogous to Kalshi. This is a different initiative with different integration surfaces:

| Dimension | News-source model (existing docs) | Trading-venue model (this doc) |
|---|---|---|
| Integration point | `feeds/` monitor emitting `PredictionMarketQuote` | `trading/` + new `polymarket/` client package |
| Data direction | Read-only (prices → signals) | Read + write (prices, orders, fills, positions) |
| Auth surface | None (public Gamma API) | L1 + L2 signed; KYC; regulated broker relationship |
| Bankroll implication | None | Per-venue bankroll tracking required |
| Market identity | Ephemeral input to matching | First-class venue-namespaced market records |
| Risk profile | Low (no trading exposure) | High (regulatory + operational + settlement risk) |

The two models are **not mutually exclusive** — a Polymarket price feed can legitimately be both a trading venue *and* a cross-reference signal for Kalshi decisions — but the trading-venue initiative must be designed first because it subsumes the read path.

---

## 2. Regulatory landscape (as of 2026-04-22)

A material change occurred in late 2025 that invalidates most pre-2026 analysis:

- **2025-11**: CFTC issued an Amended Order of Designation permitting Polymarket to operate an intermediated trading platform subject to the full requirements applicable to federally regulated US exchanges.
- **2025-12**: Polymarket relaunched for US residents via a waitlist + KYC onboarding flow, working through approved broker intermediaries. US users **cannot** trade via direct crypto wallets under this pathway.
- **2026-Q1**: State-level challenges ongoing. Massachusetts issued a preliminary injunction (applies to Kalshi's sports contracts and Polymarket geoblocked MA residents in response); Nevada Gaming Control Board filed suit in 2026-01; Tennessee issued cease-and-desist letters; 9th Circuit (Nevada) and 4th Circuit (Maryland) scheduled to hear cases later in 2026.

**Two distinct Polymarket surfaces now exist:**

1. **Polymarket Global (CLOB)** — USDC-settled, Polygon Layer 2, EIP-712 + HMAC-SHA256 signing, ~0–1.80% taker fee (dynamic, probability-based, free in geopolitics/world-events category), public `docs.polymarket.com` API. **Not available to US residents.**
2. **Polymarket US (CFTC-regulated)** — flat 0.30% taker fee, intermediated via approved brokers, KYC required, geoblocked from some states. API documentation surface for this pathway is **less public** — broker-integrated access is the stated path, and direct programmatic access is an open question (§8).

**Implication for this repo:** the operator is US-based (per user memory and hardware notes). The Global CLOB path is the one documented publicly, but using it from a US residence is non-compliant. The US-regulated path is the lawful one, but its programmatic access surface is unclear. This gating question must be resolved before any implementation work.

---

## 3. Kalshi vs. Polymarket — venue characteristics

| Characteristic | Kalshi | Polymarket Global | Polymarket US |
|---|---|---|---|
| Regulatory frame | CFTC DCM | Offshore (CLOB on Polygon) | CFTC DCM (Nov 2025 amended order) |
| Settlement currency | USD (bank ACH/wire) | USDC (on-chain) | USD via broker intermediaries |
| Contract price native unit | Cents, integer 1–99 (implied prob) | USDC decimal, 0.0001–0.9999 | Presumed cents/decimal — open |
| Market structure | Binary YES / NO | Binary + **multi-outcome (3+)** | Binary + multi-outcome |
| Auth algo | RSA-PSS/SHA-256 (PEM key) — see [CLAUDE.md](../../CLAUDE.md) "Kalshi API" section | L1 EIP-712 signed struct + L2 HMAC-SHA256 | Unknown — likely broker-gated |
| Fees (taker) | ~1% — trading-tier dependent | 0–1.80% dynamic; **0% in geopolitics/world-events** | Flat 0.30% |
| Fees (maker) | Free (rebates in some tiers) | Free | Free |
| Liquidity (2026-Q1) | ~60%+ prediction-market share; deeper consistency | Deeper in some trending markets (culture/crypto) | New platform — thin |
| Kalshi-exclusive categories | Economic indicators (CPI, GDP, Fed), weather, FOMC | — | — |
| Polymarket-exclusive categories | — | International politics, crypto protocol events, culture/news-of-the-day (fast listing) | Presumably similar subset |
| Market-ID format | `KX*` ticker (e.g., `KXTRUMPENDORSE-...`) | UUID or slug | Unknown |
| Official client | In-house [`kalshi/`](../../kalshi/) package | [`py-clob-client`](https://github.com/Polymarket/py-clob-client) | None public |
| WebSocket | Yes — custom auth upgrade (see [CLAUDE.md](../../CLAUDE.md) "WebSocket" section) | Yes — CLOB market-data + user channels | Unknown |

**Key takeaways for this bot's strategy:**

- The bot's current edge concentrates in **geopolitical markets** (Russia/Ukraine/Iran/Middle East) surfaced by LLM analysis of wire-service news. Polymarket's **zero-fee geopolitics category** aligns perfectly with that edge and could materially improve unit economics on overlapping markets.
- Polymarket's **multi-outcome markets** (3+ possible outcomes) are a genuinely novel surface that Kalshi cannot replicate. The bot's current binary YES/NO model in [`kalshi/__init__.py`](../../kalshi/__init__.py) cannot trade these without architectural change.
- Kalshi retains exclusive coverage of economic indicators (CPI/GDP/Fed) and weather. A dual-venue bot would not lose Kalshi-specific lanes by adding Polymarket.
- Liquidity is **lane-dependent**, not globally better on one side. Neither venue dominates; the right posture is "route to the venue with better price for the same exposure," not "migrate everything."

---

## 4. Current codebase coupling to Kalshi

(Architectural audit, 2026-04-22.)

| Layer | Coupling | Evidence |
|---|---|---|
| `/kalshi/` | Fully Kalshi-specific | Expected — this is the venue client. No changes needed; Polymarket gets its own peer package. |
| `/trading/executor.py` | Tight — imports `KalshiRestClient` directly | Must introduce a `VenueClient` protocol/interface and route via it. |
| `/trading/portfolio.py` | Schema mostly venue-neutral | No `venue` column in [`data/paper_trades.db`](../../data/paper_trades.db); columns like `price_cents`, `market_yes_price` bake in Kalshi's cents/binary model. |
| `/analysis/market_matcher.py` | Partially coupled | Depends on `KalshiRestClient.get_markets()`; assumes Kalshi ticker prefix filtering via [`config.py`](../../config.py) `MARKET_SERIES_BLOCKLIST_PREFIXES` (`KX*`). |
| `/analysis/kelly.py` | Venue-neutral after internal conversion | `market_price_cents / 100.0` conversion is local; Kelly math operates on probabilities in `[0,1]`. Polymarket's native `[0,1]` decimals work with minimal change. |
| `/analysis/signal_analyzer.py`, `regime_classifier.py`, `dossier_builder.py` | Venue-neutral | Operate on probabilities, not venue identities. No changes needed. |
| `/tasks/` | Venue-neutral | Orchestration layer; no Kalshi-specific references. |
| `/feeds/` | Venue-neutral | News-side only. |
| `/config.py` | ~10 `KALSHI_*` env vars; `MARKET_SERIES_BLOCKLIST_PREFIXES` hardcoded to `KX*` | A Polymarket venue needs a parallel `POLYMARKET_*` block and series/slug filtering appropriate to its ID scheme. |
| `data/paper_trades.db` | No `venue` column | Must add `venue TEXT` and migrate; cascading query updates in `source_credibility.py`, `source_stats.py`, paper-performance tooling, and all reporting in [`scripts/daily_review.py`](../../scripts/daily_review.py). |

**Integration complexity estimate (tentative):**

| Layer | Effort | Notes |
|---|---|---|
| New `polymarket/` client package (auth, REST, WebSocket) | HIGH (~2 weeks) | EIP-712 signing + HMAC-SHA256 — wholly different from Kalshi's RSA-PSS path. py-clob-client is available but the bot's current style is hand-rolled clients. |
| `VenueClient` protocol in `trading/` | MEDIUM (~3 days) | Refactor executor to accept a venue handle rather than a concrete `KalshiRestClient`. |
| Price/probability normalization | MEDIUM (~2 days) | Pick single internal representation (recommend: probability ∈ [0,1]) and convert at client boundaries. Retire "cents" from internal naming. |
| Ticker/market identity namespacing | MEDIUM (~2 days) | Introduce `venue:market_id` tuple as the primary key; update matcher, dedup, paper DB. |
| Paper DB migration (add `venue` col) | LOW (~1 day) | Standard ALTER + backfill + update INSERT/SELECT sites. |
| Multi-outcome market support | HIGH (~1–2 weeks) | **Deferrable.** YES/NO model can't trade multi-outcome; initial Polymarket integration can filter to binary markets only. |
| Config + env surface | LOW | Mirror `KALSHI_*` pattern. |
| Risk & safety gates | MEDIUM | Ensure positive-EV gating, paper-trading mode, and live-trading explicit opt-in all apply per-venue. Per [CLAUDE.md](../../CLAUDE.md) global rule: preserve execution criteria on venue-specific changes. |
| Observability: per-venue metrics in `scripts/daily_review.py` | LOW | Section 8 SOURCE SCORECARD needs a venue dimension; other sections need a `venue=` column or filter. |

**Rough aggregate:** 4–6 weeks of focused engineering for a binary-market-only dual-venue bot; +1–2 weeks for multi-outcome support.

---

## 5. What exactly would the bot gain?

Tested against the bot's actual edge (LLM-driven geopolitical market trading):

1. **Zero-fee geopolitics on Polymarket Global.** The current Kalshi fee (~1%) is a material portion of the bot's modeled edge on low-edge trades. A zero-fee lane on equivalent markets would raise the executable-trade ratio above the current `MIN_EDGE` threshold (see [analysis/kelly.py](../../analysis/kelly.py)). **This is the single highest-value reason to integrate.** Caveat: only applies to the Global surface, which may not be a lawful path from the US.
2. **Cross-venue price arbitrage on overlapping markets.** If both venues list the same event, price divergence between them is a direct, venue-neutral signal — the bot can buy the cheaper side on both venues or hedge across them. Requires both venues online and synchronized market identity.
3. **Polymarket-exclusive markets.** International politics, crypto-native events, faster cultural-event listings. The bot's existing analysis layer is venue-neutral and could extend coverage with no new modeling work.
4. **Multi-outcome markets.** Genuinely novel surface; requires model extension but unlocks a category of trades Kalshi cannot offer.
5. **Resilience.** A single-venue bot halts if Kalshi has an outage or halts a market category. Dual-venue posture reduces this risk.

**What the bot would *not* gain:**

- Kalshi-exclusive categories (CPI/GDP/Fed/weather) — Polymarket does not list these.
- Automatic signal improvement — the bot's news pipeline remains the edge source; venue expansion widens execution, not prediction quality.
- Reduced infrastructure work — doubles the client-integration surface and compliance workload.

---

## 6. What the bot would *risk*

1. **Regulatory compliance.** US residents on the Global CLOB path are non-compliant. The US-regulated path is lawful but broker-mediated, and it is unclear whether it offers programmatic access suitable for algorithmic trading. This must be definitively resolved before implementation.
2. **Settlement latency and dispute risk.** Polymarket Global resolution uses UMA optimistic oracle (~2–48h dispute window). Kalshi resolution is exchange-administered. The bot's `resolve_market()` flow (see [CLAUDE.md](../../CLAUDE.md) "Signal analysis" gotcha) was designed around Kalshi's resolution model and assumes atomic credit-on-resolution. UMA delay + potential dispute-reversal requires model changes.
3. **Bankroll fragmentation.** Dynamic bet sizing via `cfg.dynamic_max_bet(notional)` (see [CLAUDE.md](../../CLAUDE.md) "Config / env" gotcha) assumes a single bankroll. Two venues mean two independent bankroll pools unless actively rebalanced, OR a "virtual unified bankroll" with per-venue caps — either adds complexity to every sizing decision.
4. **Counterparty and custody risk.** USDC on Polygon adds blockchain-operational risk (bridge failures, smart-contract risk) absent from Kalshi's USD-bank model. Mitigated on the US-regulated path.
5. **State-level geoblocking volatility.** Active litigation (NV, MA, TN, MD) may change accessibility mid-operation. Requires an enforceable block in live mode if the operator's state becomes restricted.
6. **KYC-breaks-automation risk.** If the US-regulated broker path requires per-order interactive confirmation or binds the account to a specific broker GUI, it may not support fully automated trading at all. This is the gating question to answer first.
7. **Dilution of focus.** The bot is not yet live on Kalshi (v0.6.3 paper mode per current state). Introducing a second venue before the first one has demonstrated live profitability violates the "prove one edge before scaling" discipline that the ROADMAP's P-gates encode.

---

## 7. Market-identity and matching complications

The current market matcher (see [analysis/market_matcher.py](../../analysis/market_matcher.py)) assumes a single source of truth for market records. Dual-venue matching introduces:

- **Cross-venue deduplication.** The same real-world event can be listed on both venues with different titles and identifier schemes. The matcher must either (a) match news to each venue independently and trade the best price, or (b) maintain an explicit cross-venue equivalence map. Option (a) is simpler; option (b) unlocks arbitrage.
- **Venue-namespaced market IDs.** Current ticker strings are bare (e.g., `KXTRUMPENDORSE-...`). Must evolve to `venue:id` tuples (e.g., `kalshi:KXTRUMPENDORSE-...`, `polymarket:0xabc...`). All storage, logging, and reporting sites need updating.
- **Series blocklist portability.** `MARKET_SERIES_BLOCKLIST_PREFIXES` is a Kalshi-prefix whitelist pattern and won't work for Polymarket's slug/UUID ID scheme. Needs a venue-specific blocklist predicate.

---

## 8. Open questions — Phase 0 answers recorded 2026-04-22

Phase 0 executed on 2026-04-22 via documentation review only (no sign-up, no code). Primary sources: [polymarketexchange.com/developers.html](https://www.polymarketexchange.com/developers.html) (official), [docs.polymarket.us/getting-started/welcome](https://docs.polymarket.us/getting-started/welcome) (official Exchange Gateway docs), [agentbets.ai Polymarket US API guide](https://agentbets.ai/guides/polymarket-us-api-guide/) (third-party, March 2026 pub), [tradetheoutcome.com FCM intermediated-model writeup](https://www.tradetheoutcome.com/polymarket-returns-to-the-us-the-intermediated-model-explained/), [turtelli.com state-by-state legality (April 2026)](https://www.turtelli.com/insider-knowledge/polymarket-prediction-markets-for-trading/is-polymarket-legal).

### Q1 — Programmatic API access for US retail?

> Does the Polymarket US (CFTC-regulated) pathway expose a programmatic API suitable for algorithmic trading from a retail residence, or is it broker-GUI-mediated only?

**ANSWER: The API exists and is live, but retail onboarding is WAITLIST-GATED as of 2026-04-22.**

Technical surface (confirmed available):
- Base URL: `https://api.polymarket.us`
- Architecture: unified REST + WebSocket; 23 REST endpoints across 5 resource groups (Markets, Orders, Events, Portfolio, Account); 2 WebSocket endpoints (`/v1/ws/markets` public market-data, `/v1/ws/private` authenticated order updates)
- Official SDKs: `polymarket-us` for Python 3.10+ and for TypeScript (Node 18+). Not the same package as the Global `py-clob-client`.
- Rate limits: 60 req/min REST (shared across public and authenticated endpoints for a given key); WebSocket supports up to 10 instruments per connection. Institutional higher-throughput path exists via Exchange Gateway with FIX 4.4 protocol (separate application).

Access path (gated):
- Nominal retail flow: KYC through the Polymarket US iOS app (government ID + SSN + proof of address) → web portal at `polymarket.us/developer` → self-service Ed25519 keypair generation.
- **Actual state on 2026-04-22 (operator observation):** attempting to onboard lands on a waitlist with position numbers in the low millions (operator confirmed position #1,302,257 on 2026-04-22). The platform publicly launched in December 2025 but KYC capacity is being released gradually; the "minutes-to-hours KYC approval" described by third-party guides reflects *post-waitlist* behavior, not immediate availability.
- Share-to-move-up link is offered in the waitlist flow — Polymarket's own viral mechanic. Position advancement rate is not publicly documented.
- **Caveat on the institutional path:** the official developer page at `polymarketexchange.com/developers.html` describes a separate "application + sandbox + approval" flow for "participants and technology partners" — Exchange Gateway (FIX 4.4), likely not waitlist-gated but requires business-entity onboarding and is not appropriate for retail algorithmic use.

**Phase 0 exit gate: PASS on the question itself** — the answer is conclusive: a technically viable API exists for US retail in principle. **Phase 1 entry is BLOCKED** in practice until the operator gets off the waitlist and completes KYC. This is a waiting condition, not a scope change — when the waitlist clears, the Phase 1 plan resumes as written in §9 (with the KYC + keypair step as its entry gate).

### Q2 — Is the US API the same surface as the Global CLOB?

> If programmatic US access exists, is the auth / endpoint / WebSocket surface identical to the Global CLOB, or is it a different API entirely?

**ANSWER: Entirely different API. Do not plan to reuse Global CLOB code.**

| Aspect | Global CLOB | Polymarket US |
|---|---|---|
| Base URL | `clob.polymarket.com` + separate Gamma and Data services (3 hosts) | `api.polymarket.us` (1 unified host) |
| Auth | Two-level: EIP-712 L1 + HMAC-SHA256 L2 | Single-level: Ed25519 keypair, SDK signs every request with timestamp |
| Official SDK | `py-clob-client` | `polymarket-us` |
| Market ID format | Token IDs (long hashes) | Human-readable slugs (e.g., `btc-100k-2025`) |
| Order signing | Explicit `create_order()` step | SDK handles internally |
| Price format | Float (e.g., `0.55`) | JSON object: `{"value": "0.55", "currency": "USD"}` |
| Collateral | USDC on Polygon | USDC.e |
| KYC | Optional (non-US only) | Required |
| Liquidity | Global pool | Separate US pool; positions cannot be migrated |
| Market set | Full catalog | More conservative — fewer markets, especially in politically sensitive categories |

**Implication for the bot:** the `polymarket/` client package (per §9 Phase 3) must be built against the US API, not the Global CLOB. The `polymarket-us` SDK exists and handles signing; evaluate during Phase 3 whether to vendor the SDK or implement the Ed25519 signing path directly to match this repo's hand-rolled client style in [`kalshi/`](../../kalshi/).

### Q3 — State eligibility for the operator's residence?

> Which states is the operator's residence currently cleared for under Polymarket US's state list?

**ANSWER: Operator is in Colorado — CLEAR as of 2026-04-22. Phase 0 Q3 passes.**

Colorado-specific findings (per [9news, 2026-04-09](https://www.9news.com/article/news/politics/colorado-betting-war-prediction-markets/73-84fd9cce-161c-41a7-9e27-3c573c2758f9)):
- Colorado has **not** issued cease-and-desist letters to Kalshi or Polymarket.
- The Colorado Department of Revenue (regulator of state sportsbooks) is "monitoring ongoing national litigation" but has declined to comment on specific platforms or take enforcement action.
- Colorado state Sen. Matt Ball (D), sponsor of current-session sports-betting legislation, confirmed on record that "platforms registered with the CFTC benefit from federal preemption of state law." Colorado's attorney general's office declined to confirm or comment on any potential investigations.
- Colorado's Governor and a Colorado US Senator have *called for legislation* against prediction markets, but 9news reports "states may have little authority to act." Colorado is taking a monitoring-only posture, not enforcement.
- **Category caveat (Ball, same source):** "states can still sue prediction market providers that offer contracts resembling sports gambling" (citing the Massachusetts precedent against Kalshi sports contracts). Political and geopolitical markets "remain largely beyond state reach." This is directly favorable for this bot, whose edge is LLM-driven geopolitical analysis, not sports. Do not plan to trade sports-category contracts on Polymarket from Colorado until the state-preemption question is resolved by the courts.

Broader-US state landscape as of April 2026 (for context and for re-verification before each enable action):
- **Confirmed geoblocked:** Massachusetts.
- **Active state-level restriction / litigation (high risk of geoblock or trading-category restriction):** Nevada (Gaming Control Board lawsuit filed January 2026), Tennessee (Sports Wagering Council cease-and-desist), Ohio, Connecticut, New Jersey, Pennsylvania, Maryland, New York (pending legislation on sports-style contracts).
- **Aggregate:** 11+ states have issued cease-and-desist orders against prediction-market platforms as of March 2026; active litigation in 8+ states. The landscape is moving; re-verify the operator's Colorado status on the day each branch opens (Phase 1 kickoff, Phase 4 live-enable).

**Ongoing watch-items for Colorado:**
- Track whether the Governor/Senator push succeeds in passing legislation — the 9news article frames this as low-probability in the current session, but monitor before each enable action.
- Track the Third Circuit decision on the NJ/Kalshi preemption case (and the Kalshi Supreme Court petition per [Fortune, 2026-04-20](https://fortune.com/2026/04/20/kalshi-supreme-court-sports-betting-prediction-markets/)); an adverse preemption ruling could shift state posture nationwide.

### Q4 — FCM intermediation / per-order escrow / funding latency?

> Does the Polymarket US platform require per-order funded escrow via the broker, and what is the funding latency?

**ANSWER (partial):** Polymarket US is structured under an "intermediated" model where Futures Commission Merchants (FCMs) serve as CFTC-registered broker intermediaries for customer funds. However, Polymarket acquired QCEX (the exchange + clearinghouse now operating as "QCX LLC d/b/a Polymarket US"), so the exchange and clearinghouse are vertically integrated. The retail self-service path (iOS KYC → developer portal → Ed25519 API keys) routes orders directly to `api.polymarket.us` — the FCM layer is present for funds custody and compliance (segregated accounts, KYC, tax reporting), not as an order-routing gate. Per-order funded escrow is the clearing model (standard for CFTC-regulated DCMs); specific funding latency figures for the retail self-service path are not documented in fetched public sources.

**Confirm during Phase 1 onboarding:** (a) whether retail orders flow directly to `api.polymarket.us` without per-order FCM round-trip, (b) funding-settlement latency (same-day, T+1, etc.), (c) whether a specific FCM must be selected at account creation.

### Q5 — Resolution and settlement mechanism?

> Does Polymarket's resolution model guarantee atomic credit to a predictable wallet/account within a bounded window?

**ANSWER (partial):** Polymarket US resolution goes through QC Clearing (the CFTC-registered clearinghouse acquired with QCEX), not UMA optimistic oracle (which remains the Global-CLOB mechanism). Clearinghouse-mediated settlement means standard CFTC Part 39 clearing rules apply. Public documentation in fetched sources does not specify the resolution latency window, dispute/appeal process, or whether credit is atomic-on-resolution-event. This must be pulled from the exchange's own rulebook (CFTC Part 38 filings; see [`cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571`](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571)) during Phase 3 before the bot's `resolve_market()` flow is adapted for Polymarket markets.

### Q6 — Market-ID stability?

> Are Polymarket ticker/market IDs stable across the contract lifetime, or can they change on market edits?

**ANSWER: Stable. Human-readable slugs (e.g., `btc-100k-2025`), not mutable hashes.** Per the US API rate-limit guidance, "market slugs and event structure change infrequently" — i.e., clients are expected to cache slugs locally. Namespacing as `polymarket:slug` alongside Kalshi's `kalshi:TICKER` in the venue-namespaced ticker tuple (per §4 / §9 Phase 2) is straightforward.

### Q7 — Read-only market-data WebSocket without auth?

> Is there a free-to-use market-data WebSocket that works without authenticated L1/L2 credentials?

**ANSWER: NO, not for Polymarket US.** Per the official US API guide, "there is no sandbox, demo, or unauthenticated access mode." Even the public `/v1/ws/markets` endpoint requires an Ed25519 API key — the "public" in its name refers to the data being non-private (market-level quotes, not private order state), not to the endpoint being unauthenticated. Polymarket **Global** does expose a no-auth Gamma REST surface, but US IPs are geoblocked from Global at the country level per the [official Polymarket geoblock doc](https://docs.polymarket.com/api-reference/geoblock) — the Blocked Countries table lists `US | United States | Blocked`, and the runtime geoblock check at `https://polymarket.com/api/geoblock` returns `{blocked: true, country: "US", region: "<state>"}` for any US IP. Colorado residence does not change that — Global blocks at country level, so the state field is moot.

**Material impact on §9 Phase 1:** the plan called for Phase 1 to be a "no auth, no orders" read-only observer. That is not achievable on Polymarket US. Phase 1 must begin with the KYC + Ed25519 onboarding step, then run a read-only observer against the authenticated `/v1/ws/markets` WebSocket (still no orders). The scope adjustment is additive, not structural — no orders still means no trading risk — but the entry gate for Phase 1 now includes completing KYC.

**Implementation note (Phase 1):** Polymarket's geoblock data model exposes state-level granularity (the response schema returns `country` + `region`, not just a boolean). If `api.polymarket.us` exposes an analogous endpoint — likely, given shared infrastructure patterns — Phase 1 code should call it at startup and at each trading session-open as an automated eligibility pre-check, rather than relying on the human "re-verify Colorado on the day" step in §9 Phase 1. This upgrade should land as part of the Phase 1 read-only observer module, not as a post-hoc addition. Confirm the exact endpoint path during Phase 1 kickoff.

### Phase 0 summary

**Phase 0 research is CLOSED as of 2026-04-22. All gating questions have conclusive answers. Phase 1 is BLOCKED on an external waitlist, not on research or scope.**

- Q1 = API exists and is live; retail onboarding is waitlist-gated; operator is at position #1,302,257 as of 2026-04-22.
- Q2 = different API entirely (Ed25519 single-level; different SDK; plan for a new `polymarket/` package).
- Q3 = operator is in Colorado, which is clear of geoblock and enforcement; sports contracts carry residual state risk, geopolitical contracts do not.
- Q4, Q5 = partial; sufficient to proceed to Phase 1 once waitlist clears; remaining specifics get confirmed during onboarding (Phase 1) and CFTC Part 38 rulebook review (Phase 3).
- Q6 = stable human-readable slugs.
- Q7 = no unauthenticated read-only surface on Polymarket US; Phase 1 entry gate includes KYC + Ed25519 keypair, which are themselves gated on waitlist clearance.

**Holding-pattern posture:** the initiative does not consume attention until the waitlist clears. Do not promote Phase 2 (Kalshi refactor) to start prophylactically — its value depends on Phase 1 divergence data, and refactoring without that evidence risks wasted work if Phase 1 later shows dual-venue is not worth pursuing. Continue the existing Kalshi pre-live stages (PROFIT-CAL-001, Stage 5 Phase 2, etc.) in the meantime. Revisit this doc at Phase 1 entry.

---

## 9. Execution plan — strictly sequential, one phase at a time

The six phases below execute **in order, one at a time, on separate branches**. Do not begin phase N before phase N−1 has merged to `main` and its exit gate has been verified. Do not bundle phases. Do not skip phases. There is no parallel track.

Bot is at v0.29.37 as of 2026-04-22, paper-mode, working Stage 5 Phase 2 and the pre-live `PROFIT-CAL-001` blocker. That pre-live blocker matters only at phase 4 (first real-money Polymarket order); it does not gate phases 0–3.

### Phase 0 — Resolve open questions  (documentation only; no branch)

- **Predecessor:** none. This is the starting phase.
- **Entry gate:** none.
- **Scope:** Answer §8 questions 1–3 by reading Polymarket US onboarding docs and broker disclosures. Do NOT sign up. Do NOT write code. Do NOT touch any module outside this file. Update §8 of this document with the answers found.
- **Exit gate:** §8 Q1 has a definitive answer recorded in §8 of this doc. If Q1 = "no programmatic access for US retail," **STOP** — abort the initiative and downgrade to news-source-only per [news_sources_evaluation.md](news_sources_evaluation.md) §3.2. If Q1 = "yes," §8 Q2 and Q3 also answered in the same doc update, then proceed to phase 1.
- **Branch:** none. Edit this file directly via a small doc-only PR on `main`.
- **Deliverable:** one PR, one doc changed (this one), §8 answers recorded.

### Phase 1 — Read-only market-data observer  (branch: `feature/polymarket-market-data-observer`)

- **Predecessor:** phase 0 merged AND exit gate passed AND operator's state confirmed not on geoblock / high-risk list per §8 Q3.
- **Entry gate:** §8 Q1 answered "yes," §8 Q3 state eligibility recorded, **operator off the Polymarket US waitlist** (tracked per §8 Q1 — operator is at position #1,302,257 as of 2026-04-22), **KYC completed on Polymarket US iOS app** and Ed25519 API keypair generated at `polymarket.us/developer`. Credentials stored in `.env` as `POLYMARKET_US_KEY_ID` and `POLYMARKET_US_SECRET` (not committed).
- **Scope:** Wire `api.polymarket.us` via the authenticated `/v1/ws/markets` WebSocket (plus REST for market catalog) as a market-data observer. Emit Polymarket quotes into the evidence store alongside Kalshi quotes. **No orders. No executor changes. No writes to user state beyond read-only market data.** One new file: `feeds/polymarket_market_data.py`. Small schema additions to the evidence store for cross-venue quote storage. No changes to [`trading/`](../../trading/), [`kalshi/`](../../kalshi/), or [`data/paper_trades.db`](../../data/paper_trades.db). Respect the 60 req/min REST cap and 10-instruments-per-WS-connection limit per §8 Q1.
- **Note on auth:** unlike Kalshi's public-markets REST, Polymarket US requires Ed25519 auth even for market-data reads (per §8 Q7). The Ed25519 signing implementation lands in this phase as a minimal read-only signer; the full `polymarket/` client package does not ship until Phase 3.
- **Post-merge runtime:** after the branch merges, let the observer run for **≥2 weeks** on `main` to collect divergence data before evaluating the exit gate.
- **Exit gate:** After the ≥2-week window, data shows ≥5% of overlapping market-hours register ≥3¢ edge-relevant cross-venue divergence. If yes, proceed to phase 2. If no, **STOP** — downgrade scope per the abort triggers below.
- **Deliverable:** one PR. Post-merge the observer runs and a summary note gets appended to §8 of this doc recording the measured divergence.

### Phase 2 — Venue-abstraction refactor  (branch: `refactor/venue-abstraction`)

- **Predecessor:** phase 1 merged AND its ≥2-week observation window complete AND phase 1 exit gate passed.
- **Entry gate:** phase 1 divergence evidence recorded in §8; exit gate passed.
- **Scope:** Kalshi-only refactor. **No Polymarket code of any kind on this branch.** Changes:
  - Introduce `trading/venue_client.py` protocol.
  - Refactor [`trading/executor.py`](../../trading/executor.py) to depend on the protocol, not `KalshiRestClient` directly.
  - Add `venue TEXT` column to [`data/paper_trades.db`](../../data/paper_trades.db) via a reversible migration; backfill existing rows with `venue="kalshi"`; update all INSERT/SELECT sites in `source_credibility.py`, `source_stats.py`, `paper_performance_drilldown.py`, and reporting.
  - Namespace ticker storage as `venue:market_id` tuples end-to-end.
  - Update [`scripts/daily_review.py`](../../scripts/daily_review.py) to surface a `venue` dimension in section 8 (SOURCE SCORECARD).
  - Replace Kalshi-specific `MARKET_SERIES_BLOCKLIST_PREFIXES` with a venue-aware predicate; Kalshi's behavior must remain identical.
- **Exit gate:** all existing tests pass; paper-trade replay of a recorded run produces bitwise-identical trade outcomes pre- and post-refactor; `venue="kalshi"` set correctly on every paper-trade row; `VERSION` + `CHANGELOG.md` updated.
- **Reviewer rule:** reject the PR on sight if any Polymarket-specific code appears. This branch is a pure refactor.
- **Deliverable:** one PR, no behavior change for Kalshi.

### Phase 3 — Polymarket paper-trading client  (branch: `feature/polymarket-client`)

- **Predecessor:** phase 2 merged AND exit gate passed.
- **Entry gate:** phase 2 merged; §8 Q2–Q6 resolved and recorded in §8.
- **Scope:** Implement `polymarket/` package as a peer to [`kalshi/`](../../kalshi/): REST client, WebSocket client, auth (EIP-712 L1 + HMAC-SHA256 L2, or whatever the US-regulated pathway requires per §8 Q2). Cross-venue market matcher that finds equivalent markets across Kalshi and Polymarket. **Paper mode only — no live orders.** Hard-coded per-venue live-trading guard that refuses to execute until phase 4.
- **Test coverage:** full parity with `kalshi/` test suite — auth round-trips, order round-trips in paper mode, WebSocket reconnect, error-path handling.
- **Post-merge runtime:** after the branch merges, run paper trading on Polymarket for **≥2 weeks**.
- **Exit gate:** Polymarket paper trades execute cleanly; cross-venue matcher produces sensible pairings; positive EV net of modeled fees over the ≥2-week paper run. If paper EV is negative or the matcher is unreliable, iterate on this branch — do not proceed to phase 4 with unresolved issues.
- **Deliverable:** one PR merging the client + matcher + paper-mode execution path. Post-merge the ≥2-week paper evidence gets appended to §8 of this doc.

### Phase 4 — Enable Polymarket live trading  (branch: `feature/polymarket-live-enable`)

- **Predecessor:** phase 3 merged AND its ≥2-week paper run complete AND exit gate passed.
- **Entry gate — all three must hold:**
  1. `PROFIT-CAL-001` resolved per [docs/profit_path_debt_log.md](../profit_path_debt_log.md). This is the pre-live blocker on **any** venue; it applies here whether or not Kalshi is already live.
  2. Phase 3 paper-run evidence (≥2 weeks) shows positive EV net of fees, recorded in §8.
  3. §8 Q7 state eligibility re-confirmed for the operator's residence on the day the branch opens.
- **Scope:** smallest possible diff flipping the live-trading guard on a narrow market subset (recommend: fee-free geopolitics category only). Bounded bankroll cap. Per-venue execution-criteria preservation per the [CLAUDE.md](../../CLAUDE.md) domain constraint on `/trading`. No other changes bundled.
- **Exit gate:** live orders execute cleanly for a bounded observation window; no regression on existing Kalshi behavior. Revert immediately on any unexpected fill, settlement, or resolution behavior.
- **Note on Kalshi-live sequencing:** this phase does not require Kalshi to already be live. The recommended order (Kalshi live → Polymarket live) reflects risk preference, not a technical dependency. If Kalshi-specific live-trading blockers persist after `PROFIT-CAL-001` is resolved, Polymarket-first live is defensible.
- **Deliverable:** one PR enabling live trading for a narrow subset.

### Phase 5 — Multi-outcome market support  (branch: `feature/polymarket-multi-outcome`)

- **Predecessor:** phase 4 merged AND ≥1 month of stable live operation with no regression.
- **Entry gate:** phase 4 live-run clean for ≥1 month, recorded in §8.
- **Scope:** extend the analysis layer, matcher, and executor to support 3+ outcome markets on Polymarket. Kelly sizing over categorical distributions. Binary-only assumption removed from the probability/pricing pipeline.
- **Exit gate:** multi-outcome markets trade cleanly in paper mode first, then live on a bounded subset.
- **Deliverable:** one PR per sub-step (paper-first, then live-enable for multi-outcome).

---

### Branching rules (apply to every branch)

- Branch off the current `main`; do not stack phase branches. Phase N starts only after phase N−1 has merged.
- Keep diffs scoped to the single phase; no bundled refactors (per [CLAUDE.md](../../CLAUDE.md) "Editing Safety").
- Preserve execution-criteria gates (positive-EV, paper-trading support, explicit live-trading) on every PR per the domain constraint on `/trading`.
- Rebase on `main` to keep history linear; do not long-lived-merge.
- Update `VERSION` + `CHANGELOG.md` in the same commit as any shipped-behavior change, per the release-versioning rule.
- Every PR description must cite this document and the specific phase entry/exit gates it satisfies.

### Abort triggers (stop or cancel the whole initiative)

- Phase 0: §8 Q1 answers "no programmatic access for US retail."
- Phase 1: <5% of overlapping markets show meaningful cross-venue divergence after the ≥2-week observation window.
- Any phase: state-level geoblocking changes eligibility for the operator's residence.
- Any phase: regulatory posture shifts (e.g., CFTC revokes or amends the designation).
- Any phase: a Kalshi-edge bug is discovered that requires focus — pause this initiative, fix Kalshi, resume.

---

## 10. Recommendation

**Phase 0 research CLOSED 2026-04-22. Phase 1 is BLOCKED on the Polymarket US retail waitlist. Initiative is on hold, not abandoned.**

All §8 questions have conclusive answers. The initiative is technically viable (§8 Q1 = API exists), architecturally straightforward (§8 Q2 / Q6 — new client package, stable slugs), and regulatorily available for the operator (§8 Q3 — Colorado is clear of enforcement and benefits from CFTC federal preemption). However, §8 Q1 surfaces a practical blocker that was not visible in the initial research: retail KYC onboarding is waitlist-gated, and the operator sits at position #1,302,257 as of 2026-04-22. Phase 1 cannot start until the operator has a working account with Ed25519 credentials.

**Holding-pattern posture:**

- Do not open any Polymarket code branch.
- Do not start Phase 2 (Kalshi refactor) prophylactically — its value is gated on Phase 1 divergence data. Refactoring now risks wasted work if Phase 1 later shows dual-venue is not worth pursuing.
- Continue Kalshi pre-live work (`PROFIT-CAL-001`, Stage 5 Phase 2, etc.). That work has independent value whether or not Polymarket integration ever happens, and it's the right place for focus while the waitlist clears.
- Optionally: share the waitlist referral link through whatever channels make sense for you; it's the only publicly documented mechanism to advance the position.
- Track waitlist clearance. When the operator has an active Polymarket US account + KYC complete + Ed25519 keypair generated, return to this doc and open the Phase 1 branch per §9.

**Value-proposition note.** The most valuable single angle in the earlier draft — zero-fee geopolitics on the Global CLOB — is *not* available to US residents (Global is geoblocked at country level per the [official Polymarket geoblock doc](https://docs.polymarket.com/api-reference/geoblock)). Polymarket US has its own fee schedule (flat 0.30% taker per §3). The US integration's value is therefore correctly framed as *second-venue resilience + cross-venue arbitrage on overlapping political/geopolitical events + Polymarket-exclusive market coverage*, per §5. Phase 1 empirical divergence data will quantify the arbitrage angle once it can run.

---

## 11. References

- [docs/ROADMAP.md](../ROADMAP.md) Appendix A — existing news-source entry, now partially superseded by this doc for the venue scope
- [docs/plans/news_sources_evaluation.md](news_sources_evaluation.md) §3.2, §7 — original news-source-lane framing
- [CLAUDE.md](../../CLAUDE.md) — Kalshi signing, WebSocket auth, signal analysis, and bet-sizing gotchas; all remain in force and apply per-venue
- [Polymarket CLOB authentication docs](https://docs.polymarket.com/developers/CLOB/authentication) — Global CLOB auth surface (not applicable for US retail)
- [Polymarket Global geoblock doc](https://docs.polymarket.com/api-reference/geoblock) — primary-source proof that `US | United States | Blocked` at country level on Global; documents the live geoblock-check endpoint and response schema (`{blocked, country, region}`)
- [py-clob-client](https://github.com/Polymarket/py-clob-client) — official Python client for Global CLOB (not applicable for US retail)
- [Polymarket fees](https://docs.polymarket.com/trading/fees) — dynamic fee structure for Global CLOB; Polymarket US is a flat 0.30% taker
- CFTC Amended Order of Designation (2025-11) — source of US regulatory pathway
- [Polymarket US developer resources](https://www.polymarketexchange.com/developers.html) — official onboarding, Exchange Gateway, sandbox application path
- [Polymarket US Exchange Gateway docs](https://docs.polymarket.us/getting-started/welcome) — official API documentation (replaces deprecated Trading Gateway)
- [Polymarket US API guide (agentbets.ai, March 2026)](https://agentbets.ai/guides/polymarket-us-api-guide/) — third-party reference for retail self-service flow, Ed25519 auth, rate limits, slugs
- [QCX LLC d/b/a Polymarket US CFTC filings portal](https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571) — exchange rulebook filings (resolution/settlement specifics live here)
- [TradeTheOutcome — intermediated-model explainer](https://www.tradetheoutcome.com/polymarket-returns-to-the-us-the-intermediated-model-explained/) — FCM flow and KYC requirements
- [Turtelli — state-by-state legality April 2026](https://www.turtelli.com/insider-knowledge/polymarket-prediction-markets-for-trading/is-polymarket-legal) — state restriction landscape
