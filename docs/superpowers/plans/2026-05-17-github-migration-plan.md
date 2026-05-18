# GitHub Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `kalshi-bot` from GitLab-first hosting to GitHub-first hosting without losing history, tags, CI coverage, or operational safety.

**Architecture:** Use a mirror-first cutover. Push the complete git graph to a private GitHub repository, recreate CI and protections there, verify GitHub is equivalent, then freeze or demote GitLab. Do not migrate live secrets or operational authority casually; GitHub becomes source of truth only after verification passes.

**Tech Stack:** Git, GitHub private repository, GitHub Actions, Python 3.14, ruff, pytest, existing `.gitlab-ci.yml`, existing `requirements-dev.txt`.

---

## Current Evidence

- Current branch at plan creation: `main`
- Execution branch: `chore/github-migration`
- Current remote: `origin = git@gitlab.com:HushUr2Pups8008/kalshi-bot.git`
- Current tree is dirty. Do not include unrelated working-tree changes in migration commits.
- Existing CI: `.gitlab-ci.yml`
- Existing CI jobs: `lint`, `tests`, `sims_smoke`, `p0_gate`
- Existing tags include: `v0.30.1`, `v0.30.0`, `pre-wave-1-deploy-2026-05-15`, `phase2-soak-closed`
- Existing remote branches include `main`, backup branches, docs branches, feature branches, and fix branches.

## Files

- Create: `.github/workflows/ci.yml`
- Create: `docs/github_migration_runbook.md`
- Modify: `README.md` only if it contains GitLab-specific badges or workflow instructions.
- Leave unchanged: `.gitlab-ci.yml` until GitHub Actions has passed on `main`.

## Task 1: Preflight Inventory

**Files:**
- Create: `docs/github_migration_runbook.md`

- [ ] **Step 1: Record remotes, branches, tags, and dirty tree**

Run:

```bash
git remote -v
git status --short
git branch -a
git tag --list
```

Expected:

```text
origin points to git@gitlab.com:HushUr2Pups8008/kalshi-bot.git
main is present
dirty tree may contain unrelated local work
tags are listed
```

- [ ] **Step 2: Create the migration runbook**

Create `docs/github_migration_runbook.md` with:

```markdown
# GitHub Migration Runbook

## Goal

Move `kalshi-bot` from GitLab-first hosting to GitHub-first hosting using a mirror-first cutover.

## Current GitLab Remote

`origin = git@gitlab.com:HushUr2Pups8008/kalshi-bot.git`

## GitHub Remote

To be created by operator:

`git@github.com:<owner>/kalshi-bot.git`

## Cutover Rule

GitHub is not source of truth until:

- all branches and tags are pushed
- GitHub Actions passes on `main`
- branch protection is configured
- required secrets are migrated deliberately, if a future workflow actually needs them
- GitLab is frozen or demoted intentionally

## Dirty Tree Rule

Do not stage unrelated working-tree changes for migration commits.

## CI Secret Rule

The initial GitHub Actions workflow is intentionally secretless. It runs lint,
offline tests, simulation smoke tests, and the P0 gate without live trading,
Kalshi, Anthropic, Ollama, or production database credentials.

Do not migrate live API or trading secrets as part of the CI cutover. Add
GitHub Actions secrets only after a separate workflow explicitly requires them
and has its own risk review.

## Python Version

GitHub Actions uses Python 3.14 to match the existing GitLab CI image
`python:3.14-slim`.
```

- [ ] **Step 3: Commit only the runbook**

Run:

```bash
git add docs/github_migration_runbook.md
git commit -m "docs: add GitHub migration runbook"
```

Expected: one docs-only commit.

## Task 2: Create GitHub Private Repository

**Files:**
- No repo file changes.

- [ ] **Step 1: Create an empty private GitHub repository**

Operator action:

```text
Create private GitHub repo:
owner: <owner>
name: kalshi-bot
initialize with README: no
initialize with .gitignore: no
initialize with license: no
```

Expected: GitHub shows an empty repository with SSH URL:

```text
git@github.com:<owner>/kalshi-bot.git
```

- [ ] **Step 2: Add GitHub as a second remote**

Run:

```bash
git remote add github git@github.com:<owner>/kalshi-bot.git
git remote -v
```

Expected:

```text
origin  git@gitlab.com:HushUr2Pups8008/kalshi-bot.git
github  git@github.com:<owner>/kalshi-bot.git
```

## Task 3: Push Full Git Graph to GitHub

