# Hermes Source Install Migration Plan

## Objective

Move Hermes Agent off the broken Homebrew package path and onto the recommended source install path in a separate location, migrate the current setup, verify dashboard/kanban behavior there, then cut over and remove Homebrew only after the source install is proven.

## Execution Status

Status as of 2026-05-17:

- Source checkout is active at `/Users/jacobparenti/src/hermes-agent`.
- Active runtime home is `/Users/jacobparenti/.hermes-recommended`.
- Dashboard on `127.0.0.1:9119` is served by the source install via `ai.hermes.dashboard-source`.
- Gateway LaunchAgents are cut over to the source venv and `.hermes-recommended`.
- Root/default gateway is intentionally kept active as `ai.hermes.gateway` and is pinned with `--profile default`; do not disable it as a migration fix.
- Named gateway LaunchAgents are pinned with `--profile <name>` and profile-specific `HERMES_HOME` values under `.hermes-recommended/profiles/<name>`.
- `--replace` is retained for each gateway job for same-profile handoff.
- Homebrew Hermes is not uninstalled yet. Remove only after the source install remains healthy through the final operator verification window.

Durable default-profile note:

- `.hermes-recommended/active_profile` can point at a named profile such as `programmer`.
- An unqualified root LaunchAgent can inherit that sticky active profile and collide with the named profile.
- The durable fix is explicit profile pinning: `hermes_cli.main --profile default gateway run --replace` for `ai.hermes.gateway`, not disabling the default job.

## Original Current State Summary

- Homebrew Hermes dashboard is still serving on `127.0.0.1:9119`.
- Last observed dashboard PID: `2464`.
- Current live Hermes data root exists: `/Users/jacobparenti/.hermes`.
- Planned source checkout does not yet exist: `/Users/jacobparenti/src/hermes-agent`.
- Planned isolated migration data root does not yet exist: `/Users/jacobparenti/.hermes-recommended`.
- `uv` was not installed or not on PATH during preflight.
- `python3.11` was not installed or not on PATH during preflight.
- System `python3` was `3.9.6`.
- Context-mode failed during preflight with `Transport closed`; use compact direct shell summaries until a new session restores it.

## Why Migration Is Needed

The visible kanban failure is not just board data.

Evidence from the prior session:

- The kanban board `kalshi-bot-orchestrator-v2` exists.
- Task `t_94baf588` exists.
- The original `id = slug` compatibility alias in `plugin_api.py` was not sufficient.
- Homebrew package lacked proper dashboard plugin assets.
- A later patch mounted the backend API, but the installed plugin frontend was a stub:
  - installed `dist/index.js`: 209 bytes, registers `kanban`, returns `null`
  - real source `dist/index.js`: about 131 KB, real UI
  - installed manifest hid the tab
  - real manifest exposes `/kanban` and includes CSS
- Therefore piecemeal patching Homebrew files is the wrong long-term path.

## Guardrails

- Do not touch `kalshi-bot` repo code except this saved plan.
- Do not execute trading behavior.
- Do not start or mutate the kalshi-bot runtime.
- Do not expose secret values.
- Do not remove Homebrew until the source install passes verification and survives restart.
- Stop at critical gates:
  - dependency install unavailable
  - source install cannot build web UI
  - isolated migrated data does not load
  - kanban board is not visible in source dashboard
  - source dashboard cannot run without `/tmp/hermes-dashboard-build`

## Target Layout

- Source checkout: `/Users/jacobparenti/src/hermes-agent`
- Source venv: `/Users/jacobparenti/src/hermes-agent/venv`
- Isolated migration data root: `/Users/jacobparenti/.hermes-recommended`
- Production runtime root after cutover: `/Users/jacobparenti/.hermes-recommended`
- Legacy copied source root retained for rollback/reference: `/Users/jacobparenti/.hermes`
- Test dashboard port: `9120`
- Test gateway port: `8643`
- Existing Homebrew dashboard remains on `9119` until cutover.

## Phase 1: Preserve And Inventory Current State

1. Record current Hermes processes and ports:
   - dashboard PID/command/env
   - gateway/profile PIDs if present
   - listeners on `9119`, `9120`, `8642`, `8643`
2. Backup current data:
   - timestamped copy of `/Users/jacobparenti/.hermes`
3. Create isolated migration copy:
   - copy `/Users/jacobparenti/.hermes` to `/Users/jacobparenti/.hermes-recommended`
4. Inventory without secrets:
   - top-level files/directories in `.hermes`
   - profiles present
   - plugins present
   - config files present
   - `.env` key names only, not values
   - kanban DB location and board/task counts if available

## Phase 2: Install Recommended Source Hermes

Use upstream source install instead of Homebrew packaging.

1. Ensure prerequisites:
   - `git`
   - `uv`
   - Python `3.11`
   - `node`
   - `npm`
2. Create source parent:
   - `/Users/jacobparenti/src`
3. Clone upstream with submodules:
   - `git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git /Users/jacobparenti/src/hermes-agent`
