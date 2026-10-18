#!/usr/bin/env bash
set -euo pipefail

unset ALERT_NTFY_TOPIC PUSHOVER_TOKEN PUSHOVER_USER || true
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BOTHEALTH="$REPO_ROOT/scripts/bothealth.sh"
TMP_ROOT="$(mktemp -d)"
PYTHON_BIN="$(command -v python3)"
LOCK_HOLDER_PIDS=()
LAST_LOCK_HOLDER_PID=""

cleanup() {
    local pid
    for pid in "${LOCK_HOLDER_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${LOCK_HOLDER_PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail "expected [$needle] in: $haystack"
}

assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    [[ "$haystack" != *"$needle"* ]] || fail "did not expect [$needle] in: $haystack"
}

make_fixture() {
    local root="$1"
    mkdir -p "$root/bin" "$root/data/runtime" "$root/logs/app" "$root/logs/governance" "$root/scripts/edge_replay"
    printf '0.0-test\n' >"$root/VERSION"
    cat >"$root/bin/launchctl" <<'EOF'
#!/usr/bin/env bash
pid=123
if [[ -n "${BOTHEALTH_REPO_ROOT:-}" && -r "$BOTHEALTH_REPO_ROOT/launchctl.pid" ]]; then
    IFS= read -r pid <"$BOTHEALTH_REPO_ROOT/launchctl.pid"
fi
printf '%s\t0\tcom.jake.kalshi-bot-politics\n' "$pid"
EOF
    cat >"$root/bin/osascript" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$BOTHEALTH_OSASCRIPT_CAPTURE"
EOF
    chmod +x "$root/bin/launchctl" "$root/bin/osascript"
}

make_db() {
    local db="$1"
    local sentinel="${2:-}"
    local row_ts="${3:-}"
    sqlite3 "$db" "CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT); CREATE TABLE paper_trades (ts TEXT, ticker TEXT, resolved INTEGER DEFAULT 0, pnl_dollars REAL DEFAULT 0, notional_bankroll_after REAL DEFAULT 0);"
    sqlite3 "$db" "INSERT INTO bot_state (key, value) VALUES ('notional_bankroll', '27.5'), ('paper_start_time', '2026-05-01T00:00:00+00:00');"
    if [[ -n "$sentinel" ]]; then
        sqlite3 "$db" "INSERT INTO bot_state (key, value) VALUES ('p0_price_fix_deployed_ts', '$sentinel');"
    fi
    if [[ -n "$row_ts" ]]; then
        sqlite3 "$db" "INSERT INTO paper_trades (ts, ticker, notional_bankroll_after) VALUES ('$row_ts', 'KXTEST', 27.5);"
    fi
}

mark_bot_down() {
    local root="$1"
    cat >"$root/bin/launchctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$root/bin/launchctl"
}

