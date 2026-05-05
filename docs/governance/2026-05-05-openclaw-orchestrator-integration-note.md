# OpenClaw orchestrator integration note

**Status:** planning note.
**Date:** 2026-05-05.
**Scope:** `kalshi-bot` operator interface and multi-agent workflow control plane.
**Non-scope:** autonomous trading, live execution decisions, or direct runtime override mutation.

## TL;DR

OpenClaw is a good candidate as the **front-door orchestrator** for `kalshi-bot` operator workflows, not as a trading agent. The useful integration is:

```
operator chat surface
  -> OpenClaw gateway / orchestrator
    -> role-routed agent sessions
      -> repo-local scripts, tests, docs, reports
        -> human approval gates for state-changing actions
```

It should coordinate Claude/Codex-style work, run read-only health/audit commands, and return compact pass/fail summaries with artifact paths. It should not decide trades, deploy code, change runtime overrides, push tags, restart launchd jobs, or touch live API keys without explicit operator confirmation.

## Fit to current repo shape

`kalshi-bot` already has the right primitives for an OpenClaw control plane:

- Scripted checks: `scripts/check_soak_invariant.sh`, `scripts/bothealth.sh`, `scripts/wave1_post_deploy_smoke.sh`, `scripts/operator_alert_routing_audit.sh`, `scripts/db_backup_health_audit.sh`.
- Durable artifacts: `docs/governance/*.md`, `logs/governance/decisions.jsonl`, `logs/trades/live/trades.jsonl`.
- Clear role split: `docs/IMPLEMENTATION_CONTRACT.md` §9 and `docs/governance/2026-05-05-cross-cycle-contract-adherence-review.md`.
- Human-gated runbooks: Day-7 close, Wave-1 smoke, KILL_SWITCH, dead-bot reboot, Branch D fire.

That makes OpenClaw most valuable as an **ops cockpit**: it can collect a request from Telegram/Slack/WebChat, route it to the right role/session, run allowlisted repo scripts, and return a concise answer.

## Recommended agent roles

| role | write scope | allowed by default | requires explicit approval |
|---|---|---|---|
| Orchestrator | none | route tasks, maintain checklist state, summarize artifacts | delegate write tasks |
| Docs/planning agent | `docs/` only | draft/update docs, run read-only doc audits | edit policy docs that change deployment authority |
| Codex implementation agent | `scripts/`, `tests/`, focused code files per task | implement locked specs, run tests | touch runtime trading paths during active soak |
| Review/verification agent | none by default | run pytest/ruff/smoke/audit commands, produce findings | modify files |
| Ops/reporting agent | logs/report docs only | run bothealth, backup audit, alert routing, baseline scripts | launchd restarts, tags, pushes, runtime override edits |

OpenClaw should route by intent, not by free-form autonomy. Example:

```
/day7-status
  run check_soak_invariant + alert routing + db backup audit
  return PASS/FAIL table, report paths, and next operator action

/wave1-smoke
  run wave1_fire_time_smoke.sh
  return failed check names and exact pytest selectors

/branch-d-fire-dryrun
  read Branch D runbook
  verify required debt-log/tag/TLDR artifacts exist
  return missing prerequisites
```

## Safety boundaries

Default OpenClaw mode for this repo should be read-only plus artifact-writing. The following require explicit operator confirmation every time:

- `git push`, force-push, tag push, branch creation for rollback anchors.
- `launchctl bootstrap`, `launchctl bootout`, `kill`, or bot process restart.
- Editing `data/runtime_overrides.yaml`, `.env`, API keys, or launchd plists.
- Any command that can place or alter trades.
- Any runtime path edit under `analysis/`, `tasks/`, `feeds/`, `governance/`, `trading/`, `kalshi/`, `main.py`, or `config.py` during a soak window.

The integration should prefer wrapper scripts over improvised shell. If a workflow matters enough to run through OpenClaw, it should have a repo script or runbook first.

## Timing recommendation

Do **not** install or wire OpenClaw into this repo before Day-7 close. The current priority is preserving the `PROFIT-PHASE2-001` evidence window and landing Wave-1 with minimal moving parts.

Best timing:

1. **Now through 2026-05-08T19:01Z:** documentation only. Capture the framework, command vocabulary, and guardrails. No daemon, no channel connection, no new always-on process.
2. **After Day-7 close + Wave-1 first 24 h green:** read-only pilot. OpenClaw can run `status`, `day7-status`, `backup-audit`, and `wave1-smoke` commands that call existing scripts. No write-capable agents.
3. **After Wave-1 observation stabilizes:** multi-agent routing pilot. Add role-routed sessions for docs, Codex implementation, review, and ops reporting. Still require confirmation for commits, tags, pushes, launchd, and runtime overrides.
4. **After at least one successful post-Wave-1 workflow through OpenClaw:** consider write-scoped agent roles. Start with docs-only writes, then test/script writes, then tightly scoped code writes. Never grant autonomous trading authority.

This timing avoids introducing a new orchestrator during the highest-risk close/deploy window while still letting the system become useful before Wave-2/3 and Branch D handoffs create more coordination overhead.

## First integration slice

Implement a repo-local command map before installing channels:

| command | underlying action | output |
|---|---|---|
| `status` | `bash scripts/bothealth.sh` | report path + verdict |
| `day7-status` | `check_soak_invariant`, alert routing, backup audit | PASS/FAIL matrix |
| `wave1-smoke` | `bash scripts/wave1_fire_time_smoke.sh` | failing selectors + report paths |
| `backup-audit` | `bash scripts/db_backup_health_audit.sh --json` | backup freshness/retention verdict |
| `alerts` | `bash scripts/operator_alert_routing_audit.sh --json` | safety-event counts + surfacing verdict |

Only after those commands are stable should OpenClaw own multi-agent task dispatch.
