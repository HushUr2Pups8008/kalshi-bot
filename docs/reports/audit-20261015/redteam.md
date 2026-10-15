# Red team

Read-only. Commit `17c7e47`. PID 86845 was not signaled. No Kalshi call. No model call. `.env` was not read. The loss-limit and gate tests imported `TradeExecutor` after `load_dotenv` was replaced with a no-op. Config validation got a generated dummy key in that process only. Scratch files were under `/tmp`.

## Attack A

Claim: F-B2. Seed `29.2049`, then balance `0.0`, does not halt. The limit is `cfg.bankroll * cfg.live_loss_limit_pct`. Defaults are `$500` and `0.10`, so `$50`.

Ran: unbound `TradeExecutor._check_live_loss_limit` on a dummy object. `_is_paper` was false. No executor constructor. No database.

Result: HOLD.

Evidence: first return `False`, second return `False`, baseline `29.2049`, loss `29.2049`, limit `50.0`. This interpreter loaded `cfg.bankroll == 500.0` and `cfg.live_loss_limit_pct == 0.1` with dotenv blocked. `trading/executor.py:488-499`. `config.py:1298`. `config.py:1480-1481`. A control seed of `50.0` then `0.0` returned `True`. A seed of `49.9999` then `0.0` returned `False`.

## Attack B

Claim: F-F2. A first balance of `0.0` seeds the baseline. The next call at `29.2049` does not halt. The first `0.0` disarms later checks.

Ran: same unbound method, a new dummy object.

Result: HOLD for this attack.

Evidence: seed return `False`, second return `False`, third call at `0.0` returned `False`, baseline `0.0`. `trading/executor.py:488-496` returns false on the seed. Later loss is `0.0 - balance`. For every balance `>= 0` that loss is `<= 0`, which is not `>= 50`. A synthetic later balance of `-50.0` returned `True`. `get_balance` does not return that on failure. It returns `0.0`. `kalshi/rest_client.py:964-966`.

## Attack C

Claim: F-F2 says a later `0.0` after a real seed looks like a total loss and halts. Try that at a `$29.2049` seed.

Ran: same unbound method, a new dummy object. Seed `29.2049`, then `0.0`.

Result: BROKE.

Evidence: seed return `False`, later-zero return `False`. Loss `29.2049` is under limit `50.0`. `trading/executor.py:497-499`. F-B2 already says this drop stays under `$50`. That sentence holds. The F-F2 halt sentence does not. The disarm sentence in Attack B still holds.

`get_balance` returns `0.0` on any exception. `kalshi/rest_client.py:964-966`. It was not called. On success it returns integer `balance` cents over 100, not `balance_dollars`. `kalshi/rest_client.py:960-963`. `2920 / 100` is `29.20`, which is still under `$50`. The live process env was not read again. The "BANKROLL absent on PID 86845" line stays unverified here.

## Attack D

Claim: open list contains `KXTRUMPACT-26OCT11-T5`. Candidate `KXTRUMPACT-26OCT11-T4` with event `KXTRUMPACT-26OCT11`. `event_news_blocking_open_ticker` returns `T5`. The admit success path blocks.

Ran: `utils.event_news_research.event_news_blocking_open_ticker` with `is_event_news_paper_cohort` forced true. Tiny market object. No environment print.

Result: HOLD.

Evidence: print `D KXTRUMPACT-26OCT11-T5`. `utils/event_news_research.py:293-323`. Admission returns before `trading_queue.put` when that value is set. `tasks/research_paper_admission.py:535-548`. That call sits inside `if bypass_blend`. `tasks/research_paper_admission.py:496`.

## Attack E

Claim: open list is only `KXTRUTHSOCIAL-LIVE-UNKNOWN`. Same T4 candidate. Both blockers return `None`. The sibling is not blocked. F-B1 is a fail-open on the GET-error path. The headline that a second order can be posted while the GET works is too wide.

Ran: `event_news_blocking_open_ticker` and `event_news_family_lock_ticker` on that list.

Result: HOLD for the fail-open. The wide headline does not hold for this admit path.

Evidence: print `E_block None` and `E_family None`. T4's series is `KXTRUMPACT`, so the family lock returns before the list scan. `utils/event_news_research.py:353-358`. The sentinel does not start with `KXTRUMPACT-26OCT11-`. `utils/event_news_research.py:319`. The GET failure appends the sentinel and returns. `main.py:1439-1447`. `utils/event_news_research.py:67`. It does not keep the live tickers. A T4 sibling is not blocked on that list.

