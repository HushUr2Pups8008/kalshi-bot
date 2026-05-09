# Drift Detection Report — 2026-05-09

> Phase 2 foundational-docs audit, drift-detection lens. Built on Phase 1 (`docs/housekeeping/2026-05-08/claude-md-audit.md`). Read-only — no source files were modified.
>
> Method: deep line-by-line verification of every "Critical Gotchas" entry in `kalshi-bot/CLAUDE.md` against current source. Phase 1 items re-verified at line-level granularity; Phase 1's "not verified" items resolved here.

---

## 1. Files Verified

| File | Lines | Role in audit |
|------|-------|---------------|
| `governance/prompts.py` | 203 | anchor_rate polarity block lines 27-31 |
| `governance/llm.py` | 224 | `think: False` payload structure, native `/api/generate` endpoint |
| `kalshi/rest_client.py` | 380 | `_normalize_pem` lines 28-41; RSA-PSS signing lines 103-110; class docstring |
| `kalshi/websocket_client.py` | 280 | `_normalize_pem` lines 51-64; version-detect lines 26-27 |
| `trading/executor.py` | 498 | market status check line 195; same-signal guard line 219 |
| `trading/paper_trader.py` | 984 | `_resolve_market_sync` line 685; `with self._conn:` line 723 |
| `feeds/reddit_monitor.py` | 403 | monotonic backoff lines 184/193/198; guard line 141 |
| `config.py` | 1342 | `MAX_BET_HARD_CAP` line 1050; `dynamic_max_bet` line 1323 |
| `analysis/signal_analyzer.py` | 1263 | `raw_decode` line 301; anti-blend lines 1123-1126; Ollama endpoint line 700 |
| `requirements.txt` | 30 | `aiohttp>=3.10.0` line 5 |
| `scripts/sync_readme_version.py` | 85 | `--check` flag line 57 |
| `.githooks/pre-commit` | 43 | VERSION-staging hook behavior |

---

## 2. Gotcha Verification Table

### Kalshi API

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| RSA-PSS/SHA-256, `salt_length=DIGEST_LENGTH` in REST and WS | PASS | `rest_client.py:105-108`; `websocket_client.py:93-95` |
| `_normalize_pem()` in both `rest_client.py` and `websocket_client.py` | PASS | `rest_client.py:28-41`; `websocket_client.py:51-64` |
| Executor checks market status for both `"active"` and `"open"` | PASS | `executor.py:195` — `status not in ("open", "active")` |

### WebSocket

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| `extra_headers` vs `additional_headers` version-detect at import | PASS | `websocket_client.py:26-27` |

### Signal analysis

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| Do not blend LLM probability with keyword-derived probability | PASS | `signal_analyzer.py:1123-1126` — anti-blend comment; LLM probability used directly |
| Same-signal guard queries all open trades for the ticker | PASS | `executor.py:219` — iterates `open_positions(ticker)`; inline comment at line 218 confirms all-positions semantics |
| LLM JSON extraction uses `JSONDecoder.raw_decode()` | PASS | `signal_analyzer.py:293, 301` |
| DB transaction atomicity in `resolve_market()` | PASS | `paper_trader.py:723-731` — `with self._conn:` wraps all UPDATEs + `_credit_bankroll`; `_set_state:445` commits inside the block |

### Governance LLM (qwen3 family)

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| `think: False` top-level in Ollama generate payload, sibling of `format` | PASS | `governance/llm.py:210-211` — `"format": "json"`, `"think": False`; `"options"` at line 212 is separate |
| `think: False` is Ollama-native-API only; signal_analyzer uses OpenAI-compat endpoint | PASS | `governance/llm.py:215` posts to `/api/generate`; `signal_analyzer.py:700` posts to `{cfg.ollama_base_url}/chat/completions`; `config.py:1132` default includes `/v1` making full URL `/v1/chat/completions` |
| `governance/prompts.py` lines 27-31 contain the anchor_rate polarity block | PASS | Exact: line 27 = section header; lines 28-31 = HIGH/LOW/MID anchor_rate definitions |

### Infrastructure

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| Python 3.14 requires `aiohttp>=3.10.0` | PASS | `requirements.txt:5` — `aiohttp>=3.10.0` |
| Concurrent Mac + Windows instances trigger Reddit 403s | PASS (runtime) | Architecture claim; no code that would contradict |
| Reddit backoff uses absolute monotonic timestamp | PASS | `reddit_monitor.py:184` — `_backoff[subreddit] = time.monotonic() + delay`; guard at line 141 — `if time.monotonic() < _backoff.get(subreddit, 0.0)` |

### Config / env

| Gotcha | Status | Line evidence |
|--------|--------|--------------|
| `MAX_BET_HARD_CAP` not `MAX_BET_DOLLARS` | PASS | `config.py:1050` — `os.getenv("MAX_BET_HARD_CAP", "200.0")`; no `MAX_BET_DOLLARS` env var anywhere in codebase |
| Bet size is dynamic via `cfg.dynamic_max_bet(notional)` | PASS | `config.py:1323` — method exists; called at `main.py:722` and `main.py:1768` |
| `KALSHI_GEOPOLITICAL_SERIES` allowlist is obsolete | PASS (Phase 1 confirmed) | REST client uses paginated series fetch with no allowlist |

**Result: 0 DRIFT, 0 BROKEN. All 17 gotchas PASS.**

---

## 3. Drift Findings

No new drift items found. Two cosmetic items from Phase 1 persist unchanged.

