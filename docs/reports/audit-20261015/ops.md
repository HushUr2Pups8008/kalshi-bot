# Ops and reliability

Read-only audit of `/Users/jacobparenti/vscode/kalshi-bot` at `17c7e47` (`v0.33.97`). Politics PID 86845 was alive at the start and at the end of collection (parent PID 1, state `S`, started Thu Oct 15 06:22:13 2026 local, elapsed about 01:32). It was not signaled. No launchd job was loaded, bootstrapped, kicked, or restarted. No orders were placed.

## Method

- Process: `ps -p 86845` and `ps -p 86855` with pid, ppid, stat, lstart, etime, and command. Not `ps eww`. `lsof -p 86845` for cwd and the Python text file.
- launchd: `launchctl list` filtered to kalshi labels. `launchctl print` for the politics and fill-log jobs, stopped at the first environment block. Installed plists were parsed for key names and non-secret values only (`KeepAlive`, `RunAtLoad`, `SuccessfulExit`, program argv0). Templates read from `deploy/` and `ops/launchd/`. `man caffeinate` and `man launchd.plist`. `pmset -g assertions` filtered to the caffeinate pid. No reboot.
- Disk and logs: `df -h`, `du`, and `stat` byte sizes. Log files were not deleted or rotated. The politics stderr file was not dumped.
- Secrets: `stat` on `.env` and installed plists. `find` for `*.pem` and `*.key`. Git history of 1935 commits searched by path (`*.env`, `*.pem`, `*.key`) and by the strings `BEGIN PRIVATE KEY`, `BEGIN RSA PRIVATE KEY`, and `BEGIN OPENSSH PRIVATE KEY`. Matches were classified by line shape (prose mention versus a PEM header). Matching lines were not printed. `.env` was not read.
- Alerts: repo search for alert, notify, webhook, email, healthcheck, and Telegram. `scripts/bothealth.sh` label and the 2026-10-15 health report verdict line.
- Dependencies: `.python-version`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, CI workflow `python-version`, and local `.venv/bin/pip freeze`. No CVE scanner and no upgrades.
- CI: `.github/workflows/ci.yml`, `.github/workflows/replay-ci-gate.yml`, `.gitlab-ci.yml`, `docs/governance/oos-corpus-registry.json`, and `gh api` branch protection for `HushUr2Pups8008/kalshi-bot` `main`. Test gaps from filename and symbol search under `tests/`.

## Findings

### F-F1

- Proposed severity: high
- Finding: The politics job restarts only after a non-zero exit, but the process turns SIGTERM and the live loss-limit halt into exit 0, so launchd leaves it down. A real non-zero crash restarts forever at the default 10 second throttle, with no crash-count cap.
- Evidence: Installed `com.jake.kalshi-bot-politics` has `RunAtLoad` true, `KeepAlive.SuccessfulExit` false, and no `ThrottleInterval`, `ExitTimeOut`, or crash limit. `man launchd.plist` says `SuccessfulExit` false restarts on the inverse of exit 0, and the default throttle is 10 seconds. `main.py` installs SIGINT and SIGTERM handlers that call `_shutdown`, which cancels tasks. `asyncio.run` swallows `CancelledError` and the process then ends 0. The loss-limit path calls that same shutdown callback (`trading/executor.py` around the live balance check). Config validation `sys.exit(1)` happens before those handlers, so a bad config would be the path that crash-loops. PID 86845 was not killed to demonstrate this.
- Dollar impact per week: not measured
- Unverified: no

### F-F2

- Proposed severity: high
- Finding: The live loss halt reads `KalshiRestClient.get_balance`, and that method returns `0.0` on any exception. The first failed read seeds the session baseline at zero and the limit never trips later. A later failed read, after a real seed, looks like a total loss, halts trading, and takes the exit-0 path in F-F1.
- Evidence: `kalshi/rest_client.py` `get_balance` logs a warning and returns `0.0`. `trading/executor.py` `_check_live_loss_limit` seeds on the first call and returns false, then compares later balances with `cfg.bankroll * cfg.live_loss_limit_pct` (default `LIVE_LOSS_LIMIT_PERCENT` `0.10` in `config.py`). The 2026-10-15 12:22:13 UTC boot line for pid 86845 says `mode=live` and `bankroll_source=kalshi_api`. No test calls `KalshiRestClient.get_balance`. The effective percent was not read from `.env` or the process environment. The start script does not export `LIVE_LOSS_LIMIT_PERCENT`.
- Dollar impact per week: not measured
- Unverified: yes

