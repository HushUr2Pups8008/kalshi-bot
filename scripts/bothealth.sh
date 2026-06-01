#!/usr/bin/env bash
# Daily bot-health aggregator (Proposal C — PROFIT-EDGE-004 follow-up).
#
# One Markdown report consolidating the operator-facing observability that is
# currently scattered across:
#   - launchctl list / ps        (bot process state)
#   - main.py --report           (paper-trade summary)
#   - paper_trades.db            (bankroll trajectory + win rate)
#   - evidence_store.db          (dossier coverage + delta vs. yesterday)
#   - logs/governance/*.jsonl    (Phase 2 soak invariants)
#   - logs/app/bot.log + errors  (exception class scan)
#   - docs/profit_path_debt_log.md (recent debt-entry deltas)
#
# Designed to fire daily ~05:00 MDT via
# ~/Library/LaunchAgents/com.jake.kalshi-bothealth.plist (RunAtLoad=false,
# StartCalendarInterval). The launchd template passes --daily-review so the
# morning health, pipeline daily review, and performance analysis share one
# scheduled trigger. Unlike the soak-check / midsoak scripts this one does NOT
# self-clean the trigger plist — it is meant to keep firing forever until the
# operator removes it.
#
# Read-only. No DB writes. No trade-log writes. No process control.
# Safe to run at any time.

set -uo pipefail

