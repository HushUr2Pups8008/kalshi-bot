# Cycle-13 replay harness code review

**Type:** Claude review of Codex's `scripts/edge_replay/` (commit `aadd391`).
**Drafted:** 2026-05-06 cycle 13.
**Purpose:** surface honest concerns for Codex to act on before / during Cycle-13 expansion run.

## Scope reviewed

3 files, 539 lines:
- `scripts/edge_replay/fetch_resolved_markets.py` (145 lines)
- `scripts/edge_replay/build_replay_dataset.py` (244 lines)
- `scripts/edge_replay/score_counterfactual_pnl.py` (150 lines)

## What works well

- **Bootstrap CI** (`score_counterfactual_pnl.py` lines 116-129): random-resampling with seeded RNG. Cycle-13 task #5 done preemptively.
- **Per-decision-time price reconstruction** (`build_replay_dataset.py` lines 206-242): `apply_historical_prices` walks per-ticker price rows + finds latest `<= decision_ts`. Cycle-13 task #6 done preemptively.
- **Left-on-table measure** (`score_counterfactual_pnl.py` lines 100-112): `would_have_won_if_taken` computed when readiness admits but edge < min_edge. Cycle-13 task #7 done preemptively.
- **Synthetic positive-EV self-test** (`tests/test_edge_replay_score_counterfactual_pnl.py:76-98`): validates scorer can FIND edge when present, not just report negative. Cycle-13 Claude task #1 done preemptively.
- **PnL math verified for 50¢ markets**: yes/no × win/loss combinations all correct per Kalshi binary contract semantics.

## Findings (Codex action items)

### 🔴 Finding 1 — Readiness-gate thresholds are aspirational, not production

`_readiness_admitted` (lines 63-74) defaults:
- `readiness_confidence=0.85`
- `readiness_yes_threshold=0.60`
- `readiness_no_threshold=0.40`

Production Trade Readiness Gate (`tasks/trade_readiness_gate.py:69-70`):
- `G1_CONFIDENCE_THRESHOLD = 0.05`
- `G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10`

The harness's confidence threshold (0.85) is **17× tighter than production G1 (0.05)**. The yes/no thresholds (0.60/0.40) are also tighter than the production gate's actual logic.

**Why this matters:** "would_have_traded" replay should match the bot AS DEPLOYED, not a hypothetical post-Lever-B-aspirational state. Currently the harness will under-count `would_have_traded` opportunities — many evidence rows that production WOULD admit get rejected by the harness's stricter gate.

**Cycle-13 fix (charter path a):** for the production-replay run, default thresholds to production values:
```python
readiness_confidence=0.05,
readiness_yes_threshold=0.50,   # production has no yes/no admission asymmetry; use signed-edge ≥ min_edge
readiness_no_threshold=0.50,
```

OR replicate the actual G1-G6 readiness sequence in `_readiness_admitted` rather than the simplified threshold check.

### 🟡 Finding 2 — `signal_type` semantics divergence across source tables

`build_replay_dataset.py` populates `signal_type` differently per source:
- paper_trade rows (line 94): `trade.get("signal_type")` from paper_trades column (typically `"news"`, `"fast_lane"`).
- log rows (line 134): `row.get("signal_type") or row.get("type")` — could be `"OPPORTUNITY"`, `"BLEND_DECISION"`, etc.
- evidence_store rows (line 195): `update.get("dossier_update_type") or update.get("evidence_update_type")` — typically `"state"`, `"evidence"`.

Cycle-12 first-pass output showed `signal_type` values `"state"` (evidence_store) and `"blend"` (paper_trade) grouped under same column name in slice keys.

**Why this matters:** the slice `(VitalLaw × KXFISAEXTEND × signal_type=blend)` mixes paper_trade rows (signal_type='blend' from paper_trades column) with potentially evidence_store rows (dossier_update_type='blend'). Apples-and-oranges aggregation.

