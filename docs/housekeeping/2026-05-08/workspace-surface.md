# Workspace Surface Audit — 2026-05-08

> Read-only ECC `workspace-surface-audit` skill applied to kalshi-bot. No settings, MCP, or plugin config was modified.

---

## 1. Current Surface

### Active Plugins (4 of 13 installed)

| Plugin | Version | Source | Role |
|--------|---------|--------|------|
| `caveman@caveman` | ef6050c | caveman | Compressed-comm mode + commit/review/help skills |
| `context-mode@context-mode` | 1.0.111 | context-mode | Token-efficient batch shell + indexing |
| `everything-claude-code` (ECC) | 2.0.0-rc.1 | ECC marketplace | Massive skill/agent/MCP bundle (~330 skills, ~50 agents) |
| `superpowers@claude-plugins-official` | 5.1.0 | claude-plugins-official | Brainstorming, TDD, plan/exec workflows |

### Disabled Plugins (9 of 13)

`claude-md-management`, `code-review`, `code-simplifier`, `commit-commands`, `context7`, `feature-dev`, `frontend-design`, `github`, `playwright` — all installed but currently `enabledPlugins=false`.

> Note: ECC bundles its own `context7`, `github`, and `playwright` MCPs, so the standalone plugin versions are redundant if ECC is active.

### MCP Servers

| Source | Server | Status |
|--------|--------|--------|
| ECC bundle | `github` | available |
| ECC bundle | `context7` | available |
| ECC bundle | `exa` | available |
| ECC bundle | `memory` | available |
| ECC bundle | `playwright` | available |
| ECC bundle | `sequential-thinking` | available |
| `context-mode` plugin | `context-mode` (ctx_*) | available + auto-loaded |
| project-local pipx CLI | `code-review-graph` | enabled (`enabledMcpjsonServers: ["code-review-graph"]`) |

Total active MCP surface: **8 servers**.

### Hooks

| Stage | Matcher | Command | Purpose |
|-------|---------|---------|---------|
| `SessionStart` | `''` | `code-review-graph status` | Print graph stats banner |
| `SessionStart` | `*` | `~/.claude/hooks/context-mode-cache-heal.mjs` | Repair context-mode cache state |
| `SessionStart` | `*` | `~/.claude/hooks/caveman-activate.js` | Activate caveman mode banner |
| `UserPromptSubmit` | `*` | `~/.claude/hooks/caveman-mode-tracker.js` | Inject caveman reminder per turn |
| `PreToolUse` | `Bash` | `rtk hook claude` | Rewrite Bash → `rtk <cmd>` for token savings |
| `PostToolUse` | `Edit\|Write\|Bash` | `code-review-graph update --skip-flows` | Incremental graph rebuild on edits |

GateGuard hooks are also active per session reminders (block first Bash + Write without facts).

### Harness

- `effortLevel: xhigh` (max thinking budget)
- `env: { CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: ... }`
- Statusline: bash wrapper at `~/.claude/statusline-wrapper.sh`

### Project-Local Permissions (`kalshi-bot/.claude/settings.local.json`)

- 152 allow entries, no deny entries.
- Many entries are one-off `sed -n` / `awk` patterns from past sessions — accumulated organically.
- Several entries reference paths that no longer exist or are session-specific (`/tmp/verify_*.sh`, specific PIDs `22474`, `23108`).

### Repo Surface (kalshi-bot)

- Python project, version 0.21.x (`VERSION` file + checked-in CHANGELOG, README badge sync gate, `.githooks/pre-commit` for VERSION ↔ README parity)
- Top-level layers: `analysis/`, `feeds/`, `governance/`, `kalshi/`, `tasks/`, `trading/` (audit-excluded), `utils/`, `ops/`, `scripts/`, `tests/`
- `.env.example` documents **12 keys**; `.gitignore` already excludes `.env`
- Source code references ~60+ env vars (most are config knobs, not secrets)
- Trading-bot specific surfaces: SQLite paper-trade DB at `data/paper_trades.db`, governance JSONL ledger at `logs/governance/decisions.jsonl`, RSA-PSS PEM keys via Kalshi REST + WS
- `.gitlab-ci.yml` present (3.3 KB) — GitLab is the CI surface, not GitHub

### Connected External Services (via env keys observed in code)

