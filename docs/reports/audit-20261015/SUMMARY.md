# Audit summary

Date: 2026-10-15. Code: `17c7e47` (`v0.33.97`). Host: the live checkout `/Users/jacobparenti/vscode/kalshi-bot`. Process: PID 86845, `com.jake.kalshi-bot-politics`, state `S`, started Thu Oct 15 06:22:13 2026 local (2026-10-15 12:22:13 UTC). This audit did not signal it, did not send an order, and did not change a flag or a plist.

## Status

IDLE. The process is up and the three open rows match Kalshi, and no new live order or paper row has been written since the 12:22 UTC boot.

## Critical

One Critical finding. The red team reran the halt math. No other Critical finding survived.

### F-B2. The session loss halt cannot trip on this account

The live loss halt compares the drop from the session start with 10% of `cfg.bankroll`. `BANKROLL` defaults to `$500` and `LIVE_LOSS_LIMIT_PERCENT` defaults to `0.10`, so the halt line is `$50`. `get_balance` returns the integer `balance` field divided by 100. That field is `2920`, so the halt sees `$29.20`. A fall from `$29.20` to `$0` is a `$29.20` loss. That is under `$50`, so the halt returns false.

A failed balance read returns `0.0`. If that `0.0` is the first read, it becomes the baseline. Every later balance at or above `$0` then looks like a gain, and the halt stays disarmed. A later `0.0` after a real `$29.20` seed also does not halt. The ops report's sentence that a later zero "looks like a total loss and halts" is broken. Live risk cap is off. Live exposure cap is off. Each new order is still clamped to 8 contracts and to 15% of the `$50` cohort bankroll (`$7.50`).

Evidence: `trading/executor.py:480-499` and `trading/executor.py:1421-1436`. `config.py:1298` and `config.py:1475-1481`. `kalshi/rest_client.py:958-966`. `docs/reports/audit-20261015/redteam.md` attacks A, B, and C. The 12:22:13 UTC boot line says `RISK_CAP live=off` and `EXPOSURE_PCT live=off`. The red-team control seed of `$50.0` then `$0.0` returned true. A seed of `$49.9999` then `$0.0` returned false.

Dollar impact: the halt does not stop a loss of the cash balance. Integer cash is `$29.20`. `balance_dollars` is `29.2049`. Weekly rate: not measured.

## Top 10