### F-F3

- Proposed severity: high
- Finding: Nothing pages the operator when the politics bot dies, stops trading, or loses money. The daily health check watches the unloaded freeze label and already reports RED while politics is running.
- Evidence: `scripts/bothealth.sh` sets `LAUNCHD_LABEL=com.jake.kalshi-bot` and sends one `osascript` display notification. `launchctl print` could not find that label in `gui/501`. `logs/reports/health/bothealth_2026-10-15.md` verdict is `RED — bot not running` while PID 86845 was running. `com.jake.kalshi-bothealth` is loaded, calendar-only, last exit 0, not running at check time. `observer/telegram.py` is not imported by `main.py`. No webhook, email, or dead-man healthcheck is wired to the live loop. `POSITION_DRIFT` and `LIVE LOSS LIMIT` are log lines. Loss-limit and SIGTERM exits are not restarted (F-F1).
- Dollar impact per week: not measured
- Unverified: no

### F-F4

- Proposed severity: medium
- Finding: App logs rotate. The launchd stderr capture does not. Politics stderr is still growing, and an old freeze stderr file is about 1 GB. Disk is not full.
- Evidence: `utils/logger.py` rotates `bot.log` and `errors.log` at midnight UTC with `backupCount=90`. `main.py` `_log_rotation_task` and `_log_maintenance_task` do not mention `politics.launchd.stderr.log`. That file was 222,786,747 bytes and later 222,830,598 bytes at 2026-10-15 07:53:32 local. `politics.launchd.stdout.log` is 0 bytes (mtime 2026-08-22). `launchd.stderr.log` is 1,030,084,498 bytes, last write 2026-08-30, and the freeze job is not loaded. `logs/` is 24G (`backups` 16G, `app` 5.2G, `trades` 3.2G). `logs/backups/db_snapshots` is 16G across 9 directories from `2026-10-08T0754Z` through `2026-10-15T1200Z`, in line with the snapshot script's default 7-day prune. Data volume: 926Gi, 196Gi used, 706Gi free, 22% full. 180 rotated `bot.log.*` / `errors.log.*` files exist.
- Dollar impact per week: not measured
- Unverified: no

### F-F5

- Proposed severity: medium
- Finding: The 06:05 fill-log LaunchAgent fails before it can copy or push the live fill log. `launchctl list` shows last exit status 1.
- Evidence: Installed `com.jake.kalshi-live-fill-log` runs `.venv/bin/python` on `scripts/copy_live_fill_log.py` with `RunAtLoad` false and no `KeepAlive`. `logs/app/live-fill-log-copy.stderr.log` (798 bytes, 2026-10-15 06:05:05) is two `ModuleNotFoundError: No module named 'trading'` tracebacks from `scripts/copy_live_fill_log.py` line 364. Python puts the script directory, not the repo root, on `sys.path`. Stdout has been 0 bytes since 2026-10-14 06:05:05. `logs/state/live-fill-log.md` and `docs/reports/live-fill-log.md` are both 829 bytes, mtimes 2026-10-13 14:15:20 and 14:49:17 local. The job does not retry until the next calendar fire.
- Dollar impact per week: not measured
- Unverified: no

### F-F6

