# Off-box health alerts

The health job does not send until this file exists and the job is reloaded.
Do not put the topic in the repo, in `.env`, or in a plist.

`scripts/bothealth_launchd.sh` reads one line from `~/.kalshi-alerts.env`:

```bash
ALERT_NTFY_TOPIC=your-unguessable-topic
```

A new live fill and a latched session halt call `send_offbox_alert` in
`scripts/bothealth.sh`. A cash drop over $5 and a cash file older than 2
hours call that same sender. Nothing in this setup places an order.

## 1. Create the topic

In the ntfy phone app, subscribe to a long random topic name. Anyone who
knows the name can read it, so do not use a guessable word.

## 2. Write the env file

```bash
umask 077
printf '%s\n' 'ALERT_NTFY_TOPIC=your-unguessable-topic' > "$HOME/.kalshi-alerts.env"
chmod 600 "$HOME/.kalshi-alerts.env"
```

Replace `your-unguessable-topic` with the topic from the app. The file
stays outside the repo.

## 3. Reload the health job only

These commands reload `com.jake.kalshi-bothealth`. They do not touch
`com.jake.kalshi-bot` or `com.jake.kalshi-bot-politics`.

```bash
launchctl bootout "gui/$(id -u)/com.jake.kalshi-bothealth"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jake.kalshi-bothealth.plist"
```

The installed health plist must already point at `scripts/bothealth_launchd.sh`.
That file is staged with v0.34.0. Do not bootstrap until that plist is the
one on disk.

## 4. Send one test

Run this only after the phone is subscribed. It sends one message and does
not write the health report.

```bash
KALSHI_ALERTS_ENV="$HOME/.kalshi-alerts.env" \
  /bin/bash /Users/jacobparenti/vscode/kalshi-bot/scripts/bothealth_launchd.sh --test-alert
```

Stdout should be `alert_channel=ntfy` and must not print the topic.
A missing file prints `alert channel not configured` and exits 0.
