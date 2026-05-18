# GitHub Migration Runbook

## Goal

Move `kalshi-bot` from GitLab-first hosting to GitHub-first hosting using a mirror-first cutover.

## Preflight Snapshot

- Current remote: `origin = git@gitlab.com:HushUr2Pups8008/kalshi-bot.git`
- Current branch: `chore/github-migration`
- Dirty tree existed at plan start; migration commits must not include unrelated working-tree changes.

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
