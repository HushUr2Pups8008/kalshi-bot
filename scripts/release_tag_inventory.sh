#!/usr/bin/env bash
# Print local and origin release tags with SHAs and per-tag commit subject.

set -euo pipefail

JSON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1; shift ;;
    -h|--help)
      echo "usage: bash scripts/release_tag_inventory.sh [--json]"
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

local_rows() {
  git for-each-ref refs/tags \
    --sort=-creatordate \
    --format='local|%(refname:short)|%(objectname)|%(subject)' |
    while IFS='|' read -r scope tag sha subject; do
      commit_sha="$(git rev-list -n 1 "$tag" 2>/dev/null || echo "$sha")"
      commit_subject="$(git log -1 --format=%s "$commit_sha" 2>/dev/null || echo "$subject")"
      printf '%s|%s|%s|%s\n' "$scope" "$tag" "$commit_sha" "$commit_subject"
    done
}

origin_rows() {
  if ! git ls-remote --tags origin >/tmp/kalshi_release_tag_inventory_origin.$$ 2>/dev/null; then
    rm -f /tmp/kalshi_release_tag_inventory_origin.$$
    return 0
  fi
  awk '!/\\^\\{\\}$/ {print $1 "|" $2}' /tmp/kalshi_release_tag_inventory_origin.$$ |
    sed 's#refs/tags/##' |
    while IFS='|' read -r sha tag; do
      subject="$(git log -1 --format=%s "$sha" 2>/dev/null || echo "origin tag not present locally")"
      printf 'origin|%s|%s|%s\n' "$tag" "$sha" "$subject"
    done
  rm -f /tmp/kalshi_release_tag_inventory_origin.$$
}

if [[ "$JSON" == "1" ]]; then
  printf '{"tags":['
  first=1
  while IFS='|' read -r scope tag sha subject; do
    [[ -n "${tag:-}" ]] || continue
    [[ "$first" == "1" ]] || printf ','
    first=0
    python3 -c 'import json,sys; print(json.dumps({"scope":sys.argv[1],"tag":sys.argv[2],"sha":sys.argv[3],"commit_subject":sys.argv[4]},separators=(",",":")), end="")' "$scope" "$tag" "$sha" "$subject"
  done < <( { local_rows; origin_rows; } )
  printf ']}\n'
else
  echo "# Release Tag Inventory"
  echo
  echo "| scope | tag | sha | commit subject |"
  echo "|---|---|---|---|"
  while IFS='|' read -r scope tag sha subject; do
    [[ -n "${tag:-}" ]] || continue
    echo "| $scope | \`$tag\` | \`${sha:0:12}\` | $subject |"
  done < <( { local_rows; origin_rows; } )
fi