POST_DEPLOY=0
RUN_DAILY_REVIEW=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --post-deploy)
            POST_DEPLOY=1
            shift
            ;;
        --daily-review)
            RUN_DAILY_REVIEW=1
            shift
            ;;
        -h|--help)
            echo "usage: bash scripts/bothealth.sh [--post-deploy] [--daily-review]"
            exit 0
            ;;
        *)
            echo "unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="${BOTHEALTH_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
OUTPUT_ROOT="${KALSHI_OUTPUT_ROOT:-${KALSHI_LOG_ROOT:-$REPO_ROOT/logs}}"
LOG_DIR="$OUTPUT_ROOT/app"
HEALTH_REPORT_DIR="$OUTPUT_ROOT/reports/health"
DAILY_REVIEW_SCRIPT="$REPO_ROOT/scripts/daily_review.py"
DAILY_REVIEW_DIR="$OUTPUT_ROOT/reports/daily"
PERFORMANCE_SCRIPT="$REPO_ROOT/scripts/performance_analysis.py"
PERFORMANCE_REPORT_DIR="$OUTPUT_ROOT/reports/performance"
TRADES_LOG="$OUTPUT_ROOT/trades/live/trades.jsonl"
ERRORS_LOG="$LOG_DIR/errors.log"
BOT_LOG="$LOG_DIR/bot.log"
PAPER_DB="$REPO_ROOT/data/paper_trades.db"
EVIDENCE_DB="$REPO_ROOT/data/evidence_store.db"
RUNTIME_DIR="$REPO_ROOT/data/runtime"
DRIFT_HALT_SENTINEL="$RUNTIME_DIR/kalshi_drift_halt.json"
BOT_RUNTIME_LOCK="$REPO_ROOT/data/bot_runtime.lock"
READINESS_SCRIPT="$REPO_ROOT/scripts/edge_replay/post_fix_new_readiness_status.py"
GOV_LOG_DIR="$OUTPUT_ROOT/governance"
DEBT_LOG="$REPO_ROOT/docs/profit_path_debt_log.md"
LAUNCHD_LABEL="com.jake.kalshi-bot"

DATE_ONLY="$(date -u +%Y-%m-%d)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$HEALTH_REPORT_DIR/bothealth_${DATE_ONLY}.md"

mkdir -p "$LOG_DIR" "$HEALTH_REPORT_DIR" "$DAILY_REVIEW_DIR" "$PERFORMANCE_REPORT_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
section()  { printf '\n## %s\n\n' "$1" >>"$REPORT"; }
sub()      { printf '\n### %s\n\n' "$1" >>"$REPORT"; }
codeblock_start() { printf '```\n' >>"$REPORT"; }
codeblock_end()   { printf '```\n' >>"$REPORT"; }
sql() { sqlite3 "$1" "$2" 2>/dev/null; }
sql_ro() { sqlite3 -readonly "$1" "$2" 2>/dev/null; }
python_bin() {
    local candidate
    if [[ -x "$VENV_PYTHON" ]]; then
        printf '%s\n' "$VENV_PYTHON"
        return 0
    fi
    for candidate in python3 python; do
        candidate="$(command -v "$candidate" 2>/dev/null || true)"
        if [[ -n "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}
file_mtime_utc() {
    local py
    py="$(python_bin)"
    if [[ -z "$py" ]]; then
        return 1
    fi
    "$py" -c 'from datetime import datetime, timezone; import os, sys
print(datetime.fromtimestamp(os.path.getmtime(sys.argv[1]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$1" 2>/dev/null
}
json_field() {
    local json="$1"
    local key="$2"
    local py
    py="$(python_bin)"
    if [[ -z "$py" ]]; then
        return 1
    fi
    JSON_INPUT="$json" JSON_KEY="$key" "$py" -c 'import json, os; d=json.loads(os.environ["JSON_INPUT"]); v=d.get(os.environ["JSON_KEY"]); print("" if v is None else v)' 2>/dev/null
}
runtime_started_utc() {
    local py
    py="$(python_bin)"
    if [[ ! -f "$BOT_RUNTIME_LOCK" || -z "$py" ]]; then
        return 1
    fi
    "$py" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("started_utc") or "")' "$BOT_RUNTIME_LOCK" 2>/dev/null
}
elapsed_hours_since() {
    local started="$1"
    local py
    py="$(python_bin)"
    if [[ -z "$started" || -z "$py" ]]; then
        return 1
    fi
    "$py" -c 'from datetime import datetime, timezone; import sys
raw=sys.argv[1].strip().replace("Z","+00:00")
dt=datetime.fromisoformat(raw)
if dt.tzinfo is None:
    dt=dt.replace(tzinfo=timezone.utc)
hours=(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600
print(int(hours) if hours > 0 else 0)' "$started" 2>/dev/null
}
osascript_escape() {
    local text="$1"
    text="${text//\\/\\\\}"
    text="${text//\"/\\\"}"
    printf '%s\n' "$text"
}

# ── Header ────────────────────────────────────────────────────────────────────
{
    printf '# kalshi-bot — daily health report\n\n'
    printf 'Generated: %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf 'Host: %s\n' "$(hostname -s)"
    printf 'Repo: %s @ %s (%s)\n' \
        "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')" \
        "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')" \
        "$(cat "$REPO_ROOT/VERSION" 2>/dev/null | head -1)"
} >"$REPORT"

# ── 1. Bot process state ──────────────────────────────────────────────────────
section "1. Bot process"
LAUNCHD_LINE="$(launchctl list 2>/dev/null | awk -v lbl="$LAUNCHD_LABEL" '$3 == lbl' || true)"
BOT_PID="$(printf '%s\n' "$LAUNCHD_LINE" | awk '{print $1}')"
codeblock_start
printf '%s\n' "${LAUNCHD_LINE:-(no launchctl entry for bot)}" >>"$REPORT"
if [[ -n "${BOT_PID:-}" && "$BOT_PID" != "-" && "$BOT_PID" != "0" ]]; then
    PS_LINE="$(ps -p "$BOT_PID" -o pid,etime,%cpu,%mem -ww 2>/dev/null | tail -n +2 || true)"
    printf 'ps -p %s: %s\n' "$BOT_PID" "${PS_LINE:-(process not found)}" >>"$REPORT"
    UPTIME="$(printf '%s' "$PS_LINE" | awk '{print $2}')"
    BOT_ALIVE=1
else
    UPTIME="(not running)"
    BOT_ALIVE=0
fi
codeblock_end

# ── 2. Bankroll trajectory ────────────────────────────────────────────────────
section "2. Notional bankroll"
codeblock_start
if [[ -f "$PAPER_DB" ]]; then
    NOTIONAL="$(sql "$PAPER_DB" "SELECT value FROM bot_state WHERE key='notional_bankroll';")"
    printf 'current notional_bankroll : $%s\n' "${NOTIONAL:-?}" >>"$REPORT"
    PAPER_START="$(sql "$PAPER_DB" "SELECT value FROM bot_state WHERE key='paper_start_time';")"
    printf 'paper_start_time          : %s\n' "${PAPER_START:-?}" >>"$REPORT"
    # 7-day delta via min/max of notional_bankroll_after across the window
    BR_7D="$(sql "$PAPER_DB" "SELECT printf('rows=%d min=%.2f max=%.2f last=%.2f', COUNT(*), MIN(notional_bankroll_after), MAX(notional_bankroll_after), (SELECT notional_bankroll_after FROM paper_trades ORDER BY ts DESC LIMIT 1)) FROM paper_trades WHERE ts >= datetime('now','-7 days');")"
    printf 'last 7d (paper_trades)    : %s\n' "${BR_7D:-(no rows)}" >>"$REPORT"
else
    printf '(paper_trades.db not found at %s)\n' "$PAPER_DB" >>"$REPORT"
fi
codeblock_end

# ── 3. Paper-trade summary ────────────────────────────────────────────────────
section "3. Paper-trade summary"
codeblock_start
if [[ -f "$PAPER_DB" ]]; then
    sql "$PAPER_DB" "
SELECT
  printf('lifetime: %d trades, %d resolved, %d wins, %d losses, pnl_total=%.2f, win_rate=%.1f%%',
    COUNT(*),
    SUM(CASE WHEN resolved=1 THEN 1 ELSE 0 END),
    SUM(CASE WHEN resolved=1 AND pnl_dollars > 0 THEN 1 ELSE 0 END),
    SUM(CASE WHEN resolved=1 AND pnl_dollars <= 0 THEN 1 ELSE 0 END),
    COALESCE(SUM(pnl_dollars), 0.0),
    100.0 * COALESCE(SUM(CASE WHEN resolved=1 AND pnl_dollars > 0 THEN 1 ELSE 0 END), 0)
                  / NULLIF(SUM(CASE WHEN resolved=1 THEN 1 ELSE 0 END), 0))
FROM paper_trades;
" >>"$REPORT"
    sql "$PAPER_DB" "
SELECT printf('last 24h: %d trades, %d resolved', COUNT(*), SUM(CASE WHEN resolved=1 THEN 1 ELSE 0 END))
FROM paper_trades WHERE ts >= datetime('now','-1 day');
" >>"$REPORT"
else
    printf '(paper_trades.db not found)\n' >>"$REPORT"
fi
codeblock_end

# ── 4. Evidence-store coverage ────────────────────────────────────────────────
section "4. Evidence store"
codeblock_start
if [[ -f "$EVIDENCE_DB" ]]; then
    sql "$EVIDENCE_DB" "
SELECT printf('dossiers=%d  evidence_records=%d  structural_priors=%d',
  (SELECT COUNT(*) FROM dossiers),
  (SELECT COUNT(*) FROM evidence),
  (SELECT COUNT(*) FROM structural_priors));
" >>"$REPORT"
    sql "$EVIDENCE_DB" "
SELECT printf('top markets by evidence (24h): %s',
  COALESCE(GROUP_CONCAT(market_ticker || '=' || n, ', '),
           '(none)'))
FROM (
  SELECT market_ticker, COUNT(*) AS n
  FROM evidence
  WHERE ingested_ts >= datetime('now','-1 day')
  GROUP BY market_ticker
  ORDER BY n DESC
  LIMIT 5
);
" >>"$REPORT"
else
    printf '(evidence_store.db not found)\n' >>"$REPORT"
fi
codeblock_end

# ── 5. Governance soak invariants ─────────────────────────────────────────────
section "5. Governance soak (PROFIT-PHASE2-001)"
codeblock_start
if [[ -d "$GOV_LOG_DIR" ]]; then
    GOV_FILES=()
    while IFS= read -r f; do
        GOV_FILES+=("$f")
    done < <(find "$GOV_LOG_DIR" -maxdepth 1 -type f -name 'decisions.jsonl*' 2>/dev/null | sort)
    if (( ${#GOV_FILES[@]} > 0 )); then
        ALL_RECS="$(cat "${GOV_FILES[@]}" 2>/dev/null)"
        CYCLE_END_TOTAL=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_CYCLE_END"' || true)
        DEC_TOTAL=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_DECISION"' || true)
        APPLIED=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_DECISION"/ && /"applied": true/' | wc -l | awk '{print $1}')
        KS=$(printf '%s\n' "$ALL_RECS" | grep -c '"KILL_SWITCH"' || true)
        VE=$(printf '%s\n' "$ALL_RECS" | grep -c '"VALIDATION_ERROR"' || true)
        BATCH_ABORTED=$(printf '%s\n' "$ALL_RECS" | awk '/"GOVERNANCE_CYCLE_END"/ && /"batch_aborted": true/' | wc -l | awk '{print $1}')
        PARSE_ERR=$(printf '%s\n' "$ALL_RECS" | grep -c '"GOVERNANCE_DECISION_PARSE_ERROR"' || true)
        printf 'cycles_completed     : %s\n' "$CYCLE_END_TOTAL" >>"$REPORT"
        printf 'decisions            : %s\n' "$DEC_TOTAL" >>"$REPORT"
        printf 'applied=true (must=0): %s\n' "$APPLIED" >>"$REPORT"
        printf 'KILL_SWITCH (must=0) : %s\n' "$KS" >>"$REPORT"
        printf 'VALIDATION_ERROR (must=0): %s\n' "$VE" >>"$REPORT"
        printf 'batch_aborted (must=0): %s\n' "$BATCH_ABORTED" >>"$REPORT"
        printf 'PARSE_ERROR          : %s\n' "$PARSE_ERR" >>"$REPORT"
    else
        printf '(no governance audit files yet)\n' >>"$REPORT"
        APPLIED=0; KS=0; VE=0; BATCH_ABORTED=0
    fi
else
    printf '(governance log dir does not exist)\n' >>"$REPORT"
    APPLIED=0; KS=0; VE=0; BATCH_ABORTED=0
fi
codeblock_end

# ── P0. Kalshi drift / cohort / readiness heartbeat ─────────────────────────
section "P0. Kalshi drift and cohort"
codeblock_start
KALSHI_DRIFT_STATUS="kalshi_drift=ok"
P0_COHORT_STATUS="p0_cohort=db_missing"
P0_SENTINEL_TS=""
P0_POST_SENTINEL_ROWS=""
P0_RUNTIME_HOURS=""
POST_FIX_NEW_STATUS="post_fix_new=not_checked"
POST_FIX_NEW_READINESS=""
POST_FIX_NEW_REASON=""
POST_FIX_NEW_CHECK_STATE="not_checked"

if [[ -f "$DRIFT_HALT_SENTINEL" ]]; then
    DRIFT_HALT_MTIME="$(file_mtime_utc "$DRIFT_HALT_SENTINEL" || true)"
    KALSHI_DRIFT_STATUS="kalshi_drift=HALT mtime=${DRIFT_HALT_MTIME:-unknown}"
    printf '%s path=%s\n' "$KALSHI_DRIFT_STATUS" "$DRIFT_HALT_SENTINEL" >>"$REPORT"
else
    printf '%s\n' "$KALSHI_DRIFT_STATUS" >>"$REPORT"
fi

if [[ -f "$PAPER_DB" ]]; then
    P0_SENTINEL_TS="$(sql_ro "$PAPER_DB" "SELECT value FROM bot_state WHERE key='p0_price_fix_deployed_ts';" || true)"
    if [[ -n "$P0_SENTINEL_TS" ]]; then
        P0_POST_SENTINEL_ROWS="$(sql_ro "$PAPER_DB" "SELECT COUNT(*) FROM paper_trades WHERE ts >= '$P0_SENTINEL_TS';" || true)"
        P0_COHORT_STATUS="p0_cohort=${P0_SENTINEL_TS} rows_since=${P0_POST_SENTINEL_ROWS:-unknown}"
    else
        P0_COHORT_STATUS="p0_cohort=P0 sentinel missing"
    fi
    printf '%s\n' "$P0_COHORT_STATUS" >>"$REPORT"
else
    printf '%s path=%s\n' "$P0_COHORT_STATUS" "$PAPER_DB" >>"$REPORT"
fi

RUNTIME_STARTED="$(runtime_started_utc || true)"
if [[ -n "$RUNTIME_STARTED" ]]; then
    P0_RUNTIME_HOURS="$(elapsed_hours_since "$RUNTIME_STARTED" || true)"
    printf 'bot_runtime.started_utc=%s elapsed_hours=%s\n' "$RUNTIME_STARTED" "${P0_RUNTIME_HOURS:-unknown}" >>"$REPORT"
else
    printf 'bot_runtime.started_utc=unknown\n' >>"$REPORT"
fi

if [[ -f "$READINESS_SCRIPT" && -f "$PAPER_DB" ]]; then
    READINESS_PY="$(python_bin)"
    if [[ -n "$READINESS_PY" ]]; then
        READINESS_JSON="$("$READINESS_PY" "$READINESS_SCRIPT" --db "$PAPER_DB" --json 2>/dev/null || true)"
    else
        READINESS_JSON=""
    fi
    if [[ -n "$READINESS_JSON" ]]; then
        POST_FIX_NEW_READINESS="$(json_field "$READINESS_JSON" readiness || true)"
        POST_FIX_NEW_REASON="$(json_field "$READINESS_JSON" reason || true)"
        POST_FIX_NEW_ROWS="$(json_field "$READINESS_JSON" post_clean_start_row_count || true)"
        POST_FIX_NEW_TICKERS="$(json_field "$READINESS_JSON" post_clean_start_distinct_tickers || true)"
        if [[ -n "$POST_FIX_NEW_READINESS" ]]; then
            POST_FIX_NEW_CHECK_STATE="ok"
        else
            POST_FIX_NEW_CHECK_STATE="unavailable"
        fi
        POST_FIX_NEW_STATUS="post_fix_new=${POST_FIX_NEW_READINESS:-unknown} rows=${POST_FIX_NEW_ROWS:-unknown} tickers=${POST_FIX_NEW_TICKERS:-unknown}"
        if [[ -n "$POST_FIX_NEW_REASON" ]]; then
            POST_FIX_NEW_STATUS="$POST_FIX_NEW_STATUS reason=${POST_FIX_NEW_REASON}"
        fi
    else
        POST_FIX_NEW_STATUS="post_fix_new=unavailable"
        POST_FIX_NEW_CHECK_STATE="unavailable"
    fi
else
    POST_FIX_NEW_STATUS="post_fix_new=not_checked"
fi
printf '%s\n' "$POST_FIX_NEW_STATUS" >>"$REPORT"
codeblock_end

# ── 6. Exception class scan (last 24h) ────────────────────────────────────────
section "6. Exception classes (last 24h)"
codeblock_start
if [[ -f "$BOT_LOG" || -f "$ERRORS_LOG" ]]; then
    SINCE_TS="$(date -u -v-1d +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '24 hours ago' +'%Y-%m-%dT%H:%M:%SZ')"
    EXC_LIST="$( { [[ -f "$BOT_LOG" ]] && grep -E "Traceback|^[A-Z][A-Za-z]*Error|^[A-Z][A-Za-z]*Exception" "$BOT_LOG" 2>/dev/null; \
                   [[ -f "$ERRORS_LOG" ]] && grep -E "Traceback|^[A-Z][A-Za-z]*Error|^[A-Z][A-Za-z]*Exception" "$ERRORS_LOG" 2>/dev/null; } \
                 | grep -oE "[A-Z][A-Za-z]*(Error|Exception)" | sort -u || true)"
    if [[ -z "$EXC_LIST" ]]; then
        printf '(no Python exception classes detected)\n' >>"$REPORT"
        EXC_COUNT=0
    else
        printf 'Distinct exception classes (whole log; investigate any new ones since yesterday):\n' >>"$REPORT"
        printf '%s\n' "$EXC_LIST" >>"$REPORT"
        EXC_COUNT="$(printf '%s\n' "$EXC_LIST" | wc -l | awk '{print $1}')"
    fi
else
    printf '(no logs to scan)\n' >>"$REPORT"
    EXC_COUNT=0
fi
codeblock_end

# ── 7. Open debt-log items (PROFIT-* statuses) ────────────────────────────────
section "7. Open profit-path debt items"
codeblock_start
if [[ -f "$DEBT_LOG" ]]; then
    awk '/^### PROFIT-/{id=$2}
         /\*\*Status\*\*.*OPEN|\*\*Status\*\*.*IN_PROGRESS/{print id "\t" $0}' \
         "$DEBT_LOG" | head -25 >>"$REPORT"
else
    printf '(debt log not found)\n' >>"$REPORT"
fi
codeblock_end

# ── 8. Wave-1 post-deploy smoke ──────────────────────────────────────────────
if [[ "$POST_DEPLOY" == "1" ]]; then
    section "8. Wave-1 post-deploy smoke"
    codeblock_start
    if [[ -x "$REPO_ROOT/scripts/wave1_post_deploy_smoke.sh" ]]; then
        bash "$REPO_ROOT/scripts/wave1_post_deploy_smoke.sh" >>"$REPORT" 2>&1
        SMOKE_STATUS=$?
        printf '\nwave1_post_deploy_smoke exit_status=%s\n' "$SMOKE_STATUS" >>"$REPORT"
    else
        printf 'wave1_post_deploy_smoke.sh missing or not executable at %s\n' "$REPO_ROOT/scripts/wave1_post_deploy_smoke.sh" >>"$REPORT"
        SMOKE_STATUS=127
    fi
    codeblock_end
fi

# ── 9. Opportunity-vs-trade drift ─────────────────────────────────────────────
# Wired per operator goal "fix kalshi-bot bugs to enable paper-trade
# evaluation" (Step 4): the watcher at scripts/observability/
# opportunity_trade_drift.py was authored 2026-05-13 (commit 72fdf4a)
# specifically to surface the silent-attrition condition where
# OPPORTUNITY events emit but paper_trades stays flat. It shipped without
# a delivery contract -- no scheduled surface invoked it. Wiring it here
# gives daily operator visibility into executor-boundary attrition; this
# watcher is downstream of the market-universe-shape failure mode the
# 2026-05-12 incident exposed, and is intentionally complementary to it.
# Section emits only; the verdict cascade is NOT extended in this commit
# so the existing GREEN/YELLOW/RED logic stays load-bearing-stable.
section "9. Opportunity-vs-trade drift"
codeblock_start
WATCHER_PY="$(python_bin)" || true
DRIFT_VERDICT="UNAVAILABLE"
if [[ -n "$WATCHER_PY" ]]; then
    WATCHER_SCRIPT="$REPO_ROOT/scripts/observability/opportunity_trade_drift.py"
    if [[ -f "$WATCHER_SCRIPT" ]]; then
        WATCHER_OUT="$("$WATCHER_PY" \
            "$WATCHER_SCRIPT" \
            --db "$PAPER_DB" \
            --trade-log-live "$TRADES_LOG" \
            --trade-log-archive-dir "$OUTPUT_ROOT/trades/archive" \
            --json 2>&1)"
        WATCHER_EXIT=$?
        printf '%s\n' "$WATCHER_OUT" >>"$REPORT"
        if (( WATCHER_EXIT == 0 )); then
            DRIFT_VERDICT="$(printf '%s' "$WATCHER_OUT" \
                | "$WATCHER_PY" -c 'import sys,json
try:
    print(json.load(sys.stdin).get("verdict","UNKNOWN"))
except Exception:
    print("UNKNOWN")' 2>/dev/null || echo "UNKNOWN")"
        else
            printf '\nwatcher exit_status=%s\n' "$WATCHER_EXIT" >>"$REPORT"
            DRIFT_VERDICT="WATCHER_ERROR"
        fi
    else
        printf 'watcher script missing at %s\n' "$WATCHER_SCRIPT" >>"$REPORT"
        DRIFT_VERDICT="UNAVAILABLE"
    fi
else
    printf 'python not available; skipping drift watcher\n' >>"$REPORT"
    DRIFT_VERDICT="UNAVAILABLE"
fi
printf '\ndrift_verdict=%s\n' "$DRIFT_VERDICT" >>"$REPORT"
codeblock_end

# ── 10. Daily review report ───────────────────────────────────────────────────
DAILY_REVIEW_STATUS="not_requested"
DAILY_REVIEW_REPORT=""
if [[ "$RUN_DAILY_REVIEW" == "1" ]]; then
    section "10. Daily review report"
    codeblock_start
    DAILY_REVIEW_PY="$(python_bin)" || true
    if [[ -z "$DAILY_REVIEW_PY" ]]; then
        printf 'python not available; skipping daily review\n' >>"$REPORT"
        DAILY_REVIEW_STATUS="python_unavailable"
    elif [[ ! -f "$DAILY_REVIEW_SCRIPT" ]]; then
        printf 'daily_review.py missing at %s\n' "$DAILY_REVIEW_SCRIPT" >>"$REPORT"
        DAILY_REVIEW_STATUS="script_missing"
    else
        DAILY_REVIEW_REPORT="$DAILY_REVIEW_DIR/daily_review_$(date +%Y%m%d).txt"
        DAILY_REVIEW_STDOUT="$(mktemp)"
        KALSHI_OUTPUT_ROOT="$OUTPUT_ROOT" "$DAILY_REVIEW_PY" "$DAILY_REVIEW_SCRIPT" >"$DAILY_REVIEW_STDOUT" 2>&1
        DAILY_REVIEW_EXIT=$?
        printf 'daily_review exit_status=%s\n' "$DAILY_REVIEW_EXIT" >>"$REPORT"
        printf 'daily_review report=%s\n' "$DAILY_REVIEW_REPORT" >>"$REPORT"
        if (( DAILY_REVIEW_EXIT == 0 )); then
            DAILY_REVIEW_STATUS="ok"
            if [[ -f "$DAILY_REVIEW_REPORT" ]]; then
                printf 'daily_review lines=%s bytes=%s\n' \
                    "$(wc -l <"$DAILY_REVIEW_REPORT" | tr -d ' ')" \
                    "$(wc -c <"$DAILY_REVIEW_REPORT" | tr -d ' ')" >>"$REPORT"
            fi
            printf 'daily_review body=standalone artifact only; not embedded in bothealth\n' >>"$REPORT"
        else
            DAILY_REVIEW_STATUS="failed"
            printf 'daily_review tail:\n' >>"$REPORT"
            tail -20 "$DAILY_REVIEW_STDOUT" >>"$REPORT"
        fi
        rm -f "$DAILY_REVIEW_STDOUT"
    fi
    codeblock_end
fi

# ── 11. Performance analysis report ───────────────────────────────────────────
PERFORMANCE_STATUS="not_requested"
PERFORMANCE_REPORT=""
if [[ "$RUN_DAILY_REVIEW" == "1" ]]; then
    section "11. Performance analysis report"
    codeblock_start
    PERFORMANCE_PY="$(python_bin)" || true
    if [[ -z "$PERFORMANCE_PY" ]]; then
        printf 'python not available; skipping performance analysis\n' >>"$REPORT"
        PERFORMANCE_STATUS="python_unavailable"
    elif [[ ! -f "$PERFORMANCE_SCRIPT" ]]; then
        printf 'performance_analysis.py missing at %s\n' "$PERFORMANCE_SCRIPT" >>"$REPORT"
        PERFORMANCE_STATUS="script_missing"
    else
        PERFORMANCE_STDOUT="$(mktemp)"
        (
            cd "$REPO_ROOT" || exit 1
            KALSHI_OUTPUT_ROOT="$OUTPUT_ROOT" "$PERFORMANCE_PY" -m scripts.performance_analysis
        ) >"$PERFORMANCE_STDOUT" 2>&1
        PERFORMANCE_EXIT=$?
        PERFORMANCE_REPORT="$(awk -F'Report saved to: ' '/Report saved to:/ {print $2}' "$PERFORMANCE_STDOUT" | tail -1)"
        printf 'performance_analysis exit_status=%s\n' "$PERFORMANCE_EXIT" >>"$REPORT"
        if [[ -n "$PERFORMANCE_REPORT" ]]; then
            printf 'performance_analysis report=%s\n' "$PERFORMANCE_REPORT" >>"$REPORT"
        else
            printf 'performance_analysis report=(not reported)\n' >>"$REPORT"
        fi
        if (( PERFORMANCE_EXIT == 0 )); then
            PERFORMANCE_STATUS="ok"
            if [[ -n "$PERFORMANCE_REPORT" && -f "$PERFORMANCE_REPORT" ]]; then
                printf 'performance_analysis lines=%s bytes=%s\n' \
                    "$(wc -l <"$PERFORMANCE_REPORT" | tr -d ' ')" \
                    "$(wc -c <"$PERFORMANCE_REPORT" | tr -d ' ')" >>"$REPORT"
            fi
            printf 'performance_analysis body=standalone artifact only; not embedded in bothealth\n' >>"$REPORT"
        else
            PERFORMANCE_STATUS="failed"
            printf 'performance_analysis tail:\n' >>"$REPORT"
            tail -20 "$PERFORMANCE_STDOUT" >>"$REPORT"
        fi
        rm -f "$PERFORMANCE_STDOUT"
    fi
    codeblock_end
fi

# ── 12. Verdict line ──────────────────────────────────────────────────────────
section "Verdict"
{
    if [[ "$KALSHI_DRIFT_STATUS" == kalshi_drift=HALT* ]]; then
        VERDICT="**RED** — DRIFT HALT — kalshi contract drift; bot fail-closed (${KALSHI_DRIFT_STATUS})"
    elif (( BOT_ALIVE == 0 )); then
        VERDICT="**RED** — bot not running"
    elif (( APPLIED > 0 || KS > 0 || VE > 0 || BATCH_ABORTED > 0 )); then
        VERDICT="**RED** — governance shadow-mode invariant violated (applied=$APPLIED, KILL_SWITCH=$KS, VALIDATION_ERROR=$VE, batch_aborted=$BATCH_ABORTED)"
    elif [[ -f "$PAPER_DB" && -z "$P0_SENTINEL_TS" ]]; then
        VERDICT="**YELLOW** — P0 sentinel missing"
    elif [[ "$POST_FIX_NEW_READINESS" == "NOT_READY" ]]; then
        VERDICT="**RED** — POST_FIX_NEW readiness NOT_READY (${POST_FIX_NEW_REASON:-review P0 section})"
    elif [[ "$POST_FIX_NEW_CHECK_STATE" == "unavailable" ]]; then
        VERDICT="**YELLOW** — POST_FIX_NEW readiness unavailable; review P0 section"
    elif [[ "${P0_POST_SENTINEL_ROWS:-}" == "0" && "${P0_RUNTIME_HOURS:-0}" =~ ^[0-9]+$ && "$P0_RUNTIME_HOURS" -ge 6 ]]; then
        VERDICT="**YELLOW** — No new paper_trades since clean-start (${P0_RUNTIME_HOURS}h elapsed)"
    elif [[ "$DAILY_REVIEW_STATUS" == "failed" || "$DAILY_REVIEW_STATUS" == "script_missing" || "$DAILY_REVIEW_STATUS" == "python_unavailable" ]]; then
        VERDICT="**YELLOW** — daily review ${DAILY_REVIEW_STATUS}; review section 10"
    elif (( EXC_COUNT > 5 )); then
        VERDICT="**YELLOW** — $EXC_COUNT distinct exception classes; review section 6"
    else
        VERDICT="**GREEN** — bot alive (uptime $UPTIME), governance shadow invariant holding"
    fi
    printf '%s\n' "$VERDICT"
} >>"$REPORT"

# Notify operator (best-effort, won't fail the script if osascript missing)
NOTIFY_BODY="bothealth: ${VERDICT//\*/}; ${KALSHI_DRIFT_STATUS}; ${P0_COHORT_STATUS}; ${POST_FIX_NEW_STATUS}; daily_review=${DAILY_REVIEW_STATUS}"
NOTIFY_BODY_ESCAPED="$(osascript_escape "$NOTIFY_BODY")"
osascript -e "display notification \"$NOTIFY_BODY_ESCAPED\" with title \"kalshi-bot\"" 2>/dev/null || true

echo "Report: $REPORT"
echo "Verdict: $VERDICT"
