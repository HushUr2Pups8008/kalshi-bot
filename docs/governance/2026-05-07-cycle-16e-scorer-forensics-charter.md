# Cycle-16E charter — replay scorer forensics

**Type:** focused forensics + harness-correction cycle.
**Drafted:** 2026-05-07 after operator override of Cycle-16D verdict consumption.
**Authority:** PROFIT-EDGE-010; Cycle-16D operator override appendix in `edge-replay-cycle16d-report.md`.
**Owner:** Codex implementation; Claude review / verdict consumption.
**Status:** ACTIVE until `edge-replay-cycle16e-scorer-forensics.md` is reviewed.

## TL;DR

Cycle-16D produced `237` counterfactual trades and `2` wins. That number is not accepted as an operational verdict until the scorer is audited. Cycle-16E answers whether the replay scorer is modeling production-like paper trading, or whether it over-admits rows that production gates would reject.

## Locked Checks

1. **Price units.** Verify `historical_prices_cycle16d.json` is cents-consistent from fetch to score. If any endpoint emits dollars that were treated as cents, Cycle-16D is invalid until prices are rewritten.
2. **Trade admission semantics.** Compare raw `abs(edge) >= min_edge` scoring against a production-proxy gate that requires readiness admission, signed edge consistency, and paper-mode price sanity.
3. **Episode gating.** Apply paper-mode same-ticker cooldown and open-position duplicate rules so repeated dossier updates do not become repeated trades unless production would allow them.
4. **Diagnostic cuts.** Report win rate by side, series, price bucket, and admission / skip reason.
5. **Corrected D6 re-run.** Emit corrected counterfactual scores and an IC §16 slice verdict after the scorer corrections.

## Acceptance

Cycle-16E passes when:

- `logs/edge_replay/cycle16e/scorer_forensics.json` exists and contains all five checks.
- `logs/edge_replay/cycle16e/counterfactual_scores_production_proxy.json` exists and is reproducible from one command.
- `docs/governance/edge-replay-cycle16e-scorer-forensics.md` states whether Cycle-16D's operational reading is confirmed, narrowed, or withdrawn.
- Capital posture remains PAPER-ONLY.

## Out of Scope

- Bot extraction changes.
- Source onboarding.
- Live-trading flag changes.
- New Wave-2 / Wave-3 deploy work.

## Cross-links

- `docs/governance/edge-replay-cycle16d-report.md` — withdrawn operational read and operator override.
- `scripts/edge_replay/scorer_forensics_audit.py` — Cycle-16E implementation.
- `logs/edge_replay/cycle16e/scorer_forensics.json` — audit output.
- `docs/profit_path_debt_log.md` `PROFIT-EDGE-010` — tracker.
