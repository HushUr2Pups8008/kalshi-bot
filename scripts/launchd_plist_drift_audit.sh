#!/usr/bin/env bash
# Compare installed launchd plists against repo-managed templates.

set -euo pipefail

JSON=0
INSTALL_DIR="$HOME/Library/LaunchAgents"
GOVERNANCE_LLM_MODEL="${GOVERNANCE_LLM_MODEL:-qwen2.5:7b}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "usage: bash scripts/launchd_plist_drift_audit.sh [--json] [--install-dir PATH]"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

render_template() {
  local template="$1"
  local dest="$2"
  local repo_esc venv_esc model_esc
  repo_esc=$(printf '%s' "$REPO_ROOT" | sed 's/[\&/]/\\&/g')
  venv_esc=$(printf '%s' "$VENV_PYTHON" | sed 's/[\&/]/\\&/g')
  model_esc=$(printf '%s' "$GOVERNANCE_LLM_MODEL" | sed 's/[\&/]/\\&/g')
  sed \
    -e "s/@REPO_ROOT@/$repo_esc/g" \
    -e "s/@VENV_PYTHON@/$venv_esc/g" \
    -e "s/@GOVERNANCE_LLM_MODEL@/$model_esc/g" \
    "$template" > "$dest"
}

status_for() {
  local expected="$1"
  local installed="$2"
  if [[ ! -f "$installed" ]]; then
    echo "missing"
  elif cmp -s "$expected" "$installed"; then
    echo "match"
  else
    echo "drift"
  fi
}

ENTRIES=()

add_entry() {
  local label="$1"
  local expected="$2"
  local installed="$INSTALL_DIR/$label.plist"
  local status
  status="$(status_for "$expected" "$installed")"
  ENTRIES+=("$label|$status|$expected|$installed")
}

for template in "$REPO_ROOT"/ops/launchd/*.plist.template; do
  [[ -f "$template" ]] || continue
  label="$(basename "$template" .plist.template)"
  expected="$TMP_DIR/$label.plist"
  render_template "$template" "$expected"
  add_entry "$label" "$expected"
done

STATUS="pass"
for entry in "${ENTRIES[@]}"; do
  IFS='|' read -r _label entry_status _expected _installed <<< "$entry"
  if [[ "$entry_status" != "match" ]]; then
    STATUS="fail"
  fi
done

if [[ "$JSON" == "1" ]]; then
  printf '{"status":"%s","install_dir":"%s","checks":[' "$STATUS" "$(json_escape "$INSTALL_DIR")"
  first=1
  for entry in "${ENTRIES[@]}"; do
    IFS='|' read -r label entry_status _expected installed <<< "$entry"
    if [[ "$first" == "0" ]]; then
      printf ','
    fi
    first=0
    printf '{"label":"%s","status":"%s","installed":"%s"}' \
      "$(json_escape "$label")" "$entry_status" "$(json_escape "$installed")"
  done
  printf ']}\n'
else
  echo "# launchd plist drift audit"
  echo
  echo "Status: $STATUS"
  echo
  for entry in "${ENTRIES[@]}"; do
    IFS='|' read -r label entry_status _expected installed <<< "$entry"
    echo "- $label: $entry_status ($installed)"
  done
fi

[[ "$STATUS" == "pass" ]]
