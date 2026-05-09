# Code-vs-Doc Drift — Phase 6 Agent 2

**Generated:** 2026-05-08
**Drifts surfaced:** 3 (top 3 of 3 total observed; cap not needed)
**Verification approach:** code-review-graph MCP first, grep + Read fallback

## Distribution

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 2 |

| Claim type | Drifted | Verified accurate | Could not verify |
|---|---|---|---|
| File path | 0 | 14 | 0 |
| Function/class | 1 | 10 | 0 |
| Line range | 0 | 2 | 0 |
| Env var / flag | 0 | 4 | 0 |
| Behavior | 2 | 8 | 0 |
| Tracking ID | 0 | 5 | 0 |
| Version pin | 0 | 1 | 0 |

## Drifts (sorted by severity)

### #1 — [Medium] resolve_market() called "thin wrapper" — it emits calibration events

**Source claim** — `CLAUDE.md:65`
> **DB transaction atomicity lives in `_resolve_market_sync()`** (the public `resolve_market()` is a thin wrapper).

**Reality** — observed in `trading/paper_trader.py:650-681`:
```python
async def resolve_market(self, ticker: str, resolved_yes: bool) -> None:
    """Async wrapper: run the sync resolution, then emit calibration events."""
    lane_events = await asyncio.to_thread(
        self._resolve_market_sync, ticker, resolved_yes
    )
    if not lane_events:
        return
    final_resolution = 1.0 if resolved_yes else 0.0
    for trade_id, lane_name, lane_estimate in lane_events:
        trade_log.log_calibration_check(...)
        if self._calibration_task is not None:
            await self._calibration_task.record_calibration_check(...)
```

**Drift type:** behavior change — `resolve_market()` also emits `CALIBRATION_CHECK` events per lane per resolved trade (both to structured trade log and to in-process `CalibrationTask`). The PROFIT-CAL-001 note in the file explains the expansion.

**Severity rationale:** The atomicity claim in the gotcha is correct (`_resolve_market_sync` still owns the transaction). But characterizing `resolve_market()` as "thin" understates its role — it runs calibration I/O that can fail independently, and the failure path is separate from the DB transaction. Operators who see `record_calibration_check` failures may not realize they are coming from `resolve_market()` if they believe it is thin.

---

### #2 — [Low] executor.py:218 "self-documenting comment" describes the wrong aspect

**Source claim** — `CLAUDE.md:63`
> Same-signal guard must check *all* open trades for the ticker in the in-memory `Portfolio` (not the DB), not just the most recent. When both YES and NO positions exist on a ticker, "most recent" is always the opposite side and allows redundant entries past the guard. See self-documenting comment at `executor.py:218`.

**Reality** — observed in `trading/executor.py:216-218`:
```python
        # Multi-position guard: block opposing trades (no hedges) and duplicate
        # signals (same side, same probability estimate, same market price).
        # Reads from the in-memory Portfolio — no DB query at decision time.
        for pos in self._paper.portfolio.open_positions(analysis.market.ticker):
```

Line 218 specifically reads: `# Reads from the in-memory Portfolio — no DB query at decision time.`

**Drift type:** misdescription — the comment at line 218 documents the data source (in-memory vs DB), not the "all open trades not just the most recent" rationale that CLAUDE.md says it documents. The "Multi-position guard" description is at lines 216-217. The behavior (iterating all positions via `open_positions(ticker)`) is correct; only the pin to "self-documenting comment at `executor.py:218`" misdirects the reader.

**Severity rationale:** Low — the guard logic works correctly. A reader following the CLAUDE.md pointer to line 218 will find correct code but a comment about a different aspect than expected. No safety regression.

---

### #3 — [Low] MEMORY.md index uses `p_yes_at_decision_time`; code uses `market_yes_price`

**Source claim** — `~/.claude/projects/-Users-jacobparenti-vscode-kalshi-bot/memory/MEMORY.md` and `feedback_market_implied_baseline.md`
> replay win-rate baseline is `Σ p_yes_at_decision_time`, not 50% coin-flip