## Attack F

Claim: candidate `KXTRUTHSOCIAL-26OCT24-B200` with the sentinel in the list. `event_news_family_lock_ticker` returns the sentinel. That path fails closed.

Ran: `event_news_family_lock_ticker` with series `KXTRUTHSOCIAL`.

Result: HOLD.

Evidence: print `F KXTRUTHSOCIAL-LIVE-UNKNOWN`. `utils/event_news_research.py:339-364`. Prefix blocking on that same list returned `None` (`F_block None`). Family lock runs first at admission. `tasks/research_paper_admission.py:523-534`.

## Attack G

Claim: `_positions_for_live_gates` filters `paper_shadow` and does not add REST tickers.

Ran: unbound `TradeExecutor._positions_for_live_gates` with two plain rows and one `paper_shadow=True` row. Live mode (`_is_paper` false).

Result: HOLD.

Evidence: kept `['PAPER-OPEN', 'PLAIN']`. A non-list returns `[]`. `trading/executor.py:1125-1131`. The prefix cap passes the paper book into that filter. `trading/executor.py:1278-1280`. Same-signal does the same. `trading/executor.py:1315-1317`. Neither call adds REST tickers. Family lock uses `_event_news_open_exposure_tickers`, which does add live tickers, and only `KXTRUTHSOCIAL` locks. `trading/executor.py:1133-1182`. `trading/executor.py:1294-1297`.

## F-D1

Claim: `MODEL_COUNT_LOGIC` is a Truth Social count rubric. Only `mismatch is True` skips. A timeout or bad JSON does not skip.

Ran: read the constant. Called `event_news_rules_check_skip` with a stub `post`, a stub key, and a log path under `/tmp`. No xAI call. Counted `logs/state/event_news_rules_check.jsonl` without printing bodies.

Result: HOLD.

Evidence: the constant names Truth Social and Factbase, not presidential actions. `utils/event_news_rules_check.py:35-39`. Skip is `answer.get("mismatch") is True`. `utils/event_news_rules_check.py:188-189`. Timeout, bad JSON, missing key, and empty rules returned `False`. `utils/event_news_rules_check.py:145-146`, `:153-155`, `:185-187`. A JSON boolean `true` returned `True`. The string `"true"` returned `False`. The last JSON object wins. Live default (`live=True`, flag unset) returned `False` and did not call `post`. `utils/event_news_desk_flags.py:57-69`.

Journal now: 21 rows, 21 `mismatch` true, 0 false, 0 other, 0 non-null `outcome`, ticker only `KXTRUMPACT-26OCT11-T4`. First hour `2026-10-15T12:22:13Z` through `13:22:13Z`: 12 rows, all that ticker, all mismatch true. The earlier "16 rows" count is stale. The property is not.

## F-C1

Claim: settled fee-net is `-7.5226 + 1.2047 + 4.5035 = -1.8144`. GOLF `+0.8251` stays out.

Ran: `Decimal` sum from `strategy.md` only. No API.

Result: HOLD.

Evidence: sum `-1.8144`. Cent check `-752.26 + 120.47 + 450.35 = -181.44`. GOLF was not added. `strategy.md` fee-net table.

## F-C4

Claim: first hour is 17 prefix-cap, 12 rules, 5 YES-floor, 0 paper rows. Since boot through `13:48:02Z` is 27, 17, 10, and 0.

Ran: counted those substrings in `logs/app/bot.log`. No stderr add.

Result: HOLD.

Evidence: first hour matches `17`, `12`, `5`, `0` recorded, `0` family lock, `0` excluded band. Through the `13:48:02` second the counts are `27`, `17`, `10`, `0`. The last rules line is `13:48:02.213` and the last YES-floor line is `13:48:02.215`. No needle line failed the timestamp parse.

## F-C7

Claim: `corpus_politics_oos.jsonl` is absent. The politics window cannot materialize before `2027-01-14`.

Ran: file check. `get_oos_registration("politics-oos")` then `assert_materializable` at `2026-10-15T14:00:00Z`. No gate override.

Result: HOLD.

Evidence: `corpus_exists False`. `assert_materializable` raised `OOSMaterializationTooEarlyError` for end `2027-01-14T00:00:00Z`. `scripts/edge_replay/oos_registry.py:508-524`. `docs/governance/oos-corpus-registry.json` window `2026-10-16T00:00:00Z` through `2027-01-14T00:00:00Z`.

