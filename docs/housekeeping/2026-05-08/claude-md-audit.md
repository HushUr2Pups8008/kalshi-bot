# CLAUDE.md + Rules Audit — 2026-05-08

> Read-only Phase-1 housekeeping audit. The named `claude-md-improver` skill does not exist in this environment; this audit was produced by the `code-reviewer` agent acting as a substitute. No `.md` files were modified during the audit — this report itself is the only file written.

---

## Files Reviewed

| File | Lines | Last Modified |
|------|-------|---------------|
| `~/.claude/CLAUDE.md` | 31 | 2026-05-01 |
| `~/CLAUDE.md` (user-level, code-review-graph tools) | 38 | 2026-04-30 |
| `kalshi-bot/CLAUDE.md` | 76 | 2026-05-02 |
| `~/.claude/rules/domain_constraints.md` | 25 | 2026-04-17 |
| `~/.claude/rules/editing_safety.md` | 16 | 2026-04-17 |
| `~/.claude/rules/git_workflow.md` | 19 | 2026-04-18 |
| `~/.claude/rules/planning.md` | 13 | 2026-04-17 |
| `~/.claude/rules/portability.md` | 13 | 2026-04-17 |
| `~/.claude/rules/release_versioning.md` | 12 | 2026-04-17 |
| `~/.claude/rules/risk_review.md` | 13 | 2026-04-17 |
| `~/.claude/rules/validation.md` | 16 | 2026-04-17 |
| `~/.claude/rules/windows_local.md` | 18 | 2026-04-17 |
| `~/.claude/RTK.md` | 29 | 2026-04-21 |

Code files cross-checked:
- `kalshi/rest_client.py` (366 lines)
- `kalshi/websocket_client.py` (266 lines)
- `governance/llm.py` (225 lines)
- `analysis/signal_analyzer.py` (1264 lines)
- `trading/paper_trader.py` (~750+ lines, targeted reads)
- `trading/executor.py` (~490 lines, targeted reads)
- `feeds/reddit_monitor.py` (targeted reads)
- `governance/prompts.py` (targeted reads)
- `docs/profit_path_debt_log.md` (targeted reads of relevant entries)

---

## Critical Gotchas Verification

Each gotcha from `kalshi-bot/CLAUDE.md` verified against named source files.

**Kalshi API**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| RSA-PSS/SHA-256, `salt_length=DIGEST_LENGTH` in REST + WS | confirmed-current | `rest_client.py:93-94`, `websocket_client.py:88-93` |
| `_normalize_pem()` in both `rest_client.py` + `websocket_client.py` | confirmed-current | `rest_client.py:28-41`, `websocket_client.py:51-64` (identical) |
| Market status `"active"` not `"open"`; executor checks both | confirmed-current | `executor.py:195` — `status not in ("open", "active")` |

**WebSocket**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| `extra_headers` vs `additional_headers` version-detect at import | confirmed-current | `websocket_client.py:26-27` |

**Signal analysis**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| Do not blend LLM probability with keyword probability | confirmed-current | `signal_analyzer.py:1123-1126` (anti-blend comment) |
| Same-signal guard queries all open trades | confirmed-current (mechanism drift, see D-3) | `executor.py:219` — iterates `portfolio.open_positions(ticker)` |
| LLM JSON extraction uses `JSONDecoder.raw_decode()` | confirmed-current | `signal_analyzer.py:281-308` |
| `resolve_market()` DB atomicity with `with self._conn:` | confirmed-current (method name drift, see D-2) | `paper_trader.py:720-731` (`_resolve_market_sync()`) |

**Governance LLM**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| `think: False` top-level in Ollama generate payload for qwen3 | confirmed-current | `governance/llm.py:196-212` |
| PROFIT-GOV-001 closed 2026-05-02 | confirmed | Debt log entry status COMPLETE |

**Infrastructure**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| Reddit backoff uses absolute monotonic timestamp | confirmed-current | `feeds/reddit_monitor.py:184` |
| Python 3.14 + aiohttp>=3.10.0 | not verified | requirements files not read |
| Concurrent Mac+Windows instances → Reddit 403s | not verified | runtime constraint |

**Config / env**