**Carry-over Phase 1 D-2 (P3, persists):** CLAUDE.md says "DB transaction atomicity in `resolve_market()`." The actual transaction logic is in `_resolve_market_sync()` at `paper_trader.py:685`. `resolve_market()` at line 650 is the public async wrapper. Behavioral claim is correct; function-name reference is imprecise. No functional risk.

**Carry-over Phase 1 D-3 (P3, persists):** CLAUDE.md says same-signal guard "must query all open trades for the ticker." Implementation uses in-memory `Portfolio.open_positions(ticker)` at `executor.py:219`. Since Phase 1, the code added an explicit inline comment at line 218: "Reads from the in-memory Portfolio — no DB query at decision time." The CLAUDE.md description could still mislead an agent into adding a redundant DB query, but the code is now self-documenting. Low risk.

---

## 4. Phase 1 Findings Resolved Between 2026-05-08 and 2026-05-09

**D-1 RESOLVED (P2):** `KalshiRestClient` class docstring previously said "HMAC-SHA256." `rest_client.py:56` now correctly reads "Authentication uses RSA-PSS/SHA-256 (salt_length=DIGEST_LENGTH)."

**M-1 RESOLVED (P1):** CLAUDE.md now includes the anchor_rate polarity gotcha at line 68, referencing `governance/prompts.py:27-31` and naming PROFIT-GOV-002.

**M-2 RESOLVED (P1):** CLAUDE.md line 67 now explicitly documents that `think: False` is Ollama-native-API only and that `signal_analyzer.py` uses the OpenAI-compat endpoint.

---

## 5. Release Versioning Section Verification

New section in CLAUDE.md not present at Phase 1. All claims verified:

| Claim | Status | Evidence |
|-------|--------|----------|
| CI runs `scripts/sync_readme_version.py --check` on drift | PASS | Script at 85 lines; `--check` argparse flag at line 57 exits non-zero on mismatch |
| `git config core.hooksPath .githooks` activates the pre-commit hook | PASS | `core.hooksPath` is set to `.githooks` in this clone |
| Hook rewrites README badges + "Current through" line when VERSION is staged | PASS | `.githooks/pre-commit:41` calls `"$PYTHON" "$SYNC_SCRIPT" --write` after detecting staged VERSION |
| Hook re-stages README so bump and sync land in same commit | PASS | `.githooks/pre-commit:42` — `git add README.md` |
| `git commit --no-verify` bypasses; CI still catches drift | PASS | Pre-commit header documents bypass; `--check` CI gate is backstop |

**Undocumented additional behavior (P3):** `.githooks/pre-commit:15-21` also runs `scripts/launchd_template_equivalence_audit.py` when `.plist.template` files are staged. Not in CLAUDE.md. Additive, not contradictory — but agents bumping launchd templates may be surprised.

---

## 6. Working Style / Bug-Fixing / Continuous Improvement

| Section | Status | Notes |
|---------|--------|-------|
| Working Style | PASS | Behavioral directives; no code to contradict. Project-unique meta-instruction at line 5 ("honor intent") is appropriate. |
| Bug-Fixing Preference | PASS | Behavioral directive; no code to contradict. |
| Continuous Improvement | PASS | `docs/profit_path_debt_log.md` exists and is the unified tracker. No parallel debt logs found. |

Phase 1 redundancy findings R-1 through R-4 (Working Style / Bug-Fixing / CI sections are ~90% verbatim copies of global CLAUDE.md) persist unchanged. Open P3.

---

## 7. Top 5 Actions (P0 → P3)

1. **(P3, S) Clarify `resolve_market()` naming in Signal analysis gotcha** — append "(implemented in `_resolve_market_sync()`; `resolve_market()` is the async wrapper at line 650)." Carry-over Phase 1 D-2.

2. **(P3, S) Tighten same-signal guard description** — replace "must query all open trades" with "iterates all in-memory Portfolio positions for the ticker (no DB query at decision time)." Code already has this comment at `executor.py:218`. Carry-over Phase 1 D-3.

3. **(P3, S) Add `_normalize_pem` duplication note to PEM gotcha** — `rest_client.py:28-41` and `websocket_client.py:51-64` are identical copies. Carry-over Phase 1 M-4.

4. **(P3, S) Document pre-commit hook's launchd audit behavior** — add one sentence to Release Versioning section describing the `.plist.template` staging path.

5. **(P3, M) Deduplicate Working Style / Bug-Fixing / Continuous Improvement** — three sections are ~90% verbatim copies of global CLAUDE.md. Replace with a deferral line; keep only project-unique content. Carry-over Phase 1 R-1/R-2/R-3.

---

## 8. Three-Bullet Summary

- **Zero active drift.** All 17 Critical Gotchas match current source with full line-number verification. Three P1 Phase 1 findings (HMAC docstring, missing anchor_rate gotcha, missing signal_analyzer endpoint warning) were resolved between 2026-05-08 and 2026-05-09. CLAUDE.md is accurate as of today.
- **Two cosmetic imprecisions persist (P3 only).** D-2: `resolve_market()` names the wrong function for the transaction logic (it's in `_resolve_market_sync()`). D-3: "query all open trades" implies DB access but code uses in-memory Portfolio with a self-documenting inline comment. Neither causes incorrect agent behavior.
- **Release Versioning section is accurate.** `scripts/sync_readme_version.py --check` exists, `.githooks/pre-commit` is functional, and `core.hooksPath` is set correctly in this clone. The hook does slightly more than documented (launchd audit on `.plist.template` staging) — worth a one-line addition.
