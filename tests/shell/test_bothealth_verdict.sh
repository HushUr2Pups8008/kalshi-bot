#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BOTHEALTH="$REPO_ROOT/scripts/bothealth.sh"
TMP_ROOT="$(mktemp -d)"

cleanup() {
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

make_fixture() {
    local root="$1"
    mkdir -p "$root/bin" "$root/data/runtime" "$root/logs/app" "$root/logs/governance" "$root/scripts/edge_replay"
    printf '0.0-test\n' >"$root/VERSION"
    cat >"$root/bin/launchctl" <<'EOF'
#!/usr/bin/env bash
printf '123\t0\tcom.jake.kalshi-bot\n'
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

run_bothealth() {
    local root="$1"
    local capture="$root/osascript.args"
    BOTHEALTH_REPO_ROOT="$root" \
    KALSHI_OUTPUT_ROOT="$root/logs" \
    BOTHEALTH_OSASCRIPT_CAPTURE="$capture" \
    PATH="$root/bin:$PATH" \
    bash "$BOTHEALTH"
}

fixture="$TMP_ROOT/green"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **GREEN**"
toast="$(cat "$fixture/osascript.args")"
assert_contains "$toast" "kalshi_drift=ok"
assert_contains "$toast" "p0_cohort=2026-05-12T23:50:04+00:00 rows_since=1"

fixture="$TMP_ROOT/missing-sentinel"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — P0 sentinel missing"

fixture="$TMP_ROOT/missing-sentinel-readiness-not-ready"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db"
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

fixture="$TMP_ROOT/zero-after-6h"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00"
cat >"$fixture/data/bot_runtime.lock" <<'EOF'
{"pid":123,"cwd":"/tmp","started_utc":"2000-01-01T00:00:00+00:00","argv":[]}
EOF
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **YELLOW** — No new paper_trades since clean-start"

fixture="$TMP_ROOT/drift"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
printf '{"halt_ts":"2026-05-13T03:00:00Z"}\n' >"$fixture/data/runtime/kalshi_drift_halt.json"
out="$(run_bothealth "$fixture")"
assert_contains "$out" "Verdict: **RED** — DRIFT HALT — kalshi contract drift; bot fail-closed"

fixture="$TMP_ROOT/readiness"
make_fixture "$fixture"
make_db "$fixture/data/paper_trades.db" "2026-05-12T23:50:04+00:00" "2026-05-13T01:00:00+00:00"
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