4. Create venv with Python 3.11:
   - `uv venv venv --python 3.11`
5. Install Hermes from source:
   - from source root, with venv active
   - `uv pip install -e ".[all,dev]"`
6. Build dashboard web UI:
   - `npm install`
   - `npm run build`

## Phase 3: Verify Source Artifacts Before Running

Required files:

- `/Users/jacobparenti/src/hermes-agent/hermes_cli/web_dist/index.html`
- `/Users/jacobparenti/src/hermes-agent/plugins/kanban/dashboard/manifest.json`
- `/Users/jacobparenti/src/hermes-agent/plugins/kanban/dashboard/plugin_api.py`
- `/Users/jacobparenti/src/hermes-agent/plugins/kanban/dashboard/dist/index.js`
- `/Users/jacobparenti/src/hermes-agent/plugins/kanban/dashboard/dist/style.css`

Expected kanban manifest properties:

- `name`: `kanban`
- `tab.path`: `/kanban`
- `entry`: `dist/index.js`
- `css`: `dist/style.css`
- `api`: `plugin_api.py`
- not hidden

## Phase 4: Run Isolated Source Dashboard

Run source Hermes against the copied data root, not production data first.

Expected environment:

- `HERMES_HOME=/Users/jacobparenti/.hermes-recommended`
- dashboard port `9120`
- gateway port `8643` if gateway is needed
- source venv Hermes binary, not `/opt/homebrew/bin/hermes`

If Hermes does not expose clean port flags/config for dashboard/gateway, stop and inspect source-supported config before improvising.

## Phase 5: Verify Isolated Migration

Required checks on `127.0.0.1:9120`:

- dashboard root returns built HTML
- no dependency on `/tmp/hermes-dashboard-build`
- `/api/dashboard/plugins` includes `kanban`
- `kanban` plugin is not hidden
- `/dashboard-plugins/kanban/dist/index.js` loads real bundle
- `/dashboard-plugins/kanban/dist/style.css` loads
- authenticated `/api/plugins/kanban/boards` returns JSON
- board `kalshi-bot-orchestrator-v2` appears
- board has both `slug` and `id`
- task `t_94baf588` appears
- browser UI shows Kanban navigation/tab and board cards

Use browser verification if available. API-only success is not enough because the prior failure was API-mounted but UI-invisible.

## Phase 6: Cut Over

Only after Phase 5 passes:

1. Stop Homebrew dashboard on `9119`.
2. Start source dashboard on `9119`.
3. Use production data root `/Users/jacobparenti/.hermes` only after isolated root passes.
4. Re-run all Phase 5 checks on `9119`.
5. Create/update a durable launcher that calls the source venv Hermes binary, not `/opt/homebrew/bin/hermes`.
6. Confirm restart survives without `/tmp/hermes-dashboard-build`.

## Phase 7: Remove Homebrew

Only after source cutover passes and survives restart:

1. Record installed Homebrew formula/version.
2. Confirm no running process uses `/opt/homebrew/bin/hermes`.
3. Uninstall Homebrew Hermes.
4. Confirm:
   - `which hermes` points to source launcher or no global Homebrew binary
   - dashboard still works
   - kanban still visible
   - source install serves frontend assets directly

## Rollback

If any critical check fails:

1. Stop source dashboard/gateway.
2. Keep or restart Homebrew dashboard on `9119`.
3. Do not uninstall Homebrew.
4. Restore `/Users/jacobparenti/.hermes` from timestamped backup only if production data was mutated.
5. Preserve failure logs and exact artifact checks for follow-up.

## Fresh Session Start Commands

Start with read-only preflight:

```bash
node - <<'NODE'
const fs=require('fs');
const {execFileSync}=require('child_process');
function run(cmd,args){try{return execFileSync(cmd,args,{encoding:'utf8',stdio:['ignore','pipe','pipe'],timeout:8000}).trim()}catch(e){return ((e.stdout||'')+(e.stderr||'')||e.message).trim()}}
const out={
  targetExists:fs.existsSync('/Users/jacobparenti/src/hermes-agent'),
  recommendedExists:fs.existsSync('/Users/jacobparenti/.hermes-recommended'),
  hermesHomeExists:fs.existsSync('/Users/jacobparenti/.hermes'),
  tools:{git:run('git',['--version']),uv:run('uv',['--version']),node:run('node',['--version']),npm:run('npm',['--version']),python311:run('python3.11',['--version']),python3:run('python3',['--version'])},
  ports:{p9119:run('lsof',['-nP','-iTCP:9119','-sTCP:LISTEN']).split('\\n').slice(0,2),p9120:run('lsof',['-nP','-iTCP:9120','-sTCP:LISTEN']).split('\\n').slice(0,2),p8643:run('lsof',['-nP','-iTCP:8643','-sTCP:LISTEN']).split('\\n').slice(0,2)}
};
console.log(JSON.stringify(out,null,2));
NODE
```

Then proceed from Phase 1.
