# Execution and money safety

## Method

Read-only audit of `/Users/jacobparenti/vscode/kalshi-bot` at `17c7e47a52ba0fb5037798a0a6110a65f2db954c` (`v0.33.97`). Politics PID 86845 was `Python main.py`, state `S`, parent 1, at the start and again at elapsed `01:32:57`. This audit did not signal it. Its child 86855 is `caffeinate` and was not signaled.

Process env was read with `ps eww` and filtered to allowlisted names. `LIVE_TRADING_ENABLED=true`, `EVENT_NEWS_LIVE_ALLOW=true`, `PAPER_COHORT_ID=kalshi-event-news-20260820`, `PAPER_ACTIVE_COHORT_BANKROLL=50.00`, `EVENT_NEWS_YES_FLOOR_CENTS=55`, `EVENT_NEWS_REQUOTE_BAND_CHECK=true`, `EVENT_NEWS_EXCLUDE_EXACT_35=true`, `MAX_BET_HARD_CAP=25.00`, `MAX_BET_PCT_BANKROLL=0.15`. `BANKROLL`, `LIVE_LOSS_LIMIT_PERCENT`, `EVENT_NEWS_ONE_PER_FAMILY`, `EVENT_NEWS_MAX_RISK_CENTS`, `EVENT_NEWS_MAKER_FIRST`, and `MAX_BET_DOLLARS` were absent.

Boot line in `logs/app/politics.launchd.stderr.log` at `2026-10-15 12:22:13 UTC`: `EVENT_NEWS live-allow + LIVE_TRADING_ENABLED -- bot is in LIVE TRADING mode.` The same second logs `YES_FLOOR live=on cents=55`, `RISK_CAP live=off`, `EXPOSURE_PCT live=off`, `ONE_PER_FAMILY live=off`, `MAKER_FIRST live=off`, `REQUOTE_BAND_CHECK=on`, `EXCLUDE_EXACT_35=on`.

Kalshi calls were `KalshiRestClient._request("GET", ...)` only. Endpoints: `/portfolio/balance`, `/portfolio/positions`, `/portfolio/fills`, `/portfolio/settlements`, `/portfolio/orders`. No POST, DELETE, cancel, amend, or retry. Local sqlite was opened `mode=ro`. Order ids were not printed.

## Findings

### F-B1

- Proposed severity: High
- Finding: A second live order can be posted on an already-open `KXTRUMPSAY` or `KXTRUMPACT` ticker. The same-signal guard, the per-event prefix cap, and the restart cooldown seed read the paper portfolio. That portfolio has zero open rows. `EVENT_NEWS_ONE_PER_FAMILY` is off in this live process, so those two families are not locked. `KXTRUTHSOCIAL` is still locked by the always-on family check when the live GET succeeds.
- Evidence: `trading/executor.py:1312` loops `self._paper.portfolio.open_positions` for the same-signal skip. `trading/executor.py:1278` uses that same paper book for the prefix cap. `trading/executor.py:501` seeds cooldowns from `self._paper.portfolio.tickers()` only. `data/paper_cohorts/kalshi-event-news-20260820/paper_trades.db` has 6 rows, all `resolved=1`, and none of the three open October tickers. `utils/event_news_desk_flags.py:201` returns no block unless `EVENT_NEWS_ONE_PER_FAMILY` is on. Boot log says `ONE_PER_FAMILY live=off`. `utils/event_news_research.py:339` locks only `KXTRUTHSOCIAL`. Live cooldown default is 600 seconds at `config.py:1428` and is not seeded from the exchange. GET `/portfolio/positions` still shows GOLF `position_fp=8.00` and T5 `position_fp=-8.00`.
- Dollar impact per week: one extra order is at most `min($7.50, 8 contracts)`. `$7.50 = 0.15 × $50` configured cohort bankroll (`config.py:1910`, process `MAX_BET_PCT_BANKROLL=0.15`). At the last GOLF fill, 8 contracts cost `$7.12`. Weekly count not measured.
- Unverified: no

### F-B2

