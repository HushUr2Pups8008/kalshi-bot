#!/usr/bin/env bash
# Restore the MacBook → Mac Studio handoff state from
# transfer/macbook_handoff_2026-05-01/ into data/.
#
# Background:
#   - 2026-05-01 cutover: MacBook → Mac Studio. See:
#       README.md "Mac Studio operational handoff (2026-05-01)"
#       transfer/macbook_handoff_2026-05-01/{MANIFEST.md,PROVENANCE.md}
#       docs/profit_path_debt_log.md `PROFIT-CUTOVER-001`
#   - data/*.db is .gitignore'd; the SQL dumps are how state crosses
#     hosts via git clone.
#
# Behaviour:
#   - Refuses to overwrite existing data/*.db unless --force.
#   - Restores from transfer/macbook_handoff_2026-05-01/{paper_trades,evidence_store}.sql.
#   - Verifies post-restore row counts + key values against the manifest.
#   - Optionally extracts the bundled logs tarball to mac_archive/macbook_2026-05-01_import/
#     (the extraction destination is .gitignore'd; the tarball in transfer/ is canonical).
#   - Exits non-zero on any verification failure.
#
# Usage:
#   ./scripts/restore_macbook_handoff.sh                       # restore DBs only (safe default)
#   ./scripts/restore_macbook_handoff.sh --force               # overwrite existing DBs
#   ./scripts/restore_macbook_handoff.sh --extract-logs        # restore DBs + extract logs tarball
#   ./scripts/restore_macbook_handoff.sh --extract-logs-only   # extract logs tarball, skip DB restore
#   ./scripts/restore_macbook_handoff.sh --dry-run             # show what would happen
#
# Re-runnable: yes (idempotent under --force; refuses without --force when
# the destination dbs already exist). Compatible with macOS-default bash 3.2.

set -euo pipefail

# ---------- Resolve repo root (script may be called from anywhere) ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

# ---------- Paths ----------
HANDOFF_DIR="transfer/macbook_handoff_2026-05-01"
PAPER_SQL="${HANDOFF_DIR}/paper_trades.sql"
EVID_SQL="${HANDOFF_DIR}/evidence_store.sql"
LOGS_TARBALL="${HANDOFF_DIR}/logs_app_and_trades.tar.gz"
PAPER_DB="data/paper_trades.db"
EVID_DB="data/evidence_store.db"
LOCK_FILE="data/bot_runtime.lock"
LOGS_EXTRACT_DIR="mac_archive/macbook_2026-05-01_import"

# ---------- CLI flags ----------
FORCE=0
DRY_RUN=0
EXTRACT_LOGS=0
EXTRACT_ONLY=0
for arg in "$@"; do
  case "${arg}" in
    --force)              FORCE=1 ;;
    --dry-run)            DRY_RUN=1 ;;
    --extract-logs)       EXTRACT_LOGS=1 ;;
    --extract-logs-only)  EXTRACT_LOGS=1; EXTRACT_ONLY=1 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
      exit 0 ;;
    *)
      echo "ERROR: unknown argument: ${arg}" >&2
      echo "Usage: $0 [--force] [--dry-run] [--extract-logs] [--extract-logs-only]" >&2
      exit 2 ;;
  esac
done

# ---------- Pre-flight checks ----------
echo "==> kalshi-bot: restoring MacBook → Mac Studio handoff state"
echo "    repo root: ${REPO_ROOT}"
echo "    handoff dir: ${HANDOFF_DIR}"
echo

if [ ! -d "${HANDOFF_DIR}" ]; then
  echo "ERROR: handoff directory not found: ${HANDOFF_DIR}" >&2
  echo "       Did you forget to 'git pull'? Or is this an old clone?" >&2
  exit 3
fi

REQUIRED_FILES="${PAPER_SQL} ${EVID_SQL}"
if [ "${EXTRACT_LOGS}" = "1" ]; then
  REQUIRED_FILES="${REQUIRED_FILES} ${LOGS_TARBALL}"
fi
for f in ${REQUIRED_FILES}; do
  if [ ! -f "${f}" ]; then
    echo "ERROR: required handoff artifact missing: ${f}" >&2
    exit 3
  fi
