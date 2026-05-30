# CLAUDE.md

## Working Style

- Understand and honor the intent of these local instructions fully: they direct the agent back to the broader global guidance, and that guidance must be followed accordingly rather than interpreted narrowly.

## Agent Collaboration

This repo follows the global agent-collaboration policy at `~/.claude/rules/agent_collaboration.md`: Claude Code and Codex are peer coding agents, roles are assigned by blast radius rather than identity, independence is required when risk is high, and the operator owns live-state authority.

For `kalshi-bot`, use the high-assurance workflow for changes that touch execution paths, order submission, bet sizing, Kelly or bankroll logic, hard caps, paper/live mode, readiness gates, market resolution, signal-generating news ingestion, database schema or live-state mutation, launchd/service behavior, paper-to-live cutover, or anything that could create real Kalshi orders. The first agent plans or implements, the second agent reviews adversarially, and the operator performs or explicitly approves merges, tags, restarts, database changes, launchd state changes, production changes, paper/live transitions, and irreversible actions.

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
- **Pre-commit-msg auto-skip-ci hook** (`.githooks/prepare-commit-msg`) appends `[skip ci]` to the commit message when every staged file matches the docs-only allowlist (`docs/**`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`). Saves CI minutes on doc-only commits. Bypass with `--no-verify` if a docs change should still trigger CI.

## Critical Gotchas

Non-obvious constraints that have each cost real debugging time. Treat as load-bearing.

### Kalshi API
- **Signing algorithm is RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH`.** Required for both REST headers and the WebSocket HTTP upgrade handshake. HMAC and PKCS1v15 padding both return 401 with a generic error that does not name the algorithm. Never change the signing algorithm.
- **PEM key in `.env` is a single line with literal `\n` sequences.** Windows `.env` cannot store multi-line values reliably. `_normalize_pem()` in `kalshi/rest_client.py` and `kalshi/websocket_client.py` converts `\n` → real newlines before loading. Do not remove this. **The two `_normalize_pem()` implementations are identical copies** — any bug fix to one must propagate to the other.
- **`/markets` has two `status` contracts. They are not interchangeable.** The **request query parameter** accepts `status="open"` (and only `"open"`; anything else returns `400 bad_request "invalid status filter"`). The **response field** on each returned market reports the live tradeable state as `status="active"`. Send `?status=open`; read `market.status == "active"` downstream as the tradeable predicate. **Do not change the request filter to `"active"`** — the v0.30.0 P-7 packet did exactly that based on a misread of the captured-fixture metadata (which observed `status="active"` *in responses* after requesting `?status=open`), producing a 2726-error 400 storm on first restart. Hotfix `!14` restored `?status=open` in `analysis/market_matcher.py:440,490`; tag `v0.30.0` (`0a513e4`) is published-broken and immobile, `v0.30.1` (current `main` lineage) is the operative release. See `analysis/market_matcher.py:440,490` (request side) and the executor / normalizer downstream readers (response side) for the two enforced sites.

### WebSocket
- **`websockets` library custom-header kwarg alternates by version** (`extra_headers` vs `additional_headers`). `kalshi/websocket_client.py` detects the version at import and picks the correct name. Do not hardcode either — auth headers pass silently into `**kwargs` on the wrong version and never reach the HTTP upgrade.

### Readiness gate
- **G1 reads scaled_confidence, NOT blended_confidence directly.** `tasks/trade_readiness_gate.py` `G1_CONFIDENCE_THRESHOLD = 0.05` (normal) / `G1_FAILSAFE_CONFIDENCE_THRESHOLD = 0.10` (fail-safe). The gate compares `scaled_confidence = blended_confidence * regime_confidence` against the threshold. Reading the SKIPPED `reason="G1_blended_confidence"` string and assuming the comparison is `blended_confidence < 0.05` is wrong — the **2026-05-12 zero-trade incident** diagnostic spent real cycles on this misread. When G1 *does* fire, trace the actual inputs on the skip records: low `scaled_confidence` comes from **either** a weak/low-confidence LLM signal (low `blended_confidence` — the PROFIT-EDGE-004 no-signal ceiling) **or** low `regime_confidence` (fail-safe). Do not assume one cause without checking the recorded `regime_confidence` and `blended_confidence`.
- **`_time_prior` defaults fast-dominant since PROFIT-PRIORS-002 (2026-05-24) — uninstrumented series are NOT in fail-safe.** `analysis/regime_classifier._time_prior` returns `(0.65, 0.25, 0.10)` (rc≈0.22) for any series ≥1 day to close with no `_SERIES_PRIORS` entry, which **clears G4=0.20**. The older claim that the fallback yields `regime_confidence ∈ {0.063, 0.08, 0.14}` → fails G4 → fail-safe describes the **pre-2026-05-24** `_time_prior` and is obsolete; **G4 is no longer the usual binding constraint.** Consequence: adding a categorical prior with the same fast-dominant shape an uninstrumented series already receives is a **test-pin, not a behavior change** (PROFIT-PRIORS-003 says this explicitly). Verified 2026-05-29: post-fix G1 skips collapsed 44→3→0 and the binding constraint on trade volume is now **opportunity throughput** (the news→tradeable-market match surface), not the readiness gate. Structural-/interpretation-dominant priors only help series that actually have dossier/structural lane data wired; for fast-lane-only series they *dilute* `blended_confidence` and can fail G1 (PROFIT-PRIORS-001).

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
