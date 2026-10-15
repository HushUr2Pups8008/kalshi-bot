# Strategy and edge

## Method

Read-only audit at 2026-10-15. Code sha 17c7e47 (v0.33.97). Boot line is 2026-10-15 12:22:13 UTC, PID 86845. No orders were sent. No flags, code, or git state were changed.

Live fee-net uses GET /portfolio/settlements and GET /portfolio/fills. Both cursors ended. Settlement `revenue` is integer cents. `yes_total_cost_dollars`, `no_total_cost_dollars`, and `fee_cost` are dollar strings. Fill `fee_cost` matches settlement `fee_cost` on all three settled tickers.

Formula: fee-net = revenue/100 - yes_total_cost_dollars - no_total_cost_dollars - fee_cost.

Paper fee-net is the six rows in `paper_trade_accounting` for cohort kalshi-event-news-20260820, opened read-only. Formula: fee-net = net_settlement_payout_dollars - net_entry_debit_dollars. Fees are the stored taker quote, coefficient 0.07, settlement fee 0. They are not exchange `fee_cost`. The legacy `data/paper_trades.db` was not used.

Capital is one GET /portfolio/balance. `updated_ts` 1792072165 is 2026-10-15 13:49:25 UTC. Cash is `balance_dollars`. Position value is `portfolio_value` / 100.

Logs are `logs/app/bot.log` plus `bot.log.2026-10-08` through `bot.log.2026-10-15`. Timestamps in those files are UTC. The file named `bot.log.2026-10-15` ends at 2026-10-15 00:00:05 UTC and holds 2026-10-14. The scan ends at the last line, 2026-10-15 13:48:02 UTC. Launchd stderr was not added, so lines are not double-counted. Prefix-cap means `blocked reason=event_open_prefix_cap`. Rules mismatch means `skip=mismatch`. YES floor means `yes_below_floor`. Paper rows means `[EVENT_NEWS_PAPER_SHADOW] recorded`.

`logs/edge_replay/corpus_politics_oos.jsonl` is absent. The gate was not run. `REPLAY_GATE_OVERRIDE` was not set.

## Findings

### F-C1

- Proposed severity: high
- Finding: There is no measured live edge. Three settled live trades are fee-net -$1.8144. The claim -$1.81 is that total rounded to the cent. Two NO trades made +$5.7082. One YES trade lost -$7.5226. Three positions are not settled, so the account result is not known.
- Evidence: GET /portfolio/settlements returned only KXTRUTHSOCIAL-26OCT03-B129, KXTRUTHSOCIAL-26OCT10-B149, and KXTRUMPACT-26OCT04-T6. Fees match the fills. The local fill log prices T6 at 43c. The fill and the settlement cost are 42c (8 x $0.42 = $3.36). Fee-net uses 42c. KXTRUMPSAY-26OCT19-GOLF is `status=determined`, `result=yes`, `settlement_value_dollars=1.0000`, and it is not in /portfolio/settlements. Its fee-net is not booked. A $1 payout would be 8.00 - 7.12 - 0.0549 = +0.8251, and the settled total would still be about -$0.99. That number is not a settlement.
- Dollar impact per week: not measured
- Unverified: no

### F-C2

- Proposed severity: medium
- Finding: The paper +$1.54 claim is the cent rounding of +$1.5360 on n=6. It is not live edge. The cohort file says `cohort_type=active`, not sealed. All six accounting rows are resolved, and there is no seventh row. Five paper sides won. The 22-lot NO at 33c lost the whole stake, -$7.6005. That price is inside today's 0-35c band, and 22 contracts is above the 8-lot cap.
- Evidence: Sum of `fee_net_pnl_dollars` is 1.1286 + 1.9722 + 1.3573 + 0.8464 - 7.6005 + 3.832 = 1.5360. Each fee matches ceil(0.07 x price x (1-price) x contracts) to $0.0001. `pnl_dollars` equals fee-net because `cost_dollars` already includes the fee.
- Dollar impact per week: not measured
- Unverified: no

### F-C3

