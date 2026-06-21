# ops/launchd

Repo-managed launchd source of truth. Checked-in files are `*.plist.template`;
generated `*.plist` files are per-machine artifacts ignored by git.

> **Maintainer rule (load-bearing):** these templates are byte-faithful captures
> of the production-machine installed plists. Any change must follow
> [Implementation Contract §15](../../docs/IMPLEMENTATION_CONTRACT.md#15-production-configuration-capture-invariants)
> — only the allowlisted token substitutions (`@REPO_ROOT@`, `@VENV_PYTHON@`,
> `@GOVERNANCE_LLM_MODEL@`) may transform installed-state content. Authoring
> a template "from intent" rather than from the installed artifact is a
> contract violation. The cycle-8 incident (2026-05-05) traced to this
> exact failure mode; see commit `96e2995` for the byte-faithful rewrite.

## Managed plists

| label | purpose | trigger |
|---|---|---|
| `com.jake.kalshi-bot` | main paper-trading bot via `caffeinate -dimsu @VENV_PYTHON@ main.py` | `RunAtLoad`, restart on non-success exit |
| `com.jake.kalshi-bothealth` | bot health report wrapper | local 05:00 America/Denver daily |
| `com.jake.kalshi-soak-check` | one-shot soak check report wrapper | one-shot pinned 2026-05-03 09:07 local |
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

### Drift gate (IC §15 Rule 5)

Default install runs `scripts/launchd_template_equivalence_audit.py --installed`
and refuses to proceed if any rendered template diverges from the installed
plist. Operator escape hatch:

```bash
bash ops/launchd/install.sh --allow-drift
```

In an interactive TTY this prompts for a literal typed `ALLOW-DRIFT`
confirmation; in non-interactive contexts the flag alone bypasses. Use only
after operator-approved drift review.

The pre-commit hook also runs the audit when any
`ops/launchd/*.plist.template` is staged. Bypass: `git commit --no-verify`
(emergency only — CI / manual review still expected to catch).

## Uninstall

```bash
bash ops/launchd/install.sh --uninstall
```

This attempts `launchctl bootout` for each managed label and removes the
generated plist files. Runtime logs, databases, and `mac_archive/` snapshots are
preserved.