**Files:**
- No repo file changes.

- [ ] **Step 1: Push all branches**

Run:

```bash
git push github --all
```

Expected: all local branches push to GitHub.

- [ ] **Step 2: Push all tags**

Run:

```bash
git push github --tags
```

Expected: all tags push to GitHub.

- [ ] **Step 3: Verify remote refs**

Run:

```bash
git ls-remote --heads github
git ls-remote --tags github
```

Expected: GitHub has `main`, active feature/fix/docs branches, backup branches, and existing tags.

## Task 4: Port GitLab CI to GitHub Actions

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches:
      - main
    tags:
      - "*"
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      # actions/checkout v4.2.2
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      # actions/setup-python v5.6.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
      - name: Install dependencies
        run: |
          python -m venv .venv
          .venv/bin/pip install --upgrade pip wheel
          .venv/bin/pip install -r requirements-dev.txt
      - name: Ruff
        run: .venv/bin/ruff check .
      - name: README version drift
        run: .venv/bin/python scripts/sync_readme_version.py --check

  tests:
    runs-on: ubuntu-latest
    steps:
      # actions/checkout v4.2.2
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      # actions/setup-python v5.6.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
      - name: Install OS dependencies
        run: sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends git nodejs make sqlite3
      - name: Install dependencies
        run: |
          python -m venv .venv
          .venv/bin/pip install --upgrade pip wheel
          .venv/bin/pip install -r requirements-dev.txt
      - name: Pytest
        run: .venv/bin/python -m pytest -q --tb=short --junitxml=junit.xml
      - name: Upload junit
        if: always()
        # actions/upload-artifact v4.6.2
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: junit
          path: junit.xml
          retention-days: 7

  sims-smoke:
    runs-on: ubuntu-latest
    steps:
      # actions/checkout v4.2.2
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      # actions/setup-python v5.6.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
      - name: Install dependencies
        run: |
          python -m venv .venv
          .venv/bin/pip install --upgrade pip wheel
          .venv/bin/pip install -r requirements-dev.txt
      - name: Simulation smoke tests
        run: .venv/bin/python -m pytest tests/test_simulations_smoke.py -q --tb=short

  p0-gate:
    runs-on: ubuntu-latest
    steps:
      # actions/checkout v4.2.2
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      # actions/setup-python v5.6.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
        with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
      - name: Install dependencies
        run: |
          python -m venv .venv
          .venv/bin/pip install --upgrade pip wheel
          .venv/bin/pip install -r requirements-dev.txt
      - name: P0 gate
        run: >
          .venv/bin/python -m pytest
          tests/test_kalshi_normalizer_p0.py
          tests/test_kalshi_pricing_p0.py
          tests/test_kalshi_pricing_p0_replay.py
          tests/test_drift_counter_halt_p0.py
          tests/test_kalshi_signing_failfast.py
          -q --tb=short
```

- [ ] **Step 2: Validate workflow syntax locally**

Run:

```bash
python - <<'PY'
from pathlib import Path
import yaml
path = Path(".github/workflows/ci.yml")
data = yaml.safe_load(path.read_text())
assert data["name"] == "CI"
assert "jobs" in data
assert {"lint", "tests", "sims-smoke", "p0-gate"} <= set(data["jobs"])
print("workflow_yaml_ok")
PY
```

Expected:

```text
workflow_yaml_ok
```

- [ ] **Step 3: Commit the GitHub Actions workflow**

Run:

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow"
```

Expected: one CI-only commit.

## Task 5: Push CI Migration Commit and Verify GitHub Actions

**Files:**
- No new file changes.

- [ ] **Step 1: Push `main` to GitHub**

Run:

```bash
git push github main
```

Expected: GitHub Actions starts for `main`.

- [ ] **Step 2: Verify GitHub checks**

Run:

```bash
gh run list --repo <owner>/kalshi-bot --limit 5
gh run watch --repo <owner>/kalshi-bot
```

Expected:

```text
lint: pass
tests: pass
sims-smoke: pass
p0-gate: pass
```

## Task 6: Recreate Repository Protections

**Files:**
- No repo file changes.

- [ ] **Step 1: Configure GitHub branch protection for `main`**

Operator action in GitHub UI:

```text
Settings -> Branches -> Add branch protection rule
Branch name pattern: main
Require pull request before merging: enabled
Require status checks before merging: enabled
Required checks:
- lint
- tests
- sims-smoke
- p0-gate
Require branches to be up to date before merging: enabled
Restrict force pushes: enabled
Restrict deletions: enabled
```