**Cycle-13 fix:** rename per-source: `paper_trade_signal_type`, `log_signal_type`, `dossier_update_type`. Or add a unified `decision_kind`-prefixed value (e.g. `"paper_trade:blend"` vs `"dossier_update:state"`) so slicing doesn't conflate.

### 🟡 Finding 3 — Sizing fixed at 1 contract for counterfactual decisions

`score_candidate` line 89: `contracts = int(_as_float(row.get("contracts")) or default_contracts)`. `default_contracts=1`. Paper-trade rows preserve `contracts=5` (actual sized bet); evidence-store rows default to 1.

**Why this matters:** counterfactual P&L for evidence-store rows under-states dollar magnitude. If a positive-EV slice is identified, the actual bet size (Kelly-driven) would be larger; replay shows 1-contract P&L which is conservative but not the realistic projection.

**Cycle-13 fix (optional):** apply a sizing model to counterfactual rows. Either (a) Kelly-sizing on `model_prob` and assumed bankroll, or (b) flat 5 contracts (matches actual paper-trade history). Document the choice.

### 🟢 Finding 4 — `price_cents` fallback for paper_trade rows is conceptually wrong but not currently breaking

`build_replay_dataset.py` line 91:
```python
"market_yes_price": _as_float(trade.get("market_yes_price") or trade.get("price_cents")),
```

`price_cents` is the bot's BUY price (cost basis), not the market YES price. For YES-side trades they coincide; for NO-side trades at non-50¢ markets, they differ (`price_cents` ≈ `100 - market_yes_price`).

**Why this matters:** for the 3 historical paper trades (all 50¢ markets, all NO-side), they coincidentally match. For future paper trades at other prices, replay would corrupt `market_yes_price` if `market_yes_price` column is null.

**Cycle-13 fix (defensive):** if `market_yes_price` is missing, derive from `price_cents` via:
```python
if side == "no":
    market_yes_price = 100 - price_cents
else:
    market_yes_price = price_cents
```

### 🟢 Finding 5 — `series_ticker` derivation fallback is fragile for some Kalshi tickers

`_market_fields` line 59: `market.get("series_ticker") or ticker.split("-")[0]`. For `KXFISAEXTEND-26APR-MAY01` → `KXFISAEXTEND` (correct). For `KXMVESPORTSMULTIGAMEEXTENDED-S2026FA4A1C48163-2FF61ABFC82` → `KXMVESPORTSMULTIGAMEEXTENDED` (correct, but unusual format).

**Why this matters:** Kalshi's `series_ticker` API field is the authoritative source. The split fallback works for typical tickers but may break on edge-case naming conventions.

**Cycle-13 fix:** confirm Kalshi `/markets/{ticker}` returns `series_ticker` for our 24 markets; if yes, the API value (NOT the split) populates the row.

## Action items priority

| finding | severity | priority | scope of fix |
|---|---|---|---|
| 1: readiness thresholds | 🔴 high | NOW | match production G1 in default parameters |
| 2: signal_type divergence | 🟡 medium | Cycle-13 | rename per source-table OR prefix |
| 3: sizing fixed at 1 | 🟡 medium | Cycle-13 | document or implement Kelly |
| 4: price_cents fallback | 🟢 low | defensive | conditional based on side |
| 5: series_ticker | 🟢 low | defensive | use API value not split fallback |

## Out of scope for this review

- Statistical interpretation (covered by Cycle-13 charter + pivot playbook).
- Test coverage gaps beyond the synthetic +EV self-test (Codex tests look adequate for current scope).
- API-level concerns (covered by `2026-05-06-cycle-13-live-api-coordination.md`).

## Cross-links

- `scripts/edge_replay/{fetch_resolved_markets,build_replay_dataset,score_counterfactual_pnl}.py` — files reviewed.
- `tests/test_edge_replay_*.py` — test coverage.
- `docs/governance/2026-05-06-cycle-13-replay-scope-expansion-charter.md` — Cycle-13 charter (Codex action).
- `tasks/trade_readiness_gate.py:69-70` — production G1 thresholds (referenced in Finding 1).