## F-D3

Claim: the fill line has no research run id, and the run table has no outcome column.

Ran: read the writer and the `CREATE TABLE`. Did not open the dossier database.

Result: HOLD for that code claim. The stored-row census is unverified here.

Evidence: `append_live_fill_log` writes time, ticker, side, price, contracts, client order id, and HTTP result. `trading/executor.py:321-323`. `research_runs` has no outcome column. `tasks/research_dossier.py:481-506`. The rules writer stores `"outcome": None`. `utils/event_news_rules_check.py:200`.

## F-F1

Claim: SIGTERM schedules `_shutdown`. `CancelledError` is swallowed. The process ends 0.

Ran: read `main.py` only. Did not send a signal. Did not kill a process.

Result: HOLD on the source path. The live exit is unverified.

Evidence: the loop registers SIGINT and SIGTERM to `asyncio.create_task(_shutdown(bot))`. `main.py:5558-5560`. `_shutdown` cancels every other task. `main.py:6093-6104`. `asyncio.run` catches `CancelledError` and passes. `main.py:6107-6111`. `bot.run` also catches `CancelledError`. `main.py:5325-5328`. Either path falls off the end. The loss-limit callback schedules that same `_shutdown`. `main.py:1179-1180`. `trading/executor.py:1434-1435`. Installed `KeepAlive` is `SuccessfulExit` false. `~/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist:17-20`. That launchd reaction was not exercised.

## F-F3

Claim: `scripts/bothealth.sh` watches `com.jake.kalshi-bot`, not `com.jake.kalshi-bot-politics`. The 2026-10-15 verdict is RED while politics is up.

Ran: read the label, the verdict branch, and the verdict line. Did not notify.

Result: HOLD.

Evidence: `LAUNCHD_LABEL="com.jake.kalshi-bot"`. `scripts/bothealth.sh:73`. `BOT_ALIVE == 0` sets `**RED** — bot not running`. `scripts/bothealth.sh:709-710`. The report line is `**RED** — bot not running`. `logs/reports/health/bothealth_2026-10-15.md:104`. The script has no `kalshi-bot-politics` label.

## F-E1 / F-F5

Claim: `--dry-run` returns before `git commit` and `git push`. The `trading` import runs at main level, so a missing `PYTHONPATH` raises before the copy.

Ran: read the script and the installed plist. Did not run the script. Did not push.

Result: HOLD.

Evidence: dry-run prints and returns 0 before copy, commit, or push. `scripts/copy_live_fill_log.py:300-302`. `_payload_differs` only reads bytes. `scripts/copy_live_fill_log.py:215-223`. The import is inside `main`, after the unknown-arg check and before `publish_live_fill_report`. `scripts/copy_live_fill_log.py:364-370`. The plist runs that script with no `PYTHONPATH` and no `--dry-run`. `~/Library/LaunchAgents/com.jake.kalshi-live-fill-log.plist:9-18`. Python then puts `scripts/` on `sys.path[0]`, so `from trading.executor` fails before the copy. A prior dry-run, not this one, printed `dry-run: unchanged` (`tracking.md`).

## Still high

- F-B1. Fail-open when the positions GET fails. Executor prefix cap and same-signal still read the paper book only.
- F-B2. A `$29.2049` seed then `$0` does not halt. The limit in this test was `$50`.
- F-D1. Truth Social rubric. Only boolean `mismatch` true skips. Journal rows are still all T4 and all mismatch true.
- F-D3. Code does not store a settlement outcome on the fill or the research run. Row census unverified here.
- F-C1. Settled fee-net is `-1.8144`. GOLF stayed out.
- F-C4. First-hour and through-`13:48:02` funnel counts match.
- F-C7. Politics OOS file is absent. Materialize is too early.
- F-F1. Source turns the handled SIGTERM and the loss-limit callback into a normal exit. Live process not signaled.
- F-F2 disarm sentence only. A first `0.0` keeps later non-negative balances from halting.
- F-F3. Health check watches the unloaded freeze label and the report is RED.

## Broke

- F-F2 halt sentence. A later `0.0` after a real `$29.2049` seed does not halt. Loss `$29.2049` is under the `$50` limit.
- Wide F-B1 headline. A second T4 order is not what the admit success path does while the GET returns T5. That path returns T5 and does not enqueue. The GET-error list does not block T4.
