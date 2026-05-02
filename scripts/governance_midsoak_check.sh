#!/usr/bin/env bash
# One-shot governance Phase 2 mid-soak check report generator.
#
# Captures the current state of the PROFIT-PHASE2-001 14-day shadow-mode
# governance soak (started 2026-05-01 ~14:00 UTC, ETA close 2026-05-15
# ~14:00 UTC) and writes a timestamped Markdown report to
# logs/app/governance_midsoak_<ts>.md.
#
# Designed to be triggered once by ~/Library/LaunchAgents/
# com.jake.kalshi-governance-midsoak.plist on 2026-05-08 09:07 MDT
# (~T+7d, mid-window). After the script runs it self-removes that plist
# via launchctl bootout + rm so it does not refire.
#
# Safe to run manually at any time (will not break or duplicate anything).

set -uo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/logs/app"
GOV_LOG_DIR="$REPO_ROOT/logs/governance"
OVERRIDES_YAML="$REPO_ROOT/data/runtime_overrides.yaml"
MIDSOAK_LABEL="com.jake.kalshi-governance-midsoak"
MIDSOAK_PLIST="$HOME/Library/LaunchAgents/$MIDSOAK_LABEL.plist"

# Soak window anchors — match the PROFIT-PHASE2-001 entry exactly.
SOAK_START_UTC="2026-05-01T14:00:00Z"
SOAK_TARGET_CLOSE_UTC="2026-05-15T14:00:00Z"
ACCEPTANCE_MIN_DECISIONS=30
ACCEPTANCE_REASONABLE_PCT=85

TS="$(date -u +%Y%m%d_%H%M%SZ)"
REPORT="$LOG_DIR/governance_midsoak_$TS.md"

mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
section() { printf '\n## %s\n\n' "$1" >>"$REPORT"; }
codeblock_start() { printf '```\n' >>"$REPORT"; }
codeblock_end()   { printf '```\n' >>"$REPORT"; }

# ── Header ────────────────────────────────────────────────────────────────────
{
    printf '# Governance Phase 2 — mid-soak check report\n\n'
    printf 'Generated: %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf 'Host: %s\n' "$(hostname -s)"
    printf 'Repo: %s\n' "$REPO_ROOT"
    printf 'Branch: %s @ %s\n' "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)" "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    printf '\n'
    printf 'Soak window: %s → %s (target)\n' "$SOAK_START_UTC" "$SOAK_TARGET_CLOSE_UTC"
    printf 'Acceptance:  ≥%d decisions, ≥%d%% reasonable on manual review, applied=0 throughout, KILL_SWITCH=0 throughout\n' \
        "$ACCEPTANCE_MIN_DECISIONS" "$ACCEPTANCE_REASONABLE_PCT"
} >"$REPORT"

# ── 1. Window progress ────────────────────────────────────────────────────────
section "Window progress"
{
    NOW_EPOCH="$(date -u +%s)"
    START_EPOCH="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$SOAK_START_UTC" +%s 2>/dev/null || date -d "$SOAK_START_UTC" +%s)"
    END_EPOCH="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$SOAK_TARGET_CLOSE_UTC" +%s 2>/dev/null || date -d "$SOAK_TARGET_CLOSE_UTC" +%s)"
    ELAPSED_SEC=$((NOW_EPOCH - START_EPOCH))
    REMAINING_SEC=$((END_EPOCH - NOW_EPOCH))
    TOTAL_SEC=$((END_EPOCH - START_EPOCH))
    if (( ELAPSED_SEC > 0 && TOTAL_SEC > 0 )); then
        PCT=$(( ELAPSED_SEC * 100 / TOTAL_SEC ))
    else
        PCT=0
    fi
    printf '```\n'
    printf 'Elapsed   : %d days %d hours\n' $((ELAPSED_SEC / 86400)) $(((ELAPSED_SEC % 86400) / 3600))
    printf 'Remaining : %d days %d hours\n' $((REMAINING_SEC / 86400)) $(((REMAINING_SEC % 86400) / 3600))
    printf 'Progress  : %d%%\n' "$PCT"
    printf '```\n'
} >>"$REPORT"

# ── 2. Audit log presence ─────────────────────────────────────────────────────
section "Audit log files"
codeblock_start
if [[ -d "$GOV_LOG_DIR" ]]; then
    ls -lah "$GOV_LOG_DIR"/decisions.jsonl* 2>/dev/null >>"$REPORT" || \
        printf '(no decisions.jsonl files found in %s)\n' "$GOV_LOG_DIR" >>"$REPORT"
else
    printf '(governance log dir does not exist: %s — soak may not have started or launchd jobs not bootstrapped)\n' \
        "$GOV_LOG_DIR" >>"$REPORT"
fi
codeblock_end

