#!/usr/bin/env bash
# One-shot soak-check report generator.
#
# Captures the current state of the Kalshi bot v0.29.58 baseline soak and
# writes a timestamped Markdown report to logs/app/soak_check_<ts>.md.
#
# Designed to be triggered once by ~/Library/LaunchAgents/com.jake.kalshi-soak-check.plist;
# self-removes that plist after the report is written so it does not refire.
#
# Safe to run manually at any time (will not break or duplicate anything).

set -uo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$REPO_ROOT/logs/app"
TRADES_LOG="$REPO_ROOT/logs/trades/live/trades.jsonl"
ERRORS_LOG="$LOG_DIR/errors.log"
BOT_LOG="$LOG_DIR/bot.log"
LAUNCHD_LABEL="com.jake.kalshi-bot"
SOAK_LABEL="com.jake.kalshi-soak-check"
SOAK_PLIST="$HOME/Library/LaunchAgents/$SOAK_LABEL.plist"

TS="$(date -u +%Y%m%d_%H%M%SZ)"
REPORT="$LOG_DIR/soak_check_$TS.md"

mkdir -p "$LOG_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
section() { printf '\n## %s\n\n' "$1" >>"$REPORT"; }
codeblock_start() { printf '```\n' >>"$REPORT"; }
codeblock_end()   { printf '```\n' >>"$REPORT"; }

# ── Header ────────────────────────────────────────────────────────────────────
{
    printf '# Kalshi v0.29.58 baseline soak — check report\n\n'
    printf 'Generated: %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf 'Host: %s\n' "$(hostname -s)"
    printf 'Repo: %s\n' "$REPO_ROOT"
    printf 'Branch: %s @ %s\n' "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)" "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
} >"$REPORT"

# ── 1. Bot liveness ───────────────────────────────────────────────────────────
section "Bot liveness"
LAUNCHD_LINE="$(launchctl list 2>/dev/null | awk -v lbl="$LAUNCHD_LABEL" '$3 == lbl' || true)"
BOT_PID="$(printf '%s\n' "$LAUNCHD_LINE" | awk '{print $1}')"
EXIT_STATUS="$(printf '%s\n' "$LAUNCHD_LINE" | awk '{print $2}')"

codeblock_start
printf 'launchctl list:\n%s\n' "${LAUNCHD_LINE:-(no entry)}" >>"$REPORT"
if [[ -n "${BOT_PID:-}" && "$BOT_PID" != "-" && "$BOT_PID" != "0" ]]; then
    PS_LINE="$(ps -p "$BOT_PID" -o pid,etime,command -ww 2>/dev/null | tail -n +2 || true)"
    printf '\nps -p %s:\n%s\n' "$BOT_PID" "${PS_LINE:-(process not found)}" >>"$REPORT"
    UPTIME="$(printf '%s' "$PS_LINE" | awk '{print $2}')"
else
    UPTIME="(bot not running per launchctl)"
    printf '\n!! Bot does not appear to be running !!\n' >>"$REPORT"
fi
codeblock_end

# ── 2. --report output ────────────────────────────────────────────────────────
section 'Performance report (`main.py --report`)'
codeblock_start
if [[ -x "$VENV_PYTHON" ]]; then
    (cd "$REPO_ROOT" && "$VENV_PYTHON" main.py --report 2>&1) >>"$REPORT" || true
else
    printf 'venv python not found at %s\n' "$VENV_PYTHON" >>"$REPORT"
fi
codeblock_end

# ── 3. Notional bankroll ──────────────────────────────────────────────────────
section "Notional bankroll (DB)"
codeblock_start
if [[ -f "$REPO_ROOT/data/paper_trades.db" ]]; then
    BANKROLL_VAL="$(sqlite3 "$REPO_ROOT/data/paper_trades.db" "SELECT value FROM bot_state WHERE key='notional_bankroll';" 2>/dev/null || echo '(query failed)')"
    printf 'notional_bankroll = %s\n' "$BANKROLL_VAL" >>"$REPORT"
else
    printf '(paper_trades.db not found)\n' >>"$REPORT"