- Proposed severity: medium
- Finding: Model p does not match outcomes at the sample sizes we can read. Settled live side-hit rate is 2/3. Mean model P(side) on those three is 0.889. The miss is the 20-lot YES with model P(YES)=0.9566. Paper side-hit rate is 5/6, and the model was 0.98 or 0.02 on every paper row. Exchange settled-market YES rates are a different object. They mix strikes. They are not a base rate for the next contract.
- Evidence: LIVE_ORDER `model_probability` is P(YES). B129 YES 0.9566 lost (result no). B149 NO, P(YES)=0.02, won. T6 NO, P(YES)=0.2702, won. Paper P(YES) versus result: 0.98 yes, 0.02 no, 0.02 no, 0.02 no, 0.02 yes (miss), 0.98 yes. GET /markets status=settled, cursor finished: KXTRUMPACT 44 yes / 98 (0.449), KXTRUMPSAY 190 yes / 361 (0.526), KXTRUTHSOCIAL 9 yes / 90 (0.100). Our settled rows by series are n=5, n=3, and n=1. That n cannot support a series edge.
- Dollar impact per week: not measured
- Unverified: no

### F-C4

- Proposed severity: high
- Finding: The first-hour claim recounts exactly. From 12:22:13Z to 13:22:13Z there were 17 prefix-cap blocks, 12 rules mismatches, 5 YES-floor skips, 0 excluded-band skips, 0 family-lock lines, and 0 paper rows. The 17 blocks are two tickers repeated. T4 died in the paper rules check. NOBE died on the paper YES floor. Nothing was recorded. Since the boot, through 13:48:02Z, the same split is 27 prefix-cap, 17 rules, 10 YES floor, and still 0 paper rows. 17+10=27.
- Evidence: Longer window 2026-10-08 00:00:00Z through 2026-10-15 13:48:02Z: prefix-cap 1679 lines (13 tickers), family lock 1157 lines (6 KXTRUTHSOCIAL tickers), excluded-band 834 lines (6 tickers), rules mismatch 17, YES floor 10, paper recorded 0. The rules journal has 17 rows, all mismatch true, all KXTRUMPACT-26OCT11-T4 side no, all `outcome=null`. Counts are prewarm repeats. RESEARCH_PREWARM_INTERVAL_SECONDS is 300. They are not 1679 separate bets. Boot flags: YES floor live=on paper=on at 55c. Rules check live=off paper=on. Excluded band is on at 36c. Family lock live=off paper=on, and the Truth Social lock still logs when it trips.
- Dollar impact per week: not measured
- Unverified: no

### F-C5

- Proposed severity: low
- Finding: Cash is $29.2049. API portfolio value is $21.60. Mark equity is $50.8049. Cash is 57.48% of that equity. The mark is 42.52%. About $13.68 of the mark is still an open outcome. About $7.92 is GOLF marked at the last price after a YES determination that has not settled. Expected dollars per week at current settings is not measured.
- Evidence: Balance integer cents are 2920. `balance_dollars` is 29.2049. `portfolio_value` is 2160 cents. Last-price reconstruction equals $21.60: GOLF 8 x 0.99 = 7.92, T5 8 x (1-0.28) = 5.76, OCT17-B129 8 x (1-0.01) = 7.92. Cost of the three positions is 7.12+4.80+6.80 = 18.72. Fees already paid on them are 0.0549+0.1344+0.0714 = 0.2607. Cost-basis equity is 29.2049+18.72 = 47.9249, so cash is 60.94% and cost at risk is 39.06%. Open fee-net is unsettled. Since the boot, live fills are 0 and paper rows are 0. The six historical fills span 9.96 days and then stop. A weekly rate from n=3 settled trades is not a measurement. The three settled fee-nets are -7.5226, +1.2047, and +4.5035.
- Dollar impact per week: not measured
- Unverified: no

### F-C6

