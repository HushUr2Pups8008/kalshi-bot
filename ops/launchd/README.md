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
| `com.jake.kalshi-bothealth` | bot health report wrapper | local 05:00 America/Denver daily |
| `com.kalshi.db-backup` | online-safe SQLite snapshot backup | local 06:00 daily plus `RunAtLoad` |
| `com.kalshi.governance.fast` | governance fast cadence | every 2 hours |
| `com.kalshi.governance.deep` | governance deep cadence | local 09:00 daily |

`com.jake.kalshi-grok-paper` is the paper-only Grok sidecar. It is not in
`install.sh` and not in the equivalence audit's default labels, because it
is not installed until the operator copies the rendered plist and bootstraps
that label alone. Do not add it to the default audit list before that copy
exists. Do not bootstrap `com.jake.kalshi-bot-politics` as part of installing
this label. `com.jake.kalshi-bot` is retired and must not be bootstrapped.

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
each generated plist with `plutil -lint`. It does not bootstrap jobs. It removes
a leftover `com.jake.kalshi-bot` plist. Do not bootstrap that label.

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
