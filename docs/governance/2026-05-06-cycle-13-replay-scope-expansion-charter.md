# Cycle-13 charter — replay scope expansion to full evidence_store

**Type:** focused single-deliverable cycle. NOT a 10+10 split.
**Drafted:** 2026-05-06 post-cycle-12 (commit `aadd391`).
**Owner:** Codex (implementation); Claude (review).
**Authority:** strategic redirect doc + IC §16 + Cycle-12 readiness inventory.

## TL;DR

Cycle-12 replay harness ran against `paper_trades` scope only — 3 markets, 1 source, 1 series, 0 positive-EV slices. Per Cycle-12 readiness inventory, `evidence_store.db` has **24 resolved markets** with **266 evidence rows** spanning 16 days, dominated by mainstream news (Guardian / NYT / Al Jazeera / France 24 = 81 % of evidence). Expanding replay scope from 3 → 24 markets is the obvious next move and was explicitly noted as future work in `edge-replay-cycle12-report.md` §"Data Limitation."

**Cycle-13 goal:** rerun the harness against the full 24-resolved-market scope. If ≥ 1 (source × market_family × signal_type) slice has positive replayed EV at 95 % CI with `trades ≥ 10`, that slice becomes the Wave-2 candidate. If still none, IC §16 Rule 5 fires: strategic-pivot conversation.

## Scope (singular)

Expand the replay scope. Nothing else. No additional features, no schema changes, no UI, no doc cleanup. The harness exists; this cycle just feeds it more data.

## Codex deliverables

### 1. Run live-Kalshi resolved-market fetch

```bash
.venv/bin/python scripts/edge_replay/fetch_resolved_markets.py \
  --live-kalshi \
  --output docs/governance/edge-replay-cycle12-artifacts/resolved_markets_full.json
```

If `fetch_resolved_markets.py` doesn't have a `--live-kalshi` flag yet (Cycle-12 first pass used `--from-paper-trades-db`), Codex extends it to query Kalshi's `get_markets(status='settled')` AND `get_markets(status='finalized')` per Cycle-12 readiness inventory's API gotcha. Filter the result to the intersection of (Kalshi-resolved markets) ∩ (markets present in `evidence_store.db`).

Expected: ~24 resolved-market records, dump as `resolved_markets_full.json`.

### 2. Rebuild dataset

```bash
.venv/bin/python scripts/edge_replay/build_replay_dataset.py \
  --markets docs/governance/edge-replay-cycle12-artifacts/resolved_markets_full.json \
  --paper-trades-db data/paper_trades.db \
  --evidence-store-db data/evidence_store.db \
  --trade-log 'logs/**/*.jsonl' \
  --output docs/governance/edge-replay-cycle12-artifacts/replay_dataset_full.jsonl
```

Expected: ≥ 266 dataset rows (one per evidence_store ingestion, plus paper-trade rows). Per Cycle-12 readiness inventory, joining 266 evidence rows × 24 resolved markets via `dossier_update_evidence` may produce up to 7,580 candidate rows depending on join semantics. Codex's harness already handles per-market join.

### 3. Rescore counterfactual P&L

```bash
.venv/bin/python scripts/edge_replay/score_counterfactual_pnl.py \
  --dataset docs/governance/edge-replay-cycle12-artifacts/replay_dataset_full.jsonl \
  --output docs/governance/edge-replay-cycle12-artifacts/counterfactual_scores_full.json
```

Expected: per-(source × market_family × signal_type) slice table covering all 24 resolved markets. Slice population grows from 2 (current) to potentially 20+ (mainstream news sources × multiple market families).

**Critical scoring constraint:** for evidence rows where the bot did NOT actually trade (no matching `paper_trades` row), counterfactual scoring requires "would the bot have traded under the current readiness gate?" That's a forward-pass through G1-G6 admission logic. Either:
- (a) Codex re-runs the readiness gate against each candidate's `model_prob` + `confidence` to determine `would_have_traded`. Then `would_have_won` derives from the resolution.
- (b) Codex falls back to "edge-as-implied-by-evidence" without full readiness-gate replay. Less accurate but cheaper.

Path (a) is the rigorous answer. Path (b) is acceptable if (a) requires non-trivial pipeline reconstruction; document the choice in the cycle-13 report.

### 4. Update report

`docs/governance/edge-replay-cycle12-report.md` is the canonical replay-result document. Codex extends it with a new section "Cycle-13 Expanded Scope Result" that:

- Reports the new slice table.
- Identifies positive-EV slices (if any) at the success criterion: `ev_ci_95_lo > 0` AND `trades ≥ 10`.
- Lists slices with positive raw EV but `trades < 10` as "low-power, possible signal — gather more evidence."
- Reports negative-result honesty per IC §16 Rule 5 if still no positive-EV slice with sufficient n.

OR Codex authors a sibling `docs/governance/edge-replay-cycle13-report.md` — operator preference.

## Decision tree at result

| outcome | next step |
|---|---|
| ≥ 1 slice with `ev_ci_95_lo > 0` AND `trades ≥ 10` | Operator + Claude design a Wave-2 deploy that targets THAT slice. NOT the speculative legal/geopolitics feeds. Spec authoring becomes Cycle-14. |
| Slices with positive raw EV but `trades < 10` | Document as "low-power signal." Cycle-14 = gather more evidence (extend evidence_store window; tag specific market families for deeper coverage). NO behavioral deploy yet. |
| No positive-EV slice at any n | IC §16 Rule 5 fires: strategic-pivot conversation. Three diagnoses to consider: (1) calibration — model produces market-equivalent probabilities; (2) sample size — 16-day window insufficient; (3) information frontier — no edge available at this trader's data access. Each has a different fix. |

## Out of scope for Cycle-13

- New features for the harness (price-history reconstruction, intra-market-lifetime EV, etc.). Those are Cycle-14+ if path (a) shows they're needed.
- Wave-2 / Wave-3 deploy work. Still HALTED per IC §16 unless cycle-13 produces positive-EV evidence.
- More HALT markers, doc cleanup, or governance work. Cycle-12 already covered those.
- Lever D escalation paths. Subsumed by IC §16.

## Operator decision points

1. **Live API call OK?** Cycle-13 needs a live Kalshi `get_markets` call to fetch resolved-market records (read-only, no trading). Confirm permission.
2. **Path (a) vs (b) for `would_have_traded` scoring?** Operator preference; recommend (a) if Codex estimates ≤ 4h work; else (b) with caveat.
3. **Report location?** Extend `edge-replay-cycle12-report.md` or new sibling `edge-replay-cycle13-report.md`.

## Cross-links

- `docs/governance/edge-replay-cycle12-report.md` — Cycle-12 first-pass result
- `docs/governance/2026-05-06-cycle-12-replay-readiness-inventory.md` — Cycle-12 readiness map (24 resolved markets identified)
- `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md` — strategic redirect authority
- `docs/IMPLEMENTATION_CONTRACT.md` §16 — replayed-EV gate
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-005` — replay harness debt entry
- `scripts/edge_replay/{fetch_resolved_markets,build_replay_dataset,score_counterfactual_pnl}.py` — harness Codex extends