| Service | Use | Secret keys |
|---------|-----|-------------|
| Kalshi | Trade execution + market data | `KALSHI_API_KEY_ID`, `KALSHI_API_KEY_SECRET` (PEM), `KALSHI_ENV` |
| Reddit | Feed source | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` |
| Ollama (local) | Governance LLM (qwen3:14b), signal LLM | `OLLAMA_MODEL`, `OLLAMA_*_TIMEOUT_SECONDS` |
| Anthropic | Signal LLM fallback | `ANTHROPIC_API_KEY` |
| GDELT | News feed | none (public API) |
| Google News + Bing News | Search-keyed feeds | none (RSS) |

---

## 2. Parity (Where ECC Already Covers the Need)

| Need | ECC coverage |
|------|--------------|
| Code review (PRs and local diff) | `everything-claude-code:code-review`, `:review-pr`, `:python-review`, `:santa-method` |
| Plan / spec / multi-step workflow | `everything-claude-code:plan`, `:prp-prd`, `:prp-plan`, `:prp-implement` + `superpowers:writing-plans` |
| Test-driven development | `everything-claude-code:tdd-workflow` + `superpowers:test-driven-development` |
| GitHub ops (issues/PRs/releases) | `everything-claude-code:github-ops` + bundled `github` MCP |
| Up-to-date docs lookup | `everything-claude-code:documentation-lookup` + `:context7` MCP |
| Refactor / dead code | `everything-claude-code:refactor-clean`, `simplify` |
| Security review | `everything-claude-code:security-review`, `:security-bounty-hunter`, `:security-scan` |
| Post-deploy / canary monitoring | `everything-claude-code:canary-watch`, `:dashboard-builder` |
| Knowledge graph queries | `code-review-graph` MCP (standalone, integrated via project) |
| Token-savings on Bash | `rtk` (PreToolUse hook) + `context-mode` (multi-cmd batches) |
| Documentation generation | `everything-claude-code:update-docs`, `:update-codemaps`, `:code-tour` |
| Compaction / session continuity | `everything-claude-code:save-session`, `:resume-session`, `:strategic-compact` |

**Verdict:** The plugin stack is *over*-provisioned for parity needs. The 9 disabled standalone plugins duplicate ECC capabilities — keeping them installed (even disabled) costs no runtime tokens but bloats marketplace state and creates "which one wins" confusion if accidentally re-enabled.

---

## 3. Primitive-Only Gaps (Tools Available, Skill Missing)

### G-1 — Kalshi-specific operator skill (P2)
- **Primitive:** `kalshi/rest_client.py` and `websocket_client.py` are well-built REST + WS bindings. There is no skill that wraps them for one-shot operator work like "show me current open positions on Kalshi", "list active markets matching keyword X", "check my account balance".
- **Recommended shape:** Skill (not agent / not MCP). A `kalshi-ops` skill that the operator can invoke during paper-trade soak windows for one-shot diagnostic queries. ECC's `terminal-ops` is the precedent shape.

### G-2 — Governance decision-ledger inspector (P2)
- **Primitive:** `logs/governance/decisions.jsonl` is the source of truth for every governance cycle. Reading it requires hand-rolled `awk`/`jq` (visible in the `.claude/settings.local.json` allowlist — multiple permissions for `awk` parsers against this exact file).
- **Recommended shape:** Skill. Wraps the inspect-by-cycle-id, inspect-by-decision-type, and "show pending real-mode flips" patterns. Removes the need for ad-hoc `awk` patterns to be allowlisted one at a time.

### G-3 — Profit-path debt-log triage skill (P2)
- **Primitive:** `docs/profit_path_debt_log.md` is 407 KB and growing. Adding entries, finding open items, and aging them is currently manual. The settings.local.json shows multiple ad-hoc `awk` matchers for `PROFIT-OBS-003`, `PROFIT-OBS-004`, etc.
- **Recommended shape:** Skill. CRUD on debt-log entries with severity/aging awareness. ECC's debt-log pattern is unspecified, so this would be project-local.

### G-4 — Soak-monitor / paper-trade summary (P3)
- **Primitive:** `data/paper_trades.db` (SQLite) + `analysis/source_credibility.py:format_table()` already produce summaries; `scripts/soak_check.sh` is a partial wrapper.
- **Recommended shape:** Skill that calls existing primitives in a known order (open positions → recent resolves → bankroll trajectory → source-credibility table) so daily soak reports stop being recomposed by hand each morning.

### G-5 — `.env.example` ↔ code parity check (P3)
- **Primitive:** Code references ~60+ env vars; `.env.example` has only 12. Many are intentional config-knob defaults (no need to surface), but some are required-secret-but-missing (e.g., `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `ANTHROPIC_API_KEY`).
- **Recommended shape:** A small CI-callable script (`scripts/env_parity_check.py`) — not a skill. Compares grep-extracted env vars to `.env.example` keys, flags missing-required (heuristic: any var with `_KEY`, `_SECRET`, `_TOKEN`, `_API_KEY_*` suffix that has no default in `config.py`).

---

## 4. Missing Integrations

### MI-1 — GitLab MCP (P2)
- Project CI is GitLab (`.gitlab-ci.yml` present, `glab` CLI used in allowlist). The active MCP set has `github` but no GitLab equivalent.
- **Shape:** the existing `glab *` Bash allowlist already provides operator surface; an MCP would add structured tool calls for issue/MR lookups. **Lower priority** — `glab` Bash is sufficient.

