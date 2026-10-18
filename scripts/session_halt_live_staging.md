# SESSION_HALT live staging

Do not apply this until the operator chooses to restart politics.
The repo plist `deploy/com.jake.kalshi-bot-politics.plist` and
`scripts/start_politics_paper.sh` stay unarmed. Merging v0.34.0 does
not set `EVENT_NEWS_SESSION_HALT`.

The patch adds `EVENT_NEWS_SESSION_HALT` = `true` to the installed
politics plist only. That string is on for live and for paper. Do not
use a number.

Before the restart, `logs/state/politics_session_halt.json` must be
absent. A present latch would boot already halted.

The loss line is `min(LIVE_LOSS_LIMIT_PERCENT * equity, $10)` where
equity is the GET cash plus the GET portfolio value, in dollars.
Recompute it from a fresh GET before restarting. The ship report
states the line at the GET taken for v0.34.0.

Label is `com.jake.kalshi-bot-politics` only. Do not bootstrap
`com.jake.kalshi-bot`.

## Backup

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist.bak-${STAMP}"
/bin/cp -p "$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist" "$BACKUP"
echo "$BACKUP"
```

## Apply

```bash
/usr/bin/patch "$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist" \
  /Users/jacobparenti/vscode/kalshi-bot/deploy/politics-session-halt-live.patch
```

## Restart

```bash
launchctl bootout "gui/$(id -u)/com.jake.kalshi-bot-politics"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist"
```

## Verify

The version and the flag are two boot lines.

```bash
grep -F "Kalshi Trading Bot v0.34.0" /Users/jacobparenti/vscode/kalshi-bot/logs/app/bot.log | tail -n 1
grep -F "SESSION_HALT live=on paper=on" /Users/jacobparenti/vscode/kalshi-bot/logs/app/bot.log | tail -n 1
```

Expected: a `v0.34.0` start line, and `SESSION_HALT live=on paper=on`.

## Rollback

```bash
/bin/cp -p "$BACKUP" "$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist"
launchctl bootout "gui/$(id -u)/com.jake.kalshi-bot-politics"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jake.kalshi-bot-politics.plist"
```

Use the backup path printed by the backup step.
