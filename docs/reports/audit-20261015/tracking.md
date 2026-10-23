# Tracking and data integrity

Read-only audit of the live tree `/Users/jacobparenti/vscode/kalshi-bot` at `17c7e47` (`v0.33.97`). PID 86845 was left running. No code, flags, plists, or git state were changed. No orders were placed or cancelled. SQLite was opened with `file:<path>?mode=ro` and `PRAGMA query_only=ON`. Kalshi calls were `KalshiRestClient()._request("GET", ...)` only. Backup database files were not opened.

## Method

- Fill-log path: read `scripts/copy_live_fill_log.py` and `deploy/com.jake.kalshi-live-fill-log.plist` (same text as `~/Library/LaunchAgents/com.jake.kalshi-live-fill-log.plist`). `git ls-remote` for `refs/heads/ops/live-fill-log` and `refs/heads/main`. Compared `logs/state/live-fill-log.md` with `docs/reports/live-fill-log.md`. Read `logs/app/live-fill-log-copy.stderr.log` and `launchctl print` for `com.jake.kalshi-live-fill-log` (label, exit code, `FILL_LOG_PUSH_REF` only).
- `--dry-run` returns inside `publish_live_fill_report` before copy, commit, or push. It was run from the live tree as `PYTHONPATH=. .venv/bin/python scripts/copy_live_fill_log.py --dry-run`.
- Paper books under `data/` were counted for blank `series_ticker`, outbox orphans, duplicate natural keys, and timestamp shapes. The warning string was counted on the `errors.log*` channel (WARNING+). `bot.log` and launchd stderr copies of the same line were not added.
- Portfolio GET at 2026-10-15T13:52Z, one page each: `/portfolio/balance`, `/portfolio/fills`, `/portfolio/settlements`, `/portfolio/positions`. Identifiers were redacted before this report.
- Backups: `du -sk` / `du -sh` / `ls` of `logs/backups`, `mac_archive`, and nearby backup-named directories. `logs/app/db-backup.stdout.log` sizes were used. Snapshot files were not opened. Host timezone at audit time was MDT.

## Findings

### F-E1

- Proposed severity: medium
- Finding: `ops/live-fill-log` is not on origin. The 06:05 job crashes before it can create the branch, and a healthy run still would not create it while the runtime log and the committed report are the same bytes.
- Evidence: `git ls-remote origin refs/heads/ops/live-fill-log` printed nothing and exited 0. `git ls-remote --heads origin` returned only `17c7e47a52ba0fb5037798a0a6110a65f2db954c refs/heads/main`. The publisher creates that branch only in `_push_side_ref`, and only after the report bytes change. A missing remote ref is ignored; the first successful push would be `HEAD:refs/heads/<ref>`. The loaded job sets `FILL_LOG_PUSH_REF=ops/live-fill-log` and does not set `PYTHONPATH`. `sys.path[0]` for `python scripts/copy_live_fill_log.py` is `scripts/`, so `from trading.executor import ...` raises `ModuleNotFoundError: No module named 'trading'`. `launchctl` shows `runs = 2` and `last exit code = 1`. `logs/app/live-fill-log-copy.stderr.log` contains that traceback twice. `logs/state/fill-log-push.err` does not exist, because the import is outside the handler that records push failures and still exits 0. `cmp` of `logs/state/live-fill-log.md` and `docs/reports/live-fill-log.md` was equal (both 16 lines). The dry run printed `dry-run: unchanged`, so today's job would not push even with `PYTHONPATH` set.
- Dollar impact per week: not measured
- Unverified: no

### F-E2

- Proposed severity: low
- Finding: The committed fill log's price for `KXTRUMPACT-26OCT04-T6` is 43 cents. The exchange fill and settlement cost are 42 cents. The column is the order limit, not the average fill.
- Evidence: `append_live_fill_log` writes the caller's `price_cents`. The live caller passes the same `price_cents` it sends as `limit_price`. Detail columns (`avg_fill_cents`, fee) are appended only when `EVENT_NEWS_FILL_LOG_DETAIL` is on; these six lines have no detail columns. Log row: 2026-10-08T10:05:00Z, `KXTRUMPACT-26OCT04-T6`, no, price 43, contracts 8, HTTP token `201`. API fill: `created_time` 2026-10-08T10:05:00.943945Z, `side=no`, `action=sell`, `count_fp=8.00`, `no_price_dollars=0.4200`, `yes_price_dollars=0.5800`, `fee_cost=0.136500`. Settlement: `no_count_fp=8.00`, `no_total_cost_dollars=3.360000` (42 cents times 8), `fee_cost=0.136500`, `revenue=800`, `market_result=no`, `settled_time=2026-10-11T17:42:58.645421Z`. The other five log prices match the API dollar price times 100. The politics replay row for this market uses 42 cents and fee-net `4.5035`, which matches `8.00 - 3.36 - 0.1365`. The funnel total is therefore right; the fill log is the row that is wrong. One cent times 8 contracts is $0.08 of cost overstated in the log.
- Dollar impact per week: not measured
- Unverified: no

