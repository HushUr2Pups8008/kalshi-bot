# Pre-Wave-1 VERSION-bump dry-run

Date: 2026-05-05
Scratch worktree: `/private/tmp/kalshi-version-dryrun`
Hypothetical bump: `0.29.59 -> 0.30.0`

## Commands exercised

- Set scratch `VERSION` to `0.30.0`.
- Ran `scripts/sync_readme_version.py --check`.
- Ran `scripts/sync_readme_version.py --write`.
- Ran `scripts/sync_readme_version.py --check` again.
- Staged `VERSION` and ran `.githooks/pre-commit`.
- Checked staged files and staged diff.

## Results

| check | result |
| --- | --- |
| `--check` before sync | failed as expected: README drifted from `VERSION=0.30.0` |
| `--write` | rewrote README to `0.30.0` |
| `--check` after sync | passed |
| pre-commit hook | passed; re-staged README |
| staged files | `README.md`, `VERSION` |

Expected staged diff:

- README version badge: `0.29.59 -> 0.30.0`
- README release-history row: `Current through v0.29.59 -> Current through v0.30.0`
- `VERSION`: `0.29.59 -> 0.30.0`

## Readout

The version-sync mechanism works for the Wave-1 minor bump. CI's drift check should pass after the hook has run because `scripts/sync_readme_version.py --check` reports README in sync with `VERSION=0.30.0`.

Deploy-day note: the hook only syncs README/version references. It does not edit `CHANGELOG.md`; the operator still needs to paste or adapt `docs/governance/wave-1-changelog-entry-prestaged.md`.
