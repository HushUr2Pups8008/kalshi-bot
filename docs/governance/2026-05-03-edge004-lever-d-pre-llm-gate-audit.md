# EDGE-004 Lever D Pre-LLM Gate Re-Enable Audit

Read-only sweep over the 13-day MacBook archive. The simulation keeps an `OPPORTUNITY` only when its archived `MATCH_DIAGNOSTIC.match_score` clears the tested floor and the archived pre-LLM gate diagnostics say the candidate would pass.

## Summary

- MATCH_DIAGNOSTIC total: 2838
- SIGNAL_ANALYSIS_DETAIL total: 1315
- OPPORTUNITY total: 260
- PAPER_TRADE total: 3
- Baseline positive-edge OPPORTUNITY: 2
- Baseline nonzero-edge OPPORTUNITY: 5
- Limitation: Archive MATCH_DIAGNOSTIC records only cover candidates that cleared the live matcher. Thresholds below the live 0.06 floor cannot recover never-logged below-floor candidates.

## Retention Curve

| min_match_score | diagnostics retained by gate | OPPORTUNITY retained | OPPORTUNITY retention | score-blocked OPP | gate-blocked OPP | PAPER_TRADE retained | positive-edge retained | nonzero-edge retained | mean abs edge lift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.04 | 943 | 67 | 25.8% | 0 | 193 | 3 | 1/2 | 4/5 | 3.17x |
| 0.05 | 943 | 67 | 25.8% | 0 | 193 | 3 | 1/2 | 4/5 | 3.17x |
| 0.06 | 943 | 67 | 25.8% | 0 | 193 | 3 | 1/2 | 4/5 | 3.17x |
| 0.08 | 516 | 56 | 21.5% | 117 | 87 | 3 | 1/2 | 4/5 | 3.79x |

## Read

Lever D is an aggressive volume filter, not a clean EDGE-004 primary fix. At the live floor range (0.04-0.06) it retains 67/260 OPPORTUNITY records (-74%) while preserving all 3 historical PAPER_TRADE records and 4/5 nonzero-edge OPPORTUNITY records. At 0.08 it tightens further to 56/260 (-78%) and still preserves all 3 historical PAPER_TRADE records.

The edge enrichment is real but partly misleading: the retained set has a higher mean absolute edge because it keeps the three FISA paper trades, which were losing same-series trades and are already the EXEC-002 target. Lever D also retains only 1/2 positive-edge non-trades, so it would hide one known OBS-003 positive-edge miss rather than make it executable.

## Recommendation

Do not select Lever D as the first EDGE-004 Wave-2 implementation. Use it as a secondary LLM-budget / noise-control knob after MATCH-001 B' and OBS-003 make the post-fix attribution stream visible. Lever A remains the better first design target despite its weak standalone case because it changes weighting rather than deleting roughly three quarters of the archive OPPORTUNITY surface.
