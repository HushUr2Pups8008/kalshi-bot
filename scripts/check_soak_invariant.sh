#!/usr/bin/env bash
# §8.5.1 gate-7 audit: confirm no behavioural code change to the running bot
# during the active PROFIT-PHASE2-001 governance shadow-soak.
#
# Wraps the manual `git log -- <behaviour-paths>` check in the early-close
# runbook (docs/governance/PROFIT-PHASE2-001-early-close-criteria.md gate 7)
# so the operator can run a single command and get a pass/fail verdict.
#
# Usage:
#   bash scripts/check_soak_invariant.sh                  # default: since 2026-05-01T19:01Z
#   bash scripts/check_soak_invariant.sh --since <ISO>    # custom window start
#   bash scripts/check_soak_invariant.sh --verbose        # list any offending commits
#
# Exit codes:
#   0  invariant holds (zero behavioural commits in the window)
#   1  invariant violated (≥ 1 behavioural commit found)
#   2  invocation error
#
# What counts as "behavioural":
#   The set of paths that hold runtime code the bot executes. Doc-only,
#   test-only, script-only, and config commits to the soak-running services
#   are explicitly EXCLUDED — those are soak-safe by the established rules.

set -euo pipefail

SOAK_START="2026-05-01T19:01Z"
VERBOSE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SOAK_START="$2"; shift 2 ;;
    --verbose) VERBOSE=1; shift ;;
    -h|--help)
      head -22 "$0" | tail -20
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Behavioural paths: anything under these is bot-runtime code.
# Tests / scripts / docs are explicitly NOT here — those are soak-safe.
BEHAVIOURAL_PATHS=(
  "analysis/"
  "tasks/"
  "feeds/"
  "governance/"
  "executor/"
  "kalshi/"
  "main.py"
  "config.py"
)

echo "==> §8.5.1 gate-7 invariant audit"
echo "  soak start: ${SOAK_START}"
echo "  HEAD:       $(git rev-parse --short HEAD)"
echo "  paths:      ${BEHAVIOURAL_PATHS[*]}"
echo

# Capture commits touching any behavioural path since soak start.
COMMITS="$(git log --format='%H %h %s' --since "${SOAK_START}" -- "${BEHAVIOURAL_PATHS[@]}")"
COMMIT_COUNT="$(echo -n "$COMMITS" | grep -c "^" || true)"

if [[ "$COMMIT_COUNT" == "0" ]]; then
  echo "PASS — invariant holds. 0 behavioural commits since ${SOAK_START}."
  exit 0
fi

echo "FAIL — invariant violated. ${COMMIT_COUNT} behavioural commit(s) found:"
echo
echo "$COMMITS" | awk '{print "  " $2 "  " substr($0, index($0, $3))}'

if [[ "$VERBOSE" == "1" ]]; then
  echo
  echo "==> per-commit file lists:"
  while read -r FULL_HASH SHORT_HASH SUBJECT; do
    echo
    echo "  ${SHORT_HASH}  ${SUBJECT}"
    git diff-tree --no-commit-id --name-only -r "${FULL_HASH}" -- "${BEHAVIOURAL_PATHS[@]}" | sed 's/^/    /'
  done < <(echo "$COMMITS")
fi

echo
echo "Per the §8.5.1 addendum, the early-close path is INVALID with any"
echo "behavioural commit in the soak window. Either:"
echo "  (a) document why each commit was actually soak-safe (e.g., comments-only,"
echo "      backed out, or never reached the running bot), and re-evaluate."
echo "  (b) abandon the early-close path and fall through to the 14-day default."

exit 1