- Proposed severity: High
- Finding: The live loss halt cannot trip on this account. It compares cash with 10% of `cfg.bankroll`. `BANKROLL` is unset, so the code uses `$500`. The halt line is `$50`. Cash is `$29.2049`. A drop to `$0` is a `$29.20` loss and stays under `$50`. `get_balance()` turns a read error into `0.0`, and that fake loss is still under `$50`.
- Evidence: `trading/executor.py:497` uses `loss >= cfg.bankroll * cfg.live_loss_limit_pct`. `config.py:1298` defaults `BANKROLL` to `500`. `config.py:1480` defaults `LIVE_LOSS_LIMIT_PERCENT` to `0.10`. Both env names were absent on PID 86845. `kalshi/rest_client.py:958` returns `0.0` after a failed balance read. GET `/portfolio/balance` returned integer `balance=2920` and `balance_dollars=29.2049`. The check runs only when a live order is attempted (`trading/executor.py:1421`).
- Dollar impact per week: the halt does not stop a loss of the full cash balance, `$29.2049`. Weekly rate not measured.
- Unverified: no

### F-B3

- Proposed severity: Medium
- Finding: A partial fill leaves a `good_till_canceled` remainder working at Kalshi. The process then releases the submission hold. Restart does not adopt that order and does not cancel it. Maker-first, the only cancel path, is off. This account has no resting order right now.
- Evidence: `kalshi/rest_client.py:1097` sets `time_in_force=good_till_canceled`. `kalshi/rest_client.py:1125` marks `executed` only when `remaining_count==0` and `filled>0`; otherwise it keeps the venue status or `"resting"`. `trading/executor.py:2480` releases the hold after a verified order id and a durable journal, including a resting order. `trading/executor.py:2615` and `:2627` are the only `cancel_order` call sites, both inside `_event_news_maker_first`. Boot log says `MAKER_FIRST live=off`. No startup path calls `GET /portfolio/orders` or `cancel_order`. GET `/portfolio/orders` returned 6 orders, all `status=executed` and `remaining_count_fp=0.00`. `logs/state/live_submission/unknown_submission_holds.json` has `held_tickers=[]`.
- Dollar impact per week: not measured. Current resting count is 0. A later remainder is still capped at 8 contracts by the pre-post clamp.
- Unverified: no

### F-B4

- Proposed severity: Low
- Finding: This account already filled one 20-contract YES buy at 36c. Current code clamps politics orders to 8 and skips YES below 55c. That historical order is not a live resting order.
- Evidence: GET `/portfolio/orders` and `/portfolio/fills` show `KXTRUTHSOCIAL-26OCT03-B129`, `initial_count_fp=20.00`, `fill_count_fp=20.00`, `yes_price_dollars=0.3600`, `status=executed`, fee `$0.322600`. The redacted live fill log line is `2026-10-02T20:05:00Z, KXTRUTHSOCIAL-26OCT03-B129, yes, 36, 20, executed`. `utils/event_news_research.py:63` sets `EVENT_NEWS_MAX_CONTRACTS=8`. `trading/executor.py:1465` clamps the plan before `_execute_live`. `utils/event_news_desk_flags.py:139` skips YES asks below the floor. Boot log says the floor is on at 55c. The other five venue orders are 8 contracts.
- Dollar impact per week: `$0` on the current path. The realized loss on that 20-lot is already inside the settled `-$1.8144` (`$7.20` cost plus `$0.3226` fee, payout `$0`).
- Unverified: no

## Reconciliation

API figures are from the GETs above. The configured `$50.00` is process env `PAPER_ACTIVE_COHORT_BANKROLL`, not a Kalshi field. No deposit endpoint was called. Starting cash is unverified. The identity below closes only if the account began at that `$50.00`.

Settled fee-net, in cents, fees not double-counted (settlement `fee_cost` equals the fill `fee_cost` on the same three tickers):

- `KXTRUMPACT-26OCT04-T6` NO 8: revenue `800` − cost `336` − fee `13.65` = `+450.35`
- `KXTRUTHSOCIAL-26OCT10-B149` NO 8: `800` − `672` − `7.53` = `+120.47`
- `KXTRUTHSOCIAL-26OCT03-B129` YES 20: `0` − `720` − `32.26` = `−752.26`
- Sum = `−181.44` cents = `−$1.8144`

Open cash still out, cost plus fill fee:

