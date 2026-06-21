# context-mode version-vs-env isolation test plan

Date: 2026-05-17
Scope: Codex context-mode MCP transport recovery only.

## Guardrails

- Do not kill, restart, unload, kickstart, or otherwise touch Hermes gateways.
- Do not uninstall or reinstall context-mode during this test.
- Keep backups before every config change.
- Run each runtime phase in a fresh Codex session after changing `~/.codex/config.toml`.
- Treat `ctx_doctor` and `ctx_stats` as health checks only. A real `ctx_execute` or `ctx_search` is required for pass.

## Baseline Already Established

| Case | Result |
| --- | --- |
| `v1.0.135` without Codex MCP env mitigation | Failed with sibling reap / `Transport closed` |
| `v1.0.135` with `CONTEXT_MODE_STARTUP_SWEEP=0` and `CONTEXT_MODE_IDLE_TIMEOUT_MS=0` | Passed |

Current Codex-only mitigation:

```toml
[mcp_servers.context-mode]
command = "/opt/homebrew/bin/context-mode"
startup_timeout_sec = 30
tool_timeout_sec = 180
env = { CONTEXT_MODE_STARTUP_SWEEP = "0", CONTEXT_MODE_IDLE_TIMEOUT_MS = "0" }
```

## Phase 1 - Upgrade Safety With Env Held

Purpose: verify `v1.0.136` works with the known-good Codex mitigation.

1. Confirm current config includes both env vars.
2. Run the approved context-mode upgrade workflow.
3. Start a fresh Codex session.
4. Run:
   - `ctx_doctor`
   - `ctx_stats`
   - real parent `ctx_execute`: `console.log("ctx_parent_v136_env_on_ok")`
   - child/subagent Codex context-mode call: `console.log("ctx_child_v136_env_on_ok")`
5. Scan Codex log after the upgrade/session cutoff:
   - `0` fresh `Transport closed`
   - `0` `Reaped 1 stale sibling MCP server(s)` under Codex
6. Hermes read-only check:
   - 8 Hermes gateway parents
   - 8 Hermes `context-mode` children
   - one child per parent
   - 0 Hermes-side `Transport closed` after cutoff

Pass means `v1.0.136 + env on` is safe enough to continue testing.
Fail means rollback to `v1.0.135` or stop and investigate the upgrade regression.

## Phase 2 - Version Fix Test With Env Removed

Purpose: determine whether `v1.0.136` fixed the root cause without the Codex env mitigation.

1. Back up `~/.codex/config.toml`.
2. Temporarily remove only this line from `[mcp_servers.context-mode]`:

```toml
env = { CONTEXT_MODE_STARTUP_SWEEP = "0", CONTEXT_MODE_IDLE_TIMEOUT_MS = "0" }
```

3. Do not alter hooks, other MCP servers, or Hermes config.
4. Start a fresh Codex session.
5. Run:
   - `ctx_doctor`
   - `ctx_stats`
   - real parent `ctx_execute`: `console.log("ctx_parent_v136_env_off_ok")`
   - child/subagent Codex context-mode call: `console.log("ctx_child_v136_env_off_ok")`
6. Scan Codex log after env-removal session cutoff:
   - count `Transport closed`
   - count `Reaped 1 stale sibling MCP server(s)`
   - capture thread IDs and line numbers for any failures
7. Hermes read-only check again.

## Interpretation

| Result | Meaning | Action |
| --- | --- | --- |
| `v1.0.136 + env on` passes, `v1.0.136 + env off` passes | Version likely fixed root cause | Keep env removed only after repeated fresh-session confirmation |
| `v1.0.136 + env on` passes, `v1.0.136 + env off` fails or reaps | Env mitigation still required | Restore env line and keep it |
| `v1.0.136 + env on` fails | Upgrade regression or new bug | Stop; do not test env-off until explained |
| Any Hermes `Transport closed` or gateway child loss | Stop; Hermes impact detected | Restore last known-good state and investigate Hermes separately |

## Phase 3 - Restore Final State

If `v1.0.136 + env off` fails, restore:

```toml
env = { CONTEXT_MODE_STARTUP_SWEEP = "0", CONTEXT_MODE_IDLE_TIMEOUT_MS = "0" }
```

Then start one more fresh Codex session and rerun:

- `ctx_doctor`
- `ctx_stats`
- real `ctx_execute`: `console.log("ctx_final_restored_ok")`
- Codex log scan: `0` fresh `Transport closed`, `0` fresh sibling reaps
- Hermes read-only check unchanged

## Required Report Back

Report:

- context-mode version tested
- env state: on or off
- parent real call result
- child real call result
- Codex fresh `Transport closed` count
- Codex fresh sibling reap count
- Hermes gateway parent count
- Hermes context-mode child count
- final config state
- whether env mitigation is still required