**Reality** — observed in `scripts/edge_replay/scorer_forensics_audit.py:71-77`:
```python
def market_implied_win_prob(row: dict[str, Any]) -> float | None:
    price = _as_float(row.get("market_yes_price"))
    side = str(row.get("side") or "").lower()
    if price is None or side not in {"yes", "no"}:
        return None
    yes_prob = price / 100.0
    return yes_prob if side == "yes" else 1.0 - yes_prob
```

The column name in the data is `market_yes_price` (in cents), not `p_yes_at_decision_time`. The MEMORY.md index shorthand `Σ p_yes_at_decision_time` is conceptually correct (market price in cents / 100 = implied probability) but uses a symbol name not present anywhere in the codebase.

**Drift type:** terminological — the math is accurate; the symbol name is an alias not found in code. A developer searching for `p_yes_at_decision_time` in the codebase will find nothing.

**Severity rationale:** Low — the test `test_market_implied_win_prob_uses_price_not_coin_flip` locks the formula. The discrepancy is in naming only.

---

## Drifts seen but cut (above cap)

None — only 3 drifts total. Cap not reached.

---

## Verifications worth recording (positive findings)

All safety-critical claims in project `CLAUDE.md` were verified against the live working tree:

- [x] **`_normalize_pem()` duplicated in both `kalshi/rest_client.py` and `kalshi/websocket_client.py`?**
  ✓ Confirmed. Both exist at module level (`rest_client.py:28`, `websocket_client.py:51`). Bodies are byte-for-byte identical (both handle `\n` escape normalization and missing PEM headers). Grep: 5 total matches across 2 files.

- [x] **`executor.py:218` self-documenting comment for same-signal guard still present?**
  ✗ Drifted — cross-ref drift #2. The comment at line 218 says "Reads from the in-memory Portfolio — no DB query at decision time." The multi-position guard description is at lines 216-217. The behavior (checking all open positions via `open_positions(ticker)`) is correct; the line-pin in the gotcha misdirects. Severity: Low.

- [x] **`governance/prompts.py:27-31` anchor_rate polarity block still there with same intent?**
  ✓ Confirmed. Lines 27-31 verbatim:
  ```
  27: Interpreting disable_source evidence:
  28: - LLM anchor rate (final_probability == market_price): the rate at which our LLM agreed with market price.
  29:   - HIGH (>=0.95) = the source contributes NO information edge. ... This is a strong DISABLE signal.
  30:   - LOW (<=0.50)  = the source produces directional views distinct from market price. ... KEEP this source.
  31:   - MID (0.50-0.95) = some information edge, no clear signal either way.
  ```
  HIGH → DISABLE, LOW → KEEP polarity is intact. Do not remove.

- [x] **`governance/llm.py:LocalQwenLLM.complete` still passes `think: False` at top level?**
  ✓ Confirmed at `governance/llm.py:211`. The payload dict has `"think": False` as a sibling of `"format"`, not nested under `"options"`. The five-line comment block at lines 196-203 explains the Ollama qwen3 interaction. PROFIT-GOV-001 reference at line 204 is also present.

- [x] **Market status check covers both `"active"` and `"open"`?**
  ✓ Confirmed at `trading/executor.py:195`:
  ```python
  if analysis.market.status not in ("open", "active"):
      return f"market status={analysis.market.status}"
  ```
  Both strings are checked.

- [x] **`MAX_BET_HARD_CAP` (not `MAX_BET_DOLLARS`) is the live env var?**
  ✓ Confirmed. `config.py:1050` uses `os.getenv("MAX_BET_HARD_CAP", "200.0")`. Grep across all `.py`, `.env`, and `.env.example` files: zero hits for `MAX_BET_DOLLARS`. `.env:75` and `.env.example:74` both use `MAX_BET_HARD_CAP`.

- [x] **`_resolve_market_sync()` exists with `with self._conn:` transaction wrapper?**
  ✓ Confirmed. `trading/paper_trader.py:685` defines `_resolve_market_sync`. The `with self._conn:` context manager appears at `paper_trader.py:723`, wrapping the UPDATE loop. Pre-calculation of all outcomes before writes is at lines 704-715. Atomicity structure is intact.

