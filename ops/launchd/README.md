# ops/launchd

Repo-managed launchd source of truth. Checked-in files are `*.plist.template`;
generated `*.plist` files are per-machine artifacts ignored by git.

## Managed plists

| label | purpose | trigger |
|---|---|---|
| `com.jake.kalshi-bot` | main paper-trading bot via `caffeinate -dimsu @VENV_PYTHON@ main.py` | `RunAtLoad`, restart on non-success exit |
| `com.jake.kalshi-bothealth` | bot health report wrapper | every 15 minutes |
| `com.jake.kalshi-soak-check` | one-shot soak check report wrapper | local 09:07 calendar trigger |
| `com.kalshi.db-backup` | online-safe SQLite snapshot backup | local 06:00 daily plus `RunAtLoad` |
| `com.kalshi.governance.fast` | governance fast cadence | every 2 hours |
| `com.kalshi.governance.deep` | governance deep cadence | local 09:00 daily |

## Generate

```bash
bash ops/launchd/install.sh --print
```

Print mode renders every template with local `@REPO_ROOT@`, `@VENV_PYTHON@`,
and `@GOVERNANCE_LLM_MODEL@` substitutions without writing LaunchAgents.

## Install

```bash
bash ops/launchd/install.sh
```

The installer writes rendered plists to `~/Library/LaunchAgents/` and validates
each generated plist with `plutil -lint`. It does not bootstrap jobs. Load only
the jobs called for by the active runbook, for example:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jake.kalshi-bot.plist
```

## Uninstall

```bash
bash ops/launchd/install.sh --uninstall
```

This attempts `launchctl bootout` for each managed label and removes the
generated plist files. Runtime logs, databases, and `mac_archive/` snapshots are
preserved.