start_runtime_lock_holder() {
    local root="$1"
    local cohort_id="$2"
    local cohort_kind="$3"
    local lock_name="${4:-bot_runtime.lock}"
    local lock_path="$root/data/$lock_name"
    local ready_path="$root/runtime_lock_holder.ready"
    local attempts=0

    cat >"$root/bin/hold_runtime_lock.py" <<'PY'
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

lock_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
cohort_id = sys.argv[3]
cohort_kind = sys.argv[4]
started_utc = "2026-07-29T10:37:05+00:00"
owner_pid = os.getpid()
metadata = {
    "pid": owner_pid,
    "cwd": str(lock_path.parent.parent),
    "started_utc": started_utc,
    "argv": [str(lock_path.parent.parent / "main.py")],
    "runtime_paper_cohort": {
        "schema_version": 1,
        "cohort_id": cohort_id,
        "cohort_kind": cohort_kind,
        "owner_pid": owner_pid,
        "boot_started_utc": started_utc,
    },
}

with lock_path.open("w", encoding="utf-8") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    json.dump(metadata, handle, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
    ready_path.write_text(f"{owner_pid}\n", encoding="utf-8")
    signal.pause()
PY

    "$PYTHON_BIN" "$root/bin/hold_runtime_lock.py" \
        "$lock_path" "$ready_path" "$cohort_id" "$cohort_kind" \
        >"$root/runtime_lock_holder.out" 2>&1 &
    LAST_LOCK_HOLDER_PID="$!"
    LOCK_HOLDER_PIDS+=("$LAST_LOCK_HOLDER_PID")

    while (( attempts < 100 )); do
        if [[ -s "$ready_path" ]]; then
            local ready_pid
            ready_pid="$(<"$ready_path")"
            [[ "$ready_pid" == "$LAST_LOCK_HOLDER_PID" ]] || fail "runtime lock holder pid mismatch"
            printf '%s\n' "$ready_pid" >"$root/launchctl.pid"
            return 0
        fi
        if ! kill -0 "$LAST_LOCK_HOLDER_PID" 2>/dev/null; then
            cat "$root/runtime_lock_holder.out" >&2 || true
            fail "runtime lock holder exited before acquiring the lock"
        fi
        ((attempts += 1))
        sleep 0.02
    done
    fail "runtime lock holder did not become ready"
}

write_unlocked_runtime_lock() {
    local root="$1"
    local cohort_id="$2"
    local cohort_kind="$3"
    cat >"$root/data/bot_runtime.lock" <<EOF
{"pid":123,"cwd":"$root","started_utc":"2026-07-29T10:37:05+00:00","argv":["$root/main.py"],"runtime_paper_cohort":{"schema_version":1,"cohort_id":"$cohort_id","cohort_kind":"$cohort_kind","owner_pid":123,"boot_started_utc":"2026-07-29T10:37:05+00:00"}}
EOF
}

install_daily_review_stub() {
    local root="$1"
    cat >"$root/scripts/daily_review.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

daily_dir = Path(os.environ["KALSHI_OUTPUT_ROOT"]) / "reports" / "daily"
daily_dir.mkdir(parents=True, exist_ok=True)
(daily_dir / "daily_review_args.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
(daily_dir / "daily_review_marker.txt").write_text("ok\n", encoding="utf-8")
print("daily review body should stay out of bothealth")
PY
}

install_performance_analysis_stub() {
    local root="$1"
    cat >"$root/scripts/performance_analysis.py" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path

target = Path(os.environ["KALSHI_OUTPUT_ROOT"]) / "reports" / "performance" / "analysis_marker.txt"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("ok\n", encoding="utf-8")
print("performance body should stay out of bothealth")
print(f"Report saved to: {target}")
PY
}

assert_daily_review_args() {
    local args_path="$1"
    local cohort_id="$2"
    local cohort_kind="$3"
    local cohort_db="$4"
    local root_db="$5"
    "$PYTHON_BIN" - "$args_path" "$cohort_id" "$cohort_kind" "$cohort_db" "$root_db" <<'PY'
import json
import sys

args = json.loads(open(sys.argv[1], encoding="utf-8").read())
expected = [
    "--runtime-paper-cohort-id", sys.argv[2],
    "--runtime-paper-cohort-kind", sys.argv[3],
    "--paper-db", sys.argv[4],
]
if args != expected:
    raise SystemExit(f"unexpected daily review argv: {args!r}")
if sys.argv[4] != sys.argv[5] and sys.argv[5] in args:
    raise SystemExit(f"legacy root database was forwarded: {args!r}")
PY
}

run_bothealth() {
    local root="$1"
    shift || true
    local capture="$root/osascript.args"
    BOTHEALTH_REPO_ROOT="$root" \
    KALSHI_OUTPUT_ROOT="$root/logs" \
    BOTHEALTH_OSASCRIPT_CAPTURE="$capture" \
    PATH="$root/bin:$PATH" \
    bash "$BOTHEALTH" "$@"
}

fixture="$TMP_ROOT/green"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
toast="$(cat "$fixture/osascript.args")"
assert_contains "$toast" "kalshi_drift=ok"
assert_contains "$toast" "p0_cohort=2026-05-12T23:50:04+00:00 rows_since=1"

fixture="$TMP_ROOT/governance-parse-error-lifetime"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf '{"type":"GOVERNANCE_DECISION_PARSE_ERROR","cycle_id":"gc_2026-05-02_030140","error":"old parse error"}\n' >"$fixture/logs/governance/decisions.jsonl"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "PARSE_ERROR_24H (must=0): 0"
assert_contains "$report_body" "PARSE_ERROR_lifetime : 1"
assert_not_contains "$report_body" "PARSE_ERROR          : 1"

fixture="$TMP_ROOT/matcher-weights-unverified"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/analysis"
printf '' >"$fixture/analysis/__init__.py"
cat >"$fixture/analysis/match_feedback.py" <<'EOF'
def matcher_weights_status():
    return {
        "status": "unverified",
        "reason": "matcher weights staged: data/matcher_token_weights.json",
        "path": "data/matcher_token_weights.json",
        "count": 0,
    }
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — matcher weights unverified; matcher fail-closed"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "matcher_weights=unverified count=0 reason=matcher weights staged: data/matcher_token_weights.json"
toast="$(cat "$fixture/osascript.args")"
assert_contains "$toast" "matcher_weights=unverified"

fixture="$TMP_ROOT/daily-review-runtime-cohort-verified"
cohort_id="legacy-pending-20260729"
cohort_kind="legacy_pending"
cohort_db="$fixture/data/legacy_pending_paper_cohorts/$cohort_id/paper_trades.db"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$(dirname "$cohort_db")"
make_db "$cohort_db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "$cohort_id" "$cohort_kind"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **GREEN**"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "daily_review cohort_scope=verified id=$cohort_id kind=$cohort_kind db=$cohort_db"
assert_contains "$report_body" "daily_review exit_status=0"
assert_contains "$report_body" "performance_analysis=skipped reason=unscoped_db_and_log"
assert_not_contains "$report_body" "daily review body should stay out of bothealth"
assert_not_contains "$report_body" "performance body should stay out of bothealth"
[[ -f "$fixture/logs/reports/daily/daily_review_marker.txt" ]] || fail "daily review marker not written"
[[ ! -f "$fixture/logs/reports/performance/analysis_marker.txt" ]] || fail "performance analysis must not run for cohort-scoped daily review"
assert_daily_review_args \
    "$fixture/logs/reports/daily/daily_review_args.json" \
    "$cohort_id" "$cohort_kind" "$cohort_db" "$fixture/data/paper_trades.db"

fixture="$TMP_ROOT/daily-review-runtime-cohort-active"
cohort_id="active-20260729"
cohort_kind="active"
cohort_db="$fixture/data/paper_cohorts/$cohort_id/paper_trades.db"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
mkdir -p "$(dirname "$cohort_db")"
make_db "$cohort_db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "$cohort_id" "$cohort_kind"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **GREEN**"
assert_daily_review_args \
    "$fixture/logs/reports/daily/daily_review_args.json" \
    "$cohort_id" "$cohort_kind" "$cohort_db" "$fixture/data/paper_trades.db"
[[ ! -f "$fixture/logs/reports/performance/analysis_marker.txt" ]] || fail "performance analysis must not run for active cohort"

fixture="$TMP_ROOT/daily-review-runtime-cohort-legacy"
cohort_id="legacy"
cohort_kind="legacy"
cohort_db="$fixture/data/paper_trades.db"
make_fixture "$fixture"
make_db "$cohort_db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "$cohort_id" "$cohort_kind"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **GREEN**"
assert_daily_review_args \
    "$fixture/logs/reports/daily/daily_review_args.json" \
    "$cohort_id" "$cohort_kind" "$cohort_db" "$fixture/data/paper_trades.db"
[[ ! -f "$fixture/logs/reports/performance/analysis_marker.txt" ]] || fail "performance analysis must not run for legacy cohort"

fixture="$TMP_ROOT/daily-review-runtime-cohort-pid-mismatch"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "$cohort_id" "$cohort_kind"
printf '%s\n' "$((LAST_LOCK_HOLDER_PID + 1))" >"$fixture/launchctl.pid"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "daily_review cohort_scope=skipped reason="
assert_contains "$report_body" "performance_analysis=skipped reason=unscoped_db_and_log"
[[ ! -f "$fixture/logs/reports/daily/daily_review_marker.txt" ]] || fail "daily review must not run for mismatched runtime lock pid"
[[ ! -f "$fixture/logs/reports/performance/analysis_marker.txt" ]] || fail "performance analysis must not run for mismatched runtime lock pid"

fixture="$TMP_ROOT/daily-review-runtime-cohort-unlocked"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
write_unlocked_runtime_lock "$fixture" "$cohort_id" "$cohort_kind"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "daily_review cohort_scope=skipped reason="
assert_contains "$report_body" "performance_analysis=skipped reason=unscoped_db_and_log"
[[ ! -f "$fixture/logs/reports/daily/daily_review_marker.txt" ]] || fail "daily review must not run for an unlocked runtime lock"
[[ ! -f "$fixture/logs/reports/performance/analysis_marker.txt" ]] || fail "performance analysis must not run for an unlocked runtime lock"

fixture="$TMP_ROOT/daily-review-runtime-cohort-invalid-pair"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "legacy" "active"
install_daily_review_stub "$fixture"
install_performance_analysis_stub "$fixture"
out="$(run_bothealth "$fixture" --daily-review)"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit)"
report_body="$(cat "$report")"
assert_contains "$report_body" "daily_review cohort_scope=skipped reason=runtime_cohort_pair_invalid"
[[ ! -f "$fixture/logs/reports/daily/daily_review_marker.txt" ]] || fail "daily review must not run for invalid cohort pair"

fixture="$TMP_ROOT/missing-sentinel"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
start_runtime_lock_holder "$fixture" "legacy" "legacy"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — P0 sentinel missing"

fixture="$TMP_ROOT/missing-sentinel-readiness-not-ready"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
start_runtime_lock_holder "$fixture" "legacy" "legacy"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
import json
print(json.dumps({
    "readiness": "NOT_READY",
    "reason": "p0_price_fix_deployed_ts sentinel missing",
    "post_clean_start_row_count": 0,
    "post_clean_start_distinct_tickers": 0,
}))
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — P0 sentinel missing"

fixture="$TMP_ROOT/unavailable-runtime-cohort-global-readiness-not-ready"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
write_unlocked_runtime_lock "$fixture" "legacy-pending-20260729" "legacy_pending"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
import json
print(json.dumps({
    "readiness": "NOT_READY",
    "reason": "global legacy readiness should not characterize an unavailable cohort",
    "post_clean_start_row_count": 0,
    "post_clean_start_distinct_tickers": 0,
}))
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"

fixture="$TMP_ROOT/zero-after-6h"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00"
cat >"$fixture/data/bot_runtime.lock" <<'EOF'
{"pid":123,"cwd":"/tmp","started_utc":"2000-01-01T00:00:00+00:00","argv":[]}
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"

fixture="$TMP_ROOT/drift"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf '{"halt_ts":"2026-05-13T03:00:00Z"}\n' >"$fixture/data/runtime/kalshi_drift_halt.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — DRIFT HALT — kalshi contract drift; bot fail-closed"

fixture="$TMP_ROOT/readiness"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "legacy" "legacy"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
import json
print(json.dumps({
    "readiness": "NOT_READY",
    "reason": "post-clean-start paper_trades count 1 < min_trades 10",
    "post_clean_start_row_count": 1,
    "post_clean_start_distinct_tickers": 1,
}))
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — POST_FIX_NEW readiness NOT_READY"

fixture="$TMP_ROOT/readiness-invalid"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
start_runtime_lock_holder "$fixture" "legacy" "legacy"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
print("not-json")
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — POST_FIX_NEW readiness unavailable; review P0 section"

fixture="$TMP_ROOT/readiness-invalid-governance-red"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf '{"event":"GOVERNANCE_DECISION","applied": true}\n' >"$fixture/logs/governance/decisions.jsonl"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
print("not-json")
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — governance shadow-mode invariant violated"

fixture="$TMP_ROOT/readiness-invalid-bot-down"
make_fixture "$fixture"
mark_bot_down "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
cat >"$fixture/scripts/edge_replay/post_fix_new_readiness_status.py" <<'EOF'
#!/usr/bin/env python3
print("not-json")
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — bot not running"

fixture="$TMP_ROOT/politics-stale-log"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf 'old line\n' >"$fixture/logs/app/bot.log"
"$PYTHON_BIN" -c 'import os, sys, time; t=time.time()-7200; os.utime(sys.argv[1], (t, t))' "$fixture/logs/app/bot.log"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — no log lines for 30 min"
assert_contains "$out" "politics_log=stale"

fixture="$TMP_ROOT/politics-fresh-log"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf 'new line\n' >"$fixture/logs/app/bot.log"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
assert_contains "$out" "politics_log=fresh"
assert_not_contains "$out" "no log lines for 30 min"

fixture="$TMP_ROOT/politics-halt-up"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"halted": true, "line": 2.92, "loss": 10.2, "seed_equity": 29.2, "ts": "2026-10-15T00:00:00Z"}' >"$fixture/logs/state/politics_session_halt.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — session halt latched"
assert_contains "$out" "politics_halt=latched"
assert_contains "$out" "politics_alert_counts=yes"