- Proposed severity: low
- Finding: `.env` is mode 644. No other local user can reach it today, and git history did not show a committed PEM block or a committed `.env` / `*.pem` / `*.key` path. Installed plists have no secret-named environment keys.
- Evidence: `.env` is 16,116 bytes, mode `0644`, group `staff`. Home is `0750` and `~/Library/LaunchAgents` is `0700`. `staff` membership is only `jacobparenti`. The only local account with uid >= 500 is `jacobparenti`. The repo directory is `0755`, so a future staff user who can traverse home could read `.env`. Eleven worktree `.env` symlinks point at this same file. No `*.pem` or `*.key` files were found outside `.git` and `.venv`. `git check-ignore` ignores `.env`. Across 1935 commits, path history for `.env`, `*.pem`, and `*.key` was empty, and `BEGIN OPENSSH PRIVATE KEY` was empty. `BEGIN PRIVATE KEY` / `BEGIN RSA PRIVATE KEY` hits in HEAD (`.env.example`, `README.md`, `config.py`, `kalshi/rest_client.py`, `kalshi/websocket_client.py`, `tests/test_observer.py`, and one archived note) were prose mentions, not PEM headers. Historical commits that introduced those mention strings: `4badc1d06c6f1fa78060be730bd7eba9db60f5c3` (`tests/test_observer.py`), `1f602fc113d8d1372fd8b9ec24c4b2d727bf5aae` (`README.md`), `cd50b76cc991a56f8d2db690ed319c1c7794f707` (`tasks/lessons.md`), `91a8e8ecd6ad6aa6ead1a1faa6e4b5aeef34a6ab` (`config.py`), `0949f6b0ac263e1d19a71ce69db409d458337175` and `2c3cf1016da8d047d4753c23ebbad2ed324cd61e` (`CLAUDE.md`, and the second also `tasks/lessons.md`), `fdefc4ec2bb5e400c761cb8ea901cfcd0be1e7af` (`docs/websocket_fix.md`), `a54a4cae51f01669db0114c5f90833d99325bb78` (`.env.example`, `kalshi/rest_client.py`, `kalshi/websocket_client.py`). Values were not printed. Plist environment name scan: politics 6 flag names, fill-log `FILL_LOG_PUSH_REF`, freeze `PYTHONUNBUFFERED`, bothealth none. Sensitive name count was 0. Share or backup exposure of the 644 file was not checked.
- Dollar impact per week: not measured
- Unverified: yes

### F-F7

- Proposed severity: medium
- Finding: `replay-ci-gate` is not a required check. It does not run on push to `main`. A T3 change still fails closed with `InsufficientCorpusError` until a `registered_oos` corpus exists. The registered window cannot be materialized before `2027-01-14T00:00:00Z`.
- Evidence: `gh api` branch protection on `main` requires `lint`, `tests`, `sims-smoke`, and `p0-gate`, with `strict` true, `enforce_admins` true, and `required_approving_review_count` 0. `replay-ci-gate` is absent. `.github/workflows/replay-ci-gate.yml` triggers on `pull_request` and `workflow_dispatch` only, and the enforce step exits 1 when the gate exit is non-zero. `scripts/edge_replay/tier_classifier.py` maps `trading/executor.py` to T3 and defaults unknown paths to T3. `scripts/edge_replay/replay_gate.py` records `InsufficientCorpusError: 0 usable corpora` when `allow_in_period_only` is false and no diverse corpus is selected. `docs/governance/oos-corpus-registry.json` registration `politics-oos` window is `start_utc` `2026-10-16T00:00:00Z` and `end_utc` `2027-01-14T00:00:00Z`, regime `politics_event_news_oos`, families `KXTRUMPACT`, `KXTRUMPSAY`, `KXTRUTHSOCIAL`. `assert_materializable` raises `OOSMaterializationTooEarlyError` while `as_of` is before `window.end_utc`. `utils/event_news_oos_capture.py` writes `evidence_class` `prospective_capture` and states the gate does not accept those lines. `_validate_registered_oos_corpus` requires `registered_oos`.
- Dollar impact per week: not measured
- Unverified: no

### F-F8

- Proposed severity: medium
- Finding: The live cancel HTTP call, the balance-failure halt, the politics start script, and the fill-log launchd invocation have no test. Order POST and the loss-limit arithmetic do have tests.
- Evidence: `KalshiRestClient.cancel_order` (`kalshi/rest_client.py`, `DELETE /portfolio/orders/{order_id}`, all exceptions return false) has no caller under `tests/` except mocks of `executor._rest.cancel_order` in `tests/test_event_news_grok_flags.py` and `assert_not_called` in `tests/test_event_news_paper_shadow.py`. `tests/test_venue_client_protocol.py` only lists the method name. No test file mentions `scripts/start_politics_paper.sh` or `com.jake.kalshi-bot-politics`. No test calls `KalshiRestClient.get_balance`. `tests/test_executor.py` calls `_check_live_loss_limit` with a passed-in balance. `tests/test_kalshi_rest_transient_logging.py` does call `place_limit_order`. `tests/test_kalshi_websocket_client.py` covers a reconnect sleep after a handshake error. `tests/test_copy_live_fill_log.py` exists, but nothing launches the script the way the plist does, which is the failure in F-F5. `scripts/bothealth.sh` is pinned by skipped-trade tests, not by the politics label.
- Dollar impact per week: not measured
- Unverified: no