- Proposed severity: medium
- Finding: The current book holds the four invariants. The only live YES under 55c, and the only fill over 8 contracts, is the same settled trade, KXTRUTHSOCIAL-26OCT03-B129 YES 20 at 36c. That trade is the entire live loss. No fill is at or below 35c. The open book is one row per event prefix, and each open row is 8 contracts. GOLF YES is 89c.
- Evidence: Six fills from GET /portfolio/fills. Side prices are 36c, 84c, 42c, 85c, 60c, and 89c. Contracts are 20, 8, 8, 8, 8, and 8. Open event tickers are KXTRUMPSAY-26OCT19, KXTRUMPACT-26OCT11, and KXTRUTHSOCIAL-26OCT17, one market each. Positions are +8, -8, and -8. September LIVE_SUBMISSION_INTENT rows above 8 contracts have no fill. The fills cursor ended at n=6. Boot flags keep the 0-35c band and the 55c YES floor on. Since the boot, NOBE YES at 43-44c was skipped and no POST was logged.
- Dollar impact per week: not measured
- Unverified: no

### F-C7

- Proposed severity: high
- Finding: No proposal has registered_oos replay evidence. The politics OOS file is not on disk. The registered window cannot be materialized before 2027-01-14. The gate was not weakened and was not overridden.
- Evidence: `logs/edge_replay/corpus_politics_oos.jsonl` is missing. Debt-log registration `politics-oos` runs 2026-10-16T00:00:00Z through 2027-01-14T00:00:00Z and still requires `registered_oos`, 30 settled trades, coverage 0.95, and an EV CI above 0. Desk-history fixtures are not that class. Paper rules rows have `outcome=null`, so the rules check has no scored fee-net.
- Dollar impact per week: not measured
- Unverified: no

## Fee-net table

Live settled. Payout is revenue/100. Fee is API `fee_cost`. Price is cost/contracts.

| ticker | side | price | contracts | fees | payout | fee-net |
| --- | --- | --- | --- | --- | --- | --- |
| KXTRUTHSOCIAL-26OCT03-B129 | yes | 0.36 | 20 | 0.3226 | 0.00 | -7.5226 |
| KXTRUTHSOCIAL-26OCT10-B149 | no | 0.84 | 8 | 0.0753 | 8.00 | 1.2047 |
| KXTRUMPACT-26OCT04-T6 | no | 0.42 | 8 | 0.1365 | 8.00 | 4.5035 |
| live settled total |  |  |  | 0.5344 | 16.00 | -1.8144 |

Paper accounting. Fee is the modeled taker fee, not an exchange fill. Payout is gross settlement payout.

| ticker | side | price | contracts | fees | payout | fee-net |
| --- | --- | --- | --- | --- | --- | --- |
| KXTRUTHSOCIAL-26AUG29-T240 | yes | 0.85 | 8 | 0.0714 | 8.00 | 1.1286 |
| KXTRUTHSOCIAL-26SEP05-T240 | no | 0.74 | 8 | 0.1078 | 8.00 | 1.9722 |
| KXTRUMPACT-26AUG30-T8 | no | 0.82 | 8 | 0.0827 | 8.00 | 1.3573 |
| KXTRUTHSOCIAL-26SEP12-B230 | no | 0.85 | 6 | 0.0536 | 6.00 | 0.8464 |
| KXTRUMPACT-26SEP06-T4 | no | 0.33 | 22 | 0.3405 | 0.00 | -7.6005 |
| KXTRUMPSAY-26SEP14-UKRA | yes | 0.60 | 10 | 0.1680 | 10.00 | 3.8320 |
| paper total |  |  |  | 0.8240 | 40.00 | 1.5360 |

Open book. Payout and fee-net are unsettled. Fees are fill `fee_cost` already paid. GOLF is determined YES and is still absent from settlements.

| ticker | side | price | contracts | fees | payout | fee-net |
| --- | --- | --- | --- | --- | --- | --- |
| KXTRUMPSAY-26OCT19-GOLF | yes | 0.89 | 8 | 0.0549 | unsettled | unsettled |
| KXTRUMPACT-26OCT11-T5 | no | 0.60 | 8 | 0.1344 | unsettled | unsettled |
| KXTRUTHSOCIAL-26OCT17-B129 | no | 0.85 | 8 | 0.0714 | unsettled | unsettled |

## Funnel counts

First hour, 2026-10-15 12:22:13Z through 13:22:13Z. Recount matches the claim.