### F-E3

- Proposed severity: medium
- Finding: Every settled outbox payload in the live event-news paper book has `series_ticker=""`. Each boot logs `canonical outbox series_ticker is invalid`, catches it, and clears keyword accuracy multipliers. That is a warning, not a crash. The current process logged it once at start and kept running.
- Evidence: PID 86845 has `data/paper_cohorts/kalshi-event-news-20260820/paper_trades.db` open (mtime 2026-09-27T15:24:05Z, 245760 bytes). `PRAGMA quick_check` was `ok`. 6 `paper_trades`, all `resolved=1`, all `series_ticker` blank (`length` 0). 6 outbox rows, 24 requirements, 24 receipts, 0 foreign-key violations, 0 outbox rows whose trade or observation is missing, 0 duplicate `(ticker, side, ts, contracts, entry_price_cents)`, 0 duplicate `trade_id`. All 6 payloads are drained and `series_ticker` is `""`. Tickers are the six sealed paper rows (`KXTRUTHSOCIAL-26AUG29-T240`, `KXTRUTHSOCIAL-26SEP05-T240`, `KXTRUMPACT-26AUG30-T8`, `KXTRUTHSOCIAL-26SEP12-B230`, `KXTRUMPACT-26SEP06-T4`, `KXTRUMPSAY-26SEP14-UKRA`). Their `pnl_dollars` values are `1.1286`, `1.9722`, `1.3573`, `0.8464`, `-7.6005`, and `3.832` (sum `1.5360`). `KeywordStats._load` raises `ValueError("canonical outbox series_ticker is invalid")` when `series_ticker` is not a non-empty string, then the `except` clears the in-memory tables. `analysis/signal_analyzer.py` multiplies keyword weight by `get_multiplier`, which stays at the neutral 1.0 fallback while those tables are empty. `errors.log*` contains 306 matching lines, all `WARNING`, 0 with an adjacent `Traceback`. The line for this process is `2026-10-15 12:22:13` UTC, the same instant as the local start `Thu Oct 15 06:22:13 2026` MDT. By UTC day the counts are 2026-08-16: 1, 2026-08-17: 40, 2026-09-02: 3, 2026-09-04: 1, 2026-09-12: 18, 2026-09-27: 227, 2026-10-02: 2, 2026-10-03: 1, 2026-10-08: 1, 2026-10-11: 5, 2026-10-13: 5, 2026-10-14: 1, 2026-10-15: 1. The 2026-09-27 cluster has minimum gap 1s and median gap 11s. That burst is historical. It is the same caught warning, not a new crash, and its cause was not pursued. `politics.launchd.stderr.log` has 265 copies of the same text (last stamp also `2026-10-15 12:22:13`); those were not added to 306.
- Dollar impact per week: not measured
- Unverified: no

### F-E4

- Proposed severity: medium
- Finding: The daily snapshot backs up the frozen root `data/paper_trades.db` and `data/evidence_store.db`. It does not back up the paper database PID 86845 has open, or that cohort's `research_dossier.db`, which was written during this audit. A production restore of the live cohort was not found. Snapshot publish does run `PRAGMA integrity_check` on the two root files; `sqlite_restore` runs only for `weather_shadow.db` when `--include-weather` is set, and the installed plist does not set it.
- Evidence: `scripts/db_snapshot_backup.sh` snapshots `data/paper_trades.db` and `data/evidence_store.db` into `logs/backups/db_snapshots/<UTC stamp>/`. `~/Library/LaunchAgents/com.kalshi.db-backup.plist` runs that script with no extra arguments at local 06:00. Host TZ is MDT, and the newest directory is `2026-10-15T1200Z` (06:00 MDT). Stdout ends with `snapshot ok` for that directory, `paper_trades.db: 1851392 bytes`, `evidence_store.db: 1895067648 bytes`, and a prune of `2026-10-07T1200Z`. Those byte sizes match the live root files. Root `paper_trades.db` mtime is 2026-07-29T00:22:14Z (47 trades, 32 blank `series_ticker`, outbox empty, 36 resolved rows with no outbox event). Root `evidence_store.db` mtime is 2026-09-12T01:05:50Z. The open cohort paper file is 245760 bytes and is not in the snapshot file list. `data/paper_cohorts/kalshi-event-news-20260820/research_dossier.db` is 469041152 bytes, mtime 2026-10-15T13:48:00Z, and is not a snapshot source. `logs/app/db-backup.stderr.log` is 150 bytes and contains two undated `unable to open database file` / `evidence_store.db backup failed` lines. Later stdout still shows successful snapshots, so those lines are not the current run. Unit tests in `tests/test_db_snapshot_backup.py` restore weather only in a temp dir. This audit did not restore anything.
- Dollar impact per week: not measured
- Unverified: no