fixture="$TMP_ROOT/politics-halt-down"
make_fixture "$fixture"
mark_bot_down "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"halted": true, "line": 2.92, "loss": 10.2, "seed_equity": 29.2, "ts": "2026-10-15T00:00:00Z"}' >"$fixture/logs/state/politics_session_halt.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — bot not running"
assert_contains "$out" "politics_halt=latched"

fixture="$TMP_ROOT/politics-handled-stop-up"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"reason": "handled_stop", "ts": "2026-10-15T00:00:00Z"}' >"$fixture/logs/state/politics_operator_alert.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
assert_contains "$out" "politics_operator_alert=handled_stop counts=no"

fixture="$TMP_ROOT/politics-restart-cap-down"
make_fixture "$fixture"
mark_bot_down "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"reason": "restart_cap", "ts": "2026-10-15T00:00:00Z"}' >"$fixture/logs/state/politics_operator_alert.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — bot not running"
assert_contains "$out" "politics_operator_alert=restart_cap counts=yes"

today="$(date -u +%Y-%m-%d)"
cash_epoch="$(TS="${today}T00:00:00+00:00" "$PYTHON_BIN" -c 'import datetime, os; print(int(datetime.datetime.fromisoformat(os.environ["TS"]).timestamp()))')"
fixture="$TMP_ROOT/politics-cash-drop"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":2419,"start_cents":2920,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
out="$(BOTHEALTH_NOW_EPOCH="$cash_epoch" run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — cash integer dropped more than \$5 today"
assert_contains "$out" "politics_cash=drop"

