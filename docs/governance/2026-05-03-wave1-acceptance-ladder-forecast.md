# Wave-1 Acceptance-Ladder Forecast

Wave-1 expected end state after all four items: 87 OPPORTUNITY, 89 SKIPPED, 1 PAPER_TRADE on the 13-day archive counterfactual.

| item | validation window | OPPORTUNITY | SKIPPED | PAPER_TRADE | expected post-state | rollback trigger |
| --- | --- | ---: | ---: | ---: | --- | --- |
| OBS-005 | 24h | 260 | 17 | 3 | No archive-visible count change; cooldown sentinel no longer blocks never-traded tickers after restart. | Fresh-process never-traded ticker still gets cooldown-blocked, or cooldown bypasses a genuinely recent paper trade. |
| MATCH-001_B_prime | 24h | 87 | 9 | 3 | OPPORTUNITY retained forecast 87/260; PAPER_TRADE retained 3/3 before EXEC-002; MATCH_SUPPRESSED rate rises materially. | Any canonical-event headline is suppressed for its canonical ticker, or suppression count moves opposite the forecast. |
| OBS-003 | 24h | 87 | 87 | 3 | Visible SKIPPED forecast 87 retained records after MATCH-001; dominant new reasons G1=59, G6=14, G2=5. | OPPORTUNITY without PAPER_TRADE/SKIPPED persists, or SKIPPED payload misses required keys. |
| EXEC-002 | 72h | 87 | 89 | 1 | PAPER_TRADE forecast drops from 3 to 1 on archive due same-series burst suppression; SKIPPED +2. | Same-series duplicate paper trades still occur inside 1h, or unrelated series are suppressed. |

## Notes

- Forecast uses the existing 13-day MacBook archive replay, not live post-soak records.
- Counts are acceptance windows, not guarantees; live feed mix can shift.
- The ladder pairs with the pre-soak-close rehearsal checklist and post-soak rollback runbook.