### F-E5

- Proposed severity: info
- Finding: No 18GB backup tree was found on this host. The backup tree that does exist is about 16 GiB.
- Evidence: `du -sk logs/backups` was 16732848 KiB, which is 17134436352 bytes. `du -sh` prints `16G`. That is nine snapshot directories from `2026-10-08T0754Z` through `2026-10-15T1200Z`, each about 1.8G, plus small `launchd` and pm-feedback directories. `mac_archive` is 152M. `data/` is 2639308 KiB. Other backup-named directories under the home directory at depth 3 are kilobytes to 502M (`~/src/hermes-agent-legacy-backups`), not 18GB. `/Volumes` has no external disk. The in-repo "18GB" strings are MacBook RAM, not a backup path. An unplugged disk or another machine was not searched.
- Dollar impact per week: not measured
- Unverified: yes

### F-E6

- Proposed severity: info
- Finding: The checked paper databases have no duplicate trade keys, no dangling outbox foreign keys, and no naive or non-UTC timestamp strings. Empty `series_ticker` is the defect in F-E3, not a second clock bug.
- Evidence: Natural-key duplicate groups were 0 on the event-news cohort, both macro cohorts (0 trades), the legacy-pending book, root `paper_trades.db`, and the cutover copies. `PRAGMA foreign_key_check` was 0 rows on each outbox database that was opened. Timestamp columns `ts`, `resolved_ts`, `settled_at`, `price_retrieved_at`, outbox `created_at`, observation `observed_at` / `effective_at`, and receipt `processed_at` were either null or `+00:00`. No `Z`, no other offset, and no naive `YYYY-MM-DD HH:MM:SS` values in those columns. Fill-log timestamps match API `created_time` to the second, and 06:00 MDT backup stamps are `1200Z`. Root and cutover copies have an empty outbox and 36 resolved rows with `settled_at` null but `resolved_ts` set; that is a pre-outbox book, not a dangling row. `data/paper_settlement.db` is 0 bytes. Macro cohort paper files have 0 trades. Legacy-pending `paper_trades.db` has 7 trades, 4 drained outbox rows, 2 of those with `series_ticker=""`, and 0 FK violations. Its non-blank series values are `polymarket_us:...` labels, so a Kalshi ticker-prefix comparison was not treated as a defect.
- Dollar impact per week: not measured
- Unverified: no

## Fill-log dry run

Command, from the live tree:

`PYTHONPATH=. .venv/bin/python scripts/copy_live_fill_log.py --dry-run`

Exact stdout:

```
dry-run: unchanged
```

Exit code 0. Afterward, `git status --porcelain` for `docs/reports/live-fill-log.md` and `logs/state/live-fill-log.md` was empty, and `logs/state/fill-log-push.err` was still absent.

Path actually used: the desk appends to `logs/state/live-fill-log.md` (`LIVE_FILL_LOG_PATH`). The script copies that file onto `docs/reports/live-fill-log.md` (`LIVE_FILL_LOG_ARCHIVE_PATH`). Launchd `com.jake.kalshi-live-fill-log` is the 06:05 MDT push. With `FILL_LOG_PUSH_REF=ops/live-fill-log` the commit is made in a temporary worktree and pushed to that branch so the live checkout stays put. The branch is not created in git config or at install time. It appears only after a push of a changed report. That push has not succeeded: the remote ref is absent, and the two recorded runs died on `import trading`.

## Mismatches

GET time was about 2026-10-15T13:52Z. `balance` is the integer 2920, `balance_dollars` is `29.2049`, `portfolio_value` is 2160. None of the four reports state a balance. A later balance is not a mismatch with a 2026-10-13 snapshot.

Fills: 6. Settlements: 3. Open `market_positions`: 3. No extra fill tickers.

