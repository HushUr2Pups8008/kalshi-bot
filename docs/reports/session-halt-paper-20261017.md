# SESSION_HALT paper proof 2026-10-17

Ready for live: **no**. `EVENT_NEWS_SESSION_HALT` stays unset. Unset is paper=on and live=off. This run did not export the flag and did not set live=on.

Cohort: event-news only (`kalshi-event-news-20260820`). Other cohorts stay off.

## Threshold

`SESSION_HALT_DOLLAR_CAP` is $10. The line is `min(LIVE_LOSS_LIMIT_PERCENT * seeded equity, $10)`.

The paper fixture seeds $50 at 10%. The line is $5.00. A drop from $50 to $46 does not trip. A drop from $50 to $45 trips (`loss >= line`). `session_halt_loss_line(200, 0.10)` is $10.00, so the dollar cap binds before 10% of a larger book.

Paper equity is `get_notional_bankroll()` plus open `cost_dollars`. The check does not call `get_balance` and does not POST. A trip writes `logs/state/politics_session_halt.json` and does not call shutdown.

## Three cases

Run: `tests/test_event_news_session_halt.py` with `CI=true` from the worktree. 22 passed, including these three.

| case | test | result |
|---|---|---|
| Bankroll read OK, halt trips at the line | `test_paper_bankroll_drop_trips_at_the_equity_line` | pass |
| Bankroll read fails, admit nothing, do not seed | `test_paper_bankroll_read_failure_admits_nothing` | pass |
| Latch clears only when the file is removed | `test_paper_halt_clears_only_when_the_latch_file_is_removed` | pass |

Failure reads covered: exception, `None`, and `0.0`. Each returns `paper bankroll unreadable`, leaves no latch, and does not call `place_limit_order`. A later readable $50 seeds.

Reset: delete `logs/state/politics_session_halt.json`. A new process does not clear the latch. `record_cash_cents` on a new UTC date resets the cash file's start cents and does not clear the latch. There is no clear function.

The tripped reason string is `PAPER HALTED: session loss limit reached -- all trading suspended`. The live path still says `LIVE HALTED` and still runs only when the flag is on.

## Health cohort lock

The politics process was up while health said the cohort lock was unheld. `scripts/bothealth.sh` read `data/bot_runtime.lock`. That file is the 2026-08-30 freeze-era lock (pid 3318, argv `main.py`) and it is not held. PID 81396 holds `data/bot_runtime.kalshi-event-news-20260820.lock`. `scripts/start_politics_paper.sh` exports `BOT_RUNTIME_LOCK_NAME=bot_runtime.kalshi-event-news-20260820.lock`. The plist does not set that variable, so health never saw the held file.

The health script now takes that export, keeps the basename, and rejects an empty name, `.`, `..`, a slash, or any character outside `A-Za-z0-9._-`. A fixture with no start script still uses `data/bot_runtime.lock`. `tests/shell/test_bothealth_verdict.sh` passed, including a fixture that holds the event-news lock, leaves the old lock unheld, and expects GREEN.

The running health job keeps the old script until that job is restarted. This change does not kickstart it.
