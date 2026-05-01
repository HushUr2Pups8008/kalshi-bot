# Governance Agent Phase 2 — Operator Runbook

This runbook covers Phase 2 (shadow mode) operations. Phase 3 (real mode)
adds an additional flip protocol; Phase 4 adds Claude-API escalation.

> **Active soak (2026-05-01):** the Phase 2 launchd jobs `com.kalshi.governance.fast`
> and `com.kalshi.governance.deep` were bootstrapped on the **Mac Studio** at
> approximately **2026-05-01 ~14:00 UTC** against `qwen3:14b` (Mac-Studio-only
> model selection per "Model selection (hardware-conditional)" below). The §8.5
> 14-day acceptance window targets close on **2026-05-15 ~14:00 UTC**, requires
> **≥30 `GOVERNANCE_DECISION` events** accumulated and **≥85%** deemed reasonable
> on manual review (per the "Manual decision review" section). The **MacBook never
> hosted these launchd jobs** (`launchctl list` on the MacBook returns zero
> `com.kalshi.governance.*` entries) — the soak clock starts on the Studio, not
> on whichever host the bot itself was running on previously. Engineering tracker:
> `PROFIT-PHASE2-001` in [`docs/profit_path_debt_log.md`](../profit_path_debt_log.md);
> operational state: `PROFIT-CUTOVER-001` in the same file. Do **not** restart the
> soak from zero unless `applied=` in `runtime_overrides.yaml` grew during the
> window or a `KILL_SWITCH` event was emitted; benign interruptions (host reboot,
> Ollama restart) pause the clock, not reset it — record the gap in the entry's
> Notes.

## Prerequisites

- macOS host (MacBook 18GB or Mac Studio) with Ollama installed.
- Governance LLM pulled. The launchd plists ship configured for the
  MacBook host (`qwen3:8b`). On Mac Studio, you'll edit them to
  `qwen3:14b` (see "Model selection" below).
- Kalshi-bot Phase 1 governance plumbing merged (`AuditLogger`,
  `KillSwitch`, `RuntimeOverridesReader` exist in `governance/` and
  `utils/`).
- venv at `/Users/Jake/vscode/kalshi_bot/.venv` with Phase 2 deps.

Verify Ollama:

```bash
curl -s http://localhost:11434/api/tags | jq -r '.models[].name'
# expected: qwen3:8b appears (MacBook) or qwen3:14b appears (Mac Studio).
```

## Model selection (hardware-conditional)

The launchd plists in `ops/launchd/` ship with `GOVERNANCE_LLM_MODEL=qwen3:8b`
— the MacBook 18GB target. On Mac Studio, change the value in both plists
before installing:

```bash
# Mac Studio only:
sed -i '' 's|qwen3:8b|qwen3:14b|g' ops/launchd/com.kalshi.governance.fast.plist
sed -i '' 's|qwen3:8b|qwen3:14b|g' ops/launchd/com.kalshi.governance.deep.plist
ollama pull qwen3:14b
```

Verify the model file size fits the host's headroom:

```bash
ollama ls | grep -E "qwen3:(8b|14b)"
```

Note: the trading-bot's signal analyzer remains on `qwen2.5:7b` for Phase 2;
unification with the governance model is intentionally deferred and tracked
as `PROFIT-LLM-001` in `docs/profit_path_debt_log.md`. Read that entry before
considering an `OLLAMA_MODEL` change in `.env`.

## Install the launchd agents

```bash
cp ops/launchd/com.kalshi.governance.fast.plist ~/Library/LaunchAgents/
cp ops/launchd/com.kalshi.governance.deep.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kalshi.governance.deep.plist
launchctl print gui/$(id -u)/com.kalshi.governance.fast | head -20
```

The plist's `RunAtLoad` is `false` for the fast cadence so install does
not immediately fire a cycle. To trigger a fast cycle on demand:

```bash
launchctl kickstart gui/$(id -u)/com.kalshi.governance.fast
```

## Smoke-test (manual, before enabling launchd)

```bash
cd /Users/Jake/vscode/kalshi_bot
GOVERNANCE_DISABLED=false \
  ./.venv/bin/python -m governance --cadence fast --llm fake
```

Expected: exit 0; new entries in `logs/governance/decisions.jsonl`
including `GOVERNANCE_CYCLE_START` and `GOVERNANCE_CYCLE_END`. With
the FakeLLM, you may see `GOVERNANCE_DECISION` records with
`action="no_action"` (FakeLLM defaults to no_action when no canned
response matches the prompt hash; intended).