### MI-2 — Ollama health probe MCP / skill (P3)
- The bot's governance stack depends on local Ollama running qwen3:14b. There is `OLLAMA_PROBE_TIMEOUT_SECONDS` but no operator skill to ask "is Ollama healthy and does the configured model exist".
- **Shape:** Lightweight skill or extension to the Kalshi-ops skill (G-1). Can be folded into a general "soak readiness" skill.

### MI-3 — None other identified
- No critical external service (Slack, Datadog, etc.) is missing. Ops surface for this project is largely local — launchd, sqlite, jsonl logs.

---

## 5. Top 3-5 Next Moves

### NM-1 — [P2] Disable the 9 plugin redundancies
- 9 plugins are installed but disabled. Either uninstall them or document why they're held disabled. The `claude-md-management` plugin in particular shadows ECC's audit primitives without adding value (this Phase-1 audit hit the gap when the named "claude-md-improver" skill turned out not to exist). ECC's own coverage is sufficient.
- **Effort:** S (one-time uninstall via `/plugin` UI)
- **Risk:** zero — they're already disabled.

### NM-2 — [P2] Build the 4 missing operator skills
- `kalshi-ops` (G-1), `governance-ledger` (G-2), `debt-log-triage` (G-3), `soak-summary` (G-4). Together these eliminate the ad-hoc `awk`/`sed` allowlist entries that have grown to 152 in `settings.local.json`.
- **Effort:** M total. Each skill is one CLAUDE-style markdown file referencing existing primitives; no new code under `/analysis` or `/trading`.
- **Risk:** low — pure operator-surface addition, no behavioral change.

### NM-3 — [P3] Sweep `settings.local.json` allowlist
- 152 entries with many one-off `sed -n` / specific-PID / `/tmp/verify_*.sh` patterns. After NM-2, many of these become unnecessary. Replace with categorical wildcards where safe (`Bash(awk *)`, `Bash(sed -n *)` already exist alongside the one-offs).
- **Effort:** S
- **Risk:** low. Use `everything-claude-code:fewer-permission-prompts` skill to assist.

### NM-4 — [P2] Fix `.env.example` ↔ code parity gap
- Add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, and `ANTHROPIC_API_KEY` (with placeholder values) to `.env.example`. New contributors today cannot stand up the Reddit feed or the Anthropic LLM fallback from the example file alone.
- **Effort:** S
- **Risk:** zero — `.env.example` contains placeholders only; no secrets.

### NM-5 — [P3] Document the GateGuard escape hatch in CLAUDE.md
- This audit hit GateGuard twice (initial Bash + initial Write). The recovery message says `ECC_GATEGUARD=off` or `ECC_DISABLED_HOOKS` env var. Neither is mentioned in any CLAUDE.md or rule file. New agents will keep tripping on it for the same reason.
- **Effort:** S — one paragraph in `kalshi-bot/CLAUDE.md`.
- **Risk:** zero. Documentation only.

---

## Security / Permissions Notes

- No secrets observed in any settings or `.env.example`.
- `.env` is gitignored (verified).
- `.claude/settings.local.json` allows broad `Bash(rm *)`, `Bash(chmod *)`, `Bash(curl *)` — appropriate for a single-operator dev box but noteworthy if this repo ever gains contributors.
- `enableAllProjectMcpServers: true` accepts any `.mcp.json` in the project. Currently fine (no `.mcp.json` exists at the project root), but a risk vector if a future PR adds one.

---

## Alignment with Shorthand Guide Directives

The Affaan Mustafa "Shorthand Guide" was referenced in the user's Phase-1 prompt. Direct file not located in repo or Claude config. Best-effort alignment with widely-cited shorthand-guide principles:

- **Skills > one-shot agents** for repeatable operator workflows: aligned. NM-2 specifically recommends skills, not agents.
- **MCPs only when no Bash equivalent**: aligned. NM-1 leans toward disabling redundant plugin MCPs; NM-2 leans toward skills wrapping existing CLIs.
- **Hooks for enforcement, not behavior**: aligned. Current hook set (rtk, code-review-graph, caveman, GateGuard) is enforcement-flavored, not behavior-flavored.

---

## 3-Bullet Summary

- **Surface state:** 4 active plugins + 8 MCP servers + 5 hooks. Stack is over-provisioned via 9 disabled-but-installed plugins that duplicate ECC capabilities.
- **Biggest gap:** four primitive-only operator workflows powered by accumulated `awk`/`sed` allowlist entries in `settings.local.json` (now at 152 entries). Missing skills: `kalshi-ops`, `governance-ledger`, `debt-log-triage`, `soak-summary`.
- **Top concrete fix:** add the 4 missing required-secret keys (`REDDIT_*`, `ANTHROPIC_API_KEY`) to `.env.example`. New-contributor onboarding currently fails silently — the bot starts, but Reddit feed and Anthropic fallback are dark.
