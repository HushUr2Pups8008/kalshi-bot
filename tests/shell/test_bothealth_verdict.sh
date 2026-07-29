#!/usr/bin/env bash
set -euo pipefail

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
printf '%s\t0\tcom.jake.kalshi-bot\n' "$pid"
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
    local lock_path="$root/data/bot_runtime.lock"
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

echo "test_bothealth_verdict.sh: PASS"