# ── 3. Cycle + decision counts since soak start ───────────────────────────────
section "Cycle + decision counts (full soak window)"

# Concatenate all daily-rotated audit files plus the live one. The audit
# logger writes to decisions.jsonl and rotates to decisions.jsonl.<DATE>
# at midnight UTC — we want every file.
GOV_FILES=()
if [[ -d "$GOV_LOG_DIR" ]]; then
    while IFS= read -r f; do
        GOV_FILES+=("$f")
    done < <(find "$GOV_LOG_DIR" -maxdepth 1 -type f -name 'decisions.jsonl*' 2>/dev/null | sort)
fi

codeblock_start
if (( ${#GOV_FILES[@]} == 0 )); then
    printf '(no audit files to scan)\n' >>"$REPORT"
    CYCLE_START=0; CYCLE_END=0; DECISIONS=0; APPLIED=0; PROPOSED=0
    PARSE_ERR=0; VAL_ERR=0; BATCH_ABORT=0; KILL_SWITCH=0
    NO_ACTION=0; DISABLE_SOURCE=0; DISABLE_KEYWORD=0; TUNE_THRESHOLD=0
    FAST_CYCLES=0; DEEP_CYCLES=0
else
    ALL_RECS="$(cat "${GOV_FILES[@]}" 2>/dev/null)"

    CYCLE_START=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_CYCLE_START"' || true)
    CYCLE_END=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_CYCLE_END"' || true)
    DECISIONS=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_DECISION"' || true)
    APPLIED=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"applied": true/' | wc -l | awk '{print $1}')
    PROPOSED=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"applied": false/' | wc -l | awk '{print $1}')

    PARSE_ERR=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_DECISION_PARSE_ERROR"' || true)
    VAL_ERR=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_DECISION_VALIDATION_ERROR"' || true)
    BATCH_ABORT=$(printf '%s\n' "$ALL_RECS" | grep -c '"batch_aborted": true' || true)
    KILL_SWITCH=$(printf '%s\n' "$ALL_RECS" | grep -c '"KILL_SWITCH"' || true)

    NO_ACTION=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"action": "no_action"/' | wc -l | awk '{print $1}')
    DISABLE_SOURCE=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"action": "disable_source"/' | wc -l | awk '{print $1}')
    DISABLE_KEYWORD=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"action": "disable_keyword"/' | wc -l | awk '{print $1}')
    TUNE_THRESHOLD=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"action": "tune_threshold"/' | wc -l | awk '{print $1}')

    FAST_CYCLES=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_CYCLE_START"/ && /"cadence": "fast"/' | wc -l | awk '{print $1}')
    DEEP_CYCLES=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_CYCLE_START"/ && /"cadence": "deep"/' | wc -l | awk '{print $1}')

    printf 'GOVERNANCE_CYCLE_START : %s\n' "$CYCLE_START" >>"$REPORT"
    printf '  fast cadence         : %s\n' "$FAST_CYCLES" >>"$REPORT"
    printf '  deep cadence         : %s\n' "$DEEP_CYCLES" >>"$REPORT"
    printf 'GOVERNANCE_CYCLE_END   : %s\n' "$CYCLE_END" >>"$REPORT"
    printf 'GOVERNANCE_DECISION    : %s\n' "$DECISIONS" >>"$REPORT"
    printf '  applied=true         : %s\n' "$APPLIED" >>"$REPORT"
    printf '  applied=false        : %s\n' "$PROPOSED" >>"$REPORT"
    printf 'Decisions by action:\n' >>"$REPORT"
    printf '  no_action           : %s\n' "$NO_ACTION" >>"$REPORT"
    printf '  disable_source      : %s\n' "$DISABLE_SOURCE" >>"$REPORT"
    printf '  disable_keyword     : %s\n' "$DISABLE_KEYWORD" >>"$REPORT"
    printf '  tune_threshold      : %s\n' "$TUNE_THRESHOLD" >>"$REPORT"
    printf '\n' >>"$REPORT"
    printf 'Errors:\n' >>"$REPORT"
    printf '  PARSE_ERROR         : %s\n' "$PARSE_ERR" >>"$REPORT"
    printf '  VALIDATION_ERROR    : %s\n' "$VAL_ERR" >>"$REPORT"
    printf '  batch_aborted=true  : %s\n' "$BATCH_ABORT" >>"$REPORT"
    printf '  KILL_SWITCH events  : %s\n' "$KILL_SWITCH" >>"$REPORT"
fi
codeblock_end

# ── 4. Shadow-mode invariant: applied_disabled_sources unchanged ─────────────
section "Shadow-mode invariant — runtime_overrides.yaml"
codeblock_start
if [[ -f "$OVERRIDES_YAML" ]]; then
    if [[ -x "$VENV_PYTHON" ]]; then
        (cd "$REPO_ROOT" && "$VENV_PYTHON" - <<'PY' 2>&1) >>"$REPORT" || true
import sys
from utils.runtime_overrides import RuntimeOverridesReader
r = RuntimeOverridesReader()
r.reload()
s = r.snapshot()
print(f"mode                       : {s.mode}")
print(f"applied_disabled_sources   : {len(s.applied_disabled_sources)} entries")
for e in s.applied_disabled_sources:
    print(f"  - {e}")
print(f"applied_disabled_keywords  : {len(s.applied_disabled_keywords)} entries")
print(f"applied_threshold_overrides: {len(s.applied_threshold_overrides)} entries")
print(f"proposed_disabled_sources  : {len(s.proposed_disabled_sources)} entries")
print(f"proposed_disabled_keywords : {len(s.proposed_disabled_keywords)} entries")
print(f"proposed_threshold_overrides: {len(s.proposed_threshold_overrides)} entries")
PY
    else
        printf '(venv python missing — falling back to grep)\n' >>"$REPORT"
        grep -E '^(mode|applied_|proposed_)' "$OVERRIDES_YAML" >>"$REPORT" 2>/dev/null || true
    fi
else
    printf '(runtime_overrides.yaml not found at %s)\n' "$OVERRIDES_YAML" >>"$REPORT"
fi
codeblock_end

# ── 5. launchd plist status ───────────────────────────────────────────────────
section "Governance launchd jobs"
codeblock_start
launchctl list 2>/dev/null | awk '$3 ~ /^com\.kalshi\.governance\./' >>"$REPORT" || \
    printf '(launchctl list returned no governance entries)\n' >>"$REPORT"
codeblock_end

# ── 6. Verdict + projection ───────────────────────────────────────────────────
section "Verdict"
{
    if [[ "${KILL_SWITCH:-0}" -gt 0 ]]; then
        printf 'Kill switch tripped during soak: **YES** (%s events) — investigate immediately.\n\n' "$KILL_SWITCH"
    fi
    if [[ "${APPLIED:-0}" -gt 0 ]]; then
        printf 'Shadow-mode invariant violated: **YES** (%s applied=true decisions) — soak invalid; reset clock.\n\n' "$APPLIED"
    else
        printf 'Shadow-mode invariant: **HOLDING** (applied=true count is 0).\n\n'
    fi

    # Projection: are we on track for ≥30 decisions by 2026-05-15?
    if [[ -n "${ELAPSED_SEC:-}" && "$ELAPSED_SEC" -gt 0 && "${DECISIONS:-0}" -gt 0 ]]; then
        DAYS_ELAPSED=$((ELAPSED_SEC / 86400))
        if (( DAYS_ELAPSED > 0 )); then
            DECISIONS_PER_DAY=$((DECISIONS / DAYS_ELAPSED))
            DAYS_REMAINING=$((REMAINING_SEC / 86400))
            PROJECTED_TOTAL=$((DECISIONS + DECISIONS_PER_DAY * DAYS_REMAINING))
            printf 'Decision rate: ~%s/day so far (%d days observed).\n' "$DECISIONS_PER_DAY" "$DAYS_ELAPSED"
            printf 'Projected total at soak close: ~%s decisions (acceptance floor: %d).\n\n' \
                "$PROJECTED_TOTAL" "$ACCEPTANCE_MIN_DECISIONS"
            if (( PROJECTED_TOTAL >= ACCEPTANCE_MIN_DECISIONS )); then
                printf 'On-track for decision-count acceptance.\n\n'
            else
                printf 'BELOW projected acceptance floor — investigate cadence / FakeLLM hit rate.\n\n'
            fi
        fi
    else
        printf '(insufficient data to project decision rate)\n\n'
    fi

    printf 'Next operator action: continue daily monitoring per docs/governance/PHASE2_RUNBOOK.md.\n'
    printf 'Next gate: 2026-05-15 ~14:00 UTC — soak close + manual reasonable-rate review (10/day x 14 days = 140 max sample).\n'
} >>"$REPORT"

# ── 7. Self-cleanup: bootout + remove the trigger plist ───────────────────────
if [[ -f "$MIDSOAK_PLIST" ]]; then
    launchctl bootout "gui/$(id -u)" "$MIDSOAK_PLIST" 2>/dev/null || \
        launchctl bootout "gui/$(id -u)/$MIDSOAK_LABEL" 2>/dev/null || true
    rm -f "$MIDSOAK_PLIST"
fi

# ── 8. Notify (system notification, best-effort) ──────────────────────────────
osascript -e "display notification \"Governance mid-soak report at $REPORT\" with title \"Kalshi governance\"" 2>/dev/null || true

echo "Report: $REPORT"