done

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "ERROR: sqlite3 not on PATH; install via: brew install sqlite" >&2
  exit 4
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not on PATH; required for row-count verification." >&2
  exit 4
fi

mkdir -p data

# Detect existing dbs (informational always; blocking only when not --dry-run)
COLLISION=0
for db in "${PAPER_DB}" "${EVID_DB}"; do
  if [ -f "${db}" ]; then
    COLLISION=1
    echo "    found existing: ${db}"
  fi
done

# Stale-lock advisory (do not block; the bot's own self-heal handles this)
if [ -f "${LOCK_FILE}" ]; then
  echo "    NOTE: ${LOCK_FILE} exists. The bot self-heals on stale PIDs at"
  echo "          startup, but if this is a host migration you can rm it now:"
  echo "          rm ${LOCK_FILE}"
fi

if [ "${DRY_RUN}" = "1" ]; then
  echo
  echo "==> --dry-run: no changes made"
  if [ "${EXTRACT_ONLY}" != "1" ]; then
    echo "    Would have restored:"
    echo "      ${PAPER_SQL}  ->  ${PAPER_DB}"
    echo "      ${EVID_SQL}   ->  ${EVID_DB}"
    echo "    And verified against ${HANDOFF_DIR}/MANIFEST.md row counts."
    if [ "${COLLISION}" = "1" ]; then
      echo "    NOTE: existing data/*.db detected; a real run would require --force."
    fi
  fi
  if [ "${EXTRACT_LOGS}" = "1" ]; then
    echo "    Would have extracted:"
    echo "      ${LOGS_TARBALL}  ->  ${LOGS_EXTRACT_DIR}/"
    if [ -d "${LOGS_EXTRACT_DIR}" ]; then
      echo "    NOTE: ${LOGS_EXTRACT_DIR} already exists; tar would overwrite-on-newer."
    fi
  fi
  exit 0
fi

# Refuse to clobber existing dbs unless --force (only enforced for real runs that touch DBs)
if [ "${EXTRACT_ONLY}" != "1" ] && [ "${COLLISION}" = "1" ] && [ "${FORCE}" != "1" ]; then
  echo
  echo "ERROR: data/*.db already exists. Re-run with --force to overwrite." >&2
  echo "       (the existing files will be replaced, not merged)" >&2
  echo "       Or pass --extract-logs-only to extract just the logs tarball." >&2
  exit 5
fi

# ---------- Restore SQLite dbs (skipped if --extract-logs-only) ----------
if [ "${EXTRACT_ONLY}" != "1" ]; then
  echo
  echo "==> restoring paper_trades.db"
  rm -f "${PAPER_DB}" "${PAPER_DB}-shm" "${PAPER_DB}-wal" "${PAPER_DB}-journal"
  sqlite3 "${PAPER_DB}" < "${PAPER_SQL}"
  echo "    restored: ${PAPER_DB}"

  echo "==> restoring evidence_store.db"
  rm -f "${EVID_DB}" "${EVID_DB}-shm" "${EVID_DB}-wal" "${EVID_DB}-journal"
  sqlite3 "${EVID_DB}" < "${EVID_SQL}"
  echo "    restored: ${EVID_DB}"
fi

# ---------- Extract logs tarball (only if --extract-logs / --extract-logs-only) ----------
if [ "${EXTRACT_LOGS}" = "1" ]; then
  echo
  echo "==> extracting logs tarball"
  echo "    source:      ${LOGS_TARBALL}"
  echo "    destination: ${LOGS_EXTRACT_DIR}/"
  echo "    (this directory is .gitignore'd; the tarball in transfer/ is canonical)"
  mkdir -p "${LOGS_EXTRACT_DIR}"
  tar -xzf "${LOGS_TARBALL}" -C "${LOGS_EXTRACT_DIR}"
  echo "    extracted: $(find "${LOGS_EXTRACT_DIR}" -type f | wc -l | tr -d ' ') files"
  echo "    (logs/app/* + logs/trades/* — the 13-day MacBook paper-soak archive)"
fi

# ---------- Verify row counts + key values via Python ----------
# Python is more portable than bash assoc-arrays and the verification table
# is data, not logic — easy to keep in sync with MANIFEST.md.
# Skipped on --extract-logs-only since no DB restore happened.