fi
codeblock_end

# ── 4. BLEND_DECISION count over the past 48h ─────────────────────────────────
section "BLEND_DECISION count (past 48h)"
codeblock_start
if [[ -f "$TRADES_LOG" ]]; then
    SINCE_TS="$(date -u -v-48H +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '48 hours ago' +'%Y-%m-%dT%H:%M:%SZ')"
    BLEND_COUNT="$(awk -F'"' -v since="$SINCE_TS" '
        /"type":"BLEND_DECISION"/ {
            ts=""
            for (i=1;i<=NF;i++) if ($i=="ts") { ts=$(i+2); break }
            if (ts >= since) c++
        }
        END { print c+0 }
    ' "$TRADES_LOG")"
    TOTAL_BLEND="$(grep -c '"type":"BLEND_DECISION"' "$TRADES_LOG" || true)"
    printf 'since=%s\nBLEND_DECISION (last 48h): %s\nBLEND_DECISION (lifetime in this file): %s\n' \
        "$SINCE_TS" "$BLEND_COUNT" "$TOTAL_BLEND" >>"$REPORT"
else
    printf '(trades.jsonl not found at %s)\n' "$TRADES_LOG" >>"$REPORT"
fi
codeblock_end

# ── 5. Errors log tail ────────────────────────────────────────────────────────
section "errors.log — last 100 lines"
codeblock_start
if [[ -f "$ERRORS_LOG" ]]; then
    tail -n 100 "$ERRORS_LOG" >>"$REPORT"
else
    printf '(errors.log not found)\n' >>"$REPORT"
fi
codeblock_end

# ── 6. New exception classes scan ─────────────────────────────────────────────
section "Unhandled exception class scan (bot.log + errors.log)"
codeblock_start
if [[ -f "$BOT_LOG" || -f "$ERRORS_LOG" ]]; then
    EXC_LIST="$( { [[ -f "$BOT_LOG" ]] && grep -E "Traceback|^[A-Z][A-Za-z]*Error|^[A-Z][A-Za-z]*Exception" "$BOT_LOG" 2>/dev/null; \
                   [[ -f "$ERRORS_LOG" ]] && grep -E "Traceback|^[A-Z][A-Za-z]*Error|^[A-Z][A-Za-z]*Exception" "$ERRORS_LOG" 2>/dev/null; } \
                 | grep -oE "[A-Z][A-Za-z]*(Error|Exception)" | sort -u )"
    if [[ -z "$EXC_LIST" ]]; then
        printf '(no Python exception classes detected)\n' >>"$REPORT"
    else
        printf 'Distinct exception classes seen during soak:\n%s\n' "$EXC_LIST" >>"$REPORT"
    fi
else
    printf '(no logs to scan)\n' >>"$REPORT"
fi
codeblock_end

# ── 7. Verdict ────────────────────────────────────────────────────────────────
section "Verdict"
{
    if [[ -n "${BOT_PID:-}" && "$BOT_PID" != "-" && "$BOT_PID" != "0" ]]; then
        printf 'Bot alive: **YES** (PID %s, uptime %s)\n\n' "$BOT_PID" "${UPTIME:-?}"
    else
        printf 'Bot alive: **NO** — investigate immediately.\n\n'
    fi
    printf 'Manual review required: scan exception classes section above for any '
    printf 'class that was NOT present in the pre-restart baseline. New classes = '
    printf 'investigate before continuing the soak.\n'
} >>"$REPORT"

# ── 8. Self-cleanup: bootout + remove the trigger plist ───────────────────────
if [[ -f "$SOAK_PLIST" ]]; then
    launchctl bootout "gui/$(id -u)" "$SOAK_PLIST" 2>/dev/null || \
        launchctl bootout "gui/$(id -u)/$SOAK_LABEL" 2>/dev/null || true
    rm -f "$SOAK_PLIST"
fi

# ── 9. Notify (system notification, best-effort) ──────────────────────────────
osascript -e "display notification \"Soak check report written to $REPORT\" with title \"Kalshi bot\"" 2>/dev/null || true

echo "Report: $REPORT"