| Claim | Report | API | Verdict |
|---|---|---|---|
| Live entries in the 2026-09-30Z through 2026-10-14Z window | n=6 | 6 fills, all inside the window, no others on the account | match |
| Settled vs open | settled=3, open=3 | 3 settlement rows; 3 positions with `realized_pnl_dollars=0.000000` | match, including on 2026-10-15 |
| Settled fee-net | -1.8144 | OCT03 `-7.5226` + OCT10 `1.2047` + T6 `4.5035` = `-1.8144` | match |
| OCT03 YES 20 at 36c, result no, cost 7.20, fee 0.3226, revenue 800 cents | replay fee-net -7.5226 | `yes_price_dollars=0.3600`, `yes_total_cost_dollars=7.200000`, `revenue=800`, `market_result=no` | match |
| OCT10 NO 8 at 84c, fee 0.0753, cost 6.72 | replay fee-net 1.2047 | `no_price_dollars=0.8400`, `no_total_cost_dollars=6.720000`, `revenue=800`, `market_result=no` | match |
| T6 NO 8, fee 0.1365, cost 3.36 | replay price 42 and fee-net 4.5035 | `no_price_dollars=0.4200`, `no_total_cost_dollars=3.360000`, `revenue=800`, `market_result=no` | match to the funnel; not to the fill log |
| Fill log T6 price | 43 cents | 0.4200 dollars (42 cents) | mismatch, F-E2 |
| Open rows still unsettled | OCT17 NO 8 at 85c; T5 NO 8 at 60c; GOLF YES 8 at 89c | positions `-8` / `-8` / `+8` with exposures `6.800000` / `4.800000` / `7.120000` | match |
| All-history baseline n=9 fee-net -0.2784 | 6 paper + 3 live | paper `pnl_dollars` sum `1.5360` plus API live `-1.8144` = `-0.2784` | match |

`revenue=800` is treated as cents because `800/100 - cost - fee` equals the pinned fee-net to four decimals. Treating 800 as dollars would not.

`KXTRUMPACT-26OCT11-T5` is still an open position on 2026-10-15 (no settlement row). The 2026-10-13 report calling it open agrees with the API. It was not reclassified as a missed settlement.

`docs/reports/event-news-grounding-2026-10-13.md` says n rows 0, n vetoed 0, n with outcome 0. It does not state portfolio figures. `logs/state/event_news_grounding.jsonl` is still absent, so the zero is still true. It is a 2026-10-13 snapshot (`597f19f`, committed 2026-10-13T16:49:42-06:00), not a balance mismatch.

`docs/reports/event-news-rules-2026-10-13.md` says n rows 0. That was true at commit `e316382` (2026-10-13T16:57:12-06:00). The local jsonl now has 18 rows, all `mismatch=true`, all ticker `KXTRUMPACT-26OCT11-T4`, all `outcome=null`, timestamps 2026-10-15T12:22:36Z through 2026-10-15T13:53:32Z. That ticker is not in fills, settlements, or positions. The later rows are not a Kalshi mismatch and not a reason to call the 2026-10-13 zero wrong for its own day. n with outcome is still 0, which is what the report says.

The first fill-log status token is `executed`, not `201`. The fill object has no HTTP status. Price 36 cents and count 20 match `yes_price_dollars=0.3600` and `count_fp=20.00`, so this was not counted as an economic mismatch.

## Backup inventory

| Location | What | Size | Restore |
|---|---|---|---|
| `logs/backups` | whole tree | 16732848 KiB (17134436352 bytes, `du -sh` 16G) | not a single restore drill |
| `logs/backups/db_snapshots` | 9 dirs: `2026-10-08T0754Z`, then daily `2026-10-08T1200Z` through `2026-10-15T1200Z` | 16732480 KiB; each dir about 1.8G | `PRAGMA integrity_check` must have passed for stdout to print `snapshot ok`. Not a restore into a second database |
| each snapshot | `evidence_store.db` 1895067648 bytes and `paper_trades.db` 1851392 bytes (from the job's own stdout for the recent days; `ls -lh` shows the same 1807.3M and 1.8M names on `2026-10-15T1200Z` and `2026-10-08T0754Z`) | contents not opened | paper/evidence restore not run in production |
| `logs/backups/launchd` | plist copies | 4.0K | n/a |
| `logs/backups/pm_feedback_bare_quarantine_20260620T133920Z` | quarantine copy | 228K | not a DB restore test |
| `logs/backups/pm_weight_reset_20260613T195601Z` | weight reset copy | 136K | not a DB restore test |
| `mac_archive` | older import tree, not the current snapshot root | 152M | not tested here |
| live cohort paper DB and `research_dossier.db` | not in the snapshot script | 245760 and 469041152 bytes | no restore found |
| `data/weather_shadow.db` | omitted unless `--include-weather` | 17833984 bytes, mtime 2026-08-22 | production flag is off; unit test covers a temp restore |

Retention is on: the log shows prunes of snapshot dirs older than 7 days. The 18GB figure was not found (F-E5).

## Limits

- Large files not integrity-scanned: `data/evidence_store.db` (1895067648 bytes; `sqlite_master` was listed, rows were not counted), its July repair copy, and the cohort `research_dossier.db` (not opened). `PRAGMA quick_check` on the paper files above returned `ok`.
- `event_positions` was a key on the positions response and was not itemized. `market_positions` had exactly the three open tickers.
- The 2026-09-27 warning burst was counted, not root-caused.
- No full-disk search for an 18GB tree, and no other host. Backup file bytes were not hashed, because those files were not opened.
- Order ids, response ids, and credentials are omitted on purpose.