### F-F9

- Proposed severity: low
- Finding: The running interpreter is Python 3.14.8. The repo pin file and README still say 3.11. CI also uses 3.14. No in-repo CVE advisory was found. External CVE status was not verified.
- Evidence: PID 86845 text file is Homebrew `python@3.14` `3.14.8`. `.venv/bin/python` is a symlink to `python3.14` and reports `3.14.8`. `.python-version` is `3.11`. `README.md` says Python 3.11. `requirements-dev.txt` says 3.11+. `pyproject.toml` ruff `target-version` is `py311`. `.github/workflows/ci.yml` and `.gitlab-ci.yml` use Python 3.14. Installed, from local `pip freeze` only: `aiohttp==3.13.5` (requirement `>=3.10.0`), `cryptography==47.0.0` (`>=41.0`), `websockets==16.0` (`>=13.0`), `requests==2.32.3` (exact pin), `urllib3==2.6.3` (`>=2,<3`), `feedparser==6.0.11`, `dnspython==2.8.0`, `python-dotenv==1.0.1`, `PyYAML==6.0.3`, `RapidFuzz==3.14.5`, `colorlog==6.8.2`, `tabulate==0.9.0`, `python-dateutil==2.9.0`. Repo search for `CVE-` and `GHSA-` found no advisory ids. `.github/` has workflows only, no Dependabot config. Nothing was upgraded.
- Dollar impact per week: not measured
- Unverified: yes

### F-F10

- Proposed severity: low
- Finding: Sleep assertions are held for the live Python pid by a caffeinate child. They do not cover network loss, and launchd will not restart Python if only that child dies.
- Evidence: `scripts/start_politics_paper.sh` ends with `exec /usr/bin/caffeinate -dimsu` on `.venv/bin/python main.py`. Observed tree: PID 86845 is Python, parent 1; PID 86855 is `/usr/bin/caffeinate -dimsu ... main.py`, parent 86845. `pmset` shows pid 86855 holding `PreventUserIdleSystemSleep`, `PreventUserIdleDisplaySleep`, and `PreventSystemSleep` on behalf of pid 86845. `NetworkClientActive` is 0. `man caffeinate` says `-s` prevents system sleep only on AC power. This host is a Mac Studio (`Mac16,9`, Apple M4 Max), so the AC assertion applies. The freeze template (`ops/launchd/com.jake.kalshi-bot.plist.template`) puts caffeinate in `ProgramArguments`. The politics plist does not. It starts bash. WebSocket reconnect is in process: initial delay 2 seconds, cap 60 seconds (`kalshi/websocket_client.py`). `KeepAlive` has no `NetworkState` key, and `man launchd.plist` says `NetworkState` is no longer implemented. There is no watcher that restarts the bot if caffeinate exits and Python stays up.
- Dollar impact per week: not measured
- Unverified: no

## Launchd and disk

Politics label `com.jake.kalshi-bot-politics` is a user LaunchAgent at `~/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist` (mode 0644, directory 0700). It is running, pid 86845, last exit status 0. Program is `/bin/bash` `scripts/start_politics_paper.sh`, working directory the repo. Stdout `logs/app/politics.launchd.stdout.log`. Stderr `logs/app/politics.launchd.stderr.log`. There is no copy under `/Library/LaunchDaemons` or `/Library/LaunchAgents`. A reboot does not start it until `gui/501` loads the agent (`RunAtLoad` true). That is user login, not a pre-login daemon. Auto-login was not checked. The machine was not rebooted.

`KeepAlive` is only `SuccessfulExit` false. There is no explicit throttle and no crash-loop limit. Default spawn spacing is 10 seconds. See F-F1.

Other loaded labels, from `launchctl list` (pid, last status, label): db-backup `- 0`, fill-log `- 1`, governance fast and deep `- 0`, bothealth `- 0`, match-feedback aggregator `- 0`. Freeze label `com.jake.kalshi-bot` is not loaded. Its plist is still on disk, argv0 `/usr/bin/caffeinate`, same `SuccessfulExit` false policy, and it was not loaded.

Caffeinate flags `-dimsu` block idle system sleep, display sleep, disk idle sleep, and, on AC, system sleep. They do not hold a network assertion. On this Mac Studio the system-sleep assertion is present. Closing a laptop lid does not apply. If the machine is forced to sleep anyway, the process is frozen rather than exited, so `KeepAlive` does not run. On wake, the WebSocket loop is what reconnects.