fixture="$TMP_ROOT/politics-cash-exact-5"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":2420,"start_cents":2920,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
out="$(BOTHEALTH_NOW_EPOCH="$cash_epoch" run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
assert_contains "$out" "politics_cash=ok"

fixture="$TMP_ROOT/politics-cash-stale"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":3995,"start_cents":3995,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
out="$(BOTHEALTH_NOW_EPOCH="$((cash_epoch + 7201))" run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — cash file older than 2 hours"
assert_contains "$out" "politics_cash=stale"

fixture="$TMP_ROOT/politics-cash-age-boundary"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":3995,"start_cents":3995,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
out="$(BOTHEALTH_NOW_EPOCH="$((cash_epoch + 7200))" run_bothealth "$fixture")"
assert_contains "$out" "politics_cash=ok"
assert_not_contains "$out" "cash file older than 2 hours"

fixture="$TMP_ROOT/politics-dry-run"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
out="$(run_bothealth "$fixture" --dry-run)"
assert_contains "$out" "Verdict: **YELLOW** — runtime cohort provenance unavailable; review section 1a"
assert_contains "$out" "politics_label=com.jake.kalshi-bot-politics"
assert_contains "$out" "dry-run: notification skipped"
assert_contains "$out" "alert_channel: osascript display notification titled kalshi-bot"
[[ ! -e "$fixture/osascript.args" ]] || fail "dry-run must not call osascript"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit || true)"
[[ -z "$report" ]] || fail "dry-run must not write the dated health report"
[[ ! -e "$fixture/logs/state/bothealth_alert_state.json" ]] || fail "dry-run must not move alert state"

