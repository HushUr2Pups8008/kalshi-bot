---
name: skill-library
description: Searchable ECC skill router for kalshi-bot. Use when a task needs an off-stack or episodic skill not in the daily load set. Triggers: "find skill", "which skill", "use library skill", framework names (react, django, rust, etc.), or episodic workflows (deploy, migrate, refactor-clean, feature-dev).
---

# kalshi-bot Skill Library

## DAILY vs LIBRARY

- **DAILY** loads every session — repo stack + superpowers workflow + project-native surfaces.
- **LIBRARY** stays reachable here — search by keyword, then load the ECC skill on demand.

**Dedup rule:** when superpowers and ECC overlap, use superpowers. ECC copies of the same workflow are LIBRARY only.

## DAILY (do not route here)

### Superpowers (global plugin — preferred)

| Skill | Use when |
|-------|----------|
| `using-superpowers` | Session start; find the right skill before improvising |
| `systematic-debugging` | Bug, test failure, unexpected behavior |
| `verification-before-completion` | Before claiming done/fixed/passing |
| `test-driven-development` | Any feature, bugfix, or behavior change |
| `requesting-code-review` | After tasks; before merge on high-blast-radius diffs |
| `receiving-code-review` | Acting on review feedback |
| `brainstorming` | Before creative/design work |
| `writing-plans` | Multi-step implementation planning |
| `executing-plans` | Running an approved plan in a fresh session |
| `subagent-driven-development` | Parallel independent plan tasks |

**Supersedes ECC:** `tdd-workflow`, `verification-loop`, `code-review`, `implement`, `review`, `agent-introspection-debugging`, `blueprint`, `planner`, `tdd-guide`, `code-reviewer`

### Project-native

| Skill / agent | Use when |
|---------------|----------|
| `replay-gate-analyze` | PR touches scoring/blending/sizing/governance; replay EV evidence |
| `release-bump` | VERSION / CHANGELOG / tag release |
| `kalshi-safety-reviewer` | Execution, signing, bankroll, readiness-gate changes |
| `replay-evidence-reviewer` | Replay-ci-gate T0–T3 evidence on merge |

### ECC (project `.claude/skills/` — no superpowers equivalent)

| Skill | Use when |
|-------|----------|
| `python-patterns` | Idiomatic Python across 500+ modules |
| `llm-trading-agent-security` | LLM signal → sizing → order path |
| `safety-guard` | Paper/live, caps, irreversible ops |
| `ai-regression-testing` | Replay-ci-gate / hypothesis regression |
| `regex-vs-llm-structured-text` | LLM JSON extraction (`raw_decode`) |

### Marketplace (global — daily)

| Skill | Use when |
|-------|----------|
| `context-mode` | Logs, test output, JSONL, replay artifacts, large grep |

### Commands (invoke, not installed)

`santa-loop`, `python-review`, `test-coverage`, `quality-gate`

### Agents (ECC daily)

`python-reviewer`, `security-reviewer`, `silent-failure-hunter`, `pr-test-analyzer`

---

## LIBRARY — search by keyword

Full ECC catalog: `~/.claude/plugins/cache/everything-claude-code/everything-claude-code/2.0.0-rc.1/`

### Python / backend (episodic)

| Keywords | ECC skill |
|----------|-----------|
| git, commit, tag, release | `git-workflow` |
| github, PR, CI | `github-ops` |
| shell, launchd, restart | `terminal-ops`, `deployment-patterns` |
| sqlite, schema, migration | `database-migrations` |
| ollama, token cost, LLM budget | `cost-aware-llm-pipeline` |
| security audit, secrets | `security-review` |
| ruff, lint style | `coding-standards` |
| research before code | `search-first` |
| install ECC, trim surface | `configure-ecc`, `agent-sort`, `skill-stocktake` |
| compact context | `strategic-compact` |

### Trading / ops (this repo, not daily)

| Keywords | ECC skill |
|----------|-----------|
| canary, paper-live cutover | `canary-watch` |
| scrape, feed, ingest | `data-scraper-agent` |
| autonomous loop | `autonomous-loops`, `continuous-agent-loop` |

### Off-stack languages (not in repo)

| Keywords | Category |
|----------|----------|
| typescript, react, next, nuxt, nest | frontend / web (19 skills) |
| flutter, swift, android, compose | mobile (8) |
| kotlin, java, spring, laravel, php | JVM / PHP (16) |
| go, rust, cpp, csharp, perl, dart | systems langs (16) |
| django | Python web (4) |

### Domains (not this repo)

| Keywords | Category |
|----------|----------|
| healthcare, hipaa, emr | healthcare (5) |
| logistics, freight, customs, returns | supply-chain (7) |
| stripe, billing, procurement | finance ops (3) |
| defi, evm, x402 | crypto (3) |
| seo, content, outreach, investor | marketing (9) |
| remotion, manim, video | media (5) |
| jira, email, slack, google workspace | integrations (7) |
| postgres, docker, clickhouse | infra off-stack (3) |
| pytorch, training, benchmark | ML training (5) |

### Commands (LIBRARY)

Off-stack: `go-*`, `rust-*`, `kotlin-*`, `cpp-*`, `flutter-*`, `gradle-build`, `multi-*`, `prp-*`, `instinct-*`, `hookify*`, `build-fix`, `update-codemaps`

Episodic: `plan`, `code-review`, `review-pr`, `feature-dev`, `refactor-clean`, `update-docs`, `checkpoint`

### Agents (LIBRARY)

Off-stack reviewers/resolvers (40): `typescript-reviewer`, `go-reviewer`, `rust-reviewer`, `build-error-resolver`, etc.

On-demand: `architect`, `code-architect`, `code-explorer`, `doc-updater`, `database-reviewer` (Postgres — wrong engine for this repo)

---

## Install manifest

Machine-readable plan: `.claude/ecc-install-plan.json` (`status: installed`).

Hand off to `configure-ecc` only after operator approves the manifest.