Log rotation in code covers `bot.log`, `errors.log`, report files, and trade archives. It does not cover launchd stdout or stderr. Politics stderr is the live console capture and is unbounded (F-F4). Current sizes: politics stderr 222,830,598 bytes and growing; old freeze stderr 1,030,084,498 bytes, idle since 2026-08-30; `bot.log` about 7.1 MB; `errors.log` 2,928 bytes. `logs/` is 24G. The data volume has 706Gi free.

`com.kalshi.db-backup` last exit was 0. Newest snapshot directory is `2026-10-15T1200Z`. The fill-log job is the one that is failing (F-F5).

## Alerts and blind spots

What reaches the operator today:

- A daily 05:00 local `osascript` display notification from `com.jake.kalshi-bothealth`, plus a markdown file under `logs/reports/health/`. The check uses `com.jake.kalshi-bot`. That job is unloaded, so the 2026-10-15 verdict is RED "bot not running" even though politics is up. A politics death does not change that verdict.
- Log lines only for session loss halt, position drift, and WebSocket reconnect. Those lines go to the unrotated stderr file and to the rotated `errors.log` when the level is WARNING or above.
- Telegram code exists and stays inert unless a separate preview script is run. The trading process does not construct it.
- No email, Slack, webhook, or external dead-man check showed up in the repo search.

Blind spots: politics crash or exit-0 stop (F-F1), balance-read false halt or disarmed loss limit (F-F2), money loss while the process stays up, network loss that does not kill the process (F-F10), and a fill that is not copied or pushed (F-F5). The operator notification that does fire is already a standing false RED, so it does not distinguish a new outage.

## CI

On `pull_request`, and on `push` to `main` and any tag, `.github/workflows/ci.yml` runs four jobs: `lint` (ruff and `scripts/sync_readme_version.py --check`), `tests` (full pytest), `sims-smoke` (`tests/test_simulations_smoke.py`), and `p0-gate` (normalizer, pricing, pricing replay, drift-counter halt, signing fail-fast). All use Python 3.14. Branch protection on `main` requires exactly those four contexts, strict, admins included, and zero approving reviews.

`replay-ci-gate` runs on pull requests and manual dispatch only. It is not required. T0 skips the corpus. T3, including any change to `trading/executor.py`, still exits 1 on `InsufficientCorpusError` until a `registered_oos` corpus exists. The registration file `docs/governance/oos-corpus-registry.json` says the politics window is `2026-10-16T00:00:00Z` through `2027-01-14T00:00:00Z`. Materialization is refused before that end. Lines captured before then are `prospective_capture`, not `registered_oos`. The earliest pass date for that registered corpus is still `2027-01-14T00:00:00Z`. `REPLAY_GATE_OVERRIDE` can force a pass. The registry was not changed.

`.gitlab-ci.yml` still defines lint, tests, sims-smoke, and p0-gate on merge requests, the default branch, and tags, with image `python:3.14-slim`. It has no replay job. Whether a GitLab runner still executes it was not checked. Origin is GitHub.

Test-suite gaps, with file evidence, are in F-F8. In short: no test covers `KalshiRestClient.cancel_order`'s DELETE, `KalshiRestClient.get_balance`'s failure return, `scripts/start_politics_paper.sh`, the politics launchd label, or the fill-log plist's script invocation. Live POST, WebSocket reconnect backoff, and the numeric loss-limit comparison are tested.

## Limits

- PID 86845 was not signaled, and no launchd job was loaded or restarted. The exit-0 versus `KeepAlive` behavior was not exercised on the live pid.
- `.env` and process environments were not read. `LIVE_LOSS_LIMIT_PERCENT` in the running process is unverified. Plist values for the six politics flag names were not printed.
- No network CVE scan and no package upgrade. Absence of an in-repo advisory is not a claim that the pins are clean.
- Git secret search covered paths and PEM header strings across 1935 commits, then a line-shape check. It was not a full high-entropy scan of every assignment.
- Auto-login, FileVault, and whether a missed 05:00 calendar job fires on wake were not checked.
- Snapshot databases were not opened. The 16G figure is `du` of `logs/backups/db_snapshots` only.
- GitLab CI execution was not verified.
- Dollar impact was not measured. No account balance call was made.