install_curl_capture() {
    local root="$1"
    cat >"$root/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${BOTHEALTH_CURL_CAPTURE:?}"
exit 0
EOF
    chmod +x "$root/bin/curl"
}

fixture="$TMP_ROOT/alert-cash-stale"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":3995,"start_cents":3995,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH="$((cash_epoch + 7201))" \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "politics_cash=stale"
assert_contains "$(cat "$fixture/curl.args")" "cash_stale"
assert_contains "$(cat "$fixture/curl.args")" "https://ntfy.sh/desk-test"
assert_not_contains "$out" "desk-test"

fixture="$TMP_ROOT/alert-cash-drop"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '{"date":"%s","last_cents":3470,"prev_cents":3995,"start_cents":3995,"ts":"%sT00:00:00Z"}\n' "$today" "$today" >"$fixture/logs/state/politics_cash_cents.json"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH="$cash_epoch" \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "politics_cash=drop"
assert_contains "$(cat "$fixture/curl.args")" "cash_drop"
assert_not_contains "$out" "desk-test"

fixture="$TMP_ROOT/test-alert-unconfigured"
make_fixture "$fixture"
out="$(run_bothealth "$fixture" --test-alert)"
assert_contains "$out" "alert channel not configured"
[[ ! -e "$fixture/osascript.args" ]] || fail "test-alert must not use osascript"
report="$(find "$fixture/logs/reports/health" -name 'bothealth_*.md' -print -quit || true)"
[[ -z "$report" ]] || fail "test-alert must not write the health report"