- GOLF YES: `712` + `5.49` = `717.49`
- T5 NO: `480` + `13.44` = `493.44`
- OCT17-B129 NO: `680` + `7.14` = `687.14`
- Sum = `1898.07` cents

`181.44 + 1898.07 = 2079.51` cents left the account. `5000.00 − 2079.51 = 2920.49` cents = `balance_dollars` `29.2049`. All six fill fees sum to `$0.795100`, which matches `0.5344 + 0.2607`.

| Item | API | Local or claim | Result |
| --- | --- | --- | --- |
| Cash | `balance` `2920` cents; `balance_dollars` `29.2049` | claim `$29.20` | Integer matches the claim. The dollars field is `0.49` cent higher. |
| Portfolio value | `2160` cents | sum of `market_exposure_dollars` `1872` cents | Gap `288` cents. Position payloads had no separate mark. Unverified. |
| GOLF | `position_fp` `8.00`, exposure `$7.120000` | fill log YES 89c × 8; fill `yes_price_dollars` `0.8900` | Match. YES 8. |
| T5 | `position_fp` `-8.00`, exposure `$4.800000` | fill log NO 60c × 8; fill `no_price_dollars` `0.6000` | Match. NO 8. |
| OCT17-B129 | `position_fp` `-8.00`, exposure `$6.800000` | fill log NO 85c × 8; fill `no_price_dollars` `0.8500` | Match. NO 8. |
| Settled fee-net | `−$1.8144` on 3 settlements | claim `−$1.81` on 3 trades | Rounds to the claim. Exact value is `−181.44` cents. |
| T6 fill price | fill `no_price_dollars` `0.4200`, cost `$3.36`; order limit `0.4300` | fill log price `43` | Log matches the limit, not the fill. Cash uses `42c`. |
| Resting orders | `0` | hold file `held_tickers=[]` | No working order to adopt. |
| Paper book | not the exchange | 0 open rows; notional `$51.536`; 6 resolved Aug/Sep trades; fee-net sum `$1.536` | Does not contain the three open positions. `$50 + $1.536 = $51.536`. |

`balance_breakdown` is four rows. Index 0 is `29.2049`. Indexes 1–3 are `0.0000`.

## Paths that can send an order

`Bot._trading_queue_consumer_task` at `main.py:5106` calls `TradeExecutor.execute`. Live mode calls `_execute_live` at `trading/executor.py:677`. That calls `KalshiRestClient.place_limit_order` at `trading/executor.py:2380`. The only production Kalshi POST is `kalshi/rest_client.py:1106`, path `/portfolio/events/orders`. There is no amend method and no other `_request("POST"` in production code.

`place_limit_order` itself checks side and `1..99` cents only. The band, the YES floor, the 8-lot clamp, and the family lock run earlier in the executor.

`_event_news_maker_first` (`trading/executor.py:2537`) can POST a post-only order, `DELETE` it through `cancel_order` (`kalshi/rest_client.py:1167`), then POST a taker with a new `client_order_id`. Each POST goes back through `_execute_live`. This live process has maker-first off, so that path does not run.

`PolymarketAccountClient.place_limit_order` (`polymarket/account_client.py:78`) returns an error and does not send HTTP. Process env has `POLYMARKET_US_LIVE_TRADING_ENABLED=false`.

Paper shadow records a paper row and does not call `place_limit_order`.

Guards on the live POST path:

- Excluded band, 0c through 35c: on. `utils/event_news_grok_flags.py:106` says 36c is the first allowed cent. `utils/event_news_research.py:561` also blocks an exact 35c ask unless the env is `false`. `trading/executor.py:2154` refuses the POST when either band flag is off in live mode. `trading/executor.py:2282` checks the final limit again before reserve. An unreadable fresh ask returns `"invalid"` and skips (`utils/event_news_research.py:604`).
- YES floor 55c: on for this process. `utils/event_news_desk_flags.py:139`: YES below the floor returns `yes_below_floor`. NO does not use that floor. `trading/executor.py:610` runs that skip before `_execute_live`, and `:2259` runs it again.
- Max 8: `trading/executor.py:1465` clamps the plan with `event_news_clamp_execution_terms` before the POST. The dollar cap is `cfg.dynamic_max_bet(notional)` at `trading/executor.py:1089`, which is `$7.50` on the `$50` cohort bankroll, not a hardcoded bet. `MAX_BET_DOLLARS` is absent. The risk-cap flag is off, so `_event_news_risk_capped_plan` (`trading/executor.py:1946`) does not apply the extra `$6.00` cap. The 8-lot clamp still ran one step earlier.
- Family lock: `trading/executor.py:1294` blocks a new `KXTRUTHSOCIAL` order when any open truth-social ticker is visible. A failed live GET appends `KXTRUTHSOCIAL-LIVE-UNKNOWN` (`utils/event_news_research.py:67`), and that string's prefix is `KXTRUTHSOCIAL`, so the truth-social lock fails closed. It does not fail closed for `KXTRUMPSAY` or `KXTRUMPACT`. See F-B1.
- Fail closed: a paper executor cannot POST (`trading/executor.py:2139`). Missing `executed_price_cents` returns before the POST (`trading/executor.py:2169`). A failed intent write returns before reserve (`trading/executor.py:2309`). `can_submit` and `reserve` return before the POST when the hold file is missing, corrupt, or already holds the ticker (`trading/executor.py:2158`, `:2317`). Live order-book failure does not use the paper fallback (`trading/executor.py:1548`) and returns `G8_execution_depth_unavailable`.

## Guards that held

Idempotency. `trading/executor.py:2279` mints `uuid.uuid4().hex` once per `_execute_live` call. `kalshi/rest_client.py:1089` uses that id and does not mint another when the caller passed one. POST is not in `_REQUEST_RETRY_METHODS` (`kalshi/rest_client.py:85`). Retry budget for POST is 0 (`kalshi/rest_client.py:525`). A timeout, HTTP 429, or 5xx becomes one `OrderResult` error. `trading/executor.py:2419` then holds the ticker and does not POST again. The hold is reserved before the POST (`trading/executor.py:2317`) and reloaded from disk on the next process (`trading/live_submission_hold.py:66`). Tests: `test_execute_live_error_result_holds_ticker_across_executors` (includes the 429 and 503 strings), `test_execute_live_exception_is_unknown_after_one_post`, `test_cancelled_post_holds_ticker_and_preserves_cancellation`, `test_live_order_journal_failure_holds_ticker_across_executors`. A verified journal releases the hold, so a later new uuid is a new order. That is F-B1, not a replay of the same id.

HTTP 429 and 5xx. Statuses `{429, 500, 502, 503, 504}` are transient (`kalshi/rest_client.py:82`). GET and DELETE may retry up to 3 times with `Retry-After` or backoff (`kalshi/rest_client.py:281`). POST does not. The executor treats the POST error as an unknown outcome and blocks that ticker.

4xx. `place_limit_order` returns `status=error` and the HTTP status (`kalshi/rest_client.py:1140`). Business statuses `{400, 404, 409, 422}` are logged (`trading/executor.py:53`) and still take the unknown-hold path. They do not retry.

Clock skew. `_sign` stamps `time.time()*1000` (`kalshi/rest_client.py:462`). There is no skew offset. These GETs succeeded, so the clock was inside the venue window at audit time. A 401 is not retried on POST and becomes a ticker hold.

Websocket. `kalshi/websocket_client.py` only subscribes to `orderbook_delta`. On drop it logs and reconnects from 2 seconds up to 60 (`kalshi/websocket_client.py:265`). It does not place, cancel, or adopt orders. The pre-post size check uses the REST book (`trading/executor.py:1476`).

Open orders after restart. The code does not list or cancel resting orders at startup. It ignores them. They stay working at the venue. See F-B3. Today's GET found none.

Live mode gate. `trading/paper_trader.py:1203` leaves paper mode only when `event_news_operator_live_allow()` and `cfg.live_trading_enabled` are both true. Both are true on PID 86845. The boot log confirms live mode.

## Limits of this audit

No deposit or withdrawal endpoint was read. The `$50.00` start is the configured cohort bankroll, and it fits `balance_dollars` only as an arithmetic identity. `portfolio_value` of `2160` cents was not tied to a quote. Paper fee-net `+$1.536` is a different book from Kalshi `−$1.8144`. Tests named above were read, not executed. No 401 or 429 count was taken from the 222MB stderr log. Weekly order frequency was not measured. PID 86845 was still up at the last check.