To verify the real LLM path:

```bash
./.venv/bin/python -m governance --cadence fast --llm qwen
```

Expected: exit 0; one or more `GOVERNANCE_DECISION` records in the
decisions log; every record's `shadow_mode` is `true` and `applied`
is `false` (shadow mode invariant).

## Kill switches

Two env-var kill switches recognized by the agent on startup:

- `GOVERNANCE_DISABLED=true` — agent refuses to run; exits 2 immediately.
  Use when something has gone wrong and you want zero agent activity
  until you've debugged it.
- `GOVERNANCE_READONLY=true` — agent runs through the cycle but writes
  every decision to `proposed`, never `applied`. Equivalent to forcing
  shadow mode regardless of `runtime_overrides.yaml`'s `mode` field.
  Use when you want to keep the trust dataset growing but stop applying
  changes.

These can be set in launchd via the plist's `EnvironmentVariables`
block, in the user's shell environment, or via a wrapper script.

## Monitoring during the 14-day soak

Per spec §8.5, Phase 2 acceptance requires ≥14 days of clean shadow
operation with ≥30 decisions accumulated and ≥85% of them deemed
reasonable on manual review.

Daily monitoring checklist:

```bash
# Yesterday's cycle count + decision count
DATE=$(date -u -v-1d +%Y-%m-%d)
grep -c "GOVERNANCE_CYCLE_START" "logs/governance/decisions.jsonl.${DATE}"
grep -c "GOVERNANCE_DECISION" "logs/governance/decisions.jsonl.${DATE}"

# Any error events?
grep -E "PARSE_ERROR|VALIDATION_ERROR|BATCH_ABORTED|KILL_SWITCH" \
  "logs/governance/decisions.jsonl.${DATE}"

# Confirm shadow mode invariant — applied list in overrides should
# never grow during Phase 2.
python -m utils.runtime_overrides --status | grep "applied="
```

If `applied=` shows nonzero source/keyword/threshold counts, **stop
the soak and investigate** — that is the load-bearing safety bug for
Phase 2.

## Manual decision review

Sample ten random decisions from yesterday's log:

```bash
DATE=$(date -u -v-1d +%Y-%m-%d)
grep '"type":"GOVERNANCE_DECISION"' "logs/governance/decisions.jsonl.${DATE}" | \
  shuf -n 10 | jq '{decision_id, action, target, confidence, reasoning}'
```

Read the reasoning. Note any that are:

- Obviously wrong (the disable target is not actually problematic per
  the bot's other diagnostics).
- Confident but wrong-direction (e.g., proposing to disable a source
  that the source-scorecard tier classifier puts in 'top performers').
- Predicted_effect uses a metric the bot doesn't actually track.

Aggregate count of "reasonable / not reasonable" across the soak; the
85% target is the Phase 2 acceptance gate.

## Common failures

- **Ollama unreachable** — agent exits non-zero, stderr log shows
  `URLError`. Restart Ollama (`ollama serve`) or the host.
- **`GOVERNANCE_DECISION_PARSE_ERROR` rate >5%** — local model is
  drifting from the JSON schema. Either retune the model parameters
  or accept and lower the rate by tightening the system prompt.
- **`KillSwitchActive` raised on every cycle** — check
  `launchctl print gui/$(id -u)/com.kalshi.governance.fast` for the
  EnvironmentVariables block; an accidentally-set `GOVERNANCE_DISABLED=true`
  in the plist is the most likely cause.
- **Trade-log path is a directory, not a file** — production layout is
  `logs/trades/{archive,live}/...` and the adapter routes through
  `utils.trade_log_reader.iter_trade_records`, which handles both.
  If you see `IsADirectoryError`, you may have an old governance
  module pre-`5926d0d`.

## Uninstalling

```bash
launchctl bootout gui/$(id -u)/com.kalshi.governance.fast
launchctl bootout gui/$(id -u)/com.kalshi.governance.deep
rm ~/Library/LaunchAgents/com.kalshi.governance.fast.plist
rm ~/Library/LaunchAgents/com.kalshi.governance.deep.plist
```

The agent's only persistent side effect outside `logs/governance/`
is `data/runtime_overrides.yaml`. In Phase 2 shadow mode, that file
should never have been modified by the agent (only by manual hand-edit
or by Phase 1's `python -m utils.runtime_overrides --revert-batch`).