fixture="$TMP_ROOT/test-alert-pushover"
make_fixture "$fixture"
install_curl_capture "$fixture"
out="$(
    PUSHOVER_TOKEN=test-token \
    PUSHOVER_USER=test-user \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture" --test-alert
)"
assert_contains "$out" "alert_channel=pushover"
assert_not_contains "$out" "test-token"
assert_not_contains "$out" "test-user"
assert_contains "$(cat "$fixture/curl.args")" "https://api.pushover.net/1/messages.json"

fixture="$TMP_ROOT/alert-process-down"
make_fixture "$fixture"
mark_bot_down "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "Verdict: **RED** — bot not running"
assert_contains "$out" "alert_channel=ntfy"
assert_not_contains "$out" "desk-test"
assert_contains "$(cat "$fixture/curl.args")" "bothealth alert: red,process_down"
assert_contains "$(cat "$fixture/curl.args")" "https://ntfy.sh/desk-test"

fixture="$TMP_ROOT/alert-halt"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"halted": true, "line": 5.0, "loss": 6.0, "seed_equity": 50.0, "ts": "2026-10-17T00:00:00Z"}' >"$fixture/logs/state/politics_session_halt.json"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "Verdict: **RED** — session halt latched"
assert_contains "$(cat "$fixture/curl.args")" "bothealth alert: red,halt"

fixture="$TMP_ROOT/alert-yellow-under-30"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"fill_lines":0,"yellow_alerted":false,"yellow_since":1000}' >"$fixture/logs/state/bothealth_alert_state.json"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH=1000 \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "Verdict: **YELLOW**"
[[ ! -e "$fixture/curl.args" ]] || fail "yellow under 30 min must not send"
assert_not_contains "$out" "alert_channel=ntfy"

fixture="$TMP_ROOT/alert-yellow-over-30"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"fill_lines":0,"yellow_alerted":false,"yellow_since":1000}' >"$fixture/logs/state/bothealth_alert_state.json"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH=2801 \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "Verdict: **YELLOW**"
assert_contains "$(cat "$fixture/curl.args")" "yellow_30m"
grep -q '"yellow_alerted": true' "$fixture/logs/state/bothealth_alert_state.json" || fail "successful send did not mark yellow alerted"

fixture="$TMP_ROOT/alert-new-fill"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state"
printf '%s\n' '{"fill_lines":0,"yellow_alerted":false,"yellow_since":1000}' >"$fixture/logs/state/bothealth_alert_state.json"
printf '%s\n' '2026-10-17T03:09:47Z, KXTRUMPSAY-26OCT19-ZACH, yes, 64, 8, fixture, 201' >"$fixture/logs/state/live-fill-log.md"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH=1000 \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "Verdict: **YELLOW**"
assert_contains "$(cat "$fixture/curl.args")" "new_fill"
assert_not_contains "$(cat "$fixture/curl.args")" "yellow_30m"
grep -q '"fill_lines": 1' "$fixture/logs/state/bothealth_alert_state.json" || fail "successful send did not advance fill watermark"