| Gotcha | Status | Evidence |
|--------|--------|----------|
| `MAX_BET_HARD_CAP` not `MAX_BET_DOLLARS` | not verified | config.py not read by audit agent |
| `dynamic_max_bet(notional)` | not verified | config.py not read by audit agent |
| `KALSHI_GEOPOLITICAL_SERIES` allowlist obsolete | confirmed-consistent | `rest_client.py:259-284` uses `/series` pagination, no allowlist |

---

## Outdated

No gotchas were entirely outdated. All verifiable gotchas reflect active code.

---

## Code Drift vs CLAUDE.md Descriptions

### D-1 — `KalshiRestClient` class docstring says HMAC-SHA256 (P2)
- File: `kalshi/rest_client.py:50-54`
- Docstring reads "Authentication uses HMAC-SHA256 over the canonical request string." Actual `_sign()` at line 75 uses RSA-PSS/SHA-256 with `asym_padding.PSS`. An agent reading the docstring first would be misled before reaching the CLAUDE.md gotcha.
- Fix is in code (docstring), not CLAUDE.md.

### D-2 — CLAUDE.md names `resolve_market()`, sync work is in `_resolve_market_sync()` (P3)
- The async public `resolve_market()` is a thin wrapper that delegates to `_resolve_market_sync()` via `asyncio.to_thread()`. Transaction logic lives at `paper_trader.py:685`. Cosmetic naming imprecision; behavior matches the gotcha.

### D-3 — CLAUDE.md says same-signal guard "must query all open trades" (implies DB) (P3)
- Implementation uses in-memory `Portfolio` (`executor.py:219`: `self._paper.portfolio.open_positions(ticker)`). The Portfolio is DB-backed at startup and kept in sync. Correct outcome, different mechanism. Risk: an agent following the gotcha literally might add a redundant DB query.

---

## Redundant

### R-1 — Working Style section duplicated verbatim (P3)
- `~/.claude/CLAUDE.md:3-9` ↔ `kalshi-bot/CLAUDE.md:3-10`. Four of five bullets identical. Project file adds only the "intent-honoring" bullet. Silent-drift risk if global is updated.

### R-2 — Bug-Fixing Preference section identical (P3)
- `~/.claude/CLAUDE.md:11-14` ↔ `kalshi-bot/CLAUDE.md:12-15`. Exact copy.

### R-3 — Continuous Improvement near-identical (P3)
- Project version adds the debt-log pointer; rest is duplicated.

### R-4 — Rule cross-references appear in both CLAUDE.md files (P3)
- Both list `planning.md`, `validation.md`, `git_workflow.md`. Two places to maintain; no value.

---

## Conflicts

None found. Global and project instructions are additive; project explicitly defers to global via the meta-instruction.

---

## Missing

### M-1 — No gotcha for `governance/prompts.py` anchor_rate polarity block (P1)
- Source: PROFIT-GOV-002 (closed 2026-05-03)
- `governance/prompts.py:27-31` defines the polarity of `anchor_rate` (HIGH = no edge → disable candidate; LOW = informative → keep). This was the A5 fix that prevents qwen3:14b rubber-stamp regression. Removing or rewording the lines silently re-introduces the regression.
- **Recommendation:** add gotcha under "Governance LLM" referencing this block and naming the snapshot tests + negative-control harness as gates.

### M-2 — `think: False` gotcha doesn't warn about `signal_analyzer.py` exposure (P1)
- Source: PROFIT-LLM-001 (open, LOW)
- If `OLLAMA_MODEL` is changed to any qwen3 family model, `analysis/signal_analyzer.py:_ollama_estimate_detailed` (line 680-693) hits the same regression. Additional complication: signal_analyzer uses the OpenAI-compat `/v1/chat/completions` endpoint (`signal_analyzer.py:700`); governance/llm.py uses native `/api/generate`. The `think` parameter only works on the native endpoint.
- **Recommendation:** extend the existing gotcha with a one-line warning about endpoint differences.

### M-3 — No domain constraint for `/governance` (P2)
- `domain_constraints.md` covers `/analysis`, `/trading`, `/tasks`, `/feeds`. `/governance` is absent. Phase 3+ has real-mode flip authority; governance changes can disable sources / alter production behavior without a human trade confirmation.
- **Recommendation:** add a Trigger/Action rule covering prompt-template edits, the snapshot test gate, and the no-trade-logic boundary.