---

## Additional positive verifications (non-checklist safety claims)

- **RSA-PSS/SHA-256 with `salt_length=DIGEST_LENGTH`** — confirmed at `kalshi/rest_client.py:103-107`. Both `rest_client.py` and `websocket_client.py` use `asym_padding.PSS.DIGEST_LENGTH`.

- **`JSONDecoder.raw_decode()` in signal_analyzer** — confirmed at `analysis/signal_analyzer.py:418-434`. Uses forward-scan from each `{`, keeps last valid object. No greedy regex.

- **`websockets` header kwarg version detection** — confirmed at `kalshi/websocket_client.py:25-30`. Detects version at import, sets `_WS_HEADER_KWARG` to `"additional_headers"` (>=14.0) or `"extra_headers"`.

- **Reddit backoff uses absolute monotonic timestamp** — confirmed at `feeds/reddit_monitor.py:184,193,198`: `_backoff[subreddit] = time.monotonic() + delay` (all three error paths). Comparison at `reddit_monitor.py:372`: `resume = _backoff.get(sub, 0.0)` compared to `time.monotonic()`. Pattern is correct.

- **`aiohttp>=3.10.0`** — confirmed at `requirements.txt:5`.

- **`KALSHI_GEOPOLITICAL_SERIES` allowlist absent** — confirmed: zero grep hits in any `.py` file. Sports-prefix blocklist is `MARKET_SERIES_BLOCKLIST_PREFIXES` in `config.py:566`.

- **`cfg.dynamic_max_bet(notional)`** — confirmed at `config.py:1323,1337`.

- **scorer_forensics_audit.py at `scripts/edge_replay/scorer_forensics_audit.py`** — confirmed exists. `market_implied_win_prob` function at line 71. `test_market_implied_win_prob_uses_price_not_coin_flip` fixture at `tests/test_edge_replay_scorer_forensics_audit.py:13`.

- **Commit `c913ffd` exists** — `git cat-file -t c913ffd` returns `commit`. ✓

- **`.githooks/pre-commit:15-21` launchd template check** — confirmed. Lines 15-21 contain the `plist.template` staged-file detection block that calls `launchd_template_equivalence_audit.py --installed`.

- **PROFIT-GOV-001 closed** — confirmed `Status: COMPLETE` at `docs/profit_path_debt_log.md:3020`. `governance/llm.py:204` references it.

- **PROFIT-GOV-002** — confirmed `Status: COMPLETE` at `docs/profit_path_debt_log.md:3256`. CLAUDE.md says "Filed as PROFIT-GOV-002" without claiming open/closed status — no conflict.

- **`would_have_traded` G1-G6 gating** — the `feedback_audit_scorer_before_verdict.md` describes a historical bug (now fixed). Current `score_counterfactual_pnl.py` and `scorer_forensics_audit.py` both implement `readiness_confidence` gating. Bug is fixed; memory entry is an accurate historical record.

- **`feedback_edge_priority_over_deploy_safety.md` doc path refs** — all three cited paths exist: `docs/IMPLEMENTATION_CONTRACT.md`, `docs/governance/2026-05-06-strategic-redirect-edge-replay-priority.md`, `docs/governance/edge-replay-cycle12-report.md`.

- **`feedback_soak_acceleration_split.md` doc path refs** — `docs/superpowers/specs/2026-04-24-llm-governance-agent-design.md` exists and contains `§8.5.1 Early-close addendum` at line 398. `docs/governance/PROFIT-PHASE2-001-early-close-criteria.md` exists (9.6K).

- **`analysis/signal_analyzer.py` posts to `/chat/completions` (OpenAI-compat)** — confirmed at `signal_analyzer.py:804`: `f"{cfg.ollama_base_url}/chat/completions"`. `cfg.ollama_base_url` defaults to `http://localhost:11434/v1`, making the full URL `http://localhost:11434/v1/chat/completions`. Docstring at line 783 and 239 both say `/v1/chat/completions`. CLAUDE.md template `{ollama_base_url}/chat/completions` is accurate.