fixture="$TMP_ROOT/test-alert-dry-run"
make_fixture "$fixture"
install_curl_capture "$fixture"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture" --dry-run --test-alert
)"
assert_contains "$out" "dry-run: notification skipped"
[[ ! -e "$fixture/curl.args" ]] || fail "dry-run test-alert must not send"
[[ ! -e "$fixture/osascript.args" ]] || fail "dry-run test-alert must not use osascript"

fixture="$TMP_ROOT/alert-send-failed-keeps-watermark"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
mkdir -p "$fixture/logs/state" "$fixture/bin"
printf '%s\n' '{"fill_lines":0,"yellow_alerted":false,"yellow_since":1000}' >"$fixture/logs/state/bothealth_alert_state.json"
printf '%s\n' '2026-10-17T03:09:47Z, KXTRUMPSAY-26OCT19-ZACH, yes, 64, 8, fixture, 201' >"$fixture/logs/state/live-fill-log.md"
cat >"$fixture/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${BOTHEALTH_CURL_CAPTURE:?}"
exit 1
EOF
chmod +x "$fixture/bin/curl"
out="$(
    ALERT_NTFY_TOPIC=desk-test \
    BOTHEALTH_NOW_EPOCH=2801 \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    run_bothealth "$fixture"
)"
assert_contains "$out" "alert send failed provider=ntfy"
assert_contains "$(cat "$fixture/curl.args")" "new_fill"
assert_contains "$(cat "$fixture/curl.args")" "yellow_30m"
grep -q '"fill_lines": 0' "$fixture/logs/state/bothealth_alert_state.json" || fail "failed send advanced fill watermark"
grep -q '"yellow_alerted": false' "$fixture/logs/state/bothealth_alert_state.json" || fail "failed send marked yellow alerted"

fixture="$TMP_ROOT/politics-event-news-lock"
cohort_id="kalshi-event-news-20260820"
cohort_kind="active"
cohort_db="$fixture/data/paper_cohorts/$cohort_id/paper_trades.db"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
mkdir -p "$(dirname "$cohort_db")" "$fixture/scripts"
make_db "$cohort_db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf '%s\n' 'export BOT_RUNTIME_LOCK_NAME=bot_runtime.kalshi-event-news-20260820.lock' >"$fixture/scripts/start_politics_paper.sh"
printf '%s\n' '{"pid":3318,"cwd":"/old","started_utc":"2026-08-30T13:17:32+00:00","argv":["main.py"]}' >"$fixture/data/bot_runtime.lock"
start_runtime_lock_holder "$fixture" "$cohort_id" "$cohort_kind" "bot_runtime.kalshi-event-news-20260820.lock"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **GREEN**"
assert_not_contains "$out" "runtime_lock_unheld"

fixture="$TMP_ROOT/alert-env-file"
make_fixture "$fixture"
install_curl_capture "$fixture"
printf '%s\n' 'ALERT_NTFY_TOPIC="desk-test"' >"$fixture/alerts.env"
out="$(
    KALSHI_ALERTS_ENV="$fixture/alerts.env" \
    BOTHEALTH_CURL_CAPTURE="$fixture/curl.args" \
    PATH="$fixture/bin:$PATH" \
    /bin/bash "$REPO_ROOT/scripts/bothealth_launchd.sh" --test-alert
)"
assert_contains "$out" "alert_channel=ntfy"
assert_not_contains "$out" "desk-test"
assert_contains "$(cat "$fixture/curl.args")" "https://ntfy.sh/desk-test"

fixture="$TMP_ROOT/alert-env-missing"
make_fixture "$fixture"
out="$(
    KALSHI_ALERTS_ENV="$fixture/missing.env" \
    /bin/bash "$REPO_ROOT/scripts/bothealth_launchd.sh" --test-alert
)"
assert_contains "$out" "alert channel not configured"

echo "test_bothealth_verdict.sh: PASS"