### M-4 — `_normalize_pem()` duplication risk not documented (P3)
- The two implementations are identical copies. A bug fix to one will not propagate.
- **Recommendation:** parenthetical to existing gotcha — "These are identical copies; any fix must land in both files."

### M-5 — Hardcoded Anthropic model name not documented (P3)
- `analysis/signal_analyzer.py:890` uses `model="claude-haiku-4-5-20251001"` hardcoded. When deprecated, fallback fails silently (caught at line 924). Worth a one-line note.

---

## Stale References

### SR-1 — `RTK.md:29` circular reference (P3)
- Line 29: "Refer to CLAUDE.md for full command reference." Global CLAUDE.md has no RTK command reference — it points back to RTK.md via `@RTK.md`. Dead-end pointer.
- **Recommendation:** delete the line or replace with "See sections above for all rtk commands."

---

## Style

### S-1 — `~/CLAUDE.md` missing H1 title (P3)
- Begins with HTML comment, jumps to H2. Other CLAUDE.md files begin with `# CLAUDE.md`.

### S-2 — Critical Gotchas use bold-inline, not Trigger/Action (P3)
- All rule files use `Trigger: ... Action: ...`. Gotchas use bold headers + prose. Intentional divergence (descriptive vs prescriptive), but inconsistency may confuse agents adding new entries.

### S-3 — Working Style meta-instruction placement (P3)
- `kalshi-bot/CLAUDE.md:5` "Understand and honor the intent..." is a meta-instruction wedged into Working Style. Belongs in a preamble or its own section.

---

## Recommended Actions (Top 5)

**1. [P1] Add `governance/prompts.py` anchor_rate polarity gotcha.** Insert under "Governance LLM" in `kalshi-bot/CLAUDE.md`. Reference PROFIT-GOV-002 + the snapshot tests at `tests/test_governance_prompts.py` + negative-control harness at `scripts/simulations/governance_negative_control.py`.

**2. [P1] Extend qwen3 `think: False` gotcha with signal_analyzer endpoint warning.** Note that `signal_analyzer.py` uses the OpenAI-compat endpoint which ignores `think: False`; a qwen3 swap there requires switching to the native `/api/generate` endpoint as well.

**3. [P2] Add `/governance` domain constraint to `~/.claude/rules/domain_constraints.md`.** Trigger/Action rule covering prompt edits as high-risk + snapshot test gate + no trade logic.

**4. [P2] Fix `KalshiRestClient` class docstring** (`kalshi/rest_client.py:51-54`) — replace "HMAC-SHA256" with "RSA-PSS/SHA-256 (`salt_length=DIGEST_LENGTH`) per Kalshi's API key auth scheme." Code edit, not CLAUDE.md.

**5. [P3] Dedup Working Style / Bug-Fixing / Continuous Improvement from `kalshi-bot/CLAUDE.md`.** Replace with a single deferral line; keep only the unique content (intent-honor meta + debt-log pointer).

---

## Open Questions

1. **Is the missing `/governance` domain constraint intentional?** Governance is covered by phase runbooks (`docs/governance/PHASE2_RUNBOOK.md`). Operator may have scoped `domain_constraints.md` to the signal pipeline only.
2. **Should `RTK.md:29` circular reference be deleted?** No functional impact; cosmetic cleanup.
3. **Critical Gotchas style: adopt Trigger/Action or keep current?** Trade-off between readability of bold-inline and rule-engine parsability of Trigger/Action.

---

## 3-Bullet Summary

- **Code drift vs CLAUDE.md:** 0 critical drifts — all 8 verifiable gotchas match current code. Two wording imprecisions (D-2, D-3) and one stale class docstring in `rest_client.py` (D-1) saying "HMAC-SHA256" while the code uses RSA-PSS.
- **Biggest redundancy:** Working Style, Bug-Fixing Preference, and Continuous Improvement sections are ~90% verbatim copies between `~/.claude/CLAUDE.md` and `kalshi-bot/CLAUDE.md` — silent-drift risk on global update.
- **Single most important addition:** add a CLAUDE.md gotcha for `governance/prompts.py:27-31` (anchor_rate polarity block) — the PROFIT-GOV-002 fix preventing qwen3:14b rubber-stamp regression. Currently undocumented in any agent-instruction file.