| stage | lines | unique tickers |
| --- | --- | --- |
| prefix-cap | 17 | T4, NOBE |
| rules mismatch | 12 | T4 |
| YES floor | 5 | NOBE |
| excluded-band | 0 | none |
| family lock | 0 | none |
| paper rows recorded | 0 | none |

Since boot, 2026-10-15 12:22:13Z through 13:48:02Z.

| stage | lines | unique tickers |
| --- | --- | --- |
| prefix-cap | 27 | T4, NOBE |
| rules mismatch | 17 | T4 |
| YES floor | 10 | NOBE |
| excluded-band | 0 | none |
| family lock | 0 | none |
| paper rows recorded | 0 | none |

Longer window, 2026-10-08 00:00:00Z through 2026-10-15 13:48:02Z. Line counts are repeated scans.

| stage | lines | unique tickers |
| --- | --- | --- |
| prefix-cap | 1679 | 13 |
| family lock | 1157 | 6 |
| excluded-band | 834 | 6 |
| rules mismatch | 17 | 1 |
| YES floor | 10 | 1 |
| paper rows recorded | 0 | none |

By UTC day, lines only: 10-08 family 182, band 154, prefix 82. 10-09 family 189, prefix 263, band 164. 10-10 family 203, prefix 25, band 344. 10-11 family 56, prefix 79. 10-12 prefix 270, family 260. 10-13 family 119, prefix 293. 10-14 prefix 435, family 142, band 130. 10-15 family 6, prefix 232, band 42, rules 17, yes floor 10. The 10-15 rules and YES-floor lines are all after 12:22:13Z. The 10-15 band and family lines are before that boot.

## Capital

GET /portfolio/balance at 2026-10-15 13:49:25 UTC.

Cash = balance_dollars = 29.2049. The integer `balance` field is 2920 cents.

Portfolio value = 2160 / 100 = 21.60.

Mark equity = 29.2049 + 21.60 = 50.8049.

Idle = 29.2049 / 50.8049 = 57.48%. At risk on the mark = 21.60 / 50.8049 = 42.52%.

Active outcome mark = 5.76 + 7.92 = 13.68, which is 26.93% of mark equity. Determined GOLF mark = 7.92, which is 15.59% of mark equity, awaiting a settlement row.

Cost at risk = 18.72. Cost equity = 29.2049 + 18.72 = 47.9249. Idle on that base = 60.94%. Cost at risk = 39.06%.

Current settings, from the politics start script and the 12:22:13Z boot line: Kelly fraction 0.5, min edge 0.04, max bet 15% of bankroll, hard cap $25, min bet $2, max 8 contracts, one open row per event, YES floor 55c on live and paper, band floor 36c, rules check paper only, live risk cap off, paper risk cap 600 cents, paper exposure cap 25%, live family flag off, paper family flag on.

Expected dollars per week = not measured.

The formula that was not used as a forecast is (settled fee-net / elapsed days) x 7. Inputs would be -1.8144, 12.74 days from the first fill to this balance read, and n=3, with three unsettled positions excluded and a post-boot fill count of 0. That is not a rate under the current flags.

## Proposals (max 3)

1. Do not change the 8-contract cap or the 55c live YES floor. The only settled live loss is the 20-lot YES at 36c. No registered_oos replay. The proposal has no replay evidence.
2. Do not relax the one-open-row prefix cap to create paper rows. Since the boot, the blocked names are the occupied events' siblings. No registered_oos replay. The proposal has no replay evidence.
3. Do not size live off the paper +$1.5360. That sample is six resolved paper rows, one 33c loss, and a different probability shape from the live T6 print of 0.2702. No registered_oos replay. The proposal has no replay evidence.

No gate threshold was changed.

## Limits

GOLF, T5, and OCT17-B129 were not modified. Their fee-net is unsettled. GOLF's determination is not cash until a settlement row exists.

Series YES rates mix strikes. They are not the probability of the strike the desk buys. Our scored settled n is 3 live and 6 paper.

Funnel line counts repeat the same tickers. Unique tickers are the right denominator for "how many markets," and lines are the right denominator for the first-hour claim.

The rules journal has no outcomes, so a rules-check dollar save is not measured.

The OOS corpus does not exist yet. Nothing here is registered_oos. Weekly dollars are not measured.