VERIFY_RC=0
if [ "${EXTRACT_ONLY}" != "1" ]; then
echo
echo "==> verifying row counts and key values against MANIFEST.md"

python3 <<'PYEOF'
import sqlite3, sys

EXPECTED_PAPER = {
    "bot_state": 2,
    "keyword_outcomes": 0,
    "paper_trades": 3,
    "source_credibility": 1,
    "source_stats": 405,
    "sqlite_sequence": 0,
    "subreddit_candidates": 2508,
}
EXPECTED_EVID = {
    "dossiers": 32,
    "dossier_updates": 248,
    "dossier_update_evidence": 7510,
    "evidence": 248,
    "structural_priors": 32,
}
EXPECTED_VALUES = [
    # (db, query, expected, label)
    ("data/paper_trades.db",
     "SELECT value FROM bot_state WHERE key='notional_bankroll'",
     "42.5",
     "bot_state.notional_bankroll"),
    ("data/paper_trades.db",
     "SELECT source||'|'||wins||'|'||losses||'|'||multiplier FROM source_credibility WHERE source='VitalLaw.com'",
     "VitalLaw.com|0|3|0.5",
     "source_credibility.VitalLaw.com"),
    ("data/paper_trades.db",
     "SELECT value FROM bot_state WHERE key='paper_start_time'",
     "2026-04-18T02:11:24.442824+00:00",
     "bot_state.paper_start_time"),
]

failed = 0

def check_table(db_path, table, expected):
    global failed
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
        actual = cur.fetchone()[0]
        con.close()
    except Exception as e:
        print(f"    [FAIL] {db_path}.{table}  query failed: {e}", file=sys.stderr)
        failed += 1
        return
    if actual == expected:
        print(f"    [OK]  {db_path}.{table:<28} {actual} rows")
    else:
        print(f"    [FAIL] {db_path}.{table:<28} expected {expected}, got {actual}", file=sys.stderr)
        failed += 1

for tbl, n in EXPECTED_PAPER.items():
    check_table("data/paper_trades.db", tbl, n)
for tbl, n in EXPECTED_EVID.items():
    check_table("data/evidence_store.db", tbl, n)

print()
print("==> verifying key migrated values")
for db_path, query, expected, label in EXPECTED_VALUES:
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(query)
        row = cur.fetchone()
        actual = row[0] if row else "MISSING"
        con.close()
    except Exception as e:
        print(f"    [FAIL] {label}  query failed: {e}", file=sys.stderr)
        failed += 1
        continue
    if str(actual) == expected:
        print(f"    [OK]  {label} = {actual}")
    else:
        print(f"    [FAIL] {label} expected '{expected}', got '{actual}'", file=sys.stderr)
        failed += 1

sys.exit(1 if failed else 0)
PYEOF

VERIFY_RC=$?
fi  # end EXTRACT_ONLY guard

# ---------- Final verdict ----------
echo
if [ "${VERIFY_RC}" != "0" ]; then
  echo "==> RESTORE FAILED: one or more verification checks did not match." >&2
  echo "    Do not start the bot in this state. Either:" >&2
  echo "      (a) re-run with --force after investigating the source dump, or" >&2
  echo "      (b) escalate to a manual diff against ${HANDOFF_DIR}/MANIFEST.md." >&2
  exit 6
fi

if [ "${EXTRACT_ONLY}" = "1" ]; then
  echo "==> EXTRACT COMPLETE"
  echo "    Logs tarball extracted to ${LOGS_EXTRACT_DIR}/"
  echo "    Browse: ls ${LOGS_EXTRACT_DIR}/logs/app/ ${LOGS_EXTRACT_DIR}/logs/trades/"
else
  echo "==> RESTORE COMPLETE"
  echo "    All row counts and key values match MANIFEST.md."
  if [ "${EXTRACT_LOGS}" = "1" ]; then
    echo "    Logs tarball extracted to ${LOGS_EXTRACT_DIR}/"
  fi
  echo "    You can now start the bot:"
  echo "      .venv/bin/python main.py"
  echo
  echo "    Quick sanity-check the migrated state:"
  echo "      .venv/bin/python -m main --report"
  echo "      .venv/bin/python -m main --credibility"
fi