| Rank | Area | Finding | Evidence | $ impact per week | Fix size | Risk to fix |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Execution | The session loss halt cannot trip before cash is gone. See F-B2. | `executor.py:497-499`; red team, seed `$29.20` then `$0` returned false | not measured. Uncovered loss is the cash, `$29.20` | S | Medium. A tighter halt can stop the process, and exit 0 does not restart it |
| 2 | Execution | A second SAY or ACT order is blocked when the positions GET works. It is not blocked when that GET fails. The executor prefix cap and same-signal guard read the empty paper book. | `main.py:1439-1447`; `executor.py:1278-1280`; red team attacks D, E, G. Today's log shows the working GET blocking T4 and NOBE | not measured. One extra order is at most `$7.50` or 8 contracts | M | High. This is the live admit path |
| 3 | Strategy | No live edge is measured. Settled fee-net is `-$1.8144` on 3 trades. The loss is the 20-lot YES at 36c. Current rules skip that order. | GET `/portfolio/settlements`; `strategy.md` fee-net table | not measured | S. Do not loosen the 8-lot cap or the 55c YES floor | High if those caps are loosened |
| 4 | Grok | The paper rules check sends a Truth Social count rubric to a presidential-action market. All stored replies are real `mismatch: true`. That blocked every T4 paper shadow. | `utils/event_news_rules_check.py:35-39` and `:188-189`. 12 first-hour journal rows. Red team: timeout and bad JSON do not skip | not measured | S. Paper flag only | Low |
| 5 | Tracking | Research claims are stored on the ticker. Nothing writes the settlement back onto the fill or the run. Three settled markets still have dossier status `active`. | `executor.py:321-323`; `tasks/research_dossier.py` has no outcome column; settlements GET | not measured | M | Low |
| 6 | Ops | The daily health check watches unloaded `com.jake.kalshi-bot` and is already RED. A politics death does not change that verdict. A handled SIGTERM exits 0, so launchd does not restart politics. | `scripts/bothealth.sh:73`; `logs/reports/health/bothealth_2026-10-15.md:104`; `main.py:5558-5560` and `:6107-6111` | not measured | S | Low. Do not load the freeze label |
| 7 | Strategy | Since the boot, through 13:48:02 UTC: 27 prefix-cap blocks, 17 rules mismatches, 10 YES-floor skips, 0 paper rows, 0 live fills. The first hour was 17, 12, 5, and 0. | `strategy.md` funnel; red team recount of `logs/app/bot.log` | not measured | Do not change the prefix cap | High |
| 8 | Tracking | `ops/live-fill-log` is not on origin. The 06:05 job dies on `import trading` because `PYTHONPATH` is unset. The runtime log and the committed report are the same bytes, so a fixed job still would not push until a new fill. | `git ls-remote` empty; plist has no `PYTHONPATH`; dry-run printed `dry-run: unchanged` | not measured | S | Low |
| 9 | Grok | Live veto and the open-position watch are ungrounded. Watch said `CONTRADICT` on all three open rows with 0 sources and did not exit or cancel. | Boot line `GROK_GROUNDING=off`; watch traces at 00:52 UTC | not measured | M | Medium |
| 10 | Tracking | The daily snapshot copies the frozen root databases. It does not copy the cohort paper DB PID 86845 has open, or that cohort's 469,041,152-byte dossier. No 18GB backup tree was found. `logs/backups` is 17,134,436,352 bytes (`du -sh` 16G). A restore of the live cohort was not run. | `tracking.md` F-E4 and F-E5 | not measured | M | Low |

## Money view

Realized live fee-net is `-$1.8144` on 3 settled trades. The `-$1.81` claim is that total rounded to the cent.

| Ticker | Side | Price | Contracts | Fee | Payout | Fee-net |
| --- | --- | --- | --- | --- | --- | --- |
| KXTRUTHSOCIAL-26OCT03-B129 | yes | 0.36 | 20 | 0.3226 | 0.00 | -7.5226 |
| KXTRUTHSOCIAL-26OCT10-B149 | no | 0.84 | 8 | 0.0753 | 8.00 | 1.2047 |
| KXTRUMPACT-26OCT04-T6 | no | 0.42 | 8 | 0.1365 | 8.00 | 4.5035 |
| Total |  |  |  | 0.5344 | 16.00 | -1.8144 |

Formula: `revenue/100 - yes_cost - no_cost - fee_cost`. Fill fees match settlement fees. The fill log prices T6 at 43c. The fill and the settlement cost are 42c. Fee-net uses 42c. The 1c gap is `$0.08` of cost in the log only.

Paper fee-net is `+$1.5360` on 6 resolved rows. The `+$1.54` claim is that total rounded to the cent. It is a different book. It is not live edge.

Cash at the audit GET: integer `balance` `2920` cents (`$29.20`). `balance_dollars` is `29.2049`. `portfolio_value` is `2160` cents (`$21.60`). Mark equity is `$50.8049`. Cash is 57.48% of that equity. Cost of the three open positions is `$18.72`. Fees already paid on them are `$0.2607`.

Open rows, unchanged: GOLF YES 8 at 89c, T5 NO 8 at 60c, OCT17-B129 NO 8 at 85c. GOLF's market is `determined` with `result=yes` and is not in `/portfolio/settlements`. A `$1` payout would be `8.00 - 7.12 - 0.0549 = +0.8251`. That number is not cash. Adding it to the settled total would still leave about `-$0.99`, and it is not booked.

