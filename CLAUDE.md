# CLAUDE.md

## Working Style

- Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly.

## Continuous Improvement

- This project's unified tracking system is `docs/profit_path_debt_log.md`. Do not create parallel tracking surfaces (status / roadmap / debt / decision-log / dashboard / per-day stamps). New tracking content lands as a section in the debt log, not a new file. The 2026-05-09 docs consolidation removed 8 parallel surfaces (1 deleted, 7 archived; see `docs/_archive/plans/2026-05-09-docs-directory-consolidation.md` for the consolidation plan and `docs/_archive/2026-05-09-docs-consolidation/` for archived evidence); preserving the consolidation is now a maintenance invariant.
- Top-level project docs (`docs/profit_path_debt_log.md`, `docs/ROADMAP.md`, this `CLAUDE.md`) collectively own all tracking and documentation-creation guidance. Do not author bespoke per-subdirectory tracking conventions, README dashboards, or per-cycle decision logs that duplicate what the One Document already covers. Active-cycle ledgers in `docs/governance/` are the only sanctioned exception; they merge back to the debt log at cycle close per its `R-10 — No New Tracking Files` rule.

See `~/.claude/rules/documentation_format.md` for documentation format rules.

## Release Versioning (project-local)

Extends `~/.claude/rules/release_versioning.md`.

- **VERSION ↔ README parity is enforced by CI.** The lint job runs
  `scripts/sync_readme_version.py --check` and fails the pipeline on drift.
- **One-time setup per clone:** `git config core.hooksPath .githooks`.
  This activates the `pre-commit` hook that auto-rewrites the README
  badges + "Current through" line whenever `VERSION` is staged, then
  re-stages `README.md` so the bump and the README sync land in the same
  commit. Without this `git config`, the hook is dormant and the CI gate
  is the only safety net.
- **Trigger: when bumping VERSION.** Stage `VERSION` first; the hook
  handles README. Add a `CHANGELOG.md` entry in the same commit. Tag
  the commit (`git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z`)
  for any non-trivial release — solo project, but tags are the only
  rollback anchor and the only thing that lets `git describe HEAD`
  return a meaningful version string.
- **Bypass (emergency only):** `git commit --no-verify`. CI will still
  catch the drift; only use when the hook itself is broken.
- **Pre-commit hook also runs `scripts/launchd_template_equivalence_audit.py`** when `.plist.template` files are staged (`.githooks/pre-commit:15-21`). Audit ensures launchd templates stay equivalent across the repo; failure blocks the commit.

## Critical Gotchas

Non-obvious constraints that have each cost real debugging time. Treat as load-bearing.

### Kalshi API
- **Signing algorithm is RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH`.** Required for both REST headers and the WebSocket HTTP upgrade handshake. HMAC and PKCS1v15 padding both return 401 with a generic error that does not name the algorithm. Never change the signing algorithm.
- **PEM key in `.env` is a single line with literal `\n` sequences.** Windows `.env` cannot store multi-line values reliably. `_normalize_pem()` in `kalshi/rest_client.py` and `kalshi/websocket_client.py` converts `\n` → real newlines before loading. Do not remove this. **The two `_normalize_pem()` implementations are identical copies** — any bug fix to one must propagate to the other.
- **Market status field is `"active"`, not `"open"`.** Executor must check for both strings. Filtering on `"open"` alone silently skips tradeable markets.

### WebSocket
- **`websockets` library custom-header kwarg alternates by version** (`extra_headers` vs `additional_headers`). `kalshi/websocket_client.py` detects the version at import and picks the correct name. Do not hardcode either — auth headers pass silently into `**kwargs` on the wrong version and never reach the HTTP upgrade.

### Signal analysis
- **Do not blend LLM probability with keyword-derived probability.** Keywords are a *gate* (does this news relate to this market?), not a probability input. Blending previously pushed bets above the edge threshold even when the LLM said `magnitude="none"`.
- **Same-signal guard must check *all* open trades for the ticker** in the in-memory `Portfolio` (not the DB), not just the most recent. When both YES and NO positions exist on a ticker, "most recent" is always the opposite side and allows redundant entries past the guard. See self-documenting comment at `executor.py:218`.
- **LLM JSON extraction must use `JSONDecoder.raw_decode()`** scanning each `{` and keeping the last valid object. Greedy `re.search(r"\{.*\}", ..., re.DOTALL)` grabs from the first `{` through the last `}` and breaks on any preambles containing braces.
- **DB transaction atomicity lives in `_resolve_market_sync()`** (the public `resolve_market()` is a thin wrapper). Pre-calculate all outcomes, wrap the UPDATEs in `with self._conn:`, credit bankroll exactly once at the end. A crash mid-loop without the context manager permanently corrupts bankroll, which poisons every subsequent Kelly calculation.

### Governance LLM (qwen3 family)
- **Ollama `format=json` + qwen3 thinking returns empty `{}`.** qwen3 chain-of-thought reasoning gets consumed by the JSON grammar constraint. Pass top-level `think: False` in the Ollama generate-request payload (sibling of `format`, not nested under `options`). The `/no_think` *prompt* directive does NOT fix this — only the server-side `think` parameter. `governance/llm.py:LocalQwenLLM.complete` carries this; do not remove it. Filed as `PROFIT-GOV-001` (closed 2026-05-02).
- **`think: False` is Ollama-native-API only — does NOT apply to OpenAI-compat endpoints.** `analysis/signal_analyzer.py` posts to `{ollama_base_url}/chat/completions` (the OpenAI-compatible Chat Completions API). That endpoint silently ignores the `think` field — passing it has no effect. The empty-`{}` failure mode does not occur here because Chat Completions does not impose the same JSON grammar constraint. If a future swap moves signal analysis onto qwen3 via the native `/api/generate` endpoint, the `think: False` requirement comes back — switching the flag alone is not enough; the endpoint must change too.
- **`governance/prompts.py` lines 27–31 contain the anchor_rate polarity block** (HIGH anchor → DISABLE, LOW anchor → KEEP). Removing or weakening these lines silently re-introduces the qwen3 rubber-stamp regression where the LLM defaults toward `no_action` regardless of evidence direction. The block is load-bearing for governance decision quality. Filed as `PROFIT-GOV-002`. Treat any edit that touches lines 27–31 as needing explicit user review.

### Infrastructure
- **Python 3.14 on Windows requires `aiohttp>=3.10.0`.** `aiohttp==3.9.5` has no cp314 wheel. Do not pin aiohttp to 3.9.x.
- **Concurrent Mac + Windows instances on the same network trigger Reddit 403s.** Reddit sees the combined request rate from the shared external IP. Only one instance per external IP — stop the old before starting the new.
- **Reddit backoff must use an absolute monotonic timestamp**, not a countdown. Countdowns subtracted from by the poll interval go negative, clamp to 0, and provide zero protection. Use `_backoff[x] = time.monotonic() + delay` and compare against `time.monotonic()`.

### Config / env
- **`MAX_BET_HARD_CAP`**, not `MAX_BET_DOLLARS`. The old name silently falls back to default.
- **Bet size is dynamic** via `cfg.dynamic_max_bet(notional)`. Never hardcode a dollar cap — pass the current notional bankroll.
- **`KALSHI_GEOPOLITICAL_SERIES` allowlist is obsolete.** Kalshi retired those series; zero open markets resolve under them. Current approach: fetch all ~9k series, keyword-match titles, apply sports-prefix blocklist. Do not resurrect the allowlist.
