# MELA reconciliation 2026-10-17

Ticker: `KXTRUMPSAY-26OCT19-MELA`.
Class: **(b) filled, then settled YES**.
Reads: GET only. Balance `updated_ts` 1792276566 (2026-10-17T22:36:06Z).

## Orders

| status filter | rows |
|---|---|
| none | 1 |
| resting | 0 |
| canceled | 0 |
| cancelled | 0 |
| executed | 1 (same order) |

- order_id: `01a141dc-cd80-7da2-b82e-b76274da15e7`
- client_order_id: `e0fbe877e15a4fc3a780e85080b413d9`
- action buy, side yes, status executed
- yes_price_dollars `0.6400`, no_price_dollars `0.3600`
- initial_count_fp `8.00`, fill_count_fp `8.00`, remaining_count_fp `0.00`
- taker_fill_cost_dollars `5.120000`, maker_fill_cost_dollars `0.000000`
- taker_fees_dollars `0.129100`, maker_fees_dollars `0.000000`
- created_time `2026-10-15T23:19:12.775015Z` (17:19 MDT)

## Fill

One fill.

- fill_id and trade_id: `072446b1-3bd9-81b6-5efb-e442caf2eb28`
- order_id: `01a141dc-cd80-7da2-b82e-b76274da15e7`
- count_fp `8.00`, yes_price_dollars `0.6400`, fee_cost `0.129100`
- is_taker true, side yes, action buy
- created_time `2026-10-15T23:19:12.775015Z`, ts `1792106352`

## Settlement and position

Not in open positions (`mela_rows` 0). Current position: 0.

Settlement row:

- market_result `yes`
- yes_count_fp `8.00`, no_count_fp `0.00`
- yes_total_cost_dollars `5.120000`, no_total_cost_dollars `0.000000`
- fee_cost `0.129100`
- revenue `800` (raw integer), value `100` (raw integer)
- settled_time `2026-10-17T03:00:51.774164Z`

`revenue` 800 and `value` 100 match 8 contracts at 100 cents each ($8.00). Premium paid was $5.12. Fee was $0.1291.

## Fill log

`logs/state/live-fill-log.md` row:

`2026-10-15T23:19:14Z, KXTRUMPSAY-26OCT19-MELA, yes, 64, 8, e0fbe877e15a4fc3a780e85080b413d9, 201, 8, 64, 12.91, 01a141dc-cd80-7da2-b82e-b76274da15e7`

## Why the row says 12.91

The detail column is fee cents, not the premium. `fill_log_detail_from_receipts` multiplies `fee_cost` dollars by 100. `0.129100 * 100 = 12.91` cents. The premium is `8 * 0.64 = 5.12`, which matches `taker_fill_cost_dollars` `5.120000`. The writer and the positions parser are consistent with the GET. No formula change. `tests/test_executor.py::test_mela_fill_detail_fee_cents_are_not_the_premium` pins these numbers.

## Account snapshot from the same GET

- cash `balance` 3470 cents ($34.70). `portfolio_value` 1872 cents ($18.72).
- resting orders: 0.
- open positions (3): `KXTRUMPSAY-26OCT19-ZACH` position_fp `+8.00` exposure `5.120000` fees `0.129100`; `KXTRUMPACT-26OCT11-T5` position_fp `-8.00` exposure `4.800000` fees `0.134400`; `KXTRUTHSOCIAL-26OCT17-B129` position_fp `-8.00` exposure `6.800000` fees `0.071400`.

`logs/state/politics_cash_cents.json` is still `start_cents` 3995 / `last_cents` 3995 at `2026-10-17T03:09:46Z`. That is the pre-ZACH cash read. 3995 cents minus about 525 cents (ZACH premium $5.12 plus fee $0.1291) is 3470. The cash file is not rewritten on the fill. The >$5 drop alert stays blind until the next positive cash read.