Expected dollars per week now: not measured.

Inputs that were not turned into a rate: settled fee-net `-$1.8144`, n=3, 0 live fills since 2026-10-15 12:22:13 UTC, 3 positions still open. The rejected formula in `strategy.md` is `(settled fee-net / elapsed days) x 7`. That is not a rate under the current flags.

Expected dollars per week after the top fixes: not measured. No proposal has `registered_oos` replay evidence. `logs/edge_replay/corpus_politics_oos.jsonl` is absent. `assert_materializable` at 2026-10-15T14:00:00Z raised `OOSMaterializationTooEarlyError` for the window end `2027-01-14T00:00:00Z`. The gate was not overridden.

## What held

The happy path blocks a sibling. With T5 in the open list, `event_news_blocking_open_ticker` returns T5 and the admit gate does not enqueue T4. Truth Social still fail-closes when the positions GET fails, because the sentinel ticker starts with `KXTRUTHSOCIAL`. The band, the 55c YES floor, and the 8-lot clamp are on. GET `/portfolio/orders` showed 0 resting orders. POST is not retried. A timeout holds the ticker. Cash and the three position sizes match the API.

## Blind spots

- The health report is a standing false RED. It cannot tell Jake that politics died, stopped trading, or lost money.
- A handled stop exits 0. Launchd then leaves politics down. A non-zero crash restarts every 10 seconds with no cap. This was read from the plist and `main.py`. The live process was not killed to prove it.
- Watch and the live veto do not ground on sources. Watch does not close a position.
- The rules journal stores `mismatch` and a trap sentence. It does not store the raw reply, latency, or a dollar cost. Outcomes on those rows are null.
- The fill-log branch does not exist. Local `logs/state/live-fill-log.md` matches `docs/reports/live-fill-log.md`. The T6 price in that file is the 43c limit, not the 42c fill.
- The cohort dossier and the cohort paper DB are not in the daily snapshot. Nobody has restored them.
- `replay-ci-gate` is not a required check. It cannot pass on a `registered_oos` politics corpus before 2027-01-14.
- Keyword multipliers reset to 1.0 at every boot because all 6 cohort outbox payloads have `series_ticker=""`. Whether that changes a live official-p order was not measured.

How to see the next failure: point `scripts/bothealth.sh` at `com.jake.kalshi-bot-politics`, and alert when the process is down or when the cash integer moves. Keep that change in a paper or dry-run diff. Do not load `com.jake.kalshi-bot`.

## Proposed fix order

Each fix stays behind a flag. Test it in paper first. Do not restart PID 86845 for these.

1. Make the loss halt use the session cash, and make a failed `get_balance` skip the trade without seeding `$0`. Add a paper test for a `$29.20` seed. Do not turn the new halt on live until that test fails closed. Fix the exit-0 restart in the same change, or a halt becomes a one-way stop.
2. On a positions GET failure, fail closed for `KXTRUMPSAY` and `KXTRUMPACT` as well as `KXTRUTHSOCIAL`. Make the executor prefix cap read the same live list the admit gate reads. Paper-test the exception path. Do not relax the one-open-row cap.
3. Stop sending the Truth Social rubric to presidential-action markets. Keep `RULES_CHECK` paper-on and live-off. Read the next paper rows before any live change.
4. Point the health check at the politics label. Dry-run the script. Do not bootstrap a job.
5. Add `PYTHONPATH` to the fill-log job. The next dry-run should still say unchanged until a new fill exists. Do not push the branch by hand.
6. Write the settlement result onto the research run. Paper first. Do not touch GOLF, T5, or OCT17-B129.

Do not change the 8-contract cap, the 55c YES floor, or the 0–35c band. The only settled live loss is the order those rules now skip. Do not set `REPLAY_GATE_OVERRIDE`.

## Single next action

Approve or reject fix 1 as a paper-only PR. Leave PID 86845, the three open rows, and every live flag as they are.