Expected: direct accidental pushes to protected `main` are blocked according to owner preference.

- [ ] **Step 2: Configure GitHub repository visibility and access**

Operator action:

```text
Repository visibility: private
Collaborator access: explicit
Actions permissions: allow selected actions or GitHub-created actions plus required verified actions
```

Expected: repo access matches or is stricter than GitLab access.

## Task 7: Secrets Posture and Integrations Review

**Files:**
- Modify: `docs/github_migration_runbook.md`

- [ ] **Step 1: Confirm GitHub CI remains secretless**

Inspect `.github/workflows/ci.yml`:

```bash
rg -n "secrets\\.|KALSHI|ANTHROPIC|OLLAMA|env:" .github/workflows/ci.yml
```

Expected: no required live trading/API secrets for the initial GitHub Actions workflow.

- [ ] **Step 2: Inventory GitLab variables without printing values**

Operator action in GitHub UI:

```text
GitLab -> Settings -> CI/CD -> Variables
Record variable names only if they are needed for non-CI integrations.
Do not export or paste secret values into chat.
```

Expected: no live API or trading secrets are migrated for the CI cutover.

- [ ] **Step 3: Update runbook secret posture**

Append to `docs/github_migration_runbook.md`:

```markdown
## GitHub Secrets Posture

The initial GitHub Actions workflow is secretless and requires no Kalshi,
Anthropic, Ollama, live trading, or production database credentials.

Do not migrate live secrets during CI cutover. Add GitHub Actions secrets only
after a separate workflow explicitly requires them and has its own risk review.
```

- [ ] **Step 4: Commit runbook update**

Run:

```bash
git add docs/github_migration_runbook.md
git commit -m "docs: record GitHub migration secret posture"
```

Expected: secretless CI posture committed.

## Task 8: Cutover

**Files:**
- Modify: `docs/github_migration_runbook.md`

- [ ] **Step 1: Make GitHub the default push remote**

Run:

```bash
git remote rename origin gitlab
git remote rename github origin
git remote -v
```

Expected:

```text
origin  git@github.com:<owner>/kalshi-bot.git
gitlab  git@gitlab.com:HushUr2Pups8008/kalshi-bot.git
```

- [ ] **Step 2: Push final migration docs to GitHub**

Run:

```bash
git push origin main
```

Expected: GitHub `main` is current.

- [ ] **Step 3: Freeze GitLab**

Operator action:

```text
GitLab repository description: moved to GitHub at git@github.com:<owner>/kalshi-bot.git
Disable GitLab pipelines if no longer needed.
Set GitLab repo read-only or restrict push access if available.
```

Expected: new work lands on GitHub, not GitLab.

## Task 9: Post-Cutover Verification

**Files:**
- Modify: `docs/github_migration_runbook.md`

- [ ] **Step 1: Verify fresh clone from GitHub**

Run outside the current working tree:

```bash
cd /tmp
git clone git@github.com:<owner>/kalshi-bot.git kalshi-bot-github-verify
cd kalshi-bot-github-verify
git status --short
git tag --list | tail -20
```

Expected:

```text
clean working tree
expected tags present
```

- [ ] **Step 2: Verify local smoke checks**

Run:

```bash
python -m venv .venv
.venv/bin/pip install --upgrade pip wheel
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/python scripts/sync_readme_version.py --check
.venv/bin/python -m pytest tests/test_simulations_smoke.py -q --tb=short
```

Expected:

```text
ruff passes
README version check passes
simulation smoke tests pass
```

- [ ] **Step 3: Record cutover complete**

Append to `docs/github_migration_runbook.md`:

```markdown
## Cutover Completion

- GitHub remote is source of truth.
- GitLab remote is retained as `gitlab` for archival access.
- GitHub Actions passed on `main`.
- Fresh GitHub clone verified.
```

- [ ] **Step 4: Commit completion note**

Run:

```bash
git add docs/github_migration_runbook.md
git commit -m "docs: record GitHub migration cutover"
git push origin main
```

Expected: migration completion documented on GitHub.

## Execution Notes

- Do not stage unrelated dirty-tree files during migration commits.
- Do not delete `.gitlab-ci.yml` until GitHub Actions has passed and GitLab is intentionally frozen.
- Do not migrate secret values through chat or committed files.
- Do not change launchd, paper-trading, production database, or bot runtime as part of this migration.